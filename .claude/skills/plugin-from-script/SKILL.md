---
name: plugin-from-script
description: Turn a GitHub repo or Hugging Face model reference for an LLM-serving stack into a dgx-hub plugin.toml manifest. Use when the user wants to add a new model/plugin to dgx-hub from an upstream repo, Docker setup, or HF model card.
---

# Generating a dgx-hub plugin.toml

dgx-hub wraps a reference LLM-serving repo (or a bare Hugging Face checkpoint
served by a generic image) behind a declarative `plugin.toml` manifest. This
skill turns a GitHub repo URL or a Hugging Face model reference into a
manifest that fits the existing schema and style.

Read these two files first, every time — they are the source of truth, not
this skill's paraphrase of them:

- `src/dgx_hub/plugins/manifest.py` — the actual Pydantic schema. Every
  field, type, and validator lives here. If this skill and the schema ever
  disagree, the schema wins.
- `plugins/qwen3.8-27b-sglang/plugin.toml` — the canonical example the
  project's own README points to. Match its shape and comment density, not
  the denser files in `plugins/` (some of those predate a comment cleanup
  and are being trimmed down towards this one).

## 1. Figure out what you're dealing with

**A GitHub repo with its own Docker setup** (most common): read its
README, `Dockerfile`, any `docker-compose.yml`, and its launch script(s).
You're looking for:
- The image (pull it prebuilt, or does the repo build one? if the latter,
  note that in `[provision]` or flag it to the user — dgx-hub doesn't build
  images itself).
- How the container is actually started: a single `docker run` with a fixed
  image/flags → declarable via `[docker]` directly. A wrapper script that
  resolves host paths, generates a compose file on the fly, chains
  `--entrypoint`, or reads live host metrics (memory, GPU state) to pick
  flags → not expressible declaratively, use `[fallback]` instead (see
  below).
- Ports, env vars the script reads, and a health-check endpoint (usually an
  OpenAI-compatible `/v1/models` or `/health`).
- Whether weights are downloaded by the launch itself (no `[provision]`
  needed) or by a separate step (`[provision].command`).

**A Hugging Face model reference**: check whether it's just a checkpoint
meant to be served by a generic image this repo already uses for a similar
architecture (vLLM, SGLang, llama.cpp) — if so, `[source].repo_url` is the
HF URL, `[docker].image` is the existing generic serving image, and
`command_args` is a `vllm serve <repo_id> ...` (or equivalent) invocation.
Look at `plugins/qwen3.6-35b-a3b/plugin.toml` or
`plugins/nemotron-3-nano-4b/plugin.toml` for this pattern. If the checkpoint
needs a bespoke image or launch logic, treat it like a GitHub repo case.

## 2. `[docker]` vs `[fallback]`

Default to `[docker]` — the CLI renders it into a real `docker run`/`docker
compose` invocation itself, which is the whole point of the manifest format.
Only reach for `[fallback]` (an opaque `start_command`/`stop_command` run in
the cloned repo) when the launch genuinely can't be expressed statically:
dynamic compose generation, `--entrypoint` chaining, or flags computed from
live host state at boot. When you do use `[fallback]`, still fill in
`[docker]` as an accurate best-effort static description (image, mounts,
ports, env) — it's used for resource estimation and by anything reading the
manifest without executing it, and the generic stop/status/logs operations
still key off `[docker].container_name`. See
`plugins/qwen3.8-flash-next/plugin.toml` and
`plugins/deepseek-v4-flash-spark/plugin.toml` for worked `[fallback]`
examples, including the comment explaining *why* each needed it.

## 3. Writing `[env.*]`, `[[env.validation]]`, and `choices`

These are enforced at runtime, not cosmetic:

- `type = "enum"` requires `choices`, and both `--set` overrides and the
  interactive picker are validated against them — a bad value is rejected
  with a clear error before anything launches.
- `[[env.validation]]` rules are evaluated (via `simpleeval`) against the
  resolved, typed env values plus a special `variant` name (the selected
  variant id, `""` if none) right before launch. Write a rule as a normal
  Python-ish boolean expression referencing declared `env.*` names, e.g.:

  ```toml
  [[env.validation]]
  rule = "not (YARN and variant in ('dspark', 'dflash') and CONTEXT_LENGTH > 262144)"
  message = "YaRN is incompatible with DSpark/DFlash2 at context lengths above 262144"
  ```

  A rule that evaluates falsy blocks the launch and prints `message`. Only
  use `and`/`or`/`not`, comparisons, and `in` over tuples/lists — the
  evaluator is a restricted sandbox (`simpleeval.EvalWithCompoundTypes`), not
  full Python; no function calls, imports, or attribute access.
- Always give an `EXTRA_ARGS` (or repo-appropriate) free-form string escape
  hatch as the last `[env.*]` entry — every existing plugin does this so a
  user isn't blocked by a flag the manifest didn't anticipate.

## 4. Comment style

Match `plugins/qwen3.8-27b-sglang/plugin.toml`: short, single-line comments
only where the *why* is genuinely non-obvious (a workaround, a gotcha, a
reason a section is absent). Don't narrate what a field already says, and
don't write multi-paragraph rationale blocks — if a real gotcha needs more
than a couple of lines to explain, it's a sign the launch logic might belong
in `[fallback]` with the detail in its own comment, not scattered across
`[docker]`. `env.*.description` fields (not `#` comments) are the right home
for user-facing explanation of a variable — those aren't comments and can be
as long as needed, since they're shown to the user in the interactive picker.

## 5. Validate before handing it back

```bash
uv run python -c "
import tomllib
from pathlib import Path
from dgx_hub.plugins.manifest import PluginManifest
raw = tomllib.loads(Path('plugins/<name>/plugin.toml').read_text())
manifest = PluginManifest.from_toml_dict(raw)
print(manifest.plugin.name, manifest.default_variant_id())
"
```

This parses and runs every `model_validator` in the schema (required fields
per `[docker].mode`, host-networking port semantics, unique/single-default
variants, enum defaults within `choices`, exactly-one-kind `[[patch]]`
entries). Fix whatever it flags before considering the manifest done — don't
guess at the schema's requirements when the validator will tell you exactly
what's missing.

If you also add or change an `[[env.validation]]` rule, sanity-check it
evaluates the way you expect:

```bash
uv run python -c "
import tomllib
from pathlib import Path
from dgx_hub.plugins.manifest import PluginManifest
from dgx_hub.plugins.env_validation import evaluate_rules
raw = tomllib.loads(Path('plugins/<name>/plugin.toml').read_text())
manifest = PluginManifest.from_toml_dict(raw)
typed = manifest.resolve_env_values_typed({'SOME_VAR': 'value-that-should-fail'})
print(evaluate_rules(manifest, typed, variant_id='some-variant'))
"
```

Finally, run `uv run pytest tests/test_manifest.py` — its
`test_all_builtin_plugin_manifests_parse` case parses every file under
`plugins/*/plugin.toml`, so a broken manifest (yours or an existing one)
fails fast there too.
