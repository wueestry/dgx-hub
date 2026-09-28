"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import dgx_hub.docker_adapter as docker_adapter
import dgx_hub.gateway.lifecycle as gateway_lifecycle
import dgx_hub.launch.docker_compose as docker_compose
import dgx_hub.launch.docker_run as docker_run
from dgx_hub.container_engine import EngineInfo

FAKE_ENGINE = EngineInfo(kind="docker", rootless=False, binary="docker", version="test")

_ENGINE_CONSUMERS = (docker_adapter, docker_run, docker_compose, gateway_lifecycle)


@pytest.fixture(autouse=True)
def _stub_container_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[EngineInfo]:
    """Tests should never depend on what's actually installed on the host
    running them — every module that calls `detect_engine()` gets a fixed
    fake result instead of probing the real machine."""
    for module in _ENGINE_CONSUMERS:
        monkeypatch.setattr(module, "detect_engine", lambda: FAKE_ENGINE)
    yield FAKE_ENGINE
