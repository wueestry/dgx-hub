"""Turns a declarative [docker] manifest spec into an actual `docker run` argv.

This is what makes the "generic plugin shape" actually generic: instead of
shelling out to each repo's own wrapper script, the CLI builds and issues
the `docker run` invocation itself — which is also what lets it own port
mapping directly for bridge-networked containers, with no need to patch an
upstream script to unlock concurrent multi-model serving.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dgx_hub.plugins.base import LaunchMode, NetworkMode
from dgx_hub.plugins.manifest import DockerSpec, VariantSpec


@dataclass(frozen=True)
class BuiltCommand:
    argv: list[str]
    backend_address: str


def merge_variant(docker_spec: DockerSpec, variant: VariantSpec | None) -> DockerSpec:
    """Apply a variant's docker_overrides on top of the base [docker] spec.

    Only fields the variant explicitly sets are replaced; everything else is
    inherited from the plugin's base [docker] section.
    """
    if variant is None:
        return docker_spec

    overrides = variant.docker_overrides
    updates: dict[str, Any] = {}
    if overrides.image is not None:
        updates["image"] = overrides.image
    if overrides.command_args is not None:
        updates["command_args"] = overrides.command_args
    if overrides.network_mode is not None:
        updates["network_mode"] = overrides.network_mode
    if overrides.gpus is not None:
        updates["gpus"] = overrides.gpus
    if overrides.ports is not None:
        updates["ports"] = overrides.ports
    if overrides.volumes is not None:
        updates["volumes"] = overrides.volumes
    return docker_spec.model_copy(update=updates) if updates else docker_spec


def _substitute(value: str, repo_dir: Path) -> str:
    return value.replace("${REPO_DIR}", str(repo_dir))


def build_argv(
    docker_spec: DockerSpec,
    variant: VariantSpec | None,
    env_values: dict[str, str],
    allocated_port: int,
    repo_dir: Path,
) -> BuiltCommand:
    """Build a `docker run` argv from a declarative spec.

    `env_values` holds the plugin's configured [env.*] values. A variable
    named exactly `EXTRA_ARGS` is treated specially by convention — its
    value is shlex-split and appended to the container command instead of
    being set as an environment variable, preserving each repo's original
    free-form escape hatch.
    """
    if docker_spec.mode != LaunchMode.DOCKER_RUN:
        raise ValueError(
            f"build_argv only supports LaunchMode.DOCKER_RUN, got {docker_spec.mode!r}; "
            "use launch/docker_compose.py for compose modes"
        )

    spec = merge_variant(docker_spec, variant)
    if not spec.image or not spec.container_name:
        raise ValueError("docker-run mode requires both image and container_name")

    argv: list[str] = ["docker", "run", "--detach", "--name", spec.container_name]

    if spec.network_mode == NetworkMode.HOST:
        argv += ["--network", "host"]

    if spec.gpus:
        argv += ["--gpus", spec.gpus]
    if spec.ipc:
        argv += ["--ipc", spec.ipc]
    if spec.shm_size:
        argv += ["--shm-size", spec.shm_size]

    for vol in spec.volumes:
        argv += ["-v", _substitute(vol, repo_dir)]

    command_args = list(spec.command_args)
    for name, value in sorted(env_values.items()):
        if name == "EXTRA_ARGS":
            if value:
                command_args += shlex.split(value)
            continue
        if name in spec.env_passthrough:
            continue  # forwarded from the host environment below, not the configured value
        argv += ["-e", f"{name}={value}"]

    for name in spec.env_passthrough:
        host_value = os.environ.get(name)
        if host_value is not None:
            argv += ["-e", f"{name}={host_value}"]

    backend_address = ""
    if spec.network_mode == NetworkMode.HOST:
        # No `-p` mapping applies here: the container binds the host port(s)
        # directly. The caller is responsible for having told the app itself
        # to bind `allocated_port` via a manifest-declared port-override env
        # var already present in env_values, if concurrent serving is needed.
        if spec.ports:
            container_port = spec.ports[0].container_port
            backend_address = f"127.0.0.1:{allocated_port or container_port}"
    else:
        for port in spec.ports:
            if port.publish_strategy == "loopback-remap":
                host_port = allocated_port or port.container_port
                argv += ["-p", f"127.0.0.1:{host_port}:{port.container_port}"]
                if not backend_address:
                    backend_address = f"127.0.0.1:{host_port}"
            elif port.publish_strategy == "fixed-exclusive":
                argv += ["-p", f"{port.container_port}:{port.container_port}"]
                if not backend_address:
                    backend_address = f"127.0.0.1:{port.container_port}"
            elif port.publish_strategy == "gateway-network":
                # Requires a pre-created `dgx-hub-net` bridge network
                # (docker_adapter.ensure_network); no host port is published.
                argv += ["--network", "dgx-hub-net"]
                if not backend_address:
                    backend_address = f"{spec.container_name}:{port.container_port}"
            else:
                raise ValueError(f"unknown publish_strategy: {port.publish_strategy!r}")

    argv.append(spec.image)
    argv += command_args

    return BuiltCommand(argv=argv, backend_address=backend_address)
