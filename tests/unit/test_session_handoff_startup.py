import asyncio

import pytest

from tests.unit.session_handoff_harness import make_full_manager, drain_manager


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_start_cannot_install_or_ack_after_connect_returns(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    starting = asyncio.create_task(manager.start_session(manager.websocket, input_mode="audio", request_id="old"))
    try:
        old = await asyncio.wait_for(created.get(), 2.0)
        await asyncio.wait_for(old.connect_entered.wait(), 2.0)
        await manager.end_session(by_server=False)
        old.allow_connect.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(starting, 2.0)
        assert manager.is_active is False
        assert manager.session is None
        assert not any(message.get("type") == "session_started" for message in manager.websocket.messages)
        assert old.closed.is_set()
    finally:
        await drain_manager(manager, clients, starting)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_healthy_native_audio_start_activates_and_acks(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    starting = asyncio.create_task(manager.start_session(manager.websocket, input_mode="audio", request_id="healthy"))
    try:
        client = await asyncio.wait_for(created.get(), 2.0)
        client.allow_connect.set()
        await asyncio.wait_for(starting, 2.0)
        assert manager.session is client
        assert manager.is_active
        assert manager.session_ready
        assert any(message.get("type") == "session_started" for message in manager.websocket.messages)
    finally:
        await drain_manager(manager, clients, starting)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_late_failed_start_cannot_release_successor_guard_or_clear_socket(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    socket = manager.websocket
    first = asyncio.create_task(manager.start_session(socket, request_id="old"))
    second = None
    try:
        old = await asyncio.wait_for(created.get(), 2.0)
        await old.connect_entered.wait()
        await manager.end_session(by_server=False)
        second = asyncio.create_task(manager.start_session(socket, request_id="new"))
        successor = await asyncio.wait_for(created.get(), 2.0)
        await successor.connect_entered.wait()
        old.raise_on_connect = RuntimeError("old provider failed late")
        old.allow_connect.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, 2.0)
        assert manager._starting_session_count == 1
        assert manager.websocket is socket
        assert manager.session_start_failure_count == 0
        assert not successor.closed.is_set()
        successor.allow_connect.set()
        await asyncio.wait_for(second, 2.0)
        assert manager.session is successor
        assert manager.is_active
    finally:
        await drain_manager(manager, clients, first, *([second] if second else []))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fast_restart_during_slow_close_keeps_new_session_input(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    socket = manager.websocket
    first = asyncio.create_task(manager.start_session(socket, request_id="old"))
    ending = second = None
    try:
        old = await asyncio.wait_for(created.get(), 2.0)
        old.allow_connect.set()
        await asyncio.wait_for(first, 2.0)
        old.allow_close.clear()
        ending = asyncio.create_task(manager.end_session(by_server=True))
        await asyncio.wait_for(old.close_entered.wait(), 2.0)
        second = asyncio.create_task(manager.start_session(socket, request_id="new"))
        successor = await asyncio.wait_for(created.get(), 2.0)
        successor.allow_connect.set()
        await asyncio.wait_for(second, 2.0)
        assert manager.session is successor
        assert manager.session_ready
        manager.pending_input_data.append({"input_type": "text", "data": "new input"})
        old.allow_close.set()
        await asyncio.wait_for(ending, 2.0)
        assert manager.session is successor
        assert manager.session_ready
        assert manager.pending_input_data == [{"input_type": "text", "data": "new input"}]
        assert not successor.closed.is_set()
    finally:
        await drain_manager(manager, clients, first, *[task for task in (ending, second) if task])


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("callback_name", ["on_text_delta", "on_output_transcript"])
async def test_retired_output_callbacks_cannot_cross_the_memory_boundary(monkeypatch, callback_name):
    manager, created, clients = await make_full_manager(monkeypatch)
    socket = manager.websocket
    first = asyncio.create_task(manager.start_session(socket, request_id="old"))
    second = None
    try:
        old = await asyncio.wait_for(created.get(), 2.0)
        old.allow_connect.set()
        await asyncio.wait_for(first, 2.0)
        callback = getattr(old, callback_name)
        await callback("old valid", False)
        await manager.end_session(by_server=True)
        second = asyncio.create_task(manager.start_session(socket, request_id="new"))
        successor = await asyncio.wait_for(created.get(), 2.0)
        successor.allow_connect.set()
        await asyncio.wait_for(second, 2.0)
        await callback("old stale", False)
        await getattr(successor, callback_name)("new valid", False)
        memory = [item["data"].get("text") if item["type"] == "json" else item["data"]
                  for item in list(manager.sync_message_queue.queue)
                  if item["type"] == "system" or
                  (item["type"] == "json" and item["data"].get("type") == "gemini_response")]
        assert memory == ["old valid", "session end", "new valid"]
        assert not any(message.get("text") == "old stale" for message in socket.messages)
    finally:
        await drain_manager(manager, clients, first, *([second] if second else []))


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", ["preparing_send", "provider_connect", "started_send", "input_lock"])
async def test_start_cancellation_at_external_await_retires_only_its_resources(monkeypatch, cancel_at):
    manager, created, clients = await make_full_manager(monkeypatch)
    socket = manager.websocket
    reached_send = asyncio.Event()
    release_send = asyncio.Event()
    original_send = socket.send_json
    desired_type = "session_preparing" if cancel_at == "preparing_send" else "session_started"

    async def send_json(message):
        if message.get("type") == desired_type:
            reached_send.set()
            if cancel_at in {"preparing_send", "started_send"}:
                await release_send.wait()
        await original_send(message)

    socket.send_json = send_json
    starting = asyncio.create_task(manager.start_session(socket, request_id="cancel"))
    lock_held = False
    try:
        if cancel_at == "preparing_send":
            await asyncio.wait_for(reached_send.wait(), 2.0)
        else:
            client = await asyncio.wait_for(created.get(), 2.0)
            await client.connect_entered.wait()
            if cancel_at == "input_lock":
                await manager.input_cache_lock.acquire()
                lock_held = True
            if cancel_at != "provider_connect":
                client.allow_connect.set()
                if cancel_at == "input_lock":
                    await asyncio.wait_for(client.handler_entered.wait(), 2.0)
                    assert not reached_send.is_set(), "success must wait for the input gate"
                else:
                    await asyncio.wait_for(reached_send.wait(), 2.0)
        starting.cancel()
        if lock_held:
            manager.input_cache_lock.release()
            lock_held = False
        release_send.set()
        with pytest.raises(asyncio.CancelledError):
            await starting
        assert manager._starting_session_count == 0
        if manager._session_retirements:
            await asyncio.wait_for(manager._session_retirements[-1].handoff_safe.wait(), 2.0)
        assert manager.is_active is False
        assert manager.session_ready is False
        assert manager.websocket is socket
        for client in clients:
            await asyncio.wait_for(client.closed.wait(), 2.0)
    finally:
        release_send.set()
        if lock_held:
            manager.input_cache_lock.release()
        await drain_manager(manager, clients, starting)
