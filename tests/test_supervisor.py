"""Tests for process/supervisor.py's provision -> start -> poll state machine.

Uses a fake ModelPlugin and a monkeypatched docker_adapter.status so the
full ModelState progression can be exercised without real Docker/GPU.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from dgx_hub import docker_adapter
from dgx_hub.plugins.base import (
    ContainerHandle,
    HealthResult,
    HealthTiming,
    PluginMetadata,
    ResourceRequirements,
    RunContext,
    RuntimeKind,
)
from dgx_hub.process.supervisor import ModelSupervisor

FAST_TIMING = HealthTiming(
    interval_seconds=0.01, startup_grace_seconds=0.02, expected_first_boot_seconds=0.2
)

FAKE_HANDLE = ContainerHandle(kind=RuntimeKind.DOCKER_RUN, container_name="fake")


class FakePlugin:
    def __init__(
        self, *, healthy_after: int | None = 1, timing: HealthTiming = FAST_TIMING
    ) -> None:
        self.metadata = PluginMetadata(
            name="fake",
            display_name="Fake",
            description="",
            docs_url=None,
            resources=ResourceRequirements(),
            health_timing=timing,
        )
        self.healthy_after = healthy_after
        self.health_check_calls = 0
        self.provisioned = False

    def provision(self, ctx: RunContext) -> None:
        self.provisioned = True

    def start(self, ctx: RunContext) -> ContainerHandle:
        return FAKE_HANDLE

    def health_check(self, ctx: RunContext, handle: ContainerHandle) -> HealthResult:
        self.health_check_calls += 1
        if self.healthy_after is None:
            return HealthResult(healthy=False, detail="never healthy")
        if self.health_check_calls >= self.healthy_after:
            return HealthResult(healthy=True)
        return HealthResult(healthy=False, detail="not yet")


def make_ctx(tmp_path: Path) -> RunContext:
    return RunContext(repo_dir=tmp_path, state_dir=tmp_path, variant_id=None, allocated_port=1)


def _wait_until_terminal(supervisor: ModelSupervisor, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if supervisor.is_terminal():
            return
        time.sleep(0.01)
    raise AssertionError(f"supervisor did not reach a terminal state within {timeout}s")


def test_supervisor_reaches_serving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        docker_adapter,
        "status",
        lambda handle: docker_adapter.DockerStatus(exists=True, running=True),
    )
    plugin = FakePlugin(healthy_after=1)
    supervisor = ModelSupervisor("fake", plugin, make_ctx(tmp_path))

    supervisor.start()
    _wait_until_terminal(supervisor)

    status = supervisor.status
    assert status.state.value == "serving"
    assert status.handle == FAKE_HANDLE
    assert plugin.provisioned is True


def test_supervisor_fails_when_never_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        docker_adapter,
        "status",
        lambda handle: docker_adapter.DockerStatus(exists=True, running=True),
    )
    plugin = FakePlugin(healthy_after=None)
    supervisor = ModelSupervisor("fake", plugin, make_ctx(tmp_path))

    supervisor.start()
    _wait_until_terminal(supervisor)

    status = supervisor.status
    assert status.state.value == "failed"
    assert status.error is not None
    assert "never healthy" in status.error


def test_supervisor_fails_when_container_never_starts_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        docker_adapter,
        "status",
        lambda handle: docker_adapter.DockerStatus(exists=False, running=False),
    )
    plugin = FakePlugin(healthy_after=1)
    supervisor = ModelSupervisor("fake", plugin, make_ctx(tmp_path), container_start_timeout=0.05)

    supervisor.start()
    _wait_until_terminal(supervisor)

    status = supervisor.status
    assert status.state.value == "failed"
    assert status.error is not None
    assert "did not reach running state" in status.error
