"""Tests for the plugin.toml manifest schema."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from dgx_hub.plugins.env_validation import evaluate_rules
from dgx_hub.plugins.manifest import PluginManifest

REPO_ROOT = Path(__file__).resolve().parent.parent
SGLANG_MANIFEST = REPO_ROOT / "plugins" / "qwen3.8-27b-sglang" / "plugin.toml"


def test_all_builtin_plugin_manifests_parse() -> None:
    """Every shipped plugins/*/plugin.toml must satisfy the schema -- catches
    a broken manifest (this repo's own or one added via the plugin-from-script
    skill) at test time instead of only when someone runs `dgx-hub list`."""
    manifest_paths = sorted((REPO_ROOT / "plugins").glob("*/plugin.toml"))
    assert manifest_paths, "expected at least one built-in plugin manifest"
    for manifest_path in manifest_paths:
        raw = tomllib.loads(manifest_path.read_text())
        PluginManifest.from_toml_dict(raw)


def test_sglang_manifest_parses_and_validates() -> None:
    raw = tomllib.loads(SGLANG_MANIFEST.read_text())
    manifest = PluginManifest.from_toml_dict(raw)

    assert manifest.plugin.name == "qwen3.8-27b-sglang"
    assert [v.id for v in manifest.variant] == ["eagle", "dspark", "dflash"]
    assert manifest.default_variant_id() == "eagle"
    assert manifest.env["QUANT"].default == "nvfp4"
    assert len(manifest.env_validation) == 1
    assert "validation" not in manifest.env


def _minimal_docker_run_dict(**overrides: object) -> dict:
    base: dict = {
        "plugin": {"name": "x", "display_name": "X"},
        "source": {"repo_url": "https://example.com/x.git"},
        "docker": {
            "mode": "docker-run",
            "image": "example/x:latest",
            "container_name": "x",
            "ports": [{"container_port": 80}],
        },
        "health": {"url": "http://{backend_address}/"},
    }
    base.update(overrides)
    return base


def test_docker_run_requires_image_and_container_name() -> None:
    data = _minimal_docker_run_dict()
    data["docker"] = {"mode": "docker-run"}
    with pytest.raises(ValidationError):
        PluginManifest.from_toml_dict(data)


def test_compose_mode_requires_compose_fields() -> None:
    data = _minimal_docker_run_dict()
    data["docker"] = {"mode": "compose-static"}
    with pytest.raises(ValidationError):
        PluginManifest.from_toml_dict(data)


def test_compose_generated_requires_generate_command() -> None:
    data = _minimal_docker_run_dict()
    data["docker"] = {
        "mode": "compose-generated",
        "compose_file": "compose.yml",
        "compose_project": "p",
        "service_name": "svc",
    }
    with pytest.raises(ValidationError):
        PluginManifest.from_toml_dict(data)


def test_duplicate_variant_ids_rejected() -> None:
    data = _minimal_docker_run_dict()
    data["variant"] = [{"id": "a"}, {"id": "a"}]
    with pytest.raises(ValidationError):
        PluginManifest.from_toml_dict(data)


def test_multiple_default_variants_rejected() -> None:
    data = _minimal_docker_run_dict()
    data["variant"] = [{"id": "a", "default": True}, {"id": "b", "default": True}]
    with pytest.raises(ValidationError):
        PluginManifest.from_toml_dict(data)


def test_enum_default_must_be_in_choices() -> None:
    data = _minimal_docker_run_dict()
    data["env"] = {"QUANT": {"type": "enum", "choices": ["a", "b"], "default": "c"}}
    with pytest.raises(ValidationError):
        PluginManifest.from_toml_dict(data)


def test_patch_requires_exactly_one_kind() -> None:
    data = _minimal_docker_run_dict()
    data["patch"] = [{"file": "start.sh"}]
    with pytest.raises(ValidationError):
        PluginManifest.from_toml_dict(data)

    data["patch"] = [{"file": "start.sh", "find": "a", "replace": "b", "diff": "..."}]
    with pytest.raises(ValidationError):
        PluginManifest.from_toml_dict(data)


def test_host_mode_loopback_remap_without_override_var_rejected() -> None:
    data = _minimal_docker_run_dict()
    data["docker"] = {
        "mode": "docker-run",
        "image": "example/x:latest",
        "container_name": "x",
        "network_mode": "host",
        "ports": [{"container_port": 80, "publish_strategy": "loopback-remap"}],
    }
    with pytest.raises(ValidationError):
        PluginManifest.from_toml_dict(data)


def test_host_mode_loopback_remap_allowed_when_fallback_enabled() -> None:
    """[docker] is only a best-effort description when [fallback] governs the
    real launch (e.g. DeepSeek's dynamically-generated compose + entrypoint
    chaining) — the port-override requirement doesn't apply there."""
    data = _minimal_docker_run_dict()
    data["docker"] = {
        "mode": "docker-run",
        "image": "example/x:latest",
        "container_name": "x",
        "network_mode": "host",
        "ports": [{"container_port": 80, "publish_strategy": "loopback-remap"}],
    }
    data["fallback"] = {"enabled": True, "start_command": ["./start.sh"]}
    manifest = PluginManifest.from_toml_dict(data)
    assert manifest.fallback.enabled is True


def test_resolve_env_values_serializes_booleans_lowercase() -> None:
    data = _minimal_docker_run_dict()
    data["env"] = {"YARN": {"type": "boolean", "default": False}}
    manifest = PluginManifest.from_toml_dict(data)
    assert manifest.resolve_env_values() == {"YARN": "false"}
    assert manifest.resolve_env_values({"YARN": "true"}) == {"YARN": "true"}


def test_resolve_env_values_typed_rejects_enum_override_outside_choices() -> None:
    data = _minimal_docker_run_dict()
    data["env"] = {"QUANT": {"type": "enum", "choices": ["a", "b"], "default": "a"}}
    manifest = PluginManifest.from_toml_dict(data)
    with pytest.raises(ValueError, match="QUANT"):
        manifest.resolve_env_values_typed({"QUANT": "bogus"})


def test_resolve_env_values_typed_rejects_non_integer_override() -> None:
    data = _minimal_docker_run_dict()
    data["env"] = {"CONTEXT_LENGTH": {"type": "integer", "default": 1024}}
    manifest = PluginManifest.from_toml_dict(data)
    with pytest.raises(ValueError, match="CONTEXT_LENGTH"):
        manifest.resolve_env_values_typed({"CONTEXT_LENGTH": "not-a-number"})


def test_sglang_manifest_env_validation_rule_evaluates() -> None:
    raw = tomllib.loads(SGLANG_MANIFEST.read_text())
    manifest = PluginManifest.from_toml_dict(raw)

    violating = manifest.resolve_env_values_typed({"YARN": "true", "CONTEXT_LENGTH": "300000"})
    assert evaluate_rules(manifest, violating, variant_id="dspark") == [
        "YaRN is incompatible with DSpark/DFlash2 at context lengths above 262144"
    ]

    passing = manifest.resolve_env_values_typed({"YARN": "true", "CONTEXT_LENGTH": "300000"})
    assert evaluate_rules(manifest, passing, variant_id="eagle") == []
