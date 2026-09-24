"""Live rich dashboard rendering ModelSupervisor status for multiple models."""

from __future__ import annotations

import time

from rich.console import Console
from rich.live import Live
from rich.table import Table

from dgx_hub.process.supervisor import ModelSupervisor

_STATE_STYLES = {
    "not_started": "dim",
    "provisioning": "yellow",
    "starting": "yellow",
    "warming_up": "yellow",
    "serving": "green",
    "unhealthy": "red",
    "stopping": "yellow",
    "stopped": "dim",
    "failed": "red",
}


def _render(supervisors: dict[str, ModelSupervisor]) -> Table:
    table = Table(title="dgx-hub")
    table.add_column("Name", style="bold")
    table.add_column("State")
    table.add_column("Backend")
    table.add_column("Detail")

    for name, supervisor in supervisors.items():
        status = supervisor.status
        style = _STATE_STYLES.get(status.state.value, "")
        state_text = f"[{style}]{status.state.value}[/{style}]" if style else status.state.value
        backend = status.handle.backend_address if status.handle else "-"
        # One line only: the full error is printed once the dashboard exits.
        detail = (status.error or status.message or "").split("\n", 1)[0]
        table.add_row(name, state_text, backend, detail)

    return table


def run_dashboard(
    supervisors: dict[str, ModelSupervisor],
    console: Console | None = None,
    poll_interval: float = 0.5,
) -> None:
    """Render a live table until every supervisor reaches a terminal state.

    A long-booting model (up to a couple of hours for some plugins) simply
    keeps this loop running — Ctrl-C leaves the containers running in the
    background and just stops watching, since supervision only drives the
    already-launched containers' visible status, not their lifetime.
    """
    console = console or Console()
    try:
        # Non-transient: the last rendered table stays on screen after exit.
        with Live(_render(supervisors), console=console, refresh_per_second=4) as live:
            while True:
                live.update(_render(supervisors))
                if all(s.is_terminal() for s in supervisors.values()):
                    break
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        console.print(
            "[yellow]Interrupted — models continue running in the background. "
            "Use `dgx-hub status` to check on them.[/yellow]"
        )
        return
