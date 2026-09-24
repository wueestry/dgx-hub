"""Pre-launch resource checks: will this model fit next to what's running?"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dgx_hub import docker_adapter
from dgx_hub.logging_config import get_logger
from dgx_hub.plugins.base import ResourceRequirements
from dgx_hub.process.state import ModelRunRecord

logger = get_logger(__name__)

_GIB = 1024**3

# States in which a model may still be holding memory / the GPU.
ACTIVE_STATES = {"provisioning", "starting", "warming_up", "serving"}


def mem_available_gib(meminfo: Path = Path("/proc/meminfo")) -> float | None:
    """MemAvailable in GiB, or None where /proc/meminfo isn't readable."""
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024 / _GIB
    except (OSError, ValueError, IndexError):
        return None
    return None


def disk_free_gib(path: Path) -> float | None:
    """Free space on the filesystem holding `path` (or its nearest existing parent)."""
    probe = path
    while not probe.exists():
        if probe.parent == probe:
            return None
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free / _GIB
    except OSError:
        return None


@dataclass(frozen=True)
class PreflightResult:
    mem_available_gib: float | None
    mem_required_gib: float
    disk_free_gib: float | None
    disk_required_gib: float
    blockers: list[ModelRunRecord] = field(default_factory=list)
    """Other dgx-hub models whose containers are actually running and that
    stand in the way (memory short, or this plugin needs the GPU exclusively)."""
    exclusive_gpu: bool = False

    @property
    def memory_ok(self) -> bool:
        return self.mem_available_gib is None or self.mem_available_gib >= self.mem_required_gib

    @property
    def disk_ok(self) -> bool:
        return self.disk_free_gib is None or self.disk_free_gib >= self.disk_required_gib

    @property
    def ok(self) -> bool:
        """Whether launching may proceed. Disk isn't part of this:
        min_free_disk_gib includes a first-run download that an already
        provisioned model no longer needs, so a shortfall only warns."""
        return self.memory_ok and not self.blockers

    def describe(self) -> str:
        parts = []
        if not self.memory_ok:
            parts.append(
                f"needs {self.mem_required_gib:.0f} GiB free memory, "
                f"{self.mem_available_gib:.0f} GiB available"
            )
        if self.blockers and self.exclusive_gpu:
            parts.append("needs the GPU to itself")
        return "; ".join(parts)


def running_models(records: dict[str, ModelRunRecord], exclude: set[str]) -> list[ModelRunRecord]:
    """Active records whose container Docker actually reports as running --
    stale state entries (crashed containers, manual `docker rm`) are ignored.
    """
    result = []
    for name, record in records.items():
        if name in exclude or record.state not in ACTIVE_STATES:
            continue
        if record.container_name is None:
            continue
        try:
            if docker_adapter.status(record.to_handle()).running:
                result.append(record)
        except (docker_adapter.DockerAdapterError, ValueError) as exc:
            logger.debug("%s: could not inspect container: %s", name, exc)
    return result


def check(
    resources: ResourceRequirements,
    records: dict[str, ModelRunRecord],
    starting: set[str],
    disk_path: Path,
) -> PreflightResult:
    """Compare `resources` against the host right now. `starting` are the
    names being launched in this batch (never counted as blockers).
    """
    mem = mem_available_gib()
    disk = disk_free_gib(disk_path)
    others = running_models(records, exclude=starting)

    memory_short = mem is not None and mem < resources.min_free_memory_gib
    blockers: list[ModelRunRecord] = []
    if others and (memory_short or resources.exclusive_gpu):
        blockers = others

    result = PreflightResult(
        mem_available_gib=mem,
        mem_required_gib=resources.min_free_memory_gib,
        disk_free_gib=disk,
        disk_required_gib=resources.min_free_disk_gib,
        blockers=blockers,
        exclusive_gpu=resources.exclusive_gpu,
    )
    logger.info(
        "preflight: mem %s/%.0f GiB, disk %s/%.0f GiB, running=%s, blockers=%s",
        f"{mem:.0f}" if mem is not None else "?",
        resources.min_free_memory_gib,
        f"{disk:.0f}" if disk is not None else "?",
        resources.min_free_disk_gib,
        [r.name for r in others],
        [r.name for r in blockers],
    )
    return result
