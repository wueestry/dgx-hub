"""Tests for process/state.py — persistence and ContainerHandle round-tripping."""

from __future__ import annotations

from pathlib import Path

import pytest

from dgx_hub.plugins.base import RuntimeKind
from dgx_hub.process import state as state_store
from dgx_hub.process.state import ModelRunRecord


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state_store, "state_file", lambda: tmp_path / "state.json")


def test_to_handle_docker_run_default() -> None:
    record = ModelRunRecord(
        name="x",
        variant_id=None,
        container_name="c1",
        backend_address="127.0.0.1:8888",
        port=8888,
        state="serving",
        started_at="2026-01-01T00:00:00+00:00",
    )
    handle = record.to_handle()
    assert handle.kind == RuntimeKind.DOCKER_RUN
    assert handle.container_name == "c1"
    assert handle.compose_project is None


def test_to_handle_docker_compose_round_trip() -> None:
    record = ModelRunRecord(
        name="x",
        variant_id=None,
        container_name=None,
        backend_address="127.0.0.1:8888",
        port=8888,
        state="serving",
        started_at="2026-01-01T00:00:00+00:00",
        kind="docker-compose",
        compose_project="p1",
        compose_file="/repo/compose.yml",
        service_name="svc",
    )
    handle = record.to_handle()
    assert handle.kind == RuntimeKind.DOCKER_COMPOSE
    assert handle.compose_project == "p1"
    assert str(handle.compose_file) == "/repo/compose.yml"
    assert handle.service_name == "svc"


def test_save_and_load_round_trip() -> None:
    record = ModelRunRecord(
        name="x",
        variant_id="eagle",
        container_name="c1",
        backend_address="127.0.0.1:8888",
        port=8888,
        state="serving",
        started_at="2026-01-01T00:00:00+00:00",
    )
    state_store.save(record)
    loaded = state_store.load_all()
    assert loaded["x"] == record
