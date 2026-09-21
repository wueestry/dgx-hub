"""`dgx-hub` with no args — interactive model/variant selection, then the
same launch-and-dashboard flow as `dgx-hub start`.
"""

from __future__ import annotations

from rich.console import Console

from dgx_hub.commands.launch_flow import launch_and_wait
from dgx_hub.plugins.loader import discover_plugins
from dgx_hub.ui.picker import pick_models, pick_variant, prompt_env_overrides

console = Console()


def run_interactive() -> None:
    result = discover_plugins()
    if not result.plugins:
        console.print(
            "[yellow]No plugins discovered.[/yellow] Drop a plugin.toml under the "
            "built-in or user plugin directory — see `dgx-hub list`."
        )
        return

    selected = pick_models(result)
    if not selected:
        console.print("[dim]Nothing selected.[/dim]")
        return

    variant_by_name: dict[str, str | None] = {}
    env_overrides_by_name: dict[str, dict[str, str]] = {}
    for name in selected:
        manifest = result.plugins[name].plugin.manifest
        variant_by_name[name] = pick_variant(manifest)
        env_overrides_by_name[name] = prompt_env_overrides(manifest)

    launch_and_wait(selected, variant_by_name, env_overrides_by_name)
