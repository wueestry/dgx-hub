"""Tests for plugins/env_validation.py's simpleeval-backed rule evaluation."""

from __future__ import annotations

import pytest

from dgx_hub.plugins.env_validation import evaluate_rules
from dgx_hub.plugins.manifest import PluginManifest


def _manifest(rules: list[dict]) -> PluginManifest:
    return PluginManifest.from_toml_dict(
        {
            "plugin": {"name": "x", "display_name": "X"},
            "source": {"repo_url": "https://example.com/x.git"},
            "docker": {
                "mode": "docker-run",
                "image": "example/x:latest",
                "container_name": "x",
                "ports": [{"container_port": 80}],
            },
            "health": {"url": "http://{backend_address}/"},
            "env": {"validation": rules},
        }
    )


def test_passing_rule_reports_no_violations() -> None:
    manifest = _manifest([{"rule": "a > 5", "message": "a must be > 5"}])
    assert evaluate_rules(manifest, {"a": 10}, variant_id=None) == []


def test_failing_rule_reports_its_message() -> None:
    manifest = _manifest([{"rule": "a > 5", "message": "a must be > 5"}])
    assert evaluate_rules(manifest, {"a": 1}, variant_id=None) == ["a must be > 5"]


def test_rule_can_reference_variant() -> None:
    manifest = _manifest(
        [{"rule": "not (a and variant == 'bad')", "message": "a incompatible with bad variant"}]
    )
    assert evaluate_rules(manifest, {"a": True}, variant_id="bad") == [
        "a incompatible with bad variant"
    ]
    assert evaluate_rules(manifest, {"a": True}, variant_id="good") == []


def test_rule_referencing_undeclared_name_raises_clear_error() -> None:
    manifest = _manifest([{"rule": "undeclared_var > 5", "message": "nope"}])
    with pytest.raises(ValueError, match="undeclared_var"):
        evaluate_rules(manifest, {"a": 1}, variant_id=None)


def test_malformed_rule_raises_clear_error() -> None:
    manifest = _manifest([{"rule": "a > > 5", "message": "nope"}])
    with pytest.raises(ValueError, match="is invalid"):
        evaluate_rules(manifest, {"a": 1}, variant_id=None)
