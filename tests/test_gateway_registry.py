"""Tests for gateway/registry.py — ground-truth routing, including
gateway-network address resolution to a container's bridge IP."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dgx_hub import docker_adapter
from dgx_hub.gateway import registry
from dgx_hub.plugins.base import ContainerHandle
from dgx_hub.process import state as state_store
from dgx_hub.process.state import ModelRunRecord

RUNNING = docker_adapter.DockerStatus(exists=True, running=True)
NOT_RUNNING = docker_adapter.DockerStatus(exists=False, running=False)


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state_store, "state_file", lambda: tmp_path / "state.json")


def _serving_record(**overrides: object) -> ModelRunRecord:
    defaults: dict[str, object] = {
        "name": "model-a",
        "variant_id": None,
        "container_name": "model-a",
        "backend_address": "127.0.0.1:8888",
        "port": 8888,
        "state": "serving",
        "started_at": datetime.now(UTC).isoformat(),
    }
    defaults.update(overrides)
    return ModelRunRecord(**defaults)  # type: ignore[arg-type]


def test_loopback_address_passes_through_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    state_store.save(_serving_record())
    monkeypatch.setattr(docker_adapter, "status", lambda handle: RUNNING)

    def _unexpected_call(handle: ContainerHandle, network: str = "dgx-hub-net") -> str | None:
        raise AssertionError("container_ip should not be called for a loopback address")

    monkeypatch.setattr(docker_adapter, "container_ip", _unexpected_call)

    routes = registry.current_routes()

    assert routes == {"model-a": "127.0.0.1:8888"}


def test_gateway_network_address_resolved_to_container_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    state_store.save(_serving_record(backend_address="model-a:8888"))
    monkeypatch.setattr(docker_adapter, "status", lambda handle: RUNNING)

    def _resolve(handle: ContainerHandle, network: str = "dgx-hub-net") -> str | None:
        return "172.18.0.4"

    monkeypatch.setattr(docker_adapter, "container_ip", _resolve)

    routes = registry.current_routes()

    assert routes == {"model-a": "172.18.0.4:8888"}


def test_gateway_network_address_falls_back_when_ip_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_store.save(_serving_record(backend_address="model-a:8888"))
    monkeypatch.setattr(docker_adapter, "status", lambda handle: RUNNING)
    monkeypatch.setattr(docker_adapter, "container_ip", lambda handle, network="dgx-hub-net": None)

    routes = registry.current_routes()

    assert routes == {"model-a": "model-a:8888"}


def test_dead_container_is_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    state_store.save(_serving_record())
    monkeypatch.setattr(docker_adapter, "status", lambda handle: NOT_RUNNING)

    assert registry.current_routes() == {}


def test_served_model_ids_fan_out_to_same_address(monkeypatch: pytest.MonkeyPatch) -> None:
    state_store.save(_serving_record(served_model_ids=["alias-1", "alias-2"]))
    monkeypatch.setattr(docker_adapter, "status", lambda handle: RUNNING)

    routes = registry.current_routes()

    assert routes == {"alias-1": "127.0.0.1:8888", "alias-2": "127.0.0.1:8888"}
