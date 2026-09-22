"""Push registry.current_routes() into LiteLLM's live model list.

Replaces the old proxy's per-request lookup: instead of consulting ground
truth on every incoming chat completion, whatever calls this (an explicit
`dgx-hub gateway sync`, or the auto-sync hooks in commands/process) pushes
the current diff into LiteLLM via its model-admin API.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dgx_hub.gateway.litellm_admin import LiteLLMAdminClient, LiteLLMModel
from dgx_hub.gateway.registry import current_routes


@dataclass(frozen=True)
class ReconcileResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)


def diff_routes(ground_truth: dict[str, str], live_models: list[LiteLLMModel]) -> ReconcileResult:
    """Pure diff, no network calls — the part worth unit-testing on its own."""
    live_by_id = {m.model_id: m for m in live_models}

    added = []
    updated = []
    for model_id, backend_address in ground_truth.items():
        api_base = f"http://{backend_address}/v1"
        live = live_by_id.get(model_id)
        if live is None:
            added.append(model_id)
        elif live.api_base != api_base:
            updated.append(model_id)

    removed = [model_id for model_id in live_by_id if model_id not in ground_truth]
    return ReconcileResult(added=sorted(added), updated=sorted(updated), removed=sorted(removed))


def apply_reconcile(
    client: LiteLLMAdminClient, ground_truth: dict[str, str], result: ReconcileResult
) -> None:
    for model_id in result.removed:
        client.delete_model(model_id)
    for model_id in (*result.added, *result.updated):
        # delete-then-recreate rather than requiring a separate /model/update
        # call — /model/new errors if the id already exists, and delete is a
        # no-op if it doesn't, so this handles both add and address-change
        # uniformly.
        client.delete_model(model_id)
        client.add_model(model_id, f"http://{ground_truth[model_id]}/v1")


def reconcile(client: LiteLLMAdminClient) -> ReconcileResult:
    ground_truth = current_routes()
    live_models = client.list_models()
    result = diff_routes(ground_truth, live_models)
    apply_reconcile(client, ground_truth, result)
    return result
