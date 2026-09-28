"""Detects which container engine (Docker, Docker rootless, or Podman) to
drive, so the rest of the codebase never hardcodes the `docker` binary name.
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

EngineFlavor = Literal["docker", "docker-rootless", "podman"]


class _RunResult(Protocol):
    returncode: int
    stdout: str


_Run = Callable[..., _RunResult]


class ContainerEngineError(RuntimeError):
    """Raised when no working container engine (Docker or Podman) is found."""


@dataclass(frozen=True)
class EngineInfo:
    flavor: EngineFlavor
    binary: str
    version: str

    @property
    def compose_prefix(self) -> list[str]:
        return [self.binary, "compose"]


def _docker_info(run: _Run) -> dict | None:
    result = run(
        ["docker", "info", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _podman_version(run: _Run) -> str | None:
    result = run(
        ["podman", "version", "--format", "{{.Client.Version}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    version = result.stdout.strip()
    return version or None


def _detect_engine(run: _Run, which: Callable[[str], str | None]) -> EngineInfo:
    if which("docker") is not None:
        info = _docker_info(run)
        if info is not None:
            security_options = info.get("SecurityOptions") or []
            is_rootless = any("rootless" in str(opt).lower() for opt in security_options)
            flavor: EngineFlavor = "docker-rootless" if is_rootless else "docker"
            version = str(info.get("ServerVersion") or "unknown")
            return EngineInfo(flavor=flavor, binary="docker", version=version)

    if which("podman") is not None:
        podman_version = _podman_version(run)
        if podman_version is not None:
            return EngineInfo(flavor="podman", binary="podman", version=podman_version)

    raise ContainerEngineError(
        "No working container engine found. Install Docker (rootful or "
        "rootless) or Podman and make sure `docker info` or `podman "
        "version` succeeds."
    )


@functools.lru_cache(maxsize=1)
def detect_engine() -> EngineInfo:
    """The container engine to drive, probed once per process and cached."""
    return _detect_engine(subprocess.run, shutil.which)
