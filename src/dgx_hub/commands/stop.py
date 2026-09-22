"""`dgx-hub stop` — stop one or more running models."""

from __future__ import annotations

import typer
from rich.console import Console

from dgx_hub import docker_adapter
from dgx_hub.gateway import auto_sync
from dgx_hub.process import state as state_store

console = Console()


def stop_models(
    names: list[str] = typer.Argument(None, help="Plugin name(s) to stop"),
    all_: bool = typer.Option(False, "--all", help="Stop every known running model"),
    timeout: int = typer.Option(
        30, "--timeout", help="Grace period in seconds before Docker's SIGKILL fallback"
    ),
) -> None:
    """Stop one or more running models."""
    running = state_store.load_all()

    if all_:
        targets = list(running.keys())
    else:
        if not names:
            console.print("[red]Specify model name(s) or pass --all[/red]")
            raise typer.Exit(code=1)
        targets = names

    for name in targets:
        record = running.get(name)
        if record is None or record.container_name is None:
            console.print(f"[yellow]{name}: no state on record, nothing to stop[/yellow]")
            continue

        handle = record.to_handle()
        console.print(f"[bold]{name}[/bold]: stopping (timeout={timeout}s)...")
        try:
            docker_adapter.stop(handle, grace_seconds=timeout)
        except docker_adapter.DockerAdapterError as exc:
            console.print(f"[red]{name}: stop failed: {exc}[/red]")
            continue

        record.state = "stopped"
        state_store.save(record)
        console.print(f"[green]{name}[/green]: stopped")

    auto_sync.try_reconcile_quietly()
