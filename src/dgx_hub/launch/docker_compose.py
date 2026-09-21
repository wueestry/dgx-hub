"""Builds `docker compose` invocations for compose-backed plugins."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dgx_hub.plugins.base import LaunchMode
from dgx_hub.plugins.manifest import DockerSpec


@dataclass(frozen=True)
class BuiltComposeCommand:
    argv: list[str]
    env: dict[str, str]
    backend_address: str


def _compose_base_argv(docker_spec: DockerSpec) -> list[str]:
    argv = ["docker", "compose"]
    if docker_spec.compose_file:
        argv += ["-f", docker_spec.compose_file]
    if docker_spec.compose_project:
        argv += ["-p", docker_spec.compose_project]
    return argv


def build_generate_command(
    docker_spec: DockerSpec, env_values: dict[str, str]
) -> tuple[list[str], dict[str, str]]:
    """The repo's own compose-file-generation step, for `compose-generated`
    mode — run this before `build_compose_up`.
    """
    if docker_spec.mode != LaunchMode.COMPOSE_GENERATED:
        raise ValueError(
            f"build_generate_command only applies to COMPOSE_GENERATED, "
            f"got {docker_spec.mode!r}"
        )
    if not docker_spec.generate_command:
        raise ValueError("[docker].generate_command is required for compose-generated mode")

    env = dict(os.environ)
    env.update(env_values)
    return list(docker_spec.generate_command), env


def build_compose_up(
    docker_spec: DockerSpec,
    env_values: dict[str, str],
    allocated_port: int,
) -> BuiltComposeCommand:
    """`docker compose up -d`, with env values (including any port override)
    passed through the process environment for the compose file's own
    `${VAR}` substitutions to resolve.
    """
    if docker_spec.mode not in (LaunchMode.COMPOSE_STATIC, LaunchMode.COMPOSE_GENERATED):
        raise ValueError(
            f"build_compose_up only supports compose modes, got {docker_spec.mode!r}"
        )

    argv = [*_compose_base_argv(docker_spec), "up", "--detach"]

    env = dict(os.environ)
    env.update(env_values)

    backend_address = ""
    if docker_spec.ports:
        port = docker_spec.ports[0]
        host_port = allocated_port or port.container_port
        if port.port_override_env_var:
            env[port.port_override_env_var] = str(host_port)
        backend_address = f"127.0.0.1:{host_port}"

    return BuiltComposeCommand(argv=argv, env=env, backend_address=backend_address)
