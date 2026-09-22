"""Thin client for LiteLLM proxy's model-admin REST API."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

import httpx


@dataclass(frozen=True)
class LiteLLMModel:
    model_name: str
    model_id: str
    api_base: str


class LiteLLMAdminClient:
    def __init__(self, base_url: str, master_key: str, timeout: float = 10.0) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {master_key}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LiteLLMAdminClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def list_models(self) -> list[LiteLLMModel]:
        response = self._client.get("/model/info")
        response.raise_for_status()
        payload = response.json()

        models = []
        for entry in payload.get("data", []):
            info = entry.get("model_info") or {}
            params = entry.get("litellm_params") or {}
            model_id = info.get("id") or entry.get("model_name", "")
            models.append(
                LiteLLMModel(
                    model_name=entry.get("model_name", ""),
                    model_id=model_id,
                    api_base=params.get("api_base", ""),
                )
            )
        return models

    def add_model(self, model_id: str, api_base: str) -> None:
        response = self._client.post(
            "/model/new",
            json={
                "model_name": model_id,
                "litellm_params": {
                    "model": f"openai/{model_id}",
                    "api_base": api_base,
                    # Local backends don't check this; LiteLLM still requires
                    # the field to be present.
                    "api_key": "not-needed-local-backend",
                },
                "model_info": {"id": model_id},
            },
        )
        response.raise_for_status()

    def delete_model(self, model_id: str) -> None:
        """No-op if `model_id` isn't currently registered.

        LiteLLM answers an unknown id with 400 (`"Model with id=... not
        found in db"`), not 404 — checked against a live main-stable proxy.
        """
        response = self._client.post("/model/delete", json={"id": model_id})
        if response.status_code in (400, 404) and "not found" in response.text.lower():
            return
        response.raise_for_status()

    def generate_key(self, name: str, budget_usd: float | None = None) -> str:
        payload: dict[str, object] = {"key_alias": name}
        if budget_usd is not None:
            payload["max_budget"] = budget_usd
        response = self._client.post("/key/generate", json=payload)
        response.raise_for_status()
        key = response.json().get("key")
        if not key:
            raise RuntimeError(
                f"litellm did not return a key in its /key/generate response: {response.text}"
            )
        return str(key)

    def list_keys(self) -> list[dict[str, object]]:
        """`return_full_object=true` is required — by default LiteLLM's
        /key/list returns bare hashed-key strings, not the alias/spend/budget
        metadata this is for. Verified against a live main-stable proxy.
        """
        response = self._client.get("/key/list", params={"return_full_object": "true"})
        response.raise_for_status()
        return list(response.json().get("keys", []))
