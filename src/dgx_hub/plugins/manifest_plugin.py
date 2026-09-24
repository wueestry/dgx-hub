"""ManifestPlugin: turns a parsed plugin.toml into a working ModelPlugin."""

from __future__ import annotations

import os
import shlex
from pathlib import Path

import httpx

from dgx_hub import docker_adapter
from dgx_hub.launch.docker_compose import build_compose_up, build_generate_command
from dgx_hub.launch.docker_run import build_argv, merge_variant
from dgx_hub.logging_config import get_logger
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
from dgx_hub.plugins.patching import apply_patches
from dgx_hub.process.stream import StreamResult, failure_summary, run_streaming

logger = get_logger(__name__)


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
                exclusive_gpu=manifest.resources.exclusive_gpu,
            ),
            health_timing=HealthTiming(
                interval_seconds=manifest.health.interval_seconds,
                startup_grace_seconds=manifest.health.startup_grace_seconds,
                expected_first_boot_seconds=manifest.health.expected_first_boot_seconds,
            ),
        )

    def _repo_required(self) -> bool:
        """Whether this plugin's [provision]/[fallback]/[docker] steps
        actually need `self.repo_dir` to exist. Several manifests declare
        [source].repo_url purely as documentation (e.g. a Hugging Face
        checkpoint id, resolved by the image itself at boot) and never touch
        the clone -- cloning those would be wasted work at best and, for a
        Hugging Face "repo", a multi-GB weight download at worst.
        """
        if self.manifest.provision is not None or self.manifest.patch:
            return True
        if self.manifest.fallback.enabled:
            return True
        docker = self.manifest.docker
        if any("${REPO_DIR}" in v for v in docker.volumes) or any(
            "${REPO_DIR}" in a for a in docker.command_args
        ):
            return True
        for variant in self.manifest.variant:
            overrides = variant.docker_overrides
            if overrides.volumes and any("${REPO_DIR}" in v for v in overrides.volumes):
                return True
            if overrides.command_args and any(
                "${REPO_DIR}" in a for a in overrides.command_args
            ):
                return True
        return False

    def _run(
        self,
        argv: list[str],
        ctx: RunContext | None,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> StreamResult:
        return run_streaming(
            argv,
            logger=logger,
            prefix=self.metadata.name,
            cwd=cwd,
            env=env,
            on_line=ctx.on_output if ctx is not None else None,
        )

    def _fail(self, what: str, result: StreamResult) -> ManifestPluginError:
        summary = failure_summary(result.tail)
        # Every line was already logged as it streamed; only the file needs
        # the tail again, next to the failure.
        logger.debug(
            "%s: %s failed (exit %d); last output:\n%s",
            self.metadata.name,
            what,
            result.returncode,
            result.output,
        )
        return ManifestPluginError(
            f"{what} for {self.metadata.name!r} failed (exit {result.returncode}): {summary}"
        )

    def _ensure_repo(self, ctx: RunContext | None = None) -> None:
        """Clone [source].repo_url into `self.repo_dir` if it isn't there yet.

        Idempotent: an existing directory (from a previous clone) is left
        alone -- plugins that want fresher sources re-run their own
        [provision]/[fallback] scripts, which pull/update inside the clone
        themselves where that matters.
        """
        if not self._repo_required() or self.repo_dir.exists():
            return
        repo_url = self.manifest.source.repo_url
        logger.info("%s: cloning %s into %s", self.metadata.name, repo_url, self.repo_dir)
        self.repo_dir.parent.mkdir(parents=True, exist_ok=True)
        result = self._run(["git", "clone", "--depth", "1", repo_url, str(self.repo_dir)], ctx)
        if result.returncode != 0:
            raise self._fail(f"cloning {repo_url}", result)
        logger.info("%s: clone finished", self.metadata.name)

    def provision(self, ctx: RunContext) -> None:
        self._ensure_repo(ctx)
        if self.manifest.patch:
            apply_patches(self.repo_dir, self.manifest.patch, logger, self.metadata.name)
        spec = self.manifest.provision
        if spec is None:
            logger.debug("%s: no [provision] step declared, skipping", self.metadata.name)
            return
        logger.info(
            "%s: provisioning: %s (cwd=%s)",
            self.metadata.name,
            shlex.join(spec.command),
            self.repo_dir,
        )
        result = self._run(spec.command, ctx, cwd=self.repo_dir)
        if result.returncode != 0:
            raise self._fail("provisioning", result)
        logger.info("%s: provisioning finished", self.metadata.name)

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

        self._reclaim_stale_container_name(effective.container_name)

        built = build_argv(
            docker_spec=docker_spec,
            variant=variant,
            env_values=ctx.env_values,
            allocated_port=ctx.allocated_port,
            repo_dir=self.repo_dir,
        )
        logger.info("%s: starting: %s", self.metadata.name, shlex.join(built.argv))
        result = self._run(built.argv, ctx)
        if result.returncode != 0:
            raise self._fail("docker run", result)
        logger.info("%s: container %s started", self.metadata.name, effective.container_name)

        return ContainerHandle(
            kind=RuntimeKind.DOCKER_RUN,
            container_name=effective.container_name,
            backend_address=built.backend_address,
            gateway_address=built.gateway_address,
        )

    def _reclaim_stale_container_name(self, container_name: str | None) -> None:
        """Remove a leftover exited container occupying `container_name`.

        `docker stop` (used by `dgx-hub stop`) never removes the container,
        just stops it -- so `docker run --name X` on the next `dgx-hub
        start` hits `Conflict. The container name "/X" is already in use`.
        A container that's still running under that name is left alone and
        reported as a real conflict instead of force-removed out from under
        whatever is using it.
        """
        if not container_name:
            return
        handle = ContainerHandle(kind=RuntimeKind.DOCKER_RUN, container_name=container_name)
        existing = docker_adapter.status(handle)
        if not existing.exists:
            return
        if existing.running:
            raise ManifestPluginError(
                f"{self.metadata.name!r}: a container named {container_name!r} is already "
                "running outside dgx-hub's tracked state; stop it manually "
                "(`docker stop`/`docker rm`) before starting again"
            )
        logger.info(
            "%s: removing stale exited container %r left behind by a previous stop",
            self.metadata.name,
            container_name,
        )
        docker_adapter.remove(handle)

    def _start_via_compose(self, ctx: RunContext, docker_spec: DockerSpec) -> ContainerHandle:
        if docker_spec.mode == LaunchMode.COMPOSE_GENERATED:
            gen_argv, gen_env = build_generate_command(docker_spec, ctx.env_values)
            logger.info("%s: generating compose file: %s", self.metadata.name, shlex.join(gen_argv))
            gen_result = self._run(gen_argv, ctx, cwd=self.repo_dir, env=gen_env)
            if gen_result.returncode != 0:
                raise self._fail("compose generation", gen_result)

        built = build_compose_up(docker_spec, ctx.env_values, ctx.allocated_port)
        logger.info("%s: starting: %s", self.metadata.name, shlex.join(built.argv))
        result = self._run(built.argv, ctx, cwd=self.repo_dir, env=built.env)
        if result.returncode != 0:
            raise self._fail("docker compose up", result)
        logger.info("%s: compose stack started", self.metadata.name)

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

        docker_spec = self.manifest.docker
        if docker_spec.network_mode != NetworkMode.HOST:
            # Mirrors _start_via_docker_run: the repo's own launch script is
            # expected to join dgx-hub-net itself (see the plugin's own
            # [[patch]]/docs) so the gateway container can reach it by name;
            # this only has to exist first.
            docker_adapter.ensure_network()

        command = [
            token.format(variant=ctx.variant_id or "") for token in spec.start_command
        ]

        env = dict(os.environ)
        env.update(ctx.env_values)
        port_var = self._fallback_port_override_var()
        if port_var and ctx.allocated_port:
            env[port_var] = str(ctx.allocated_port)

        logger.info(
            "%s: starting via fallback: %s (cwd=%s)",
            self.metadata.name,
            shlex.join(command),
            self.repo_dir,
        )
        result = self._run(command, ctx, cwd=self.repo_dir, env=env)
        if result.returncode != 0:
            raise self._fail("fallback start", result)
        logger.info("%s: fallback start finished", self.metadata.name)

        backend_address = ""
        gateway_address = ""
        if docker_spec.ports:
            port = ctx.allocated_port or docker_spec.ports[0].container_port
            backend_address = f"127.0.0.1:{port}"
            # Only reachable by name from the gateway's own container if the
            # repo's script actually joined dgx-hub-net -- true under host
            # networking regardless (nothing to join), so left empty there
            # like registry.current_routes() already expects.
            if docker_spec.network_mode != NetworkMode.HOST and docker_spec.container_name:
                gateway_address = f"{docker_spec.container_name}:{port}"
        return ContainerHandle(
            kind=RuntimeKind.DOCKER_RUN,
            container_name=docker_spec.container_name,
            backend_address=backend_address,
            gateway_address=gateway_address,
        )

    def run_fallback_stop(self) -> bool:
        """Run [fallback].stop_command in the repo, if this plugin launches
        via [fallback] and declares one. Returns whether it ran. Repo stop
        scripts also tear down sidecars dgx-hub doesn't know about (memory
        watchdogs, shm segments), which a bare `docker stop` would leak.
        """
        spec = self.manifest.fallback
        if not spec.enabled or not spec.stop_command or not self.repo_dir.exists():
            return False
        logger.info(
            "%s: stopping via fallback: %s", self.metadata.name, shlex.join(spec.stop_command)
        )
        result = self._run(list(spec.stop_command), None, cwd=self.repo_dir)
        if result.returncode != 0:
            raise self._fail("fallback stop", result)
        return True

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
