"""Tests for gateway/litellm_config.py — reading/setting litellm's config.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from dgx_hub.gateway import compose, litellm_config


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compose, "user_config_dir", lambda: tmp_path)


def test_set_value_requires_gateway_started() -> None:
    with pytest.raises(litellm_config.LiteLLMConfigError, match="gateway start"):
        litellm_config.set_value("general_settings.disable_env_credential_login", "true")


def test_set_and_get_bool_value() -> None:
    compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")

    coerced = litellm_config.set_value("general_settings.disable_env_credential_login", "true")

    assert coerced is True
    assert litellm_config.get_value("general_settings.disable_env_credential_login") is True


def test_set_creates_missing_intermediate_sections() -> None:
    compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")

    litellm_config.set_value("litellm_settings.some_new_flag", "true")

    assert litellm_config.get_value("litellm_settings.some_new_flag") is True


def test_get_missing_key_returns_none() -> None:
    compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")

    assert litellm_config.get_value("general_settings.nope") is None
    assert litellm_config.get_value("nonexistent_section.nope") is None


def test_set_value_type_coercion() -> None:
    compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")

    assert litellm_config.set_value("general_settings.a", "true") is True
    assert litellm_config.set_value("general_settings.b", "false") is False
    assert litellm_config.set_value("general_settings.c", "42") == 42
    assert litellm_config.set_value("general_settings.d", "3.5") == 3.5
    assert litellm_config.set_value("general_settings.e", "hello") == "hello"


def test_existing_keys_survive_an_unrelated_set() -> None:
    """Round-trips through the file rather than overwriting it wholesale --
    master_key/database_url (written by ensure_gateway_files) must survive."""
    paths = compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")
    original = paths.config_file.read_text()
    assert "master_key" in original

    litellm_config.set_value("general_settings.disable_env_credential_login", "true")

    updated = paths.config_file.read_text()
    assert "master_key: os.environ/LITELLM_MASTER_KEY" in updated
    assert "database_url: os.environ/DATABASE_URL" in updated


def test_set_preserves_hand_added_comments() -> None:
    paths = compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")
    annotated = paths.config_file.read_text().replace(
        "litellm_settings:\n", "# a hand-added note\nlitellm_settings:\n"
    )
    paths.config_file.write_text(annotated)

    litellm_config.set_value("general_settings.disable_env_credential_login", "true")

    assert "# a hand-added note" in paths.config_file.read_text()
