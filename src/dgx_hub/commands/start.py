"""`dgx-hub start` — provision, launch, and wait for one or more models."""

from __future__ import annotations

import typer

from dgx_hub.commands.launch_flow import launch_and_wait


def start_models(
    names: list[str] = typer.Argument(..., help="Plugin name(s) to start"),
    variant: str | None = typer.Option(
        None, "--variant", help="Variant id (defaults to the plugin's default variant)"
    ),
    set_env: list[str] = typer.Option(
        [], "--set", help="Override an env var, KEY=VALUE (repeatable)"
    ),
) -> None:
    """Start one or more models by plugin name, showing a live dashboard
    until each reaches SERVING (or FAILED)."""
    overrides = _parse_overrides(set_env)
    variant_by_name: dict[str, str | None] = dict.fromkeys(names, variant)
    env_overrides_by_name: dict[str, dict[str, str]] = dict.fromkeys(names, overrides)
    launch_and_wait(names, variant_by_name, env_overrides_by_name)


def _parse_overrides(pairs: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            typer.echo(f"Invalid --set value (expected KEY=VALUE): {pair}", err=True)
            raise typer.Exit(code=1)
        key, _, value = pair.partition("=")
        overrides[key] = value
    return overrides
