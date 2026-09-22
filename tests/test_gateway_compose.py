"""Tests for gateway/compose.py — generated compose stack and secret persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from dgx_hub.gateway import compose


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compose, "user_config_dir", lambda: tmp_path)


def test_ensure_gateway_files_creates_all_three() -> None:
    paths = compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")

    assert paths.compose_file.is_file()
    assert paths.config_file.is_file()
    assert paths.env_file.is_file()


def test_secrets_persist_across_calls() -> None:
    first = compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")
    first_env = compose.read_env_file(first.env_file)

    second = compose.ensure_gateway_files(litellm_port=9999, host="127.0.0.1")
    second_env = compose.read_env_file(second.env_file)

    assert second_env["LITELLM_MASTER_KEY"] == first_env["LITELLM_MASTER_KEY"]
    assert second_env["POSTGRES_PASSWORD"] == first_env["POSTGRES_PASSWORD"]
    assert second_env["LITELLM_PORT"] == "9999"
    assert second_env["LITELLM_HOST"] == "127.0.0.1"


def test_master_key_is_sk_prefixed_and_file_is_private() -> None:
    paths = compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")
    env = compose.read_env_file(paths.env_file)

    assert env["LITELLM_MASTER_KEY"].startswith("sk-")
    assert (paths.env_file.stat().st_mode & 0o777) == 0o600


def test_config_file_not_overwritten_once_present() -> None:
    paths = compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")
    paths.config_file.write_text("model_list: [{model_name: custom}]\n")

    compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")

    assert "custom" in paths.config_file.read_text()


def test_compose_yaml_references_config_file_and_litellm_port() -> None:
    paths = compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")
    content = paths.compose_file.read_text()

    assert str(paths.config_file) in content
    assert "${LITELLM_PORT}" in content


def test_litellm_is_published_not_host_networked() -> None:
    """The whole point of this compose file: litellm must be reachable from
    the real host (a published port) rather than `network_mode: host`, which
    under rootless Docker shares RootlessKit's private namespace instead of
    the actual host -- see gateway/lifecycle.py's readiness check, which
    polls 127.0.0.1 and would hang forever against a host-mode container
    there.
    """
    paths = compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")
    lines = [line.strip() for line in paths.compose_file.read_text().splitlines()]

    assert "network_mode: host" not in lines
    assert '"${LITELLM_HOST}:${LITELLM_PORT}:${LITELLM_PORT}"' in "\n".join(lines)


def test_postgres_has_no_published_port() -> None:
    """Postgres is internal-only -- reachable from litellm by service name
    on dgx-hub-net, never exposed to the host."""
    paths = compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")
    content = paths.compose_file.read_text()

    assert "5432:5432" not in content
    assert "POSTGRES_PORT" not in content


def test_both_services_join_external_dgx_hub_net() -> None:
    paths = compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")
    content = paths.compose_file.read_text()

    assert "dgx-hub-net" in content
    assert "external: true" in content


def test_postgres_and_litellm_handles_share_compose_identity() -> None:
    paths = compose.ensure_gateway_files(litellm_port=8888, host="0.0.0.0")

    pg = compose.postgres_handle(paths)
    llm = compose.litellm_handle(paths)

    assert pg.compose_project == llm.compose_project == compose.COMPOSE_PROJECT
    assert pg.service_name == "postgres"
    assert llm.service_name == "litellm"
