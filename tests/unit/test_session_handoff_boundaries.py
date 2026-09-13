"""Capacity and settlement behavior at the actual resource/queue boundaries."""

import asyncio
from types import SimpleNamespace

import pytest

from main_logic import cross_server
from tests.unit.session_handoff_harness import (
    ProviderClient, drain_manager, make_full_manager,
)


async def close_connections(manager, clients, tasks):
    for client in clients:
        client.allow_connect.set()
        client.allow_close.set()
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for record in tuple(manager._connection_records):
        manager._close_connection_record(record)
    await asyncio.wait_for(asyncio.gather(*tuple(manager._session_cleanup_tasks), return_exceptions=True), 2.0)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("existing_kind", ["current", "prewarm", "connecting", "retired"])
async def test_third_llm_connect_waits_for_real_capacity(monkeypatch, existing_kind):
    manager, _, _ = await make_full_manager(monkeypatch)
    first, second, third = ProviderClient(), ProviderClient(), ProviderClient()
    tasks = []
    manager._current_start_deadline = lambda: asyncio.get_running_loop().time() + 10.0
    try:
        if existing_kind == "current":
            manager.session = first
        elif existing_kind == "prewarm":
            manager.pending_session = first
        elif existing_kind == "retired":
            first.allow_close.clear()
            record = manager._register_connection(first)
            manager._close_connection_record(record)
            await first.close_entered.wait()
        else:
            tasks.append(asyncio.create_task(manager._connect_owned_session(first, "prompt")))
            await asyncio.wait_for(first.connect_entered.wait(), 1.0)
        tasks.append(asyncio.create_task(manager._connect_owned_session(second, "prompt")))
        await asyncio.wait_for(second.connect_entered.wait(), 1.0)
        # Keep both occupied while a third attempts to connect.
        manager._current_start_deadline = lambda: asyncio.get_running_loop().time() + 0.1
        with pytest.raises(TimeoutError):
            await manager._connect_owned_session(third, "prompt")
        assert not third.connect_entered.is_set()
        assert sum(not record.closed for record in manager._connection_records) == 2
    finally:
        await close_connections(manager, [first, second, third], tasks)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_overlap_opt_out_serializes_connect(monkeypatch):
    manager, _, _ = await make_full_manager(monkeypatch)
    old, candidate = ProviderClient(), ProviderClient()
    manager.session = old
    candidate.supports_session_overlap = False
    manager._current_start_deadline = lambda: asyncio.get_running_loop().time() + 0.03
    try:
        with pytest.raises(TimeoutError):
            await manager._connect_owned_session(candidate, "prompt")
        assert not candidate.connect_entered.is_set()
    finally:
        await close_connections(manager, [old, candidate], [])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_resistant_handshakes_keep_capacity_until_they_stop(monkeypatch):
    manager, _, _ = await make_full_manager(monkeypatch)

    class ResistantProvider(ProviderClient):
        async def connect(self, *args, **kwargs):
            self.connect_entered.set()
            while not self.allow_connect.is_set():
                try:
                    await self.allow_connect.wait()
                except asyncio.CancelledError:
                    continue

    first, second, third = ResistantProvider(), ResistantProvider(), ProviderClient()
    manager._current_start_deadline = lambda: asyncio.get_running_loop().time() + 0.05
    tasks = [asyncio.create_task(manager._connect_owned_session(client, "prompt")) for client in (first, second)]
    try:
        await asyncio.gather(first.connect_entered.wait(), second.connect_entered.wait())
        for task in tasks:
            task.cancel()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1.0)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        await asyncio.gather(first.close_entered.wait(), second.close_entered.wait())
        assert sum(not record.closed for record in manager._connection_records) == 2
        with pytest.raises(TimeoutError):
            await manager._connect_owned_session(third, "prompt")
        assert not third.connect_entered.is_set()
    finally:
        await close_connections(manager, [first, second, third], tasks)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("initial_state", ["active", "idle"])
