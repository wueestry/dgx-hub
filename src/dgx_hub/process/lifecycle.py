"""Stopping a model -- shared by `dgx-hub stop` and the start preflight."""

from __future__ import annotations

from dgx_hub import docker_adapter
from dgx_hub.logging_config import get_logger
from dgx_hub.plugins.base import ContainerHandle, RuntimeKind
from dgx_hub.plugins.manifest_plugin import ManifestPlugin
from dgx_hub.process import state as state_store
from dgx_hub.process.state import ModelRunRecord

logger = get_logger(__name__)


class StopError(RuntimeError):
    """Raised when a model could not be stopped."""


def handle_for(record: ModelRunRecord, plugin: ManifestPlugin | None) -> ContainerHandle | None:
    """The record's container identity, falling back to the plugin's static
    [docker].container_name when a start never got as far as recording one
    (failed or interrupted [fallback] start).
    """
    if record.container_name is not None:
        return record.to_handle()
    if plugin is not None and plugin.manifest.docker.container_name:
        return ContainerHandle(
            kind=RuntimeKind.DOCKER_RUN, container_name=plugin.manifest.docker.container_name
        )
    return None


def stop_model(record: ModelRunRecord, plugin: ManifestPlugin | None, timeout: int = 30) -> None:
    """Stop one model and mark its record stopped.

    [fallback] plugins go through their own stop script (which also tears
    down sidecars like memory watchdogs); everything else is a plain
    `docker stop`.
    """
    handle = handle_for(record, plugin)
    try:
        stopped = plugin is not None and plugin.run_fallback_stop()
        if not stopped:
            if handle is None:
                raise StopError(f"{record.name}: no container on record, nothing to stop")
            docker_adapter.stop(handle, grace_seconds=timeout)
    except StopError:
        raise
    except RuntimeError as exc:  # DockerAdapterError, ManifestPluginError
        raise StopError(f"{record.name}: stop failed: {exc}") from exc

    record.state = "stopped"
    state_store.save(record)
    logger.info("%s: stopped", record.name)
