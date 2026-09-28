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
    assert engine.kind == "docker"
    assert engine.rootless is False
    assert engine.flavor == "docker"
    assert engine.binary == "docker"
    assert engine.version == "27.0.0"
    assert engine.compose_prefix == ["docker", "compose"]
    assert engine.supports_host_networking is True


def test_rootless_docker() -> None:
    def run(argv: list[str], **kwargs: object) -> FakeResult:
        return FakeResult(
            returncode=0,
            stdout='{"ServerVersion": "27.0.0", "SecurityOptions": ["name=rootless"]}',
        )

    engine = _detect_engine(run, _which({"docker"}))
    assert engine.kind == "docker"
    assert engine.rootless is True
    assert engine.flavor == "docker-rootless"
    assert engine.binary == "docker"
    assert engine.supports_host_networking is False


def _podman_run(version: str, rootless: str) -> Callable[[list[str]], FakeResult]:
    def run(argv: list[str], **kwargs: object) -> FakeResult:
        assert argv[0] == "podman"
        if argv[1] == "version":
            return FakeResult(returncode=0, stdout=version)
        assert argv[1] == "info"
        return FakeResult(returncode=0, stdout=rootless)

    return run


def test_rootful_podman() -> None:
    engine = _detect_engine(_podman_run("4.9.4", "false"), _which({"podman"}))
    assert engine.kind == "podman"
    assert engine.rootless is False
    assert engine.flavor == "podman"
    assert engine.version == "4.9.4"
    assert engine.compose_prefix == ["podman", "compose"]
    assert engine.supports_host_networking is True


def test_rootless_podman() -> None:
    engine = _detect_engine(_podman_run("4.9.4", "true"), _which({"podman"}))
    assert engine.kind == "podman"
    assert engine.rootless is True
    assert engine.flavor == "podman-rootless"
    assert engine.supports_host_networking is False


def test_podman_rootless_defaults_true_when_undetermined() -> None:
    """`podman info`'s rootless field can't always be read (older podman,
    unexpected format output) -- default to rootless (the safe direction:
    it only costs an unnecessary bridge+-p publish, never a break)."""
    engine = _detect_engine(_podman_run("4.9.4", "unexpected-output"), _which({"podman"}))
    assert engine.rootless is True


def test_docker_absent_falls_back_to_podman() -> None:
    engine = _detect_engine(_podman_run("4.9.4", "false"), _which({"podman"}))
    assert engine.kind == "podman"
    assert engine.binary == "podman"
    assert engine.version == "4.9.4"


def test_docker_present_but_broken_falls_back_to_podman() -> None:
    def run(argv: list[str], **kwargs: object) -> FakeResult:
        if argv[0] == "docker":
            return FakeResult(returncode=1, stdout="Cannot connect to the Docker daemon")
        return _podman_run("4.9.4", "false")(argv)

    engine = _detect_engine(run, _which({"docker", "podman"}))
    assert engine.kind == "podman"


def test_neither_engine_available_raises() -> None:
    def run(argv: list[str], **kwargs: object) -> FakeResult:
        return FakeResult(returncode=1)

    with pytest.raises(ContainerEngineError):
        _detect_engine(run, _which(set()))
