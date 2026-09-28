"""`dgx-hub doctor` — read-only environment diagnostics."""

from __future__ import annotations

import shutil
import subprocess

from rich.console import Console
from rich.table import Table

from dgx_hub import docker_adapter
from dgx_hub.container_engine import ContainerEngineError, detect_engine

console = Console()


def _engine_check() -> tuple[str, str]:
    try:
        engine = detect_engine()
    except ContainerEngineError as exc:
        return "fail", str(exc)
    return "ok", f"{engine.flavor} {engine.version} (binary: {engine.binary})"


def _git_check() -> tuple[str, str]:
    path = shutil.which("git")
    if path is None:
        return "fail", "not found on PATH (needed to clone plugin source repos)"
    return "ok", path


def _gpu_check() -> tuple[str, str]:
    if shutil.which("nvidia-smi") is None:
        return "fail", "nvidia-smi not found on PATH"
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return "fail", "nvidia-smi ran but reported no GPU"
    return "ok", "; ".join(line.strip() for line in result.stdout.strip().splitlines())


def _network_check() -> tuple[str, str]:
    try:
        exists = docker_adapter.network_exists()
    except ContainerEngineError as exc:
        return "fail", str(exc)
    name = docker_adapter.DEFAULT_GATEWAY_NETWORK
    if exists:
        return "ok", f"{name} exists"
    return "warn", f"{name} not created yet (created on first start)"


def run_doctor() -> None:
    """Report the state of everything dgx-hub needs from the host: the
    container engine, git, GPU visibility, and the shared gateway network."""
    checks = [
        ("Container engine", *_engine_check()),
        ("git", *_git_check()),
        ("GPU (nvidia-smi)", *_gpu_check()),
        ("dgx-hub-net network", *_network_check()),
    ]

    style = {"ok": "green", "warn": "yellow", "fail": "red"}
    table = Table(title="dgx-hub doctor")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail")
    for name, status, detail in checks:
        table.add_row(name, f"[{style[status]}]{status}[/{style[status]}]", detail)
    console.print(table)
