"""Tests for ManifestPlugin's [fallback] dispatch — the last-resort escape
hatch two of the real plugin manifests (DeepSeek, Flash-Next) depend on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dgx_hub.plugins.base import RunContext
from dgx_hub.plugins.manifest import PluginManifest
from dgx_hub.plugins.manifest_plugin import ManifestPlugin, ManifestPluginError
from dgx_hub.process.stream import StreamResult


def _fallback_manifest_dict(**docker_overrides: object) -> dict:
    docker: dict = {
        "mode": "docker-run",
        "image": "example/x:latest",
        "container_name": "x",
        "network_mode": "host",
        "ports": [
            {
                "container_port": 8888,
                "publish_strategy": "loopback-remap",
                "port_override_env_var": "SERVING_PORT",
            }
        ],
    }
    docker.update(docker_overrides)
    return {
        "plugin": {"name": "x", "display_name": "X"},
        "source": {"repo_url": "https://example.com/x.git"},
        "docker": docker,
        "health": {"url": "http://{backend_address}/"},
        "fallback": {"enabled": True, "start_command": ["./start.sh", "{variant}"]},
    }


def make_plugin(tmp_path: Path, **docker_overrides: object) -> ManifestPlugin:
    manifest = PluginManifest.from_toml_dict(_fallback_manifest_dict(**docker_overrides))
    return ManifestPlugin(manifest=manifest, repo_dir=tmp_path)


def test_fallback_start_substitutes_variant_and_injects_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = make_plugin(tmp_path)
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return StreamResult(returncode=0, tail=[])

    monkeypatch.setattr("dgx_hub.plugins.manifest_plugin.run_streaming", fake_run)

    ctx = RunContext(
        repo_dir=tmp_path,
        state_dir=tmp_path,
        variant_id="context-1m",
        env_values={},
        allocated_port=19999,
    )
    handle = plugin.start(ctx)

    assert captured["command"] == ["./start.sh", "context-1m"]
    assert captured["cwd"] == tmp_path
    assert captured["env"]["SERVING_PORT"] == "19999"
    assert handle.container_name == "x"
    assert handle.backend_address == "127.0.0.1:19999"
    # network_mode = host: nothing to join, so no gateway_address (mirrors
    # registry.current_routes(), which excludes routes without one).
    assert handle.gateway_address == ""


def test_fallback_start_on_bridge_network_sets_gateway_address_and_joins_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = make_plugin(tmp_path, network_mode="bridge")
    ensured: list[bool] = []
    monkeypatch.setattr(
        "dgx_hub.plugins.manifest_plugin.docker_adapter.ensure_network",
        lambda: ensured.append(True),
    )
    monkeypatch.setattr(
        "dgx_hub.plugins.manifest_plugin.run_streaming",
        lambda *a, **k: StreamResult(returncode=0, tail=[]),
    )

    ctx = RunContext(repo_dir=tmp_path, state_dir=tmp_path, variant_id=None, allocated_port=19999)
    handle = plugin.start(ctx)

    assert ensured == [True]
    assert handle.gateway_address == "x:19999"


def test_fallback_start_raises_on_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = make_plugin(tmp_path)

    def fake_run(*args, **kwargs):
        return StreamResult(returncode=1, tail=["boom"])

    monkeypatch.setattr("dgx_hub.plugins.manifest_plugin.run_streaming", fake_run)
    ctx = RunContext(repo_dir=tmp_path, state_dir=tmp_path, variant_id=None, allocated_port=1)
    with pytest.raises(ManifestPluginError, match="boom"):
        plugin.start(ctx)


def test_provision_clones_repo_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    manifest = PluginManifest.from_toml_dict(_fallback_manifest_dict())
    plugin = ManifestPlugin(manifest=manifest, repo_dir=repo_dir)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["git", "clone"]:
            Path(command[-1]).mkdir(parents=True)
        return StreamResult(returncode=0, tail=[])

    monkeypatch.setattr("dgx_hub.plugins.manifest_plugin.run_streaming", fake_run)

    ctx = RunContext(repo_dir=repo_dir, state_dir=tmp_path, variant_id=None)
    plugin.provision(ctx)

    assert calls[0] == ["git", "clone", "--depth", "1", "https://example.com/x.git", str(repo_dir)]
    assert repo_dir.exists()


def test_provision_skips_clone_when_repo_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = make_plugin(tmp_path)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return StreamResult(returncode=0, tail=[])

    monkeypatch.setattr("dgx_hub.plugins.manifest_plugin.run_streaming", fake_run)

    ctx = RunContext(repo_dir=tmp_path, state_dir=tmp_path, variant_id=None)
    plugin.provision(ctx)

    assert calls == []


def test_provision_does_not_clone_when_repo_not_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _fallback_manifest_dict()
    data["fallback"] = {"enabled": False}
    manifest = PluginManifest.from_toml_dict(data)
    repo_dir = tmp_path / "unused-repo"
    plugin = ManifestPlugin(manifest=manifest, repo_dir=repo_dir)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "dgx_hub.plugins.manifest_plugin.run_streaming",
        lambda command, **kwargs: calls.append(command) or StreamResult(returncode=0, tail=[]),
    )

    ctx = RunContext(repo_dir=repo_dir, state_dir=tmp_path, variant_id=None)
    plugin.provision(ctx)

    assert calls == []
    assert not repo_dir.exists()


def test_provision_raises_when_clone_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_dir = tmp_path / "repo"
    manifest = PluginManifest.from_toml_dict(_fallback_manifest_dict())
    plugin = ManifestPlugin(manifest=manifest, repo_dir=repo_dir)

    monkeypatch.setattr(
        "dgx_hub.plugins.manifest_plugin.run_streaming",
        lambda *a, **k: StreamResult(returncode=128, tail=["not found"]),
    )

    ctx = RunContext(repo_dir=repo_dir, state_dir=tmp_path, variant_id=None)
    with pytest.raises(ManifestPluginError, match="not found"):
        plugin.provision(ctx)


def test_fallback_requires_start_command(tmp_path: Path) -> None:
    data = _fallback_manifest_dict()
    data["fallback"] = {"enabled": True, "start_command": []}
    manifest = PluginManifest.from_toml_dict(data)
    plugin = ManifestPlugin(manifest=manifest, repo_dir=tmp_path)
    ctx = RunContext(repo_dir=tmp_path, state_dir=tmp_path, variant_id=None)
    with pytest.raises(ManifestPluginError, match="start_command"):
        plugin.start(ctx)


def test_fallback_failure_message_uses_error_lines_from_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = make_plugin(tmp_path)
    monkeypatch.setattr(
        "dgx_hub.plugins.manifest_plugin.run_streaming",
        lambda *a, **k: StreamResult(
            returncode=1,
            tail=["[INFO]  === Step 1 ===", "[ERR ]  ABLIT must be 0 or 1 (got: 'false')"],
        ),
    )
    ctx = RunContext(repo_dir=tmp_path, state_dir=tmp_path, variant_id=None, allocated_port=1)
    with pytest.raises(ManifestPluginError, match=r"exit 1\): \[ERR \]  ABLIT must be 0 or 1"):
        plugin.start(ctx)


def test_subprocess_output_is_forwarded_to_ctx_on_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = make_plugin(tmp_path)
    seen: list[str] = []

    def fake_run(command, **kwargs):
        kwargs["on_line"]("Loading weights")
        return StreamResult(returncode=0)

    monkeypatch.setattr("dgx_hub.plugins.manifest_plugin.run_streaming", fake_run)
    ctx = RunContext(repo_dir=tmp_path, state_dir=tmp_path, variant_id=None, on_output=seen.append)
    plugin.start(ctx)
    assert seen == ["Loading weights"]


def test_provision_applies_patches_before_the_provision_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "start.sh").write_text("if [[ ! -f x ]]; then\n")
    data = _fallback_manifest_dict()
    data["provision"] = {"command": ["./download.sh"]}
    data["patch"] = [{"file": "start.sh", "find": "[[ ! -f x ]]", "replace": "true"}]
    plugin = ManifestPlugin(manifest=PluginManifest.from_toml_dict(data), repo_dir=tmp_path)
    seen_content: list[str] = []

    def fake_run(command, **kwargs):
        seen_content.append((tmp_path / "start.sh").read_text())
        return StreamResult(returncode=0)

    monkeypatch.setattr("dgx_hub.plugins.manifest_plugin.run_streaming", fake_run)
    plugin.provision(RunContext(repo_dir=tmp_path, state_dir=tmp_path, variant_id=None))
    assert seen_content == ["if true; then\n"]
