"""Core plugin protocol and shared data types for dgx-hub."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable


class NetworkMode(StrEnum):
    """How a plugin's container is attached to the network.

    BRIDGE: the CLI fully controls host<->container port mapping via its own
        `docker run -p`. Always concurrency-capable, no upstream cooperation
        required.
    HOST: the container binds host ports directly (`network_mode: host`).
        Remapping is only possible if the app itself honors a port env var.
    """

    BRIDGE = "bridge"
    HOST = "host"


class LaunchMode(StrEnum):
    """How the CLI launches a plugin's container(s)."""

    DOCKER_RUN = "docker-run"
    COMPOSE_STATIC = "compose-static"
    COMPOSE_GENERATED = "compose-generated"


class RuntimeKind(StrEnum):
    """What kind of identity a ContainerHandle carries."""

    DOCKER_RUN = "docker-run"
    DOCKER_COMPOSE = "docker-compose"


class PublishStrategy(StrEnum):
    LOOPBACK_REMAP = "loopback-remap"
    GATEWAY_NETWORK = "gateway-network"
    FIXED_EXCLUSIVE = "fixed-exclusive"


class ModelState(StrEnum):
    NOT_STARTED = "not_started"
    PROVISIONING = "provisioning"
    STARTING = "starting"
    WARMING_UP = "warming_up"
    SERVING = "serving"
    UNHEALTHY = "unhealthy"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class ContainerHandle:
    """Identity of a running (or stopped) container/compose stack.

    Returned by `ModelPlugin.start()`; consumed generically by
    `docker_adapter.py` for stop/status/logs, and by the gateway to know
    where to proxy requests for this model.
    """

    kind: RuntimeKind
    container_name: str | None = None
    compose_project: str | None = None
    compose_file: Path | None = None
    service_name: str | None = None
    backend_address: str = ""


@dataclass(frozen=True)
class ResourceRequirements:
    min_free_disk_gib: float = 0.0
    min_free_memory_gib: float = 0.0
    gpu_required: bool = False


@dataclass(frozen=True)
class PluginMetadata:
    name: str
    display_name: str
    description: str
    docs_url: str | None
    resources: ResourceRequirements


@dataclass(frozen=True)
class HealthResult:
    healthy: bool
    detail: str = ""


@dataclass
class RunContext:
    """Runtime context passed into plugin methods for one start() invocation."""

    repo_dir: Path
    state_dir: Path
    variant_id: str | None
    env_values: dict[str, str] = field(default_factory=dict)
    allocated_port: int = 0


@dataclass
class PluginStatus:
    state: ModelState
    handle: ContainerHandle | None
    message: str = ""


@runtime_checkable
class ModelPlugin(Protocol):
    """The interface every plugin implements — manifest-backed or hand-written."""

    metadata: PluginMetadata

    def provision(self, ctx: RunContext) -> None:
        """Run any host-side pre-step (weight download, cache build)."""
        ...

    def start(self, ctx: RunContext) -> ContainerHandle:
        """Launch the model server and return its container identity."""
        ...

    def health_check(self, ctx: RunContext, handle: ContainerHandle) -> HealthResult:
        """Check whether the running backend is ready to serve requests."""
        ...
