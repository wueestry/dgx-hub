"""Live model_id -> gateway_address routing table for the gateway."""

from __future__ import annotations

from dgx_hub import docker_adapter
from dgx_hub.process import state as state_store


def current_routes() -> dict[str, str]:
    """model_id -> gateway_address for every model recorded as 'serving'
    that's also still actually running per `docker inspect` — a model whose
    container died (or was removed) after the CLI process exited doesn't
    linger in the routing table. Models with no `gateway_address` (started
    before this field existed, or `network_mode = host` backends, which
    can't join the gateway's bridge network) are excluded rather than
    registered with an address the gateway can't actually reach.
    """
    routes: dict[str, str] = {}
    for record in state_store.load_all().values():
        if record.state != "serving" or not record.gateway_address:
            continue
        if record.container_name is None and record.service_name is None:
            continue
        handle = record.to_handle()
        if not docker_adapter.status(handle).running:
            continue
        for model_id in record.served_model_ids or [record.name]:
            routes[model_id] = record.gateway_address
    return routes
