"""A failed startup releases its operation while retired close remains owned."""

import asyncio

import pytest

from tests.unit.session_handoff_harness import make_full_manager, drain_manager


@pytest.mark.unit
@pytest.mark.asyncio
async def test_start_timeout_does_not_wait_for_retired_connection_close(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    socket = manager.websocket
    failed = asyncio.Event()
    original_send = socket.send_json

    async def send_json(payload):
        await original_send(payload)
        if payload.get("type") == "session_failed":
            failed.set()

    socket.send_json = send_json
    starting = asyncio.create_task(manager.start_session(
        socket, request_id="deadline", _deadline=asyncio.get_running_loop().time() + 0.3,
    ))
    try:
        client = await asyncio.wait_for(created.get(), 2)
        await manager.input_cache_lock.acquire()
        client.allow_close.clear()
        client.allow_connect.set()
        await asyncio.wait_for(failed.wait(), 2)
        assert not any(payload.get("type") == "session_started" for payload in socket.messages), (
            "startup must not acknowledge success before its input gate can open"
        )
        manager.input_cache_lock.release()
        await asyncio.wait_for(client.close_entered.wait(), 2)
        done, _ = await asyncio.wait({starting}, timeout=0.2)
        assert starting in done, "retired close must not extend the failed startup budget"
        assert manager._starting_session_count == 0
        record = manager._connection_record(client)
        assert record.retired and not record.closed
        assert not record.close_task.done()
    finally:
        if manager.input_cache_lock.locked():
            manager.input_cache_lock.release()
        await drain_manager(manager, clients, starting)
