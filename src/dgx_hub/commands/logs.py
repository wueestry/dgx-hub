"""`dgx-hub logs` — tail a running model's container logs."""

from __future__ import annotations

import typer
from rich.console import Console

from dgx_hub import docker_adapter
from dgx_hub.process import state as state_store

console = Console()


def show_logs(
    name: str = typer.Argument(..., help="Plugin name"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream new log lines"),
    tail: int = typer.Option(200, "--tail", help="Number of lines to show"),
) -> None:
    """Show (or follow) a running model's container logs."""
    record = state_store.get(name)
    if record is None or record.container_name is None:
        console.print(f"[red]{name}: no state on record[/red]")
        raise typer.Exit(code=1)

    handle = record.to_handle()
    try:
        for line in docker_adapter.logs(handle, follow=follow, tail=tail):
            console.print(line, markup=False, highlight=False)
    except KeyboardInterrupt:
        pass
