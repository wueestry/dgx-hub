# dgx-hub

A CLI for selecting, starting, and monitoring LLM model servers on an NVIDIA DGX Spark.

Model servers (SGLang, vLLM, etc.) usually ship as a reference repo with its own Docker setup, environment variables, and quirks. dgx-hub wraps these behind a declarative `plugin.toml` manifest and a single, generic launch engine — so starting a model is `dgx-hub start <name>` instead of hand-rolling a `docker run` invocation for each repo.

## Features

- **Plugin-based model catalog** — each model is described by a `plugin.toml` manifest (Docker image, ports, env vars, health check, resource requirements), not custom code.
- **Variants** — a single plugin can expose multiple launch configurations (e.g. different speculative-decoding modes) that override parts of the base Docker spec.
- **Generic Docker launch engine** — manifests are rendered into `docker run` (or `docker compose`) invocations by the CLI itself; plugins don't shell out to bespoke wrapper scripts unless declared via an explicit fallback.
- **Port allocation & conflict detection** — automatically assigns host ports and prevents starting a plugin whose container name is already in use.
- **Concurrent multi-model start** — each model's provision → start → health-poll lifecycle runs in its own background supervisor thread, rendered as a live `rich` dashboard until every model reaches SERVING (or FAILED).
- **One public port, route by model** — an optional OpenAI-compatible gateway proxies `/v1/...` requests to whichever backend is currently serving the request's `"model"`, so multiple models are reachable through one familiar endpoint instead of one port each.
- **State tracking** — remembers what's been started, in `platformdirs`-managed state, ground-truthed against live Docker container status.

## Installation

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                    # core CLI
uv sync --extra gateway    # + the OpenAI-compatible routing gateway
```

This installs the `dgx-hub` command into the project's virtual environment (`.venv/bin/dgx-hub`).

## Usage

```bash
# Interactive: pick models (and variants, and any required env vars) from a menu
dgx-hub

# List all discovered plugins and their last-known status
dgx-hub list

# Start a model (default variant), watching a live dashboard until it's serving
dgx-hub start qwen3.8-27b-sglang

# Start several concurrently — each gets its own port and supervisor thread
dgx-hub start qwen3.8-27b-sglang ornith-1-5-35b-a3b

# Start with a specific variant and env var overrides
dgx-hub start qwen3.8-27b-sglang --variant dspark --set CONTEXT_LENGTH=65536

# Show status of every model ever started, ground-truthed against Docker
dgx-hub status
dgx-hub status --json

# Tail or follow a running model's container logs
dgx-hub logs qwen3.8-27b-sglang --follow

# Stop one or more models, or everything
dgx-hub stop qwen3.8-27b-sglang
dgx-hub stop --all

# Run the OpenAI-compatible gateway (needs `uv sync --extra gateway`) — routes
# http://localhost:8888/v1/... to whichever backend serves the request's "model"
dgx-hub gateway run
```

## Plugins

Plugins live under `plugins/<name>/plugin.toml`. A manifest declares:

- `[plugin]` — name, display name, description, docs URL
- `[source]` — the upstream repo URL
- `[provision]` (optional) — a host-side setup step (e.g. weight download) run before launch
- `[[patch]]` (optional, last resort) — find/replace or diff patches applied to the cloned repo
- `[docker]` — image, container name, network mode, GPUs, volumes, ports, command args
- `[[variant]]` — named overrides layered on top of `[docker]` (e.g. alternate image or extra flags)
- `[health]` — the HTTP endpoint and timing used to determine when a container is ready
- `[resources]` — minimum free disk/memory and whether a GPU is required
- `[env.*]` — typed, validated environment variables exposed to `--set KEY=VALUE`
- `[fallback]` (optional, last resort) — an opaque start command for repos whose launch logic (dynamic compose generation, entrypoint chaining, host-side path resolution) can't be expressed declaratively; `{variant}` in the command is substituted with the selected variant id. `[docker]` still supplies the container identity used for the generic stop/status/logs operations afterwards.

A port entry (`[docker].ports`) can declare `port_override_env_var` — the env var the app itself reads to bind a specific port. This is required for `network_mode = "host"` plugins using `publish_strategy = "loopback-remap"` (there's no Docker `-p` mapping under host networking), and is also how `[fallback]`-launched plugins get told which port to bind, regardless of network mode.

See `plugins/qwen3.8-27b-sglang/plugin.toml` for a complete example, and `.claude/skills/plugin-from-script` for guidance on turning a reference repo's launch script into a manifest.

User-supplied plugins can also be dropped into dgx-hub's user config directory (platform-dependent, via `platformdirs`); built-in plugins win name collisions.

## Project layout

```
src/dgx_hub/
├── cli.py              # Typer app wiring
├── commands/           # list, start, stop, status, logs, gateway, interactive,
│                       #   and launch_flow.py (shared start/interactive orchestration)
├── config.py           # filesystem locations (config/state/data dirs)
├── docker_adapter.py   # generic docker run/compose start/stop/status/logs
├── launch/             # renders a manifest + variant into a docker-run or compose launch spec
├── plugins/            # manifest schema, loader/discovery, plugin protocol
├── process/            # port allocation, run-state persistence, ModelSupervisor (provision -> start -> health-poll)
├── gateway/            # OpenAI-compatible reverse proxy: registry, streaming httpx passthrough, FastAPI app
└── ui/                 # rich Live dashboard, questionary picker/prompts
plugins/                # built-in plugin manifests
tests/                  # pytest suite
```

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run mypy .
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
