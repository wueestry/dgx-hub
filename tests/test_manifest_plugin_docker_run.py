"""Tests for ManifestPlugin's docker-run start path, in particular the
stale-container-name reclaim in `_reclaim_stale_container_name`.

Regression coverage for: `docker stop` (used by `dgx-hub stop`) never
removes the container, so a subsequent `dgx-hub start` used to hit
Docker's own `Conflict. The container name "/x" is already in use` error
straight out of `docker run`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dgx_hub import docker_adapter
from dgx_hub.plugins.base import RunContext
from dgx_hub.plugins.manifest import PluginManifest
from dgx_hub.plugins.manifest_plugin import ManifestPlugin, ManifestPluginError


def _docker_run_manifest_dict() -> dict:
    return {
        "plugin": {"name": "x", "display_name": "X"},
        "source": {"repo_url": "https://example.com/x.git"},
        "docker": {
            "mode": "docker-run",
            "image": "example/x:latest",
            "container_name": "x",
            "network_mode": "bridge",
            "ports": [{"container_port": 8888, "publish_strategy": "loopback-remap"}],
        },
        "health": {"url": "http://{backend_address}/"},
    }


def make_plugin(tmp_path: Path) -> ManifestPlugin:
    manifest = PluginManifest.from_toml_dict(_docker_run_manifest_dict())
    return ManifestPlugin(manifest=manifest, repo_dir=tmp_path)


def make_ctx(tmp_path: Path) -> RunContext:
    return RunContext(
        repo_dir=tmp_path, state_dir=tmp_path, variant_id=None, env_values={}, allocated_port=19999
    )


def test_removes_stale_exited_container_before_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = make_plugin(tmp_path)

    monkeypatch.setattr(
        docker_adapter,
        "status",
        lambda handle: docker_adapter.DockerStatus(exists=True, running=False),
    )
    removed: list[str] = []
    monkeypatch.setattr(
        docker_adapter, "remove", lambda handle, force=False: removed.append(handle.container_name)
    )

    run_calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        run_calls.append(argv)
        return MagicMock(returncode=0, stdout="deadbeef", stderr="")

    monkeypatch.setattr("dgx_hub.plugins.manifest_plugin.subprocess.run", fake_run)
    monkeypatch.setattr("dgx_hub.docker_adapter.ensure_network", lambda *a, **k: None)

    handle = plugin.start(make_ctx(tmp_path))

    assert removed == ["x"]
    assert any(argv[:2] == ["docker", "run"] for argv in run_calls)
    assert handle.container_name == "x"


def test_running_container_with_same_name_raises_instead_of_removing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = make_plugin(tmp_path)

    monkeypatch.setattr(
        docker_adapter,
        "status",
        lambda handle: docker_adapter.DockerStatus(exists=True, running=True),
    )
    remove_calls: list[str] = []
    monkeypatch.setattr(
        docker_adapter,
        "remove",
        lambda handle, force=False: remove_calls.append(handle.container_name),
    )
    monkeypatch.setattr("dgx_hub.docker_adapter.ensure_network", lambda *a, **k: None)

    def fail_run(*args, **kwargs):
        raise AssertionError("docker run should not be invoked when a name conflict exists")

    monkeypatch.setattr("dgx_hub.plugins.manifest_plugin.subprocess.run", fail_run)

    with pytest.raises(ManifestPluginError, match="already running"):
        plugin.start(make_ctx(tmp_path))

    assert remove_calls == []


def test_no_existing_container_skips_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = make_plugin(tmp_path)

    monkeypatch.setattr(
        docker_adapter,
        "status",
        lambda handle: docker_adapter.DockerStatus(exists=False, running=False),
    )
    remove_calls: list[str] = []
    monkeypatch.setattr(
        docker_adapter,
        "remove",
        lambda handle, force=False: remove_calls.append(handle.container_name),
    )
    monkeypatch.setattr("dgx_hub.docker_adapter.ensure_network", lambda *a, **k: None)
    monkeypatch.setattr(
        "dgx_hub.plugins.manifest_plugin.subprocess.run",
        lambda argv, **kwargs: MagicMock(returncode=0, stdout="deadbeef", stderr=""),
    )

    plugin.start(make_ctx(tmp_path))

    assert remove_calls == []
