"""dgx-hub Typer app entry point — command wiring only."""

from __future__ import annotations

import typer

from dgx_hub.commands import gateway_cmd, interactive, list_cmd, logs, start, status, stop

app = typer.Typer(
    name="dgx-hub",
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode="rich",
    help="Select, start, and monitor DGX Spark model servers.",
)

app.command("list")(list_cmd.list_models)
app.command("start")(start.start_models)
app.command("stop")(stop.stop_models)
app.command("status")(status.show_status)
app.command("logs")(logs.show_logs)

gateway_app = typer.Typer(help="The OpenAI-compatible routing proxy.")
gateway_app.command("start")(gateway_cmd.gateway_start)
gateway_app.command("stop")(gateway_cmd.gateway_stop)
gateway_app.command("status")(gateway_cmd.gateway_status)
gateway_app.command("sync")(gateway_cmd.gateway_sync)
gateway_app.command("logs")(gateway_cmd.gateway_logs)

gateway_keys_app = typer.Typer(help="Manage virtual API keys for calling the gateway.")
gateway_keys_app.command("create")(gateway_cmd.gateway_keys_create)
gateway_keys_app.command("list")(gateway_cmd.gateway_keys_list)
gateway_app.add_typer(gateway_keys_app, name="keys")

app.add_typer(gateway_app, name="gateway")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        interactive.run_interactive()


if __name__ == "__main__":
    app()
