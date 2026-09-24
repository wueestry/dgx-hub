"""Per-model background supervisor: provision -> start -> poll health."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from dgx_hub import docker_adapter
from dgx_hub.gateway import auto_sync
from dgx_hub.logging_config import get_logger
from dgx_hub.plugins.base import ContainerHandle, ModelPlugin, ModelState, RunContext

logger = get_logger(__name__)


@dataclass(frozen=True)
class SupervisorStatus:
    state: ModelState = ModelState.NOT_STARTED
    handle: ContainerHandle | None = None
    message: str = ""
    error: str | None = None
    started_at: datetime | None = None


class ModelSupervisor:
    """Owns one model's provision -> start -> health-poll lifecycle."""

    def __init__(
        self,
        name: str,
        plugin: ModelPlugin,
        ctx: RunContext,
        container_start_timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.plugin = plugin
        self.ctx = ctx
        self.container_start_timeout = container_start_timeout
        self._lock = threading.Lock()
        self._status = SupervisorStatus()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def status(self) -> SupervisorStatus:
        with self._lock:
            return self._status

    def _update(self, **kwargs: Any) -> None:
        with self._lock:
            self._status = replace(self._status, **kwargs)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_terminal(self) -> bool:
        return self.status.state in (ModelState.SERVING, ModelState.FAILED)

    def start(self) -> None:
        """Launch the background thread. Non-blocking."""
        self._thread = threading.Thread(
            target=self._run, name=f"supervisor-{self.name}", daemon=True
        )
        self._thread.start()

    def request_stop(self) -> None:
        """Ask an in-progress health-poll loop to exit early (does not stop
        the container itself — see docker_adapter.stop / commands/stop.py).
        """
        self._stop_event.set()

    def _run(self) -> None:
        logger.info("%s: supervisor starting (provision -> start -> health-poll)", self.name)
        self.ctx.on_output = self._on_output
        try:
            self._update(
                state=ModelState.PROVISIONING,
                started_at=datetime.now(UTC),
                message="running provisioning step",
            )
            self.plugin.provision(self.ctx)

            self._update(state=ModelState.STARTING, message="starting container")
            handle = self.plugin.start(self.ctx)
            logger.info(
                "%s: start() returned handle container_name=%r backend=%r",
                self.name,
                handle.container_name,
                handle.backend_address,
            )
            self._update(
                handle=handle,
                message=f"waiting for {handle.container_name or self.name} to report running",
            )

            self._wait_for_container_running(handle, timeout=self.container_start_timeout)

            self._update(state=ModelState.WARMING_UP, message="")
            self._poll_until_serving(handle)
            logger.info("%s: supervisor finished with state=%s", self.name, self.status.state.value)
        except Exception as exc:
            logger.error("%s: supervisor failed: %s", self.name, exc)
            self._update(state=ModelState.FAILED, error=str(exc))
        finally:
            auto_sync.try_reconcile_quietly()

    def _on_output(self, line: str) -> None:
        """Surface the latest line of provision/start script output as the
        dashboard's detail text while those (possibly very long) steps run."""
        self._update(message=line[:200])

    def _wait_for_container_running(self, handle: ContainerHandle, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        started = time.monotonic()
        while True:
            if docker_adapter.status(handle).running:
                logger.info(
                    "%s: container reached running state after %.1fs",
                    self.name,
                    time.monotonic() - started,
                )
                return
            elapsed = time.monotonic() - started
            self._update(message=f"waiting for container to start ({elapsed:.0f}s)")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self._stop_event.wait(min(1.0, remaining)):
                return
        raise RuntimeError(
            f"{self.name}: container did not reach running state within {timeout:.0f}s"
        )

    def _poll_until_serving(self, handle: ContainerHandle) -> None:
        timing = self.plugin.metadata.health_timing
        started = time.monotonic()

        while True:
            result = self.plugin.health_check(self.ctx, handle)
            elapsed = time.monotonic() - started
            logger.debug(
                "%s: health check at %.0fs: healthy=%s detail=%r",
                self.name,
                elapsed,
                result.healthy,
                result.detail,
            )

            if result.healthy:
                logger.info("%s: became healthy after %.0fs", self.name, elapsed)
                self._update(state=ModelState.SERVING, message="", error=None)
                return

            if elapsed > timing.expected_first_boot_seconds:
                self._update(
                    state=ModelState.FAILED,
                    error=(
                        f"did not become healthy within "
                        f"{timing.expected_first_boot_seconds:.0f}s: {result.detail}"
                    ),
                )
                return

            if elapsed < timing.startup_grace_seconds:
                self._update(message=f"warming up ({elapsed:.0f}s)")
            else:
                self._update(
                    message=(
                        f"waiting for health ({elapsed:.0f}s / "
                        f"{timing.expected_first_boot_seconds:.0f}s): {result.detail}"
                    )
                )

            if self._stop_event.wait(timing.interval_seconds):
                return
