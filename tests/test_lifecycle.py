"""Tests for stopping models (shared by `dgx-hub stop` and the preflight)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dgx_hub.plugins.manifest import PluginManifest
from dgx_hub.plugins.manifest_plugin import ManifestPlugin
from dgx_hub.process import lifecycle
from dgx_hub.process import state as state_store
from dgx_hub.process.lifecycle import StopError, stop_model
from dgx_hub.process.state import ModelRunRecord
from dgx_hub.process.stream import StreamResult


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state_store, "state_file", lambda: tmp_path / "state.json")


def _plugin(repo_dir: Path, fallback: bool) -> ManifestPlugin:
    data: dict = {
        "plugin": {"name": "x", "display_name": "X"},
        "source": {"repo_url": "https://example.com/x.git"},
        "docker": {"mode": "docker-run", "image": "example/x", "container_name": "x-ctr"},
        "health": {"url": "http://{backend_address}/"},
    }
    if fallback:
        data["fallback"] = {
            "enabled": True,
            "start_command": ["./start.sh"],
            "stop_command": ["./stop.sh", "-f"],
        }
    return ManifestPlugin(manifest=PluginManifest.from_toml_dict(data), repo_dir=repo_dir)


def _record(container_name: str | None = "x-ctr") -> ModelRunRecord:
    return ModelRunRecord(
        name="x",
        variant_id=None,
        container_name=container_name,
        backend_address="",
        port=8000,
        state="serving",
        started_at="2026-01-01T00:00:00+00:00",
    )


def test_fallback_plugin_stops_via_its_stop_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list = []
    monkeypatch.setattr(
        "dgx_hub.plugins.manifest_plugin.run_streaming",
        lambda argv, **kw: calls.append((argv, kw["cwd"])) or StreamResult(returncode=0),
    )
    monkeypatch.setattr(
        lifecycle.docker_adapter, "stop", lambda *a, **k: pytest.fail("docker stop used")
    )
    record = _record()
    stop_model(record, _plugin(tmp_path, fallback=True))

    assert calls == [(["./stop.sh", "-f"], tmp_path)]
    assert state_store.get("x").state == "stopped"


def test_failed_fallback_stop_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "dgx_hub.plugins.manifest_plugin.run_streaming",
        lambda argv, **kw: StreamResult(returncode=1, tail=["unknown option"]),
    )
    with pytest.raises(StopError, match="unknown option"):
        stop_model(_record(), _plugin(tmp_path, fallback=True))


def test_plain_plugin_uses_docker_stop_with_manifest_container_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped: list = []
    monkeypatch.setattr(
        lifecycle.docker_adapter,
        "stop",
        lambda handle, grace_seconds: stopped.append((handle.container_name, grace_seconds)),
    )
    stop_model(_record(container_name=None), _plugin(tmp_path, fallback=False), timeout=5)
    assert stopped == [("x-ctr", 5)]


def test_nothing_to_stop_raises() -> None:
    with pytest.raises(StopError, match="nothing to stop"):
        stop_model(_record(container_name=None), None)
