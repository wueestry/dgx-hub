"""Exercise the TensorFold fallback wrapper without downloading or serving weights."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

from dgx_hub.plugins.base import RunContext
from dgx_hub.plugins.manifest import PluginManifest
from dgx_hub.plugins.manifest_plugin import ManifestPlugin

PLUGIN_NAMES = ["qwen3.8-flash-next-tensorfold", "qwen3.8-27b-tensorfold"]


@pytest.fixture(params=PLUGIN_NAMES)
def manifest(request: pytest.FixtureRequest) -> PluginManifest:
    path = Path(__file__).resolve().parent.parent / "plugins" / request.param / "plugin.toml"
    return PluginManifest.from_toml_dict(tomllib.loads(path.read_text()))


@pytest.mark.parametrize("context", ["0", "131072"])
def test_start_wrapper_preserves_arguments_and_runtime_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: PluginManifest, context: str
) -> None:
    plugin = ManifestPlugin(manifest, tmp_path)
    script = tmp_path / "start.sh"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path('captured.json').write_text(json.dumps({\n"
        "    'args': sys.argv[1:],\n"
        "    'context': os.environ.get('CONTEXT'),\n"
        "    'yarn': os.environ.get('YARN_FACTOR'),\n"
        "    'env': {k: os.environ[k] for k in\n"
        "            ('PORT', 'CONTAINER_NAME', 'HOST', 'FOREGROUND', 'PARALLEL')}\n"
        "}))\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("CONTAINER_NAME", "unrelated-container")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("FOREGROUND", "1")
    networks: list[bool] = []
    monkeypatch.setattr(
        "dgx_hub.plugins.manifest_plugin.docker_adapter.ensure_network",
        lambda: networks.append(True),
    )
    ctx = RunContext(
        repo_dir=tmp_path,
        state_dir=tmp_path,
        variant_id="default",
        allocated_port=19999,
        env_values=manifest.resolve_env_values(
            {
                "PARALLEL": "4",
                "CONTEXT": context,
                "YARN_FACTOR": "4",
                "EXTRA_ARGS": '--name "model with spaces" --literal "$(touch injected)"',
            }
        ),
    )

    handle = plugin.start(ctx)

    captured = json.loads((tmp_path / "captured.json").read_text())
    assert captured["args"] == ["--name", "model with spaces", "--literal", "$(touch injected)"]
    assert captured["env"] == {
        "PORT": "19999",
        "CONTAINER_NAME": manifest.docker.container_name,
        "HOST": "0.0.0.0",
        "FOREGROUND": "0",
        "PARALLEL": "4",
    }
    assert networks == [True]
    assert handle.backend_address == "127.0.0.1:19999"
    assert handle.gateway_address == f"{manifest.docker.container_name}:19999"
    if manifest.plugin.name == "qwen3.8-27b-tensorfold":
        assert captured["context"] == (None if context == "0" else context)
        assert captured["yarn"] == "4"
    assert not (tmp_path / "injected").exists()


def test_stop_wrapper_uses_managed_container_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: PluginManifest
) -> None:
    plugin = ManifestPlugin(manifest, tmp_path)
    script = tmp_path / "stop.sh"
    script.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "from pathlib import Path\n"
        "Path('stopped.txt').write_text(os.environ['CONTAINER_NAME'])\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("CONTAINER_NAME", "unrelated-container")

    assert plugin.run_fallback_stop()
    assert (tmp_path / "stopped.txt").read_text() == manifest.docker.container_name
