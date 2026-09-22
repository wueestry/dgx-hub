"""Tests for gateway/reconcile.py's diff_routes — pure, no LiteLLM/network needed."""

from __future__ import annotations

from dgx_hub.gateway.litellm_admin import LiteLLMModel
from dgx_hub.gateway.reconcile import diff_routes


def test_new_model_is_added() -> None:
    result = diff_routes({"model-a": "127.0.0.1:8888"}, [])
    assert result.added == ["model-a"]
    assert result.updated == []
    assert result.removed == []
    assert result.changed is True


def test_unchanged_model_is_left_alone() -> None:
    live = [LiteLLMModel(model_name="model-a", model_id="model-a", api_base="http://127.0.0.1:8888/v1")]
    result = diff_routes({"model-a": "127.0.0.1:8888"}, live)
    assert result == diff_routes({"model-a": "127.0.0.1:8888"}, live)
    assert not result.added
    assert not result.updated
    assert not result.removed
    assert result.changed is False


def test_address_change_is_an_update() -> None:
    live = [LiteLLMModel(model_name="model-a", model_id="model-a", api_base="http://127.0.0.1:8888/v1")]
    result = diff_routes({"model-a": "127.0.0.1:9999"}, live)
    assert result.updated == ["model-a"]
    assert not result.added
    assert not result.removed


def test_dead_model_is_removed() -> None:
    live = [LiteLLMModel(model_name="model-a", model_id="model-a", api_base="http://127.0.0.1:8888/v1")]
    result = diff_routes({}, live)
    assert result.removed == ["model-a"]
    assert not result.added
    assert not result.updated


def test_mixed_diff() -> None:
    live = [
        LiteLLMModel(model_name="stale", model_id="stale", api_base="http://127.0.0.1:1111/v1"),
        LiteLLMModel(model_name="moved", model_id="moved", api_base="http://127.0.0.1:2222/v1"),
        LiteLLMModel(model_name="same", model_id="same", api_base="http://127.0.0.1:3333/v1"),
    ]
    ground_truth = {
        "moved": "127.0.0.1:4444",
        "same": "127.0.0.1:3333",
        "new": "127.0.0.1:5555",
    }
    result = diff_routes(ground_truth, live)
    assert result.added == ["new"]
    assert result.updated == ["moved"]
    assert result.removed == ["stale"]
