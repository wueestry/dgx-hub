"""Tests for gateway/registry.py — ground-truth routing table for the gateway."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dgx_hub import docker_adapter
from dgx_hub.gateway import registry
from dgx_hub.process import state as state_store
from dgx_hub.process.state import ModelRunRecord

RUNNING = docker_adapter.DockerStatus(exists=True, running=True)
NOT_RUNNING = docker_adapter.DockerStatus(exists=False, running=False)


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state_store, "state_file", lambda: tmp_path / "state.json")
    monkeypatch.setattr(docker_adapter, "status", lambda handle: RUNNING)


def _serving_record(**overrides: object) -> ModelRunRecord:
    defaults: dict[str, object] = {
        "name": "model-a",
        "variant_id": None,
        "container_name": "model-a",
        "backend_address": "127.0.0.1:8888",
        "gateway_address": "model-a:8888",
        "port": 8888,
        "state": "serving",
        "started_at": datetime.now(UTC).isoformat(),
    }
    defaults.update(overrides)
    return ModelRunRecord(**defaults)  # type: ignore[arg-type]


def test_gateway_address_used_directly() -> None:
    state_store.save(_serving_record())
    routes = registry.current_routes()

    assert routes == {"model-a": "model-a:8888"}


def test_record_without_gateway_address_is_excluded() -> None:
    """Covers both network_mode=host backends (which can never join
    dgx-hub-net) and records saved before this field existed."""
    state_store.save(_serving_record(gateway_address=""))

    assert registry.current_routes() == {}


def test_dead_container_is_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    state_store.save(_serving_record())
    monkeypatch.setattr(docker_adapter, "status", lambda handle: NOT_RUNNING)

    assert registry.current_routes() == {}


def test_non_serving_state_is_excluded() -> None:
    state_store.save(_serving_record(state="provisioning"))

    assert registry.current_routes() == {}


def test_served_model_ids_fan_out_to_same_address() -> None:
    state_store.save(_serving_record(served_model_ids=["alias-1", "alias-2"]))

    routes = registry.current_routes()

    assert routes == {"alias-1": "model-a:8888", "alias-2": "model-a:8888"}
