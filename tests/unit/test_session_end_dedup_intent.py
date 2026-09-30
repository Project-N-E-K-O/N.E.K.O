"""Duplicate cleanup must still accept a user's cancellation of a waiting start."""

import asyncio
from types import SimpleNamespace

import pytest

from tests.unit.session_handoff_harness import drain_manager, make_full_manager


@pytest.mark.asyncio
async def test_user_end_during_server_cleanup_abandons_waiting_start(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    starting = asyncio.create_task(manager.start_session(manager.websocket, request_id="old"))
    entered, release = asyncio.Event(), asyncio.Event()
    ending = waiting = None

    async def close_asr():
        entered.set()
        await release.wait()

    try:
        client = await asyncio.wait_for(created.get(), 1)
        client.allow_connect.set()
        await starting
        manager._asr_runtime._asr_session = SimpleNamespace(close=close_asr)
        manager._asr_runtime._asr_provider = "dummy"
        manager._asr_runtime._asr_runtime_close_task = None
        ending = manager.request_end_session(by_server=True)
        await asyncio.wait_for(entered.wait(), 1)
        waiting = asyncio.create_task(manager.start_session(manager.websocket, request_id="abandoned"))
        await asyncio.sleep(0)
        assert not waiting.done()
        assert created.empty()
        # Resource teardown stays idempotent, but the user's end is accepted.
        assert manager.request_end_session(by_server=False) is ending
        release.set()
        await asyncio.wait_for(asyncio.shield(ending), 1)
        done, _ = await asyncio.wait({waiting}, timeout=0.3)
        assert waiting in done, "user end was swallowed and the queued start connected"
        await waiting
        assert created.empty()
        assert not any(m.get("request_id") == "abandoned" and m.get("type") == "session_started"
                       for m in manager.websocket.messages)
    finally:
        release.set()
        await drain_manager(manager, clients, starting, *(t for t in (ending, waiting) if t))
