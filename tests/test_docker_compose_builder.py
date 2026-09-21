"""Tests for launch/docker_compose.py — the compose-mode launch builder.

Not yet exercised by any real plugin manifest (the two repos that generate
a compose file both ended up needing [fallback] instead), but the schema
and engine support it for future plugins that are pure compose.
"""

from __future__ import annotations

import pytest

from dgx_hub.launch.docker_compose import build_compose_up, build_generate_command
from dgx_hub.plugins.base import LaunchMode
from dgx_hub.plugins.manifest import DockerSpec, PortSpec


def make_static_spec(**overrides: object) -> DockerSpec:
    base: dict = {
        "mode": LaunchMode.COMPOSE_STATIC,
        "compose_file": "compose.yml",
        "compose_project": "p1",
        "service_name": "svc",
    }
    base.update(overrides)
    return DockerSpec(**base)


def test_compose_up_basic_argv() -> None:
    spec = make_static_spec()
    built = build_compose_up(spec, {}, allocated_port=0)
    assert built.argv == ["docker", "compose", "-f", "compose.yml", "-p", "p1", "up", "--detach"]


def test_compose_up_injects_port_override_env() -> None:
    spec = make_static_spec(
        ports=[
            PortSpec(
                container_port=8888,
                publish_strategy="loopback-remap",
                port_override_env_var="SERVING_PORT",
            )
        ]
    )
    built = build_compose_up(spec, {}, allocated_port=19999)
    assert built.env["SERVING_PORT"] == "19999"
    assert built.backend_address == "127.0.0.1:19999"


def test_compose_up_env_values_forwarded() -> None:
    spec = make_static_spec()
    built = build_compose_up(spec, {"MODE": "dspark"}, allocated_port=0)
    assert built.env["MODE"] == "dspark"


def test_compose_up_rejects_docker_run_mode() -> None:
    spec = DockerSpec(mode=LaunchMode.DOCKER_RUN, image="x", container_name="x")
    with pytest.raises(ValueError, match="DOCKER_RUN"):
        build_compose_up(spec, {}, allocated_port=0)


def test_generate_command_requires_generated_mode() -> None:
    spec = make_static_spec()
    with pytest.raises(ValueError, match="COMPOSE_GENERATED"):
        build_generate_command(spec, {})


def test_generate_command_returns_argv_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "secret")
    spec = DockerSpec(
        mode=LaunchMode.COMPOSE_GENERATED,
        compose_file="compose.yml",
        compose_project="p1",
        service_name="svc",
        generate_command=["./start.sh", "compose-gen"],
    )
    argv, env = build_generate_command(spec, {"MODE": "dspark"})
    assert argv == ["./start.sh", "compose-gen"]
    assert env["MODE"] == "dspark"
    assert env["HF_TOKEN"] == "secret"
