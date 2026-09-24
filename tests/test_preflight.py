"""Tests for the pre-launch resource preflight."""

from __future__ import annotations

from pathlib import Path

import pytest

from dgx_hub.docker_adapter import DockerStatus
from dgx_hub.plugins.base import ResourceRequirements
from dgx_hub.process import preflight
from dgx_hub.process.state import ModelRunRecord


def _record(name: str, state: str = "serving") -> ModelRunRecord:
    return ModelRunRecord(
        name=name,
        variant_id=None,
        container_name=name,
        backend_address="127.0.0.1:8000",
        port=8000,
        state=state,
        started_at="2026-01-01T00:00:00+00:00",
    )


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Fake host: `mem` GiB available; containers in `running` are up."""
    fake: dict = {"mem": 100.0, "running": set()}
    monkeypatch.setattr(preflight, "mem_available_gib", lambda: fake["mem"])
    monkeypatch.setattr(preflight, "disk_free_gib", lambda path: 500.0)
    monkeypatch.setattr(
        preflight.docker_adapter,
        "status",
        lambda handle: DockerStatus(exists=True, running=handle.container_name in fake["running"]),
    )
    return fake


def _check(resources: ResourceRequirements, records: dict, starting: set[str]):
    return preflight.check(resources, records, starting, Path("/"))


def test_fits_when_memory_is_available(host: dict) -> None:
    host["running"] = {"other"}
    result = _check(
        ResourceRequirements(min_free_memory_gib=50), {"other": _record("other")}, {"big"}
    )
    assert result.ok
    assert result.blockers == []


def test_running_models_block_when_memory_is_short(host: dict) -> None:
    host["mem"] = 69.0
    host["running"] = {"other"}
    result = _check(
        ResourceRequirements(min_free_memory_gib=104), {"other": _record("other")}, {"big"}
    )
    assert not result.ok
    assert [r.name for r in result.blockers] == ["other"]
    assert "104 GiB" in result.describe()


def test_memory_short_without_blockers_is_not_ok(host: dict) -> None:
    host["mem"] = 10.0
    result = _check(ResourceRequirements(min_free_memory_gib=104), {}, {"big"})
    assert not result.ok
    assert result.blockers == []


def test_exclusive_gpu_blocks_even_when_memory_fits(host: dict) -> None:
    host["running"] = {"other"}
    result = _check(
        ResourceRequirements(min_free_memory_gib=10, exclusive_gpu=True),
        {"other": _record("other")},
        {"big"},
    )
    assert not result.ok
    assert "GPU" in result.describe()


def test_stale_and_inactive_records_are_ignored(host: dict) -> None:
    host["mem"] = 1.0
    host["running"] = {"stopped-but-up"}
    records = {
        "stale": _record("stale"),  # state says serving, container gone
        "stopped-but-up": _record("stopped-but-up", state="stopped"),
        "big": _record("big"),  # the model being started
    }
    host["running"].add("big")
    result = _check(ResourceRequirements(min_free_memory_gib=50), records, {"big"})
    assert result.blockers == []


def test_disk_shortfall_only_warns(host: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight, "disk_free_gib", lambda path: 10.0)
    result = _check(ResourceRequirements(min_free_disk_gib=120), {}, {"big"})
    assert not result.disk_ok
    assert result.ok


def test_mem_available_parses_meminfo(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 127000000 kB\nMemAvailable: 72351744 kB\n")
    assert preflight.mem_available_gib(meminfo) == pytest.approx(69.0, abs=0.01)
    assert preflight.mem_available_gib(tmp_path / "missing") is None
