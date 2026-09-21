"""Tests for ui/picker.py's non-interactive short-circuit paths.

The actual questionary prompts need a real TTY and aren't exercised here —
these cover the logic that runs before/instead of calling .ask().
"""

from __future__ import annotations

from pathlib import Path

from dgx_hub.plugins.loader import DiscoveryResult, LoadedPlugin
from dgx_hub.plugins.manifest import PluginManifest
from dgx_hub.plugins.manifest_plugin import ManifestPlugin
from dgx_hub.ui.picker import pick_models, pick_variant, prompt_env_overrides


def _manifest(**overrides: object) -> PluginManifest:
    data: dict = {
        "plugin": {"name": "x", "display_name": "X"},
        "source": {"repo_url": "https://example.com/x.git"},
        "docker": {
            "mode": "docker-run",
            "image": "example/x:latest",
            "container_name": "x",
            "ports": [{"container_port": 80}],
        },
        "health": {"url": "http://{backend_address}/"},
    }
    data.update(overrides)
    return PluginManifest.from_toml_dict(data)


def test_pick_models_empty_discovery_returns_empty_list() -> None:
    assert pick_models(DiscoveryResult(plugins={}, broken=[])) == []


def test_pick_variant_returns_none_with_no_variants() -> None:
    assert pick_variant(_manifest()) is None


def test_pick_variant_returns_none_with_single_variant() -> None:
    manifest = _manifest(variant=[{"id": "only", "default": True}])
    assert pick_variant(manifest) is None


def test_prompt_env_overrides_empty_when_nothing_required() -> None:
    manifest = _manifest(env={"QUANT": {"default": "nvfp4", "required": False}})
    assert prompt_env_overrides(manifest) == {}


def test_discovery_result_smoke(tmp_path: Path) -> None:
    """Sanity-check DiscoveryResult/LoadedPlugin construction used above
    actually matches loader.py's real shape."""
    manifest = _manifest()
    plugin = ManifestPlugin(manifest=manifest, repo_dir=tmp_path)
    result = DiscoveryResult(
        plugins={"x": LoadedPlugin(plugin=plugin, manifest_path=tmp_path / "plugin.toml")},
        broken=[],
    )
    assert list(result.plugins) == ["x"]
