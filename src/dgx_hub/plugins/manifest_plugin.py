"""ManifestPlugin: turns a parsed plugin.toml into a working ModelPlugin.

Satisfies the `ModelPlugin` protocol purely by rendering the manifest's
declarative sections (`[provision]`, `[docker]`, `[[variant]]`) into actual
subprocess/docker invocations. `stop`/`status`/`logs` are intentionally
absent — those are always `docker_adapter.py` functions.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx

from dgx_hub.launch.docker_run import build_argv, merge_variant
from dgx_hub.plugins.base import (
    ContainerHandle,
    HealthResult,
    LaunchMode,
    PluginMetadata,
    ResourceRequirements,
    RunContext,
    RuntimeKind,
)
from dgx_hub.plugins.manifest import PluginManifest


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
        docker_spec = self.manifest.docker
        if docker_spec.mode != LaunchMode.DOCKER_RUN:
            raise NotImplementedError(
                f"{self.metadata.name}: launch mode {docker_spec.mode!r} not yet "
                "supported (docker-compose support lands in Phase 2)"
            )

        variant = None
        if ctx.variant_id is not None:
            variant = self.manifest.get_variant(ctx.variant_id)

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

        effective = merge_variant(docker_spec, variant)
        return ContainerHandle(
            kind=RuntimeKind.DOCKER_RUN,
            container_name=effective.container_name,
            backend_address=built.backend_address,
        )

    def health_check(self, ctx: RunContext, handle: ContainerHandle) -> HealthResult:
        url = self.manifest.health.url.format(backend_address=handle.backend_address)
        try:
            response = httpx.get(url, timeout=self.manifest.health.timeout_seconds)
        except httpx.HTTPError as exc:
            return HealthResult(healthy=False, detail=str(exc))
        if response.status_code >= 400:
            return HealthResult(healthy=False, detail=f"HTTP {response.status_code}")
        return HealthResult(healthy=True)
