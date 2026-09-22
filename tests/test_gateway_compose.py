"""Tests for gateway/compose.py — generated compose stack and secret persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from dgx_hub.gateway import compose


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compose, "user_config_dir", lambda: tmp_path)


def test_ensure_gateway_files_creates_all_three() -> None:
    paths = compose.ensure_gateway_files(litellm_port=8888, postgres_port=5442, host="0.0.0.0")

    assert paths.compose_file.is_file()
    assert paths.config_file.is_file()
    assert paths.env_file.is_file()


def test_secrets_persist_across_calls() -> None:
    first = compose.ensure_gateway_files(litellm_port=8888, postgres_port=5442, host="0.0.0.0")
    first_env = compose.read_env_file(first.env_file)

    second = compose.ensure_gateway_files(litellm_port=9999, postgres_port=5443, host="127.0.0.1")
    second_env = compose.read_env_file(second.env_file)

    assert second_env["LITELLM_MASTER_KEY"] == first_env["LITELLM_MASTER_KEY"]
    assert second_env["POSTGRES_PASSWORD"] == first_env["POSTGRES_PASSWORD"]
    assert second_env["LITELLM_PORT"] == "9999"
    assert second_env["POSTGRES_PORT"] == "5443"
    assert second_env["LITELLM_HOST"] == "127.0.0.1"


def test_master_key_is_sk_prefixed_and_file_is_private() -> None:
    paths = compose.ensure_gateway_files(litellm_port=8888, postgres_port=5442, host="0.0.0.0")
    env = compose.read_env_file(paths.env_file)

    assert env["LITELLM_MASTER_KEY"].startswith("sk-")
    assert (paths.env_file.stat().st_mode & 0o777) == 0o600


def test_config_file_not_overwritten_once_present() -> None:
    paths = compose.ensure_gateway_files(litellm_port=8888, postgres_port=5442, host="0.0.0.0")
    paths.config_file.write_text("model_list: [{model_name: custom}]\n")

    compose.ensure_gateway_files(litellm_port=8888, postgres_port=5442, host="0.0.0.0")

    assert "custom" in paths.config_file.read_text()


def test_compose_yaml_references_config_file_and_ports() -> None:
    paths = compose.ensure_gateway_files(litellm_port=8888, postgres_port=5442, host="0.0.0.0")
    content = paths.compose_file.read_text()

    assert str(paths.config_file) in content
    assert "network_mode: host" in content
    assert "${LITELLM_PORT}" in content
    assert "${POSTGRES_PORT}" in content


def test_postgres_and_litellm_handles_share_compose_identity() -> None:
    paths = compose.ensure_gateway_files(litellm_port=8888, postgres_port=5442, host="0.0.0.0")

    pg = compose.postgres_handle(paths)
    llm = compose.litellm_handle(paths)

    assert pg.compose_project == llm.compose_project == compose.COMPOSE_PROJECT
    assert pg.service_name == "postgres"
    assert llm.service_name == "litellm"