async def test_memory_timeout_fallback_does_not_open_the_next_session(monkeypatch, initial_state):
    manager, created, clients = await make_full_manager(monkeypatch)
    socket = manager.websocket
    tasks = []
    callback_calls = []

    async def after_settlement():
        callback_calls.append("clear")

    try:
        if initial_state == "active":
            start = asyncio.create_task(manager.start_session(socket, request_id="old"))
            tasks.append(start)
            old = await asyncio.wait_for(created.get(), 1.0)
            old.allow_connect.set()
            await start
        while not manager.sync_message_queue.empty():
            manager.sync_message_queue.get_nowait()
        if initial_state == "active":
            ending = asyncio.create_task(manager.end_session(
                by_server=True, after_memory_settlement=after_settlement,
                memory_settlement_timeout=0.1,
            ))
        else:
            ending = asyncio.create_task(manager.settle_session_memory_if_idle(
                after_settlement, timeout_seconds=0.1,
            ))
        tasks.append(ending)
        terminal = await asyncio.to_thread(manager.sync_message_queue.get, True, 1.0)
        await asyncio.wait_for(ending, 1.0)
        assert callback_calls == ["clear"]
        assert not terminal["_memory_settlement_done"].done()
        await manager.start_session(socket, request_id="blocked", _deadline=asyncio.get_running_loop().time() + 1.0)
        assert created.empty()
        assert any(item.get("type") == "session_failed" and item.get("request_id") == "blocked" for item in socket.messages)
        await cross_server._complete_session_end_memory_barrier(terminal, manager.lanlan_name)
        assert callback_calls == ["clear", "clear"]
        next_start = asyncio.create_task(manager.start_session(socket, request_id="new"))
        tasks.append(next_start)
        successor = await asyncio.wait_for(created.get(), 1.0)
        successor.allow_connect.set()
        await next_start
        assert manager.session is successor
        assert manager.session_ready
    finally:
        await drain_manager(manager, clients, *tasks)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_late_pcm_during_asr_close_does_not_request_another_start(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    socket = manager.websocket
    starting = asyncio.create_task(manager.start_session(socket, request_id="old"))
    client = await asyncio.wait_for(created.get(), 1.0)
    client.allow_connect.set()
    await starting
    close_entered, release_close = asyncio.Event(), asyncio.Event()

    async def provider_close():
        close_entered.set()
        await release_close.wait()

    manager._asr_runtime._asr_session = SimpleNamespace(close=provider_close)
    manager._asr_runtime._asr_provider = "dummy"
    # Native startup already closed the initially empty ASR runtime. Installing
    # a new external provider corresponds to start() resetting that close latch.
    manager._asr_runtime._asr_runtime_close_task = None
    ending = asyncio.create_task(manager.end_session(by_server=True))
    try:
        await asyncio.wait_for(close_entered.wait(), 1.0)
        await asyncio.wait_for(manager._process_stream_data_internal({"input_type": "audio", "data": [0] * 480}), 0.1)
        assert created.empty()
        assert manager._starting_session_count == 0
    finally:
        release_close.set()
        await drain_manager(manager, clients, starting, ending)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_old_finally_does_not_release_a_successor_operation_slot(monkeypatch):
    manager, _, _ = await make_full_manager(monkeypatch)
    old_claimed, new_claimed = asyncio.Event(), asyncio.Event()
    release_old, release_new = asyncio.Event(), asyncio.Event()

    async def own_slot(request, claimed, released):
        operation, token = manager._claim_start_operation(
            manager.websocket, request, "audio", asyncio.get_running_loop().time() + 15,
        )
        claimed.set()
        await released.wait()
        manager._finish_start_operation(operation, token)

    old = asyncio.create_task(own_slot("old", old_claimed, release_old))
    await old_claimed.wait()
    manager._start_operation.valid = False
    successor = asyncio.create_task(own_slot("new", new_claimed, release_new))
    try:
        await new_claimed.wait()
        release_old.set()
        await old
        assert manager._starting_session_count == 1
        assert manager._starting_input_mode == "audio"
        assert manager._start_operation.request_id == "new"
    finally:
        release_old.set()
        release_new.set()
        await asyncio.gather(old, successor)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retirement_stops_old_text_waiting_for_tts_cache_lock(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    starting = asyncio.create_task(manager.start_session(manager.websocket, request_id="old"))
    old = await asyncio.wait_for(created.get(), 1.0)
    old.allow_connect.set()
    await starting
    manager.use_tts = True
    await manager.tts_cache_lock.acquire()
    output = asyncio.create_task(old.on_text_delta("old text", True, ui_enabled=False))
    ending = None
    try:
        await asyncio.sleep(0)
        assert not output.done()
        ending = asyncio.create_task(manager.end_session(by_server=True))
        await asyncio.wait_for(ending, 1.0)
        with pytest.raises(asyncio.CancelledError):
            await output
        manager.tts_pending_chunks = [("new", "successor text")]
        manager.tts_cache_lock.release()
        assert manager.tts_pending_chunks == [("new", "successor text")]
    finally:
        if manager.tts_cache_lock.locked():
            manager.tts_cache_lock.release()
        await drain_manager(manager, clients, starting, output, *([ending] if ending else []))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ready_start_flush_dispatches_once_without_releasing_start_guard(monkeypatch):
    from main_logic import core as core_module
    from tests.unit.test_core_game_route_memory_contract import (
        _make_callback_media_manager, _make_offline_session_for_callback_media,
    )
    session = _make_offline_session_for_callback_media()
    manager = _make_callback_media_manager(session)
    submissions = []

    async def stream_text(text, **kwargs):
        assert manager._starting_session_count == 1
        submissions.append(text)
        if callable(kwargs.get("on_turn_committed")):
            kwargs["on_turn_committed"]()

    session.stream_text = stream_text
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda *args: None)
    manager.pending_input_data = [{"input_type": "text", "data": "cached input", "request_id": "cached"}]
    operation, token = manager._claim_start_operation(
        manager.websocket, "start", "text", asyncio.get_running_loop().time() + 15,
    )
    try:
        await manager._flush_pending_input_data()
        assert submissions == ["cached input"]
        assert manager.pending_input_data == []
        assert manager._starting_session_count == 1
    finally:
        manager._finish_start_operation(operation, token)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_end_during_asr_close_keeps_handoff_completion_owned(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    starting = asyncio.create_task(manager.start_session(manager.websocket, request_id="old"))
    client = await asyncio.wait_for(created.get(), 1.0)
    client.allow_connect.set()
    await starting
    close_entered, release_close = asyncio.Event(), asyncio.Event()

    async def provider_close():
        close_entered.set()
        await release_close.wait()

    manager._asr_runtime._asr_session = SimpleNamespace(close=provider_close)
    manager._asr_runtime._asr_provider = "dummy"
    manager._asr_runtime._asr_runtime_close_task = None
    ending = asyncio.create_task(manager.end_session(by_server=True))
    try:
        await asyncio.wait_for(close_entered.wait(), 1.0)
        retirement = manager._session_retirements[-1]
        ending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await ending
        release_close.set()
        await asyncio.wait_for(retirement.handoff_safe.wait(), 1.0)
        await asyncio.wait_for(retirement.cleanup_complete.wait(), 1.0)
        assert any(item.get("data") == "session end" for item in manager.sync_message_queue.queue)
    finally:
        release_close.set()
        # Even the deliberately broken mutation may have cancelled the record;
        # do not re-await it via end_session while unwinding the failing test.
        if not ending.cancelled():
            await drain_manager(manager, clients, starting, ending)
        else:
            for task in tuple(manager._session_cleanup_tasks):
                await asyncio.gather(task, return_exceptions=True)
            idle = getattr(manager, "_idle_session_reset_task", None)
            if idle:
                idle.cancel()
                await asyncio.gather(idle, return_exceptions=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_live_input_waits_behind_a_reserved_startup_flush(monkeypatch):
    from main_logic import core as core_module
    from tests.unit.test_core_game_route_memory_contract import (
        _make_callback_media_manager, _make_offline_session_for_callback_media,
    )
    session = _make_offline_session_for_callback_media()
    manager = _make_callback_media_manager(session)
    manager._bg_tasks = set()
    submissions = []

    async def stream_text(text, **kwargs):
        submissions.append(text)
        if callable(kwargs.get("on_turn_committed")):
            kwargs["on_turn_committed"]()

    session.stream_text = stream_text
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda *args: None)
    manager.pending_input_data = [{"input_type": "text", "data": "cached", "request_id": "cached"}]
    reservation = manager._pending_input_flush_scheduled = object()
    await manager._stream_data_now({"input_type": "text", "data": "live", "request_id": "live"})
    assert submissions == []
    assert [item["data"] for item in manager.pending_input_data] == ["cached", "live"]
    task = manager._schedule_session_input_flush(reservation)
    await asyncio.wait_for(task, 1.0)
    assert submissions == ["cached", "live"]
    assert manager.pending_input_data == []
