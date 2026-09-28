"""Tests for container_engine.py's engine-detection logic."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from dgx_hub.container_engine import ContainerEngineError, _detect_engine


@dataclass
class FakeResult:
    returncode: int
    stdout: str = ""


def _which(available: set[str]) -> Callable[[str], str | None]:
    return lambda name: f"/usr/bin/{name}" if name in available else None


def test_rootful_docker() -> None:
    def run(argv: list[str], **kwargs: object) -> FakeResult:
        assert argv[0] == "docker"
        return FakeResult(returncode=0, stdout='{"ServerVersion": "27.0.0", "SecurityOptions": []}')

    engine = _detect_engine(run, _which({"docker"}))
    assert engine.flavor == "docker"
    assert engine.binary == "docker"
    assert engine.version == "27.0.0"
    assert engine.compose_prefix == ["docker", "compose"]


def test_rootless_docker() -> None:
    def run(argv: list[str], **kwargs: object) -> FakeResult:
        return FakeResult(
            returncode=0,
            stdout='{"ServerVersion": "27.0.0", "SecurityOptions": ["name=rootless"]}',
        )

    engine = _detect_engine(run, _which({"docker"}))
    assert engine.flavor == "docker-rootless"
    assert engine.binary == "docker"


def test_docker_absent_falls_back_to_podman() -> None:
    def run(argv: list[str], **kwargs: object) -> FakeResult:
        assert argv[0] == "podman"
        return FakeResult(returncode=0, stdout="4.9.4")

    engine = _detect_engine(run, _which({"podman"}))
    assert engine.flavor == "podman"
    assert engine.binary == "podman"
    assert engine.version == "4.9.4"
    assert engine.compose_prefix == ["podman", "compose"]


def test_docker_present_but_broken_falls_back_to_podman() -> None:
    def run(argv: list[str], **kwargs: object) -> FakeResult:
        if argv[0] == "docker":
            return FakeResult(returncode=1, stdout="Cannot connect to the Docker daemon")
        return FakeResult(returncode=0, stdout="4.9.4")

    engine = _detect_engine(run, _which({"docker", "podman"}))
    assert engine.flavor == "podman"


def test_neither_engine_available_raises() -> None:
    def run(argv: list[str], **kwargs: object) -> FakeResult:
        return FakeResult(returncode=1)

    with pytest.raises(ContainerEngineError):
        _detect_engine(run, _which(set()))
