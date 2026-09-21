"""`dgx-hub start` — provision (if needed) and launch one or more models."""

from __future__ import annotations

from datetime import UTC, datetime

import typer
from rich.console import Console

from dgx_hub.launch.docker_run import merge_variant
from dgx_hub.plugins.base import RunContext
from dgx_hub.plugins.loader import discover_plugins
from dgx_hub.plugins.manifest import PluginManifest
from dgx_hub.process import ports as port_registry
from dgx_hub.process import state as state_store
from dgx_hub.process.state import ModelRunRecord

console = Console()

_ACTIVE_STATES = {"starting", "warming_up", "serving"}


def start_models(
    names: list[str] = typer.Argument(..., help="Plugin name(s) to start"),
    variant: str | None = typer.Option(
        None, "--variant", help="Variant id (defaults to the plugin's default variant)"
    ),
    set_env: list[str] = typer.Option(
        [], "--set", help="Override an env var, KEY=VALUE (repeatable)"
    ),
) -> None:
    """Start one or more models by plugin name."""
    result = discover_plugins()
    overrides = _parse_overrides(set_env)
    running = state_store.load_all()
    claimed_ports = {
        rec.port for rec in running.values() if rec.state in _ACTIVE_STATES
    }

    for name in names:
        loaded = result.plugins.get(name)
        if loaded is None:
            console.print(f"[red]No such plugin:[/red] {name!r}. Try `dgx-hub list`.")
            raise typer.Exit(code=1)

        plugin = loaded.plugin
        manifest = plugin.manifest
        variant_id = variant or manifest.default_variant_id()

        conflict = _conflict_check(manifest, running)
        if conflict:
            console.print(f"[red]{conflict}[/red]")
            raise typer.Exit(code=1)

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
            env_values=manifest.resolve_env_values(overrides),
            allocated_port=allocated_port,
        )

        console.print(f"[bold]{name}[/bold]: provisioning...")
        try:
            plugin.provision(ctx)
        except Exception as exc:
            console.print(f"[red]{name}: provisioning failed: {exc}[/red]")
            raise typer.Exit(code=1) from exc

        console.print(f"[bold]{name}[/bold]: starting (variant={variant_id})...")
        try:
            handle = plugin.start(ctx)
        except Exception as exc:
            console.print(f"[red]{name}: start failed: {exc}[/red]")
            raise typer.Exit(code=1) from exc

        record = ModelRunRecord(
            name=name,
            variant_id=variant_id,
            container_name=handle.container_name,
            backend_address=handle.backend_address,
            port=allocated_port,
            state="starting",
            started_at=datetime.now(UTC).isoformat(),
        )
        state_store.save(record)
        running[name] = record
        console.print(
            f"[green]{name}[/green]: launched, backend at {handle.backend_address} "
            f"(container {handle.container_name})"
        )


def _parse_overrides(pairs: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            console.print(f"[red]Invalid --set value (expected KEY=VALUE):[/red] {pair}")
            raise typer.Exit(code=1)
        key, _, value = pair.partition("=")
        overrides[key] = value
    return overrides


def _conflict_check(
    manifest: PluginManifest, running: dict[str, ModelRunRecord]
) -> str | None:
    container_name = manifest.docker.container_name
    for existing_name, record in running.items():
        if existing_name == manifest.plugin.name:
            continue
        if record.state not in _ACTIVE_STATES:
            continue
        if record.container_name and record.container_name == container_name:
            return (
                f"Container name {container_name!r} is already in use by "
                f"{existing_name!r} (variant={record.variant_id}). Stop it first."
            )
    return None
