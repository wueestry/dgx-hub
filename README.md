# dgx-hub

A CLI for selecting, starting, and monitoring LLM model servers on an NVIDIA DGX Spark.

Model servers (SGLang, vLLM, etc.) usually ship as a reference repo with its own Docker setup, environment variables, and quirks. dgx-hub wraps these behind a declarative `plugin.toml` manifest and a single, generic launch engine — so starting a model is `dgx-hub start <name>` instead of hand-rolling a `docker run` invocation for each repo.

## Features

- **Plugin-based model catalog** — each model is described by a `plugin.toml` manifest (Docker image, ports, env vars, health check, resource requirements), not custom code.
- **Variants** — a single plugin can expose multiple launch configurations (e.g. different speculative-decoding modes) that override parts of the base Docker spec.
- **Generic Docker launch engine** — manifests are rendered into `docker run` (or `docker compose`) invocations by the CLI itself; plugins don't shell out to bespoke wrapper scripts unless declared via an explicit fallback.
- **Port allocation & conflict detection** — automatically assigns host ports and prevents starting a plugin whose container name is already in use.
- **State tracking** — remembers what's been started, in `platformdirs`-managed state, ground-truthed against live Docker container status.

## Installation

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

This installs the `dgx-hub` command into the project's virtual environment (`.venv/bin/dgx-hub`).

## Usage

```bash
# List all discovered plugins and their last-known status
dgx-hub list

# Start a model (default variant)
dgx-hub start qwen3-27b-sglang

# Start with a specific variant and env var overrides
dgx-hub start qwen3-27b-sglang --variant dspark --set CONTEXT_LENGTH=65536

# Show status of every model ever started, ground-truthed against Docker
dgx-hub status
dgx-hub status --json

# Tail or follow a running model's container logs
dgx-hub logs qwen3-27b-sglang --follow

# Stop one or more models, or everything
dgx-hub stop qwen3-27b-sglang
dgx-hub stop --all
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
- `[fallback]` (optional, last resort) — opaque start/stop commands for repos whose launch logic can't be expressed declaratively

See `plugins/qwen3-27b-sglang/plugin.toml` for a complete example, and `.claude/skills/plugin-from-script` for guidance on turning a reference repo's launch script into a manifest.

User-supplied plugins can also be dropped into dgx-hub's user config directory (platform-dependent, via `platformdirs`); built-in plugins win name collisions.

## Project layout

```
src/dgx_hub/
├── cli.py              # Typer app wiring
├── commands/           # list, start, stop, status, logs
├── config.py           # filesystem locations (config/state/data dirs)
├── docker_adapter.py   # generic docker run/compose start/stop/status/logs
├── launch/             # renders a manifest + variant into a launch spec
├── plugins/            # manifest schema, loader/discovery, plugin protocol
├── process/            # port allocation, run-state persistence
├── gateway/            # (planned) reverse proxy for routing to running backends
└── ui/                 # (planned) interactive/live views
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
