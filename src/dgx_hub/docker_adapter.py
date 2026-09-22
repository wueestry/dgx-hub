"""Generic, plugin-agnostic operations against Docker / Docker Compose."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass

from dgx_hub.plugins.base import ContainerHandle, RuntimeKind

DEFAULT_GATEWAY_NETWORK = "dgx-hub-net"


class DockerAdapterError(RuntimeError):
    """Raised when a docker/docker-compose CLI invocation fails unexpectedly."""


@dataclass(frozen=True)
class DockerStatus:
    exists: bool
    running: bool
    health: str | None = None  # None | "starting" | "healthy" | "unhealthy" | "none"
    started_at: str | None = None
    exit_code: int | None = None


def _compose_base_argv(handle: ContainerHandle) -> list[str]:
    argv = ["docker", "compose"]
    if handle.compose_file is not None:
        argv += ["-f", str(handle.compose_file)]
    if handle.compose_project:
        argv += ["-p", handle.compose_project]
    return argv


def _identity(handle: ContainerHandle) -> str:
    """The name docker/compose commands address this handle by."""
    if handle.kind == RuntimeKind.DOCKER_RUN:
        if not handle.container_name:
            raise ValueError("docker-run ContainerHandle requires container_name")
        return handle.container_name
    if not handle.service_name:
        raise ValueError("docker-compose ContainerHandle requires service_name")
    return handle.service_name


def _inspect_name(handle: ContainerHandle) -> str:
    """The name `docker inspect` addresses this handle by."""
    if handle.kind == RuntimeKind.DOCKER_RUN:
        return _identity(handle)
    if handle.container_name:
        return handle.container_name
    return f"{handle.compose_project}-{handle.service_name}-1"


def stop(handle: ContainerHandle, grace_seconds: int = 30) -> None:
    """Stop the container/service."""
    if handle.kind == RuntimeKind.DOCKER_RUN:
        argv = ["docker", "stop", "--time", str(grace_seconds), _identity(handle)]
    else:
        argv = [
            *_compose_base_argv(handle),
            "stop",
            "--timeout",
            str(grace_seconds),
            _identity(handle),
        ]
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode != 0 and "No such container" not in result.stderr:
        raise DockerAdapterError(f"stop failed: {result.stderr.strip()}")


def _docker_inspect(name: str) -> dict | None:
    result = subprocess.run(
        ["docker", "inspect", name], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError) as exc:
        raise DockerAdapterError(f"could not parse `docker inspect` output: {exc}") from exc
    return payload


def status(handle: ContainerHandle) -> DockerStatus:
    """Ground-truth status read straight from `docker inspect`."""
    payload = _docker_inspect(_inspect_name(handle))
    if payload is None:
        return DockerStatus(exists=False, running=False)

    state = payload.get("State", {})
    health = state.get("Health", {}).get("Status")
    return DockerStatus(
        exists=True,
        running=bool(state.get("Running")),
        health=health,
        started_at=state.get("StartedAt"),
        exit_code=state.get("ExitCode"),
    )


def container_ip(handle: ContainerHandle, network: str = DEFAULT_GATEWAY_NETWORK) -> str | None:
    """The container's IP address on `network`, or None if it's not attached
    to it. Needed to reach a `gateway-network`-strategy backend
    (`backend_address` = `container_name:port`) from a process that isn't
    itself attached to that bridge and so can't resolve the name via
    Docker's embedded DNS -- e.g. the LiteLLM gateway container, which runs
    under `network_mode: host` to reach loopback-bound backends directly.
    """
    payload = _docker_inspect(_inspect_name(handle))
    if payload is None:
        return None
    networks = payload.get("NetworkSettings", {}).get("Networks") or {}
    net = networks.get(network)
    if not net:
        return None
    return net.get("IPAddress") or None


def inspect_ports(handle: ContainerHandle) -> dict[int, int]:
    """Real container_port -> host_port bindings, read from Docker itself
    rather than trusted from the manifest.
    """
    payload = _docker_inspect(_inspect_name(handle))
    if payload is None:
        return {}

    ports: dict[int, int] = {}
    raw_ports = payload.get("NetworkSettings", {}).get("Ports") or {}
    for container_port_proto, bindings in raw_ports.items():
        if not bindings:
            continue
        container_port = int(container_port_proto.split("/")[0])
        host_port = int(bindings[0]["HostPort"])
        ports[container_port] = host_port
    return ports


def logs(handle: ContainerHandle, follow: bool = False, tail: int = 200) -> Iterator[str]:
    """Yield log lines, optionally following (`docker logs -f` / `docker compose logs -f`)."""
    if handle.kind == RuntimeKind.DOCKER_RUN:
        argv = ["docker", "logs", "--tail", str(tail)]
    else:
        argv = [*_compose_base_argv(handle), "logs", "--tail", str(tail)]
    if follow:
        argv.append("--follow")
    argv.append(_identity(handle))

    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    stdout = process.stdout
    if stdout is None:
        return
    try:
        for line in stdout:
            yield line.rstrip("\n")
    finally:
        if process.poll() is None:
            process.terminate()


def ensure_network(name: str = DEFAULT_GATEWAY_NETWORK) -> None:
    """Idempotently create the shared bridge network used by
    `gateway-network` publish-strategy plugins."""
    check = subprocess.run(
        ["docker", "network", "inspect", name], capture_output=True, text=True, check=False
    )
    if check.returncode == 0:
        return
    create = subprocess.run(
        ["docker", "network", "create", name], capture_output=True, text=True, check=False
    )
    if create.returncode != 0:
        raise DockerAdapterError(
            f"failed to create network {name!r}: {create.stderr.strip()}"
        )
