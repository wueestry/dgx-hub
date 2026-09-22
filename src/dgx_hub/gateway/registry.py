"""Live model_id -> backend_address routing table for the gateway."""

from __future__ import annotations

from dgx_hub import docker_adapter
from dgx_hub.plugins.base import ContainerHandle
from dgx_hub.process import state as state_store
from dgx_hub.process.state import ModelRunRecord


def current_routes() -> dict[str, str]:
    """model_id -> backend_address for every model recorded as 'serving'
    that's also still actually running per `docker inspect` — a model whose
    container died (or was removed) after the CLI process exited doesn't
    linger in the routing table.
    """
    routes: dict[str, str] = {}
    for record in state_store.load_all().values():
        if record.state != "serving" or not record.backend_address:
            continue
        if record.container_name is None and record.service_name is None:
            continue
        handle = record.to_handle()
        if not docker_adapter.status(handle).running:
            continue
        address = _resolve_address(record, handle)
        for model_id in record.served_model_ids or [record.name]:
            routes[model_id] = address
    return routes


def _resolve_address(record: ModelRunRecord, handle: ContainerHandle) -> str:
    """`backend_address` is `container_name:port` for `gateway-network`
    plugins — resolvable by name only from a process attached to that same
    bridge network. Callers outside it (e.g. the LiteLLM gateway container,
    which uses host networking to reach loopback-bound backends directly)
    need the container's actual bridge IP instead.
    """
    host, _, port = record.backend_address.rpartition(":")
    if host != record.container_name:
        return record.backend_address
    ip = docker_adapter.container_ip(handle)
    if ip is None:
        return record.backend_address
    return f"{ip}:{port}"
