"""Interactive prompts built on questionary: model selection, variant choice,
required env-var overrides.
"""

from __future__ import annotations

import questionary

from dgx_hub.plugins.loader import DiscoveryResult
from dgx_hub.plugins.manifest import PluginManifest


def pick_models(result: DiscoveryResult) -> list[str]:
    """Checkbox multi-select over discovered plugin names.

    Mutual exclusion between plugins/variants sharing a container_name is
    enforced at launch time (commands/launch_flow.py's conflict_check), not
    here — the picker lets you select several models and finds out which
    combinations are actually runnable when it tries to start them.
    """
    choices = [
        questionary.Choice(
            title=f"{name} — {loaded.plugin.manifest.plugin.description}",
            value=name,
        )
        for name, loaded in sorted(result.plugins.items())
    ]
    if not choices:
        return []
    selected = questionary.checkbox("Select models to start", choices=choices).ask()
    return selected or []


def pick_variant(manifest: PluginManifest) -> str | None:
    """Prompt for a variant if the plugin declares more than one; returns
    None for single/no-variant plugins (the caller falls back to the
    manifest's default variant id).
    """
    if len(manifest.variant) <= 1:
        return None
    default_id = manifest.default_variant_id()
    choices = [
        questionary.Choice(title=(f"{v.id} — {v.label}" if v.label else v.id), value=v.id)
        for v in manifest.variant
    ]
    return questionary.select(
        f"Variant for {manifest.plugin.name}:", choices=choices, default=default_id
    ).ask()


def prompt_env_overrides(manifest: PluginManifest) -> dict[str, str]:
    """Prompt only for env vars marked `required = true`, pre-filled with
    their manifest default — everything else stays at its default unless
    overridden later via `--set` (keeps this from becoming a wall of
    prompts per model for the common case of "just start it").
    """
    overrides: dict[str, str] = {}
    for name, spec in manifest.env.items():
        if not spec.required:
            continue
        answer = questionary.text(
            f"{name} ({spec.description or spec.type}):", default=str(spec.default)
        ).ask()
        if answer is not None:
            overrides[name] = answer
    return overrides
