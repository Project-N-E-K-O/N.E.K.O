from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from plugin.core import host as host_module
from plugin.core.host import PluginHost
from plugin.core.state import state
from plugin.server.messaging.request_router import PluginRouter

pytestmark = pytest.mark.plugin_integration


@pytest.fixture
async def lifecycle_router(monkeypatch):
    monkeypatch.setattr(state, "_plugin_comm_queue", asyncio.Queue())
    monkeypatch.setattr(state, "_user_context_store", {})
    state.add_user_context_event("lifecycle_rpc", {"type": "user_message", "text": "hello"})
    router = PluginRouter()
    await router.start()
    try:
        yield router
    finally:
        await router.stop()


@pytest.fixture
async def lifecycle_host(lifecycle_router, monkeypatch, tmp_path):
    monkeypatch.setattr(host_module, "_refresh_child_storage_layout_env", lambda _: None)
    hosts = []

    async def start(phase):
        directory = tmp_path / "lifecycle_rpc"
        directory.mkdir(exist_ok=True)
        fixture = Path(__file__).resolve().parents[3] / "tests/fixtures/plugin_lifecycle_rpc_fixture.py"
        (directory / "__init__.py").write_text(fixture.read_text())
        entry = "plugins.lifecycle_rpc:LifecycleRpcPlugin"
        config = directory / "plugin.toml"
        config.write_text(
            f"[plugin]\nid='lifecycle_rpc'\nname='lifecycle_rpc'\nentry='{entry}'\n"
            "[plugin.store]\nenabled=true\n"
            "[plugin_state]\nbackend='file'\npersist_mode='manual'\n"
            f"[lifecycle_probe]\nphase='{phase}'\n"
        )
        host = PluginHost("lifecycle_rpc", entry, config, source_only=True)
        hosts.append(host)
        await host.start(startup_timeout=5, startup_failure="abort")
        return host

    try:
        yield start
    finally:
        for host in hosts:
            if host.process.is_alive():
                await host.shutdown(timeout=1)


@pytest.mark.parametrize("queue_timeout", [1.0, 0.0001])
async def test_config_change_can_query_host_memory(lifecycle_host, monkeypatch, queue_timeout):
    # Cover both inherited (fork) and re-imported (spawn) child settings.
    monkeypatch.setattr(host_module, "QUEUE_GET_TIMEOUT", queue_timeout)
    monkeypatch.setenv("NEKO_QUEUE_GET_TIMEOUT", str(queue_timeout))
    host = await lifecycle_host("config_change")
    result = await host.send_config_update({"example": "updated"}, timeout=2)
    assert result["config_applied"] is True
    snapshot = await host.trigger("snapshot", {}, timeout=2)
    assert snapshot["observations"] == {"config_change": 1}


async def test_freeze_can_query_host_memory(lifecycle_host):
    host = await lifecycle_host("freeze")
    result = await host.freeze(timeout=2)
    assert result["success"] is True
    restarted = await lifecycle_host("freeze")
    snapshot = await restarted.trigger("snapshot", {}, timeout=2)
    assert snapshot["observations"] == {"freeze": 1}


async def test_unfreeze_can_query_host_memory(lifecycle_host):
    host = await lifecycle_host("unfreeze")
    assert (await host.freeze(timeout=2))["success"] is True
    restarted = await lifecycle_host("unfreeze")
    snapshot = await restarted.trigger("snapshot", {}, timeout=2)
    assert snapshot["observations"] == {"unfreeze": 1}
    assert snapshot["restored_count"] == 1


async def test_shutdown_can_finish_host_query_before_communication_closes(lifecycle_host):
    host = await lifecycle_host("shutdown")
    await host.shutdown()
    restarted = await lifecycle_host("shutdown")
    snapshot = await restarted.trigger("snapshot", {}, timeout=2)
    assert snapshot["observations"] == {"shutdown": 1}


async def test_server_shutdown_keeps_rpc_available_for_plugin_cleanup(
    lifecycle_host, lifecycle_router, monkeypatch
):
    from plugin.server import lifecycle as server_module
    from plugin.server.application.plugins import lifecycle_service

    host = await lifecycle_host("shutdown")
    monkeypatch.setattr(state, "plugin_hosts", {"lifecycle_rpc": host})
    monkeypatch.setattr(state, "plugins", {})
    monkeypatch.setattr(state, "event_handlers", {})
    state.invalidate_snapshot_cache()
    monkeypatch.setattr(server_module, "plugin_router", lifecycle_router)
    monkeypatch.setattr(lifecycle_service, "_operations_shutting_down", False)
    await server_module.ServerLifecycleService().shutdown()

    await lifecycle_router.start()
    restarted = await lifecycle_host("shutdown")
    snapshot = await restarted.trigger("snapshot", {}, timeout=2)
    assert snapshot["observations"] == {"shutdown": 1}


async def test_blocked_shutdown_remains_bounded_and_next_process_can_start(lifecycle_host):
    host = await lifecycle_host("blocked_shutdown")
    await asyncio.wait_for(host.shutdown(timeout=0.1), timeout=2)
    assert not host.process.is_alive()
    restarted = await lifecycle_host("config_change")
    assert (await restarted.trigger("snapshot", {}, timeout=2))["observations"] == {}


async def test_commands_wait_for_config_change_while_host_replies_are_received(
    lifecycle_host, monkeypatch
):
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    get_context = state.get_user_context

    def delayed_context(**kwargs):
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(timeout=2):
            raise TimeoutError("Test memory source was not released")
        return get_context(**kwargs)

    monkeypatch.setattr(state, "get_user_context", delayed_context)
    host = await lifecycle_host("config_change")
    update = asyncio.create_task(host.send_config_update({"example": "updated"}, timeout=2))
    snapshot = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        snapshot = asyncio.create_task(host.trigger("snapshot", {}, timeout=2))
        await asyncio.sleep(0.02)
        assert not snapshot.done()
    finally:
        release.set()
        await asyncio.gather(
            update, *([snapshot] if snapshot is not None else []), return_exceptions=True
        )
    assert update.result()["config_applied"] is True
    assert snapshot.result()["observations"] == {"config_change": 1}
