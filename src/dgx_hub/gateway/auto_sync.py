"""Best-effort LiteLLM reconciliation triggered from ordinary CLI commands.

This is a no-op — no network calls at all — unless `dgx-hub gateway start`
has been run at least once (its .env holds the master key/port). A user who
has never touched the gateway sees zero behavioral change from this. When
the gateway *is* running, called from commands/stop.py, commands/status.py,
and process/supervisor.py so LiteLLM's model list stays current without a
per-request lookup; failures (gateway not currently up, network hiccup) are
swallowed since none of those commands' primary jobs depend on it.
"""

from __future__ import annotations

import httpx

from dgx_hub.gateway.compose import gateway_paths, read_env_file
from dgx_hub.gateway.litellm_admin import LiteLLMAdminClient
from dgx_hub.gateway.reconcile import reconcile


def try_reconcile_quietly() -> None:
    env = read_env_file(gateway_paths().env_file)
    master_key = env.get("LITELLM_MASTER_KEY")
    port = env.get("LITELLM_PORT")
    if not master_key or not port:
        return

    try:
        with LiteLLMAdminClient(f"http://127.0.0.1:{port}", master_key, timeout=3.0) as client:
            reconcile(client)
    except httpx.HTTPError:
        pass
