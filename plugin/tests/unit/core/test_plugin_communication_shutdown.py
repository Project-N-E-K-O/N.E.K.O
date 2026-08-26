from __future__ import annotations

import asyncio
import threading
import copy

import pytest

from plugin.core.communication import STARTUP_RESULT_REQ_ID, PluginCommunicationResourceManager
from plugin.core.state import state


class _Transport:
    async def recv(self, timeout_ms=None):
        await asyncio.sleep(10)
        return None

    async def send_command(self, msg):
        return None


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_comm_manager_shutdown_tolerates_cross_loop_uplink_task() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )

    ready = threading.Event()
    holder: dict[str, object] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _spawn() -> None:
            manager._uplink_consumer_task = loop.create_task(asyncio.sleep(10))
            holder["loop"] = loop
            ready.set()

        loop.run_until_complete(_spawn())
        loop.run_until_complete(asyncio.sleep(0.2))
        loop.close()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    assert ready.wait(timeout=2.0)

    await manager.shutdown(timeout=0.1)
    thread.join(timeout=1.0)


@pytest.mark.asyncio
async def test_comm_manager_shutdown_purges_private_proactive_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.messaging import proactive_bridge

    purged: list[str] = []
    monkeypatch.setattr(
        proactive_bridge,
        "discard_private_payloads",
        lambda plugin_id: purged.append(plugin_id),
    )
    manager = PluginCommunicationResourceManager(
        plugin_id="stopped-plugin",
        transport=_Transport(),
        logger=_Logger(),
    )

    await manager.shutdown(timeout=0.1)

    assert purged == ["stopped-plugin"]


@pytest.mark.asyncio
async def test_run_on_owner_loop_closes_coro_when_cross_loop_schedule_fails() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )

    class _FakeLoop:
        def is_closed(self) -> bool:
            return False

    manager._owner_loop = _FakeLoop()  # type: ignore[assignment]

    async def _sample() -> None:
        await asyncio.sleep(0)

    coro = _sample()
    with pytest.raises(AttributeError):
        await manager._run_on_owner_loop(coro)
    assert coro.cr_frame is None


@pytest.mark.asyncio
async def test_run_on_owner_loop_falls_back_when_owner_loop_not_running() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )

    class _StoppedLoop:
        def is_closed(self) -> bool:
            return False

        def is_running(self) -> bool:
            return False

    manager._owner_loop = _StoppedLoop()  # type: ignore[assignment]

    result = await manager._run_on_owner_loop(asyncio.sleep(0, result="ok"))

    assert result == "ok"


@pytest.mark.asyncio
async def test_route_comm_overwrites_the_plugin_supplied_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _Transport()
    transport.uplink_token = "trusted-host-generation"
    manager = PluginCommunicationResourceManager(
        plugin_id="authenticated-plugin",
        transport=transport,
        logger=_Logger(),
    )
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    monkeypatch.setattr(state, "_plugin_comm_queue", queue)

    await manager._route_comm(
        {
            "type": "LIVE_FRAME_PERMISSION_SET",
            "from_plugin": "victim-plugin",
            "host_generation": "attacker-host-generation",
            "request_id": "request-1",
            "token": "attacker-generation",
            "enabled": True,
        }
    )

    routed = queue.get_nowait()
    assert routed["from_plugin"] == "authenticated-plugin"
    assert routed["host_generation"] == "trusted-host-generation"


@pytest.mark.asyncio
async def test_token_bearing_message_uses_private_bridge_and_redacts_shared_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.messaging import proactive_bridge

    manager = PluginCommunicationResourceManager(
        plugin_id="authenticated-plugin",
        transport=_Transport(),
        logger=_Logger(),
    )
    target_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    manager._message_target_queue = target_queue
    stored: list[dict[str, object]] = []
    private: list[dict[str, object]] = []
    monkeypatch.setattr(state, "append_message_record", lambda record: stored.append(copy.deepcopy(record)))
    monkeypatch.setattr(
        proactive_bridge,
        "enqueue_private_payload",
        lambda payload: private.append(copy.deepcopy(payload)) or True,
        raising=False,
    )

    await manager._route_message(
        {
            "type": "MESSAGE_PUSH",
            "message_id": "private-message",
            "plugin_id": "authenticated-plugin",
            "_proactive_bridge_suppressed": "forged-by-plugin",
            "parts": [{"type": "text", "text": "private live-frame cue"}],
            "metadata": {
                "live_frame_permission_token": "generation-secret",
                "public_hint": "keep-me",
            },
        }
    )

    assert private[0]["metadata"]["live_frame_permission_token"] == "generation-secret"
    assert "_proactive_bridge_suppressed" not in private[0]
    assert "generation-secret" not in repr(stored)
    assert stored[0]["_proactive_bridge_suppressed"] is True
    forwarded = target_queue.get_nowait()
    assert forwarded["metadata"] == {"public_hint": "keep-me"}
    assert forwarded["_proactive_bridge_suppressed"] is True


@pytest.mark.asyncio
async def test_wait_for_startup_rejects_ready_result_with_startup_error() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )
    manager._startup_result = {
        "req_id": STARTUP_RESULT_REQ_ID,
        "success": True,
        "data": {
            "status": "ready",
            "startup_error": "lifecycle.startup failed",
        },
        "error": None,
    }

    with pytest.raises(RuntimeError, match="lifecycle\\.startup failed"):
        await manager.wait_for_startup(timeout=0.1)


@pytest.mark.asyncio
async def test_wait_for_startup_allows_startup_error_when_requested() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )
    manager._startup_result = {
        "req_id": STARTUP_RESULT_REQ_ID,
        "success": False,
        "data": {
            "status": "failed",
            "startup_error": "lifecycle.startup failed",
        },
        "error": "lifecycle.startup failed",
    }

    result = await manager.wait_for_startup(timeout=0.1, allow_startup_error=True)

    assert result == {
        "status": "failed",
        "startup_error": "lifecycle.startup failed",
    }


@pytest.mark.asyncio
async def test_wait_for_startup_uses_result_dispatched_before_wait_call() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )

    await manager.prepare_startup_wait()
    manager._dispatch_result(
        {
            "req_id": STARTUP_RESULT_REQ_ID,
            "success": True,
            "data": {"status": "ready"},
            "error": None,
        }
    )

    result = await manager.wait_for_startup(timeout=0.01)

    assert result == {"status": "ready"}
    assert STARTUP_RESULT_REQ_ID not in manager._pending_futures


@pytest.mark.asyncio
async def test_entry_update_register_uses_outer_entry_id_for_meta() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )

    handlers_backup = dict(state.event_handlers)
    cache_backup = copy.deepcopy(state._snapshot_cache)
    try:
        with state.acquire_event_handlers_write_lock():
            state.event_handlers.clear()
        state.invalidate_snapshot_cache("handlers")

        await manager._handle_entry_update({
            "type": "ENTRY_UPDATE",
            "action": "register",
            "plugin_id": "demo",
            "entry_id": "outer_id",
            "meta": {
                "id": "inner_id",
                "name": "Dynamic",
            },
        })

        with state.acquire_event_handlers_read_lock():
            handler = state.event_handlers["demo.outer_id"]
            assert handler.meta.id == "outer_id"
            assert "demo.inner_id" not in state.event_handlers
    finally:
        with state.acquire_event_handlers_write_lock():
            state.event_handlers.clear()
            state.event_handlers.update(handlers_backup)
        with state._snapshot_cache_lock:
            state._snapshot_cache = cache_backup
