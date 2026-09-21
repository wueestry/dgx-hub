"""dgx-hub Typer app entry point — command wiring only."""

from __future__ import annotations

import typer

from dgx_hub.commands import list_cmd, logs, start, status, stop

app = typer.Typer(
    name="dgx-hub",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    help="Select, start, and monitor DGX Spark model servers.",
)

app.command("list")(list_cmd.list_models)
app.command("start")(start.start_models)
app.command("stop")(stop.stop_models)
app.command("status")(status.show_status)
app.command("logs")(logs.show_logs)


if __name__ == "__main__":
    app()
