"""Detects which container engine (Docker or Podman, rootful or rootless) to
drive, so the rest of the codebase never hardcodes the `docker` binary name
and networking decisions (e.g. whether `--network host` actually reaches the
real host) are made once here instead of per-plugin.
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

EngineKind = Literal["docker", "podman"]


class _RunResult(Protocol):
    returncode: int
    stdout: str


_Run = Callable[..., _RunResult]


class ContainerEngineError(RuntimeError):
    """Raised when no working container engine (Docker or Podman) is found."""


@dataclass(frozen=True)
class EngineInfo:
    kind: EngineKind
    rootless: bool
    binary: str
    version: str

    @property
    def flavor(self) -> str:
        """Human-readable label, e.g. for `dgx-hub doctor` output."""
        return f"{self.kind}-rootless" if self.rootless else self.kind

    @property
    def compose_prefix(self) -> list[str]:
        return [self.binary, "compose"]

    @property
    def supports_host_networking(self) -> bool:
        """Whether `--network host` actually reaches the real host's network.

        Rootless Docker and rootless Podman both run containers inside a
        private user-namespaced network (RootlessKit/slirp4netns or pasta),
        so `--network host` there shares that private namespace, not the
        real host -- a port bound that way is unreachable from outside the
        container. Only a rootful engine can deliver genuine host networking.
        """
        return not self.rootless


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


def _podman_rootless(run: _Run) -> bool | None:
    """True/False if `podman info` answers clearly; None if it can't be
    determined (e.g. the format string doesn't resolve on this podman
    version) -- callers should default to True (rootless) in that case,
    since treating a rootful engine as rootless only costs an unnecessary
    bridge+-p publish instead of true host networking, never a break.
    """
    result = run(
        ["podman", "info", "--format", "{{.Host.Security.Rootless}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    text = result.stdout.strip().lower()
    if text in ("true", "false"):
        return text == "true"
    return None


def _detect_engine(run: _Run, which: Callable[[str], str | None]) -> EngineInfo:
    if which("docker") is not None:
        info = _docker_info(run)
        if info is not None:
            security_options = info.get("SecurityOptions") or []
            is_rootless = any("rootless" in str(opt).lower() for opt in security_options)
            version = str(info.get("ServerVersion") or "unknown")
            return EngineInfo(kind="docker", rootless=is_rootless, binary="docker", version=version)

    if which("podman") is not None:
        podman_version = _podman_version(run)
        if podman_version is not None:
            rootless = _podman_rootless(run)
            return EngineInfo(
                kind="podman",
                rootless=True if rootless is None else rootless,
                binary="podman",
                version=podman_version,
            )

    raise ContainerEngineError(
        "No working container engine found. Install Docker (rootful or "
        "rootless) or Podman and make sure `docker info` or `podman "
        "version` succeeds."
    )


@functools.lru_cache(maxsize=1)
def detect_engine() -> EngineInfo:
    """The container engine to drive, probed once per process and cached."""
    return _detect_engine(subprocess.run, shutil.which)
