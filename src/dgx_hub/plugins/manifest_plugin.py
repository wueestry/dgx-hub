"""ManifestPlugin: turns a parsed plugin.toml into a working ModelPlugin."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import httpx

from dgx_hub import docker_adapter
from dgx_hub.launch.docker_compose import build_compose_up, build_generate_command
from dgx_hub.launch.docker_run import build_argv, merge_variant
from dgx_hub.plugins.base import (
    ContainerHandle,
    HealthResult,
    HealthTiming,
    LaunchMode,
    NetworkMode,
    PluginMetadata,
    ResourceRequirements,
    RunContext,
    RuntimeKind,
)
from dgx_hub.plugins.manifest import DockerSpec, PluginManifest


class ManifestPluginError(RuntimeError):
    """Raised when provisioning or starting a manifest-backed plugin fails."""


class ManifestPlugin:
    """A ModelPlugin backed by a declarative plugin.toml manifest."""

    def __init__(self, manifest: PluginManifest, repo_dir: Path) -> None:
        self.manifest = manifest
        self.repo_dir = repo_dir
        self.metadata = PluginMetadata(
            name=manifest.plugin.name,
            display_name=manifest.plugin.display_name,
            description=manifest.plugin.description,
            docs_url=manifest.plugin.docs_url,
            resources=ResourceRequirements(
                min_free_disk_gib=manifest.resources.min_free_disk_gib,
                min_free_memory_gib=manifest.resources.min_free_memory_gib,
                gpu_required=manifest.resources.gpu_required,
            ),
            health_timing=HealthTiming(
                interval_seconds=manifest.health.interval_seconds,
                startup_grace_seconds=manifest.health.startup_grace_seconds,
                expected_first_boot_seconds=manifest.health.expected_first_boot_seconds,
            ),
        )

    def provision(self, ctx: RunContext) -> None:
        spec = self.manifest.provision
        if spec is None:
            return
        result = subprocess.run(
            spec.command, cwd=self.repo_dir, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise ManifestPluginError(
                f"provisioning for {self.metadata.name!r} failed: {result.stderr.strip()}"
            )

    def start(self, ctx: RunContext) -> ContainerHandle:
        if self.manifest.fallback.enabled:
            return self._start_via_fallback(ctx)

        docker_spec = self.manifest.docker
        if docker_spec.mode == LaunchMode.DOCKER_RUN:
            return self._start_via_docker_run(ctx, docker_spec)
        return self._start_via_compose(ctx, docker_spec)

    def _start_via_docker_run(self, ctx: RunContext, docker_spec: DockerSpec) -> ContainerHandle:
        variant = None
        if ctx.variant_id is not None:
            variant = self.manifest.get_variant(ctx.variant_id)

        effective = merge_variant(docker_spec, variant)
        if effective.network_mode != NetworkMode.HOST:
            docker_adapter.ensure_network()

        built = build_argv(
            docker_spec=docker_spec,
            variant=variant,
            env_values=ctx.env_values,
            allocated_port=ctx.allocated_port,
            repo_dir=self.repo_dir,
        )
        result = subprocess.run(built.argv, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise ManifestPluginError(
                f"failed to start {self.metadata.name!r}: {result.stderr.strip()}"
            )

        return ContainerHandle(
            kind=RuntimeKind.DOCKER_RUN,
            container_name=effective.container_name,
            backend_address=built.backend_address,
            gateway_address=built.gateway_address,
        )

    def _run_in_repo(self, argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, cwd=self.repo_dir, env=env, capture_output=True, text=True, check=False
        )

    def _start_via_compose(self, ctx: RunContext, docker_spec: DockerSpec) -> ContainerHandle:
        if docker_spec.mode == LaunchMode.COMPOSE_GENERATED:
            gen_argv, gen_env = build_generate_command(docker_spec, ctx.env_values)
            gen_result = self._run_in_repo(gen_argv, gen_env)
            if gen_result.returncode != 0:
                raise ManifestPluginError(
                    f"compose generation for {self.metadata.name!r} failed: "
                    f"{gen_result.stderr.strip()}"
                )

        built = build_compose_up(docker_spec, ctx.env_values, ctx.allocated_port)
        result = self._run_in_repo(built.argv, built.env)
        if result.returncode != 0:
            raise ManifestPluginError(
                f"failed to start {self.metadata.name!r}: {result.stderr.strip()}"
            )

        return ContainerHandle(
            kind=RuntimeKind.DOCKER_COMPOSE,
            compose_project=docker_spec.compose_project,
            compose_file=self.repo_dir / docker_spec.compose_file
            if docker_spec.compose_file
            else None,
            service_name=docker_spec.service_name,
            container_name=docker_spec.container_name,
            backend_address=built.backend_address,
        )

    def _start_via_fallback(self, ctx: RunContext) -> ContainerHandle:
        """Last-resort escape hatch: shell out to the repo's own launch
        script instead of the declarative [docker] path, for repos whose
        launch logic (dynamic compose generation, entrypoint chaining, host-
        side path resolution) can't be safely replicated declaratively.
        `[docker]` still supplies the container identity for the generic
        stop/status/logs operations afterwards.
        """
        spec = self.manifest.fallback
        if not spec.start_command:
            raise ManifestPluginError(
                f"{self.metadata.name!r}: [fallback].enabled is true but no "
                "start_command is declared"
            )

        command = [
            token.format(variant=ctx.variant_id or "") for token in spec.start_command
        ]

        env = dict(os.environ)
        env.update(ctx.env_values)
        port_var = self._fallback_port_override_var()
        if port_var and ctx.allocated_port:
            env[port_var] = str(ctx.allocated_port)

        result = subprocess.run(
            command, cwd=self.repo_dir, env=env, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise ManifestPluginError(
                f"fallback start for {self.metadata.name!r} failed: {result.stderr.strip()}"
            )

        docker_spec = self.manifest.docker
        backend_address = ""
        if docker_spec.ports:
            port = ctx.allocated_port or docker_spec.ports[0].container_port
            backend_address = f"127.0.0.1:{port}"
        return ContainerHandle(
            kind=RuntimeKind.DOCKER_RUN,
            container_name=docker_spec.container_name,
            backend_address=backend_address,
        )

    def _fallback_port_override_var(self) -> str | None:
        """The env var (if any) a [fallback]-launched script reads to know
        which port to bind — the only lever available when dgx-hub doesn't
        control the launch command's own `-p`/`--port` flags directly.
        """
        for port in self.manifest.docker.ports:
            if port.port_override_env_var:
                return port.port_override_env_var
        return None

    def health_check(self, ctx: RunContext, handle: ContainerHandle) -> HealthResult:
        url = self.manifest.health.url.format(backend_address=handle.backend_address)
        try:
            response = httpx.get(url, timeout=self.manifest.health.timeout_seconds)
        except httpx.HTTPError as exc:
            return HealthResult(healthy=False, detail=str(exc))
        if response.status_code >= 400:
            return HealthResult(healthy=False, detail=f"HTTP {response.status_code}")
        return HealthResult(healthy=True)
