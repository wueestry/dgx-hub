"""Plugin discovery: built-in manifests + user plugin directory."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from dgx_hub.config import builtin_plugins_dir, repos_dir, user_plugins_dir
from dgx_hub.plugins.manifest import PluginManifest
from dgx_hub.plugins.manifest_plugin import ManifestPlugin


@dataclass(frozen=True)
class LoadedPlugin:
    plugin: ManifestPlugin
    manifest_path: Path


@dataclass(frozen=True)
class BrokenPlugin:
    manifest_path: Path
    error: str


@dataclass(frozen=True)
class DiscoveryResult:
    plugins: dict[str, LoadedPlugin]
    broken: list[BrokenPlugin]


def _iter_manifest_paths(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*/plugin.toml"))


def load_manifest(manifest_path: Path) -> ManifestPlugin:
    raw = tomllib.loads(manifest_path.read_text())
    manifest = PluginManifest.from_toml_dict(raw)
    repo_dir = repos_dir() / manifest.plugin.name
    return ManifestPlugin(manifest=manifest, repo_dir=repo_dir)


def discover_plugins() -> DiscoveryResult:
    """Merge built-in and user plugin directories; built-ins win name collisions."""
    plugins: dict[str, LoadedPlugin] = {}
    broken: list[BrokenPlugin] = []

    for source_dir in (user_plugins_dir(), builtin_plugins_dir()):
        for manifest_path in _iter_manifest_paths(source_dir):
            try:
                plugin = load_manifest(manifest_path)
            except Exception as exc:
                broken.append(BrokenPlugin(manifest_path=manifest_path, error=str(exc)))
                continue
            plugins[plugin.metadata.name] = LoadedPlugin(
                plugin=plugin, manifest_path=manifest_path
            )

    return DiscoveryResult(plugins=plugins, broken=broken)
