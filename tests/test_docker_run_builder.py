"""Tests for launch/docker_run.py — the [docker] spec -> `docker run` argv builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from dgx_hub.launch.docker_run import build_argv
from dgx_hub.plugins.base import LaunchMode, NetworkMode
from dgx_hub.plugins.manifest import DockerOverrides, DockerSpec, PortSpec, VariantSpec


def make_spec(**overrides: object) -> DockerSpec:
    base: dict = {
        "mode": LaunchMode.DOCKER_RUN,
        "image": "example/img:latest",
        "container_name": "c1",
        "network_mode": NetworkMode.BRIDGE,
        "gpus": "all",
        "ports": [PortSpec(container_port=8888, publish_strategy="loopback-remap")],
    }
    base.update(overrides)
    return DockerSpec(**base)


def test_basic_loopback_remap() -> None:
    spec = make_spec()
    built = build_argv(spec, None, {}, allocated_port=19999, repo_dir=Path("/tmp"))
    assert built.argv[:4] == ["docker", "run", "--detach", "--name"]
    assert "c1" in built.argv
    assert "127.0.0.1:19999:8888" in built.argv
    assert built.backend_address == "127.0.0.1:19999"
    assert built.argv[-1] == "example/img:latest"

    # Bridge-mode backends also join dgx-hub-net alongside their loopback
    # publish (the two aren't mutually exclusive outside host networking),
    # so the gateway can reach them by container name even though the CLI's
    # own health checks keep using the loopback backend_address above.
    assert "--network" in built.argv
    assert "dgx-hub-net" in built.argv
    assert built.gateway_address == "c1:8888"


def test_fixed_exclusive_uses_container_port_on_all_interfaces() -> None:
    spec = make_spec(ports=[PortSpec(container_port=8888, publish_strategy="fixed-exclusive")])
    built = build_argv(spec, None, {}, allocated_port=0, repo_dir=Path("/tmp"))
    assert "8888:8888" in built.argv
    assert built.backend_address == "127.0.0.1:8888"
    assert built.gateway_address == "c1:8888"


def test_gateway_network_strategy_has_no_host_publish() -> None:
    spec = make_spec(ports=[PortSpec(container_port=8888, publish_strategy="gateway-network")])
    built = build_argv(spec, None, {}, allocated_port=0, repo_dir=Path("/tmp"))
    assert "-p" not in built.argv
    assert "--network" in built.argv and "dgx-hub-net" in built.argv
    assert built.backend_address == "c1:8888"
    assert built.gateway_address == "c1:8888"


def test_host_network_fixed_exclusive_skips_port_publish() -> None:
    spec = make_spec(
        network_mode=NetworkMode.HOST,
        ports=[PortSpec(container_port=8888, publish_strategy="fixed-exclusive")],
    )
    built = build_argv(spec, None, {}, allocated_port=23456, repo_dir=Path("/tmp"))
    assert "--network" in built.argv
    assert "host" in built.argv
    assert "dgx-hub-net" not in built.argv
    assert "-p" not in built.argv
    assert built.backend_address == "127.0.0.1:8888"
    assert built.gateway_address == ""


def test_host_network_loopback_remap_requires_override_var() -> None:
    # DockerSpec itself doesn't enforce this (a manifest with [fallback]
    # enabled may declare an inconsistent [docker] as a non-authoritative,
    # best-effort description) — PluginManifest enforces it when [docker]
    # actually drives the launch; build_argv enforces it defensively too.
    spec = make_spec(
        network_mode=NetworkMode.HOST,
        ports=[PortSpec(container_port=8888, publish_strategy="loopback-remap")],
    )
    with pytest.raises(ValueError, match="port_override_env_var"):
        build_argv(spec, None, {}, allocated_port=23456, repo_dir=Path("/tmp"))


def test_host_network_loopback_remap_injects_port_override_env_var() -> None:
    spec = make_spec(
        network_mode=NetworkMode.HOST,
        ports=[
            PortSpec(
                container_port=8888,
                publish_strategy="loopback-remap",
                port_override_env_var="SERVING_PORT",
            )
        ],
    )
    built = build_argv(spec, None, {}, allocated_port=23456, repo_dir=Path("/tmp"))
    assert "-p" not in built.argv
    assert "dgx-hub-net" not in built.argv
    assert "SERVING_PORT=23456" in built.argv
    assert built.backend_address == "127.0.0.1:23456"
    assert built.gateway_address == ""


def test_extra_args_are_shlex_split_into_command_not_env() -> None:
    spec = make_spec()
    built = build_argv(
        spec, None, {"EXTRA_ARGS": "--foo bar --baz"}, allocated_port=1, repo_dir=Path("/tmp")
    )
    assert built.argv[-3:] == ["--foo", "bar", "--baz"]
    assert not any(a.startswith("EXTRA_ARGS") for a in built.argv)


def test_other_env_vars_become_dash_e_flags() -> None:
    spec = make_spec()
    built = build_argv(spec, None, {"QUANT": "nvfp4"}, allocated_port=1, repo_dir=Path("/tmp"))
    assert "-e" in built.argv
    assert "QUANT=nvfp4" in built.argv


def test_variant_overrides_image_and_appends_command_args() -> None:
    spec = make_spec()
    variant = VariantSpec(
        id="v1",
        docker_overrides=DockerOverrides(image="example/img:v1", command_args=["--flag"]),
    )
    built = build_argv(spec, variant, {}, allocated_port=1, repo_dir=Path("/tmp"))
    assert built.argv[-2] == "example/img:v1"
    assert built.argv[-1] == "--flag"


def test_env_passthrough_forwards_host_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "secret123")
    spec = make_spec(env_passthrough=["HF_TOKEN"])
    built = build_argv(spec, None, {}, allocated_port=1, repo_dir=Path("/tmp"))
    assert "HF_TOKEN=secret123" in built.argv


def test_volumes_substitute_repo_dir() -> None:
    spec = make_spec(volumes=["${REPO_DIR}/cache:/root/.cache"])
    built = build_argv(spec, None, {}, allocated_port=1, repo_dir=Path("/opt/repo"))
    assert "/opt/repo/cache:/root/.cache" in built.argv


def test_compose_mode_rejected_by_docker_run_builder() -> None:
    spec = DockerSpec(
        mode=LaunchMode.COMPOSE_STATIC,
        compose_file="compose.yml",
        compose_project="p",
        service_name="svc",
    )
    with pytest.raises(ValueError, match="COMPOSE_STATIC"):
        build_argv(spec, None, {}, allocated_port=1, repo_dir=Path("/tmp"))
