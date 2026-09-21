"""`dgx-hub list` — show all discovered model plugins and their last-known status."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from dgx_hub.plugins.loader import discover_plugins
from dgx_hub.process import state as state_store

console = Console()


def list_models() -> None:
    """List all discovered model plugins."""
    result = discover_plugins()
    records = state_store.load_all()

    table = Table(title="dgx-hub plugins")
    table.add_column("Name", style="bold")
    table.add_column("Description")
    table.add_column("Variants")
    table.add_column("Last known status")

    for name in sorted(result.plugins):
        manifest = result.plugins[name].plugin.manifest
        variants = ", ".join(v.id for v in manifest.variant) or "-"
        record = records.get(name)
        status_text = f"[green]{record.state}[/green]" if record else "[dim]not started[/dim]"
        table.add_row(name, manifest.plugin.description, variants, status_text)

    console.print(table)

    if result.broken:
        console.print()
        console.print("[bold red]Broken plugins:[/bold red]")
        for b in result.broken:
            console.print(f"  [red]{b.manifest_path}[/red]: {b.error}")
