"""Shared launch orchestration used by both `dgx-hub start` and interactive mode."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import typer
from rich.console import Console

from dgx_hub.launch.docker_run import merge_variant
from dgx_hub.plugins.base import RunContext
from dgx_hub.plugins.loader import discover_plugins
from dgx_hub.plugins.manifest import PluginManifest
from dgx_hub.process import ports as port_registry
from dgx_hub.process import state as state_store
from dgx_hub.process.state import ModelRunRecord
from dgx_hub.process.supervisor import ModelSupervisor
from dgx_hub.ui.dashboard import run_dashboard

console = Console()

# States a currently-claimed port/container_name should still be treated as
# "in use" for — i.e. everything except a definitively finished run.
ACTIVE_STATES = {"provisioning", "starting", "warming_up", "serving"}


def conflict_check(manifest: PluginManifest, running: dict[str, ModelRunRecord]) -> str | None:
    """SGLang-style variants (and any two plugins) sharing one container_name
    are mutually exclusive by construction — refuse a second one without
    stopping the first, rather than letting Docker's own name clash surface
    as an opaque error later.
    """
    container_name = manifest.docker.container_name
    if not container_name:
        return None
    for existing_name, record in running.items():
        if existing_name == manifest.plugin.name:
            continue
        if record.state not in ACTIVE_STATES:
            continue
        if record.container_name == container_name:
            return (
                f"Container name {container_name!r} is already in use by "
                f"{existing_name!r} (variant={record.variant_id}). Stop it first."
            )
    return None


def launch_and_wait(
    names: list[str],
    variant_by_name: dict[str, str | None],
    env_overrides_by_name: dict[str, dict[str, str]],
) -> None:
    """Resolve + start every named plugin concurrently, show the live
    dashboard until each reaches a terminal state, then persist and report
    final status. Exits the process (via typer.Exit) on any pre-launch
    resolution error (unknown plugin, container-name conflict).
    """
    result = discover_plugins()
    running = state_store.load_all()
    claimed_ports = {rec.port for rec in running.values() if rec.state in ACTIVE_STATES}

    supervisors: dict[str, ModelSupervisor] = {}

    for name in names:
        loaded = result.plugins.get(name)
        if loaded is None:
            console.print(f"[red]No such plugin:[/red] {name!r}. Try `dgx-hub list`.")
            raise typer.Exit(code=1)

        plugin = loaded.plugin
        manifest = plugin.manifest

        conflict = conflict_check(manifest, running)
        if conflict:
            console.print(f"[red]{conflict}[/red]")
            raise typer.Exit(code=1)

        variant_id = variant_by_name.get(name) or manifest.default_variant_id()
        variant_spec = manifest.get_variant(variant_id) if variant_id else None
        effective = merge_variant(manifest.docker, variant_spec)
        preferred_port = effective.ports[0].container_port if effective.ports else 0
        publish_strategy = (
            effective.ports[0].publish_strategy if effective.ports else "fixed-exclusive"
        )
        allocated_port = (
            preferred_port
            if publish_strategy == "fixed-exclusive"
            else port_registry.allocate_port(claimed_ports, preferred=preferred_port)
        )
        claimed_ports.add(allocated_port)

        ctx = RunContext(
            repo_dir=plugin.repo_dir,
            state_dir=plugin.repo_dir,
            variant_id=variant_id,
            env_values=manifest.resolve_env_values(env_overrides_by_name.get(name, {})),
            allocated_port=allocated_port,
        )
        supervisors[name] = ModelSupervisor(name, plugin, ctx)

        record = ModelRunRecord(
            name=name,
            variant_id=variant_id,
            container_name=None,
            backend_address="",
            port=allocated_port,
            state="provisioning",
            started_at=datetime.now(UTC).isoformat(),
        )
        running[name] = record
        state_store.save(record)

    console.print(f"Starting {len(supervisors)} model(s)...")
    for supervisor in supervisors.values():
        supervisor.start()

    run_dashboard(supervisors)

    for name, supervisor in supervisors.items():
        status = supervisor.status
        record = running[name]
        record.state = status.state.value
        if status.handle is not None:
            handle = status.handle
            record.container_name = handle.container_name
            record.backend_address = handle.backend_address
            record.gateway_address = handle.gateway_address
            record.kind = handle.kind.value
            record.compose_project = handle.compose_project
            record.compose_file = str(handle.compose_file) if handle.compose_file else None
            record.service_name = handle.service_name

        if status.state.value == "serving" and record.backend_address:
            record.served_model_ids = _discover_served_model_ids(
                record.backend_address, fallback=name
            )
        state_store.save(record)

        if status.state.value == "serving":
            console.print(f"[green]{name}[/green]: serving at {record.backend_address}")
        else:
            console.print(f"[red]{name}[/red]: {status.error or status.state.value}")


def _discover_served_model_ids(backend_address: str, fallback: str) -> list[str]:
    """Ask the backend's own /v1/models what id(s) it serves — this is what
    the gateway later routes on. Falls back to the plugin name if the
    backend isn't OpenAI-/v1/models-compatible or doesn't answer.
    """
    try:
        response = httpx.get(f"http://{backend_address}/v1/models", timeout=5.0)
        response.raise_for_status()
        data = response.json().get("data", [])
        ids = [item["id"] for item in data if "id" in item]
        if ids:
            return ids
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return [fallback]
