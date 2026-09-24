"""Shared launch orchestration used by both `dgx-hub start` and interactive mode."""

from __future__ import annotations

import logging
import sys
import time
from datetime import UTC, datetime

import httpx
import typer
from rich.console import Console

from dgx_hub.config import repos_dir
from dgx_hub.launch.docker_run import merge_variant
from dgx_hub.logging_config import console_logging, get_logger, log_file_path
from dgx_hub.plugins.base import RunContext
from dgx_hub.plugins.loader import LoadedPlugin, discover_plugins
from dgx_hub.plugins.manifest import PluginManifest
from dgx_hub.process import ports as port_registry
from dgx_hub.process import preflight
from dgx_hub.process import state as state_store
from dgx_hub.process.lifecycle import StopError, stop_model
from dgx_hub.process.preflight import ACTIVE_STATES
from dgx_hub.process.state import ModelRunRecord
from dgx_hub.process.supervisor import ModelSupervisor
from dgx_hub.ui.dashboard import run_dashboard

console = Console()
logger = get_logger(__name__)

# How long to wait for freed memory to show up in MemAvailable after
# stopping blockers (unified-memory pages are released asynchronously).
_MEMORY_SETTLE_SECONDS = 30.0


def conflict_check(manifest: PluginManifest, running: dict[str, ModelRunRecord]) -> str | None:
    """Different variants sharing one container_name
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
    *,
    replace: bool = False,
    force: bool = False,
    log_level: int | None = logging.INFO,
) -> None:
    """Resolve + start every named plugin concurrently, show the live
    dashboard until each reaches a terminal state, then persist and report
    final status. Exits the process (via typer.Exit) on any pre-launch
    resolution error (unknown plugin, container-name conflict, resource
    preflight refused).

    `replace` stops conflicting running models without asking; `force`
    skips the resource preflight entirely. `log_level` is what gets
    mirrored to the terminal (None = dashboard only; the log file always
    gets everything).
    """
    if log_level is None:
        _launch_and_wait(names, variant_by_name, env_overrides_by_name, replace, force)
        return
    with console_logging(console, log_level):
        _launch_and_wait(names, variant_by_name, env_overrides_by_name, replace, force)


def _launch_and_wait(
    names: list[str],
    variant_by_name: dict[str, str | None],
    env_overrides_by_name: dict[str, dict[str, str]],
    replace: bool,
    force: bool,
) -> None:
    result = discover_plugins()
    running = state_store.load_all()

    unknown = [name for name in names if name not in result.plugins]
    if unknown:
        console.print(f"[red]No such plugin:[/red] {unknown[0]!r}. Try `dgx-hub list`.")
        raise typer.Exit(code=1)

    if not force:
        _run_preflight(names, result.plugins, running, replace)
        running = state_store.load_all()

    claimed_ports = {rec.port for rec in running.values() if rec.state in ACTIVE_STATES}

    supervisors: dict[str, ModelSupervisor] = {}

    for name in names:
        plugin = result.plugins[name].plugin
        manifest = plugin.manifest

        conflict = conflict_check(manifest, running)
        if conflict:
            logger.error("%s: %s", name, conflict)
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
        logger.info(
            "%s: resolved variant=%r container_name=%r port=%d (%s)",
            name,
            variant_id,
            effective.container_name,
            allocated_port,
            publish_strategy,
        )

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
            # Recorded up front so a failed or interrupted start can still be
            # found (and stopped) by `dgx-hub stop`.
            container_name=effective.container_name,
            backend_address="",
            port=allocated_port,
            state="provisioning",
            started_at=datetime.now(UTC).isoformat(),
        )
        running[name] = record
        state_store.save(record)

    console.print(
        f"Starting {len(supervisors)} model(s)... "
        f"(detailed startup logs: {log_file_path()})"
    )
    logger.info("starting %d model(s): %s", len(supervisors), ", ".join(supervisors))
    for supervisor in supervisors.values():
        supervisor.start()

    run_dashboard(supervisors, console=console)

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


def _run_preflight(
    names: list[str],
    plugins: dict[str, LoadedPlugin],
    running: dict[str, ModelRunRecord],
    replace: bool,
) -> None:
    """Refuse (or, with consent, make room for) a launch that won't fit next
    to the models already running. Exits via typer.Exit when it can't proceed.
    """
    starting = set(names)
    for name in names:
        plugin = plugins[name].plugin
        resources = plugin.metadata.resources
        disk_path = repos_dir()
        check = preflight.check(resources, running, starting, disk_path)

        if not check.disk_ok:
            console.print(
                f"[yellow]{name}: only {check.disk_free_gib:.0f} GiB disk free "
                f"(manifest asks for {check.disk_required_gib:.0f} GiB, including any "
                "first-run download)[/yellow]"
            )
        if check.ok:
            continue

        if not check.blockers:
            console.print(
                f"[red]{name}: not enough free memory: {check.describe()}.[/red] "
                "Nothing managed by dgx-hub is running; check `docker ps` / "
                "`ps -eo rss,cmd --sort=-rss | head`, or pass --force to try anyway."
            )
            raise typer.Exit(code=1)

        blocker_names = ", ".join(r.name for r in check.blockers)
        console.print(
            f"[yellow]{name}: {check.describe()}. Running models in the way: "
            f"{blocker_names}[/yellow]"
        )
        if not replace:
            if not sys.stdin.isatty():
                console.print(
                    "[red]Refusing to start.[/red] Stop them first, or pass --replace "
                    "(or --force to skip this check)."
                )
                raise typer.Exit(code=1)
            if not typer.confirm(f"Stop {blocker_names} first?", default=False):
                raise typer.Exit(code=1)

        for record in check.blockers:
            loaded = plugins.get(record.name)
            console.print(f"[bold]{record.name}[/bold]: stopping...")
            try:
                stop_model(record, loaded.plugin if loaded else None)
            except StopError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc

        _wait_for_memory(name, resources.min_free_memory_gib)


def _wait_for_memory(name: str, required_gib: float) -> None:
    deadline = time.monotonic() + _MEMORY_SETTLE_SECONDS
    while True:
        available = preflight.mem_available_gib()
        if available is None or available >= required_gib:
            return
        if time.monotonic() >= deadline:
            console.print(
                f"[red]{name}: still only {available:.0f} GiB memory available after "
                f"stopping (needs {required_gib:.0f} GiB).[/red] Something outside "
                "dgx-hub is holding memory, or pass --force to try anyway."
            )
            raise typer.Exit(code=1)
        time.sleep(1.0)


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
