"""Per-model background supervisor: provision -> start -> poll health."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from dgx_hub import docker_adapter
from dgx_hub.gateway import auto_sync
from dgx_hub.plugins.base import ContainerHandle, ModelPlugin, ModelState, RunContext


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
        try:
            self._update(state=ModelState.PROVISIONING, started_at=datetime.now(UTC))
            self.plugin.provision(self.ctx)

            self._update(state=ModelState.STARTING)
            handle = self.plugin.start(self.ctx)
            self._update(handle=handle)

            self._wait_for_container_running(handle, timeout=self.container_start_timeout)

            self._update(state=ModelState.WARMING_UP)
            self._poll_until_serving(handle)
        except Exception as exc:
            self._update(state=ModelState.FAILED, error=str(exc))
        finally:
            auto_sync.try_reconcile_quietly()

    def _wait_for_container_running(self, handle: ContainerHandle, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            if docker_adapter.status(handle).running:
                return
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

            if result.healthy:
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
