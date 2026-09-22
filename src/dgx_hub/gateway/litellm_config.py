"""Read/modify litellm's own config.yaml -- specifically `general_settings`
and `litellm_settings`, the parts `gateway/compose.py` writes once on first
`gateway start` and never touches again (`model_list` is managed separately,
at runtime, via the admin API -- see reconcile.py).

Round-trips through ruamel.yaml (rather than a plain load/dump) so hand
edits and comments in the file -- explicitly promised to survive by
`ensure_gateway_files`'s "edit freely, it is never overwritten" -- also
survive a `gateway config set`.
"""

from __future__ import annotations

from typing import Any

from ruamel.yaml import YAML

from dgx_hub.gateway.compose import gateway_paths

_yaml = YAML()
_yaml.preserve_quotes = True


class LiteLLMConfigError(RuntimeError):
    """Raised when the litellm config file can't be read (gateway never started)."""


def _parse_value(raw: str) -> Any:
    """Coerce a CLI-supplied string into bool/int/float, falling back to the
    raw string -- covers the common config-value shapes (feature flags,
    budgets, timeouts) without requiring the caller to know YAML syntax.
    """
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    for caster in (int, float):
        try:
            return caster(raw)
        except ValueError:
            continue
    return raw


def _load() -> Any:
    config_file = gateway_paths().config_file
    if not config_file.is_file():
        raise LiteLLMConfigError(
            f"{config_file} doesn't exist yet — run `dgx-hub gateway start` first"
        )
    return _yaml.load(config_file.read_text())


def set_value(dotted_key: str, raw_value: str) -> Any:
    """Set `dotted_key` (e.g. 'general_settings.disable_env_credential_login')
    to `raw_value`, coerced to bool/int/float/string. Returns the coerced
    value actually written. litellm reads general_settings/litellm_settings
    from this file only at startup -- restart the gateway for this to
    take effect.
    """
    data = _load()
    value = _parse_value(raw_value)

    parts = dotted_key.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value

    config_file = gateway_paths().config_file
    with config_file.open("w") as f:
        _yaml.dump(data, f)
    return value


def get_value(dotted_key: str) -> Any:
    """Read `dotted_key`, or None if it (or an ancestor) isn't set."""
    node: Any = _load()
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node
