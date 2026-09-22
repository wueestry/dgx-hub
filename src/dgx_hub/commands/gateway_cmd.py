"""`dgx-hub gateway ...` — the OpenAI-compatible routing gateway.

`start`/`stop`/`status`/`logs` manage the Postgres + LiteLLM proxy infra
that actually proxies `/v1/...` traffic; `sync` and the auto-sync hooks in
commands/process (see gateway/auto_sync.py) keep LiteLLM's model list
matched to dgx-hub's own ground truth; `keys` issues/lists virtual API keys
for calling it.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from dgx_hub.gateway import lifecycle
from dgx_hub.gateway.compose import gateway_paths, read_env_file
from dgx_hub.gateway.litellm_admin import LiteLLMAdminClient
from dgx_hub.gateway.reconcile import reconcile

console = Console()


def _admin_client() -> LiteLLMAdminClient:
    env = read_env_file(gateway_paths().env_file)
    master_key = env.get("LITELLM_MASTER_KEY")
    port = env.get("LITELLM_PORT")
    if not master_key or not port:
        console.print("[red]gateway: not set up yet — run `dgx-hub gateway start` first[/red]")
        raise typer.Exit(code=1)
    return LiteLLMAdminClient(f"http://127.0.0.1:{port}", master_key)


def gateway_start(
    host: str = typer.Option("0.0.0.0", "--host", help="Interface litellm binds"),
    port: int = typer.Option(8888, "--port", help="Public port litellm listens on"),
    postgres_port: int = typer.Option(
        5442, "--postgres-port", help="Host-loopback port for the backing Postgres"
    ),
    timeout: float = typer.Option(
        60.0, "--timeout", help="Seconds to wait for litellm to report healthy"
    ),
) -> None:
    """Start the Postgres + LiteLLM proxy infra as Docker containers (needs Docker)."""
    console.print("[bold]gateway[/bold]: starting postgres + litellm...")
    try:
        paths = lifecycle.start(
            host=host, litellm_port=port, postgres_port=postgres_port, ready_timeout=timeout
        )
    except lifecycle.GatewayLifecycleError as exc:
        console.print(f"[red]gateway: failed to start: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]gateway[/green]: litellm ready on http://{host}:{port}")
    console.print(f"[dim]compose file: {paths.compose_file}[/dim]")
    key = lifecycle.master_key()
    if key:
        console.print(f"[dim]master key (Authorization: Bearer {key})[/dim]")
        console.print(f"[dim]kept in {paths.env_file}[/dim]")


def gateway_stop(
    timeout: int = typer.Option(
        30, "--timeout", help="Grace period in seconds before Docker's SIGKILL fallback"
    ),
) -> None:
    """Stop the gateway's Postgres + LiteLLM containers."""
    outcomes = lifecycle.stop(timeout=timeout)
    for name, outcome in outcomes.items():
        style = "green" if outcome == "stopped" else "red"
        console.print(f"[{style}]{name}[/{style}]: {outcome}")


def gateway_status() -> None:
    """Show status of the gateway's Postgres + LiteLLM containers."""
    table = Table(title="dgx-hub gateway status")
    table.add_column("Service", style="bold")
    table.add_column("State")
    for name, ds in lifecycle.status().items():
        if not ds.exists:
            state = "not created"
        elif ds.running:
            state = ds.health or "running"
        else:
            state = f"exited ({ds.exit_code})"
        table.add_row(name, state)
    console.print(table)


def gateway_sync() -> None:
    """Push ground-truth model routes into LiteLLM's model list right now.

    Also runs automatically after `dgx-hub start`/`stop`/`status` once the
    gateway infra is up — this is for manual recovery or scripting.
    """
    try:
        with _admin_client() as client:
            result = reconcile(client)
    except Exception as exc:
        console.print(f"[red]gateway: sync failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if not result.changed:
        console.print("[dim]gateway: already in sync[/dim]")
        return
    for model_id in result.added:
        console.print(f"[green]+ {model_id}[/green]")
    for model_id in result.updated:
        console.print(f"[yellow]~ {model_id}[/yellow]")
    for model_id in result.removed:
        console.print(f"[red]- {model_id}[/red]")


def gateway_keys_create(
    name: str = typer.Argument(..., help="Label for this key (e.g. an app or teammate's name)"),
    budget_usd: float | None = typer.Option(
        None, "--budget", help="Optional max spend in USD before this key is rejected"
    ),
) -> None:
    """Issue a new virtual API key for calling the gateway."""
    try:
        with _admin_client() as client:
            key = client.generate_key(name, budget_usd=budget_usd)
    except Exception as exc:
        console.print(f"[red]gateway: key creation failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]{name}[/green]: {key}")
    console.print("[dim]this is shown once — store it somewhere safe[/dim]")


def gateway_keys_list() -> None:
    """List virtual API keys issued for the gateway."""
    try:
        with _admin_client() as client:
            keys = client.list_keys()
    except Exception as exc:
        console.print(f"[red]gateway: failed to list keys: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title="dgx-hub gateway keys")
    table.add_column("Alias", style="bold")
    table.add_column("Spend (USD)")
    table.add_column("Budget (USD)")
    for entry in keys:
        table.add_row(
            str(entry.get("key_alias", entry.get("key_name", "-"))),
            str(entry.get("spend", "-")),
            str(entry.get("max_budget", "unlimited")),
        )
    console.print(table)
    if not keys:
        console.print("[dim]No keys issued yet. Try `dgx-hub gateway keys create <name>`.[/dim]")


def gateway_logs(
    service: str = typer.Argument(..., help="'postgres' or 'litellm'"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream new log lines"),
    tail: int = typer.Option(200, "--tail", help="Number of lines to show"),
) -> None:
    """Tail (or follow) the gateway's postgres or litellm container logs."""
    if service not in ("postgres", "litellm"):
        console.print("[red]service must be 'postgres' or 'litellm'[/red]")
        raise typer.Exit(code=1)
    try:
        for line in lifecycle.logs(service, follow=follow, tail=tail):
            console.print(line, markup=False, highlight=False)
    except KeyboardInterrupt:
        pass
