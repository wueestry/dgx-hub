"""Tests for ManifestPlugin's [fallback] dispatch — the last-resort escape
hatch two of the real plugin manifests (DeepSeek, Flash-Next) depend on.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dgx_hub.plugins.base import RunContext
from dgx_hub.plugins.manifest import PluginManifest
from dgx_hub.plugins.manifest_plugin import ManifestPlugin, ManifestPluginError


def _fallback_manifest_dict(**docker_overrides: object) -> dict:
    docker: dict = {
        "mode": "docker-run",
        "image": "example/x:latest",
        "container_name": "x",
        "network_mode": "host",
        "ports": [
            {
                "container_port": 8888,
                "publish_strategy": "loopback-remap",
                "port_override_env_var": "SERVING_PORT",
            }
        ],
    }
    docker.update(docker_overrides)
    return {
        "plugin": {"name": "x", "display_name": "X"},
        "source": {"repo_url": "https://example.com/x.git"},
        "docker": docker,
        "health": {"url": "http://{backend_address}/"},
        "fallback": {"enabled": True, "start_command": ["./start.sh", "{variant}"]},
    }


def make_plugin(tmp_path: Path, **docker_overrides: object) -> ManifestPlugin:
    manifest = PluginManifest.from_toml_dict(_fallback_manifest_dict(**docker_overrides))
    return ManifestPlugin(manifest=manifest, repo_dir=tmp_path)


def test_fallback_start_substitutes_variant_and_injects_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = make_plugin(tmp_path)
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return MagicMock(returncode=0, stderr="")

    monkeypatch.setattr("dgx_hub.plugins.manifest_plugin.subprocess.run", fake_run)

    ctx = RunContext(
        repo_dir=tmp_path,
        state_dir=tmp_path,
        variant_id="context-1m",
        env_values={},
        allocated_port=19999,
    )
    handle = plugin.start(ctx)

    assert captured["command"] == ["./start.sh", "context-1m"]
    assert captured["cwd"] == tmp_path
    assert captured["env"]["SERVING_PORT"] == "19999"
    assert handle.container_name == "x"
    assert handle.backend_address == "127.0.0.1:19999"


def test_fallback_start_raises_on_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = make_plugin(tmp_path)

    def fake_run(*args, **kwargs):
        return MagicMock(returncode=1, stderr="boom")

    monkeypatch.setattr("dgx_hub.plugins.manifest_plugin.subprocess.run", fake_run)
    ctx = RunContext(repo_dir=tmp_path, state_dir=tmp_path, variant_id=None, allocated_port=1)
    with pytest.raises(ManifestPluginError, match="boom"):
        plugin.start(ctx)


def test_fallback_requires_start_command(tmp_path: Path) -> None:
    data = _fallback_manifest_dict()
    data["fallback"] = {"enabled": True, "start_command": []}
    manifest = PluginManifest.from_toml_dict(data)
    plugin = ManifestPlugin(manifest=manifest, repo_dir=tmp_path)
    ctx = RunContext(repo_dir=tmp_path, state_dir=tmp_path, variant_id=None)
    with pytest.raises(ManifestPluginError, match="start_command"):
        plugin.start(ctx)
