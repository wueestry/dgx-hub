"""Start/stop/status/logs for the gateway's Postgres + LiteLLM infra."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator

import httpx

from dgx_hub import docker_adapter
from dgx_hub.gateway.compose import (
    GatewayPaths,
    ensure_gateway_files,
    gateway_paths,
    litellm_handle,
    postgres_handle,
    read_env_file,
)


class GatewayLifecycleError(RuntimeError):
    """Raised when bringing up or waiting on the gateway infra fails."""


def start(
    host: str = "0.0.0.0",
    litellm_port: int = 8888,
    ready_timeout: float = 60.0,
) -> GatewayPaths:
    paths = ensure_gateway_files(litellm_port, host)
    docker_adapter.ensure_network()

    result = subprocess.run(
        [
            "docker", "compose",
            "-f", str(paths.compose_file),
            "-p", "dgx-hub-gateway",
            "--env-file", str(paths.env_file),
            "up", "--detach",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GatewayLifecycleError(f"gateway infra failed to start: {result.stderr.strip()}")

    _wait_until_ready(litellm_port, timeout=ready_timeout)
    return paths


def _wait_until_ready(litellm_port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{litellm_port}/health/liveliness"
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=3.0)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(2.0)
    raise GatewayLifecycleError(f"litellm did not become ready within {timeout:.0f}s: {last_error}")


def stop(timeout: int = 30) -> dict[str, str]:
    """Stop litellm then postgres. Returns {service: outcome} for the CLI to render."""
    paths = gateway_paths()
    outcomes: dict[str, str] = {}
    for name, handle in (("litellm", litellm_handle(paths)), ("postgres", postgres_handle(paths))):
        try:
            docker_adapter.stop(handle, grace_seconds=timeout)
            outcomes[name] = "stopped"
        except docker_adapter.DockerAdapterError as exc:
            outcomes[name] = f"failed: {exc}"
    return outcomes


def status() -> dict[str, docker_adapter.DockerStatus]:
    paths = gateway_paths()
    return {
        "postgres": docker_adapter.status(postgres_handle(paths)),
        "litellm": docker_adapter.status(litellm_handle(paths)),
    }


def logs(service: str, follow: bool = False, tail: int = 200) -> Iterator[str]:
    paths = gateway_paths()
    handle = postgres_handle(paths) if service == "postgres" else litellm_handle(paths)
    yield from docker_adapter.logs(handle, follow=follow, tail=tail)


def master_key() -> str | None:
    return read_env_file(gateway_paths().env_file).get("LITELLM_MASTER_KEY")
