"""Tests for gateway/litellm_admin.py against a mocked HTTP transport."""

from __future__ import annotations

import json

import httpx
import pytest

from dgx_hub.gateway.litellm_admin import LiteLLMAdminClient


def _client(handler: httpx.MockTransport) -> LiteLLMAdminClient:
    client = LiteLLMAdminClient("http://127.0.0.1:8888", "sk-test")
    client._client = httpx.Client(
        base_url="http://127.0.0.1:8888",
        headers={"Authorization": "Bearer sk-test"},
        transport=handler,
    )
    return client


def test_list_models_parses_model_info_and_litellm_params() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/model/info"
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "model_name": "model-a",
                        "model_info": {"id": "model-a"},
                        "litellm_params": {"api_base": "http://127.0.0.1:8888/v1"},
                    }
                ]
            },
        )

    with _client(httpx.MockTransport(handle)) as client:
        models = client.list_models()

    assert len(models) == 1
    assert models[0].model_name == "model-a"
    assert models[0].model_id == "model-a"
    assert models[0].api_base == "http://127.0.0.1:8888/v1"


def test_add_model_posts_expected_shape() -> None:
    captured: dict[str, bytes] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json={})

    with _client(httpx.MockTransport(handle)) as client:
        client.add_model("model-a", "http://127.0.0.1:8888/v1")

    body = json.loads(captured["body"])
    assert body["model_name"] == "model-a"
    assert body["model_info"] == {"id": "model-a"}
    assert body["litellm_params"]["api_base"] == "http://127.0.0.1:8888/v1"
    assert body["litellm_params"]["model"] == "openai/model-a"


def test_list_keys_requests_full_objects() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/key/list"
        assert request.url.params["return_full_object"] == "true"
        return httpx.Response(
            200, json={"keys": [{"key_alias": "a", "spend": 0.0, "max_budget": 5.0}]}
        )

    with _client(httpx.MockTransport(handle)) as client:
        keys = client.list_keys()

    assert keys == [{"key_alias": "a", "spend": 0.0, "max_budget": 5.0}]


def test_delete_model_tolerates_404() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with _client(httpx.MockTransport(handle)) as client:
        client.delete_model("does-not-exist")  # should not raise


def test_delete_model_tolerates_litellms_400_not_found() -> None:
    """Verified against a live litellm-database:main-stable proxy: an
    unknown model id 400s with `"Model with id=... not found in db"`,
    not 404.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": "{'error': 'Model with id=x not found in db'}"}}
        )

    with _client(httpx.MockTransport(handle)) as client:
        client.delete_model("does-not-exist")  # should not raise


def test_delete_model_raises_on_other_400s() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "some other validation failure"})

    with _client(httpx.MockTransport(handle)) as client, pytest.raises(httpx.HTTPStatusError):
        client.delete_model("model-a")


def test_delete_model_raises_on_other_errors() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with _client(httpx.MockTransport(handle)) as client, pytest.raises(httpx.HTTPStatusError):
        client.delete_model("model-a")
