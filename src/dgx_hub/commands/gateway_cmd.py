"""`dgx-hub gateway run` — the OpenAI-compatible routing proxy."""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()


def gateway_run(
    host: str = typer.Option("0.0.0.0", "--host", help="Interface to bind"),
    port: int = typer.Option(8888, "--port", help="Public port to listen on"),
) -> None:
    """Run the gateway in the foreground, proxying by request \"model\"."""
    try:
        import uvicorn
    except ImportError as exc:
        console.print(
            "[red]The gateway needs the optional 'gateway' extra:[/red] "
            "`uv sync --extra gateway`"
        )
        raise typer.Exit(code=1) from exc

    from dgx_hub.gateway.app import app

    console.print(f"Gateway listening on http://{host}:{port} — routes by request \"model\".")
    uvicorn.run(app, host=host, port=port, log_level="info")
