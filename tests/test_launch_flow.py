from types import SimpleNamespace

import pytest
import typer

from dgx_hub import docker_adapter
from dgx_hub.commands import launch_flow as flow
from dgx_hub.plugins.base import ContainerHandle, HealthResult, RuntimeKind
from dgx_hub.plugins.loader import discover_plugins
from dgx_hub.process import state


@pytest.fixture
def launch_env(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "state_file", lambda: tmp_path / "state.json")
    plugins = discover_plugins().plugins
    selected = list(plugins.values())[:2]
    for index, loaded in enumerate(selected):
        loaded.plugin.manifest.docker.container_name = f"test-{index}"
        monkeypatch.setattr(loaded.plugin, "provision", lambda ctx: None)
        monkeypatch.setattr(
            loaded.plugin,
            "start",
            lambda ctx: ContainerHandle(
                kind=RuntimeKind.DOCKER_RUN, container_name="test", backend_address="localhost:1"
            ),
        )
        monkeypatch.setattr(loaded.plugin, "health_check", lambda ctx, handle: HealthResult(True))
    names = [p.plugin.metadata.name for p in selected]
    monkeypatch.setattr(
        flow,
        "discover_plugins",
        lambda: SimpleNamespace(plugins=dict(zip(names, selected, strict=True))),
    )
    monkeypatch.setattr(
        flow.port_registry, "allocate_port", lambda claimed, preferred: 9000 + len(claimed)
    )
    monkeypatch.setattr(
        docker_adapter, "status", lambda handle: docker_adapter.DockerStatus(True, True)
    )
    monkeypatch.setattr(flow, "_discover_served_model_ids", lambda *args, **kwargs: ["model"])

    def dashboard(supervisors, **kwargs):
        for supervisor in supervisors.values():
            supervisor._thread.join(timeout=2)

    monkeypatch.setattr(flow, "run_dashboard", dashboard)
    monkeypatch.setattr("dgx_hub.process.supervisor.auto_sync.try_reconcile_quietly", lambda: None)
    return names, selected


def test_later_invalid_variant_has_no_side_effects(launch_env, monkeypatch):
    names, _ = launch_env
    monkeypatch.setattr(flow, "_run_preflight", lambda *args: pytest.fail("premature preflight"))
    with pytest.raises(typer.Exit):
        flow.launch_and_wait(names, {names[1]: "invalid"}, {}, replace=True, log_level=None)
    assert state.load_all() == {}


def test_duplicate_names_have_no_side_effects(launch_env):
    names, _ = launch_env
    with pytest.raises(typer.Exit):
        flow.launch_and_wait([names[0], names[0]], {}, {}, force=True, log_level=None)
    assert state.load_all() == {}


def test_mixed_success_returns_failure_and_persists(launch_env, monkeypatch):
    names, selected = launch_env

    def fail(ctx):
        raise RuntimeError("startup failed")

    monkeypatch.setattr(selected[1].plugin, "start", fail)
    with pytest.raises(typer.Exit) as exc:
        flow.launch_and_wait(names, {}, {}, force=True, log_level=None)
    assert exc.value.exit_code == 1
    assert state.get(names[0]).state == "serving"
    assert state.get(names[1]).state == "failed"


def test_gateway_sees_persisted_serving_state(launch_env, monkeypatch):
    names, _ = launch_env
    seen = []
    monkeypatch.setattr(
        "dgx_hub.process.supervisor.auto_sync.try_reconcile_quietly",
        lambda: seen.append(state.get(names[0])),
    )
    flow.launch_and_wait(names[:1], {}, {}, force=True, log_level=None)
    assert seen[0].state == "serving"
    assert seen[0].served_model_ids == ["model"]


def test_interrupt_returns_130_and_retains_handle(launch_env, monkeypatch):
    names, _ = launch_env

    def interrupt(supervisors, **kwargs):
        for supervisor in supervisors.values():
            supervisor._thread.join(timeout=2)
        raise KeyboardInterrupt

    monkeypatch.setattr(flow, "run_dashboard", interrupt)
    with pytest.raises(typer.Exit) as exc:
        flow.launch_and_wait(names[:1], {}, {}, force=True, log_level=None)
    assert exc.value.exit_code == 130
    assert state.get(names[0]).container_name == "test"


def test_duplicate_container_names_reject_batch(launch_env):
    names, selected = launch_env
    selected[1].plugin.manifest.docker.container_name = selected[
        0
    ].plugin.manifest.docker.container_name
    with pytest.raises(typer.Exit):
        flow.launch_and_wait(names, {}, {}, force=True, log_level=None)
    assert state.load_all() == {}


def test_batch_gpu_exclusivity(launch_env):
    from dataclasses import replace

    from dgx_hub.plugins.base import ResourceRequirements

    names, selected = launch_env
    for loaded in selected:
        loaded.plugin.metadata = replace(
            loaded.plugin.metadata,
            resources=ResourceRequirements(gpu_required=True, exclusive_gpu=True),
        )
    with pytest.raises(typer.Exit):
        flow.launch_and_wait(names, {}, {}, force=True, log_level=None)
    assert state.load_all() == {}


def test_aggregate_memory_preflight(launch_env, monkeypatch):
    from dataclasses import replace

    from dgx_hub.plugins.base import ResourceRequirements

    names, selected = launch_env
    for loaded in selected:
        loaded.plugin.metadata = replace(
            loaded.plugin.metadata, resources=ResourceRequirements(min_free_memory_gib=10)
        )
    monkeypatch.setattr(flow.preflight, "mem_available_gib", lambda: 15)
    with pytest.raises(typer.Exit):
        flow.launch_and_wait(names, {}, {}, log_level=None)
    assert state.load_all() == {}


def test_interrupt_during_provisioning(launch_env, monkeypatch):
    import threading

    names, selected = launch_env
    entered = threading.Event()

    def provision(ctx):
        entered.set()
        assert ctx.cancel_event.wait(2)

    monkeypatch.setattr(selected[0].plugin, "provision", provision)

    def interrupt(supervisors, **kwargs):
        assert entered.wait(2)
        raise KeyboardInterrupt

    monkeypatch.setattr(flow, "run_dashboard", interrupt)
    with pytest.raises(typer.Exit) as exc:
        flow.launch_and_wait(names[:1], {}, {}, force=True, log_level=None)
    assert exc.value.exit_code == 130
    assert state.get(names[0]).state == "failed"
    assert not state.get(names[0]).backend_address
