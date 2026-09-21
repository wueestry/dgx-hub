"""`dgx-hub status` — show status of known models, ground-truthed against Docker."""

from __future__ import annotations

import json as json_module

import typer
from rich.console import Console
from rich.table import Table

from dgx_hub import docker_adapter
from dgx_hub.plugins.base import ContainerHandle, RuntimeKind
from dgx_hub.process import state as state_store

console = Console()


def show_status(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Show status of every model dgx-hub has ever started."""
    running = state_store.load_all()

    rows: list[tuple[str, str, str, str, str]] = []
    for name, record in sorted(running.items()):
        docker_state = "-"
        if record.container_name is not None:
            handle = ContainerHandle(
                kind=RuntimeKind.DOCKER_RUN, container_name=record.container_name
            )
            ds = docker_adapter.status(handle)
            if not ds.exists:
                docker_state = "gone"
            elif ds.running:
                docker_state = ds.health or "running"
            else:
                docker_state = f"exited ({ds.exit_code})"
        rows.append(
            (name, record.variant_id or "-", str(record.port), record.backend_address, docker_state)
        )

    if json_output:
        console.print(
            json_module.dumps(
                [
                    {
                        "name": n,
                        "variant": v,
                        "port": p,
                        "backend_address": b,
                        "docker_state": d,
                    }
                    for n, v, p, b, d in rows
                ],
                indent=2,
            )
        )
        return

    table = Table(title="dgx-hub status")
    table.add_column("Name", style="bold")
    table.add_column("Variant")
    table.add_column("Port")
    table.add_column("Backend")
    table.add_column("Docker state")
    for row in rows:
        table.add_row(*row)
    console.print(table)

    if not rows:
        console.print("[dim]No models started yet. Try `dgx-hub start <name>`.[/dim]")
