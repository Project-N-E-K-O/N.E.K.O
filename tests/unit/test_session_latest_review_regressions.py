"""Late readiness and logical handoff must survive slow external resources."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock
from threading import Event

import pytest

from main_logic.core import lifecycle, streaming
from main_logic.core.tts_records import TtsCapacityError
from tests.unit.session_handoff_harness import ConnectedSocket, drain_manager, make_full_manager
from tests.unit.test_session_handoff_lifecycle import make_manager

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_settled_start_does_not_spin_scheduled_flush():
    manager = make_manager()
    manager._bg_tasks = set()
    manager.pending_input_data = [{"input_type": "text", "data": "retain"}]
    operation, context = manager._claim_start_operation(manager.websocket, "settled", "text", asyncio.get_running_loop().time() + 1)
    record = manager._register_connection(manager.session)
    manager._finish_start_operation(operation, context)
    operation.finished.wait = AsyncMock(side_effect=AssertionError("finished wait cannot yield"))
    manager._starting_session_count = 1  # A non-start promotion holds the counter.
    reservation = manager._pending_input_flush_scheduled = object()
    await asyncio.wait_for(manager._schedule_session_input_flush(reservation), 0.2)
    operation.finished.wait.assert_not_awaited()
    assert not record.callbacks
    assert manager.pending_input_data == [{"input_type": "text", "data": "retain"}]


@pytest.mark.parametrize("callback", ["handle_text_data", "handle_output_transcript"])
async def test_first_reply_chunk_retries_retired_live_tts_worker(monkeypatch, callback):
    from tests.unit.test_tts_handoff_ownership import install

    manager, _, clients = await make_full_manager(monkeypatch)
    release = Event()
    old = install(manager, release)
    manager._retire_tts_runtime(old)
    manager.use_tts = True
    manager.tts_ready = False
    manager.current_speech_id = "new reply"
    manager.send_lanlan_response = AsyncMock()
    manager._respawn_tts_worker = Mock()
    try:
        await getattr(manager, callback)("kept text", is_first_chunk=True)
        manager._respawn_tts_worker.assert_called_once()
        assert manager.tts_pending_chunks == [("new reply", "kept text")]
    finally:
        release.set()
        await drain_manager(manager, clients)
        await asyncio.gather(*manager._tts_cleanup_tasks, return_exceptions=True)


@pytest.mark.parametrize("failure", [TimeoutError, TtsCapacityError])
async def test_failed_startup_tts_recovers_after_worker_exit_without_new_reply(monkeypatch, failure):
    from tests.unit.test_tts_handoff_ownership import install

    manager, created, clients = await make_full_manager(monkeypatch)
    manager._config_manager.core["DISABLE_TTS"] = False
    monkeypatch.setattr(manager, "_resolve_session_use_tts", lambda *args: True)
    release = Event()
    loop = asyncio.get_running_loop()
    spoken = asyncio.Queue()
    old = None

    async def fail_tts():
        nonlocal old
        old = install(manager, release)
        old.supports_runtime_overlap = False
        manager._retire_tts_runtime(old)
        manager._tts_capacity_exhausted = True
        raise failure("optional TTS wait failed")

    def worker(requests, responses, *_):
        responses.put(("__ready__", True))
        while True:
            sid, text = requests.get()
            if sid == "__shutdown__":
                return
            if sid == "saved":
                loop.call_soon_threadsafe(spoken.put_nowait, text)

    worker.supports_runtime_overlap = False
    monkeypatch.setattr(manager, "_start_session_start_tts_if_needed", fail_tts)
    monkeypatch.setattr(lifecycle._core_facade, "get_tts_worker", lambda **kwargs: (worker, "key", "qwen"))
    starting = asyncio.create_task(manager.start_session(manager.websocket, request_id="recover-output"))
    try:
        client = await asyncio.wait_for(created.get(), 2)
        client.allow_connect.set()
        await asyncio.wait_for(starting, 2)
        assert manager.is_active and manager.session is client
        assert manager._tts_respawn_task is not None
        assert not manager._tts_capacity_exhausted
        manager.tts_pending_chunks = [("saved", "retained output")]
        release.set()
        assert await asyncio.wait_for(spoken.get(), 2) == "retained output"
        assert manager._tts_runtime is not old and manager.tts_ready
    finally:
        release.set()
        await drain_manager(manager, clients, starting)
        await asyncio.gather(*manager._tts_cleanup_tasks, return_exceptions=True)


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("has_session", [False, True])
async def test_user_end_announces_departure_only_for_live_session(active, has_session):
    manager = make_manager()
    manager.session.allow_close.set()
    manager.is_active = active
    if not has_session:
        manager.session = None
    await manager.end_session(by_server=False)
    notices = [json.loads(call.args[0]) for call in manager.send_status.await_args_list]
    departures = [notice for notice in notices if notice.get("code") == "CHARACTER_LEFT"]
    assert len(departures) == int(active and has_session)


async def test_scheduled_flush_does_not_spin_while_another_flush_owns_input(monkeypatch):
    manager = make_manager()
    manager.session.allow_close.set()
    manager._bg_tasks = set()
    entered, release = asyncio.Event(), asyncio.Event()
    delivered = []
    manager.pending_input_data = [{"input_type": "text", "data": "first"}]

    async def dispatch(message, *, on_dispatch_attempted):
        on_dispatch_attempted()
        delivered.append(message["data"])
        entered.set()
        await release.wait()

    monkeypatch.setattr(manager, "_process_stream_data_internal", dispatch)
    schedule = Mock(wraps=manager._schedule_session_input_flush)
    monkeypatch.setattr(manager, "_schedule_session_input_flush", schedule)
    owning = asyncio.create_task(manager._flush_pending_input_data())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        manager.pending_input_data.append({"input_type": "text", "data": "second"})
        reservation = object()
        manager._pending_input_flush_scheduled = reservation
        scheduled = manager._schedule_session_input_flush(reservation)
        await asyncio.sleep(0)
        assert not scheduled.done()
        assert manager._pending_input_flush_scheduled is reservation
        assert schedule.call_count == 1, "an occupied input gate must not create retry tasks"
        release.set()
        await asyncio.wait_for(owning, 2)
        await asyncio.wait_for(scheduled, 2)
        assert delivered == ["first", "second"]
        assert not manager.pending_input_data
    finally:
        release.set()
        await asyncio.gather(owning, *manager._bg_tasks, return_exceptions=True)
        await manager.end_session(by_server=True)


async def test_reserved_flush_replays_rollback_after_active_owner_is_cancelled(monkeypatch):
    manager = make_manager()
    manager.session.allow_close.set()
    manager._bg_tasks = set()
    entered = asyncio.Event()
    delivered = []
    first_attempt = True
    manager.pending_input_data = [{"input_type": "text", "data": "first"}]

    async def dispatch(message, *, on_dispatch_attempted):
        nonlocal first_attempt
        if first_attempt:
            first_attempt = False
            entered.set()
            await asyncio.Event().wait()  # Cancellation before the dispatch boundary.
        on_dispatch_attempted()
        delivered.append(message["data"])

    monkeypatch.setattr(manager, "_process_stream_data_internal", dispatch)
    owning = asyncio.create_task(manager._flush_pending_input_data())
    scheduled = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        manager.pending_input_data.append({"input_type": "text", "data": "second"})
        reservation = object()
        manager._pending_input_flush_scheduled = reservation
        scheduled = manager._schedule_session_input_flush(reservation)
        await asyncio.sleep(0)
        owning.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owning
        await asyncio.wait_for(scheduled, 2)
        assert delivered == ["first", "second"], "the retained reservation must drain rollback before queued input"
        assert not manager.pending_input_data
        assert manager._pending_input_flush_scheduled is None
    finally:
        owning.cancel()
        await asyncio.gather(owning, *([scheduled] if scheduled else []), *manager._bg_tasks, return_exceptions=True)
        await manager.end_session(by_server=True)


async def test_completed_flush_retains_retry_when_a_new_owner_claims_gate(monkeypatch):
    manager = make_manager()
    manager.session.allow_close.set()
    manager._bg_tasks = set()
    manager.pending_input_data = [{"input_type": "text", "data": "first"}]
    entered = asyncio.Event()
    delivered = []
    external_owner = None
    interrupted_second = False

    async def acquire_as_external_owner():
        manager.pending_input_data.append({"input_type": "text", "data": "second"})
        await manager._flush_pending_input_data()

    async def dispatch(message, *, on_dispatch_attempted):
        nonlocal external_owner, interrupted_second
        if message["data"] == "first":
            # This owner starts before the first task's completion callback.
            external_owner = asyncio.create_task(acquire_as_external_owner())
        elif message["data"] == "second" and not interrupted_second:
            interrupted_second = True
            manager.pending_input_data.append({"input_type": "text", "data": "third"})
            entered.set()
            await asyncio.Event().wait()
        on_dispatch_attempted()
        delivered.append(message["data"])

    monkeypatch.setattr(manager, "_process_stream_data_internal", dispatch)
    schedule = Mock(wraps=manager._schedule_session_input_flush)
    monkeypatch.setattr(manager, "_schedule_session_input_flush", schedule)
    reservation = object()
    manager._pending_input_flush_scheduled = reservation
    first = manager._schedule_session_input_flush(reservation)
    try:
        await asyncio.wait_for(first, 2)
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(0)
        assert manager._pending_input_flush_scheduled is reservation
        assert schedule.call_count == 2, "completion must retain one event-waiting retry, without spinning"
        external_owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await external_owner
        await asyncio.wait_for(asyncio.gather(*manager._bg_tasks), 2)
        assert delivered == ["first", "second", "third"]
        assert not manager.pending_input_data
    finally:
        if external_owner is not None:
            external_owner.cancel()
        await asyncio.gather(first, *([external_owner] if external_owner else []), *manager._bg_tasks, return_exceptions=True)
        await manager.end_session(by_server=True)


@pytest.mark.parametrize("explicit_target", [False, True])
@pytest.mark.parametrize("via_cleanup", [False, True])
async def test_predecessor_server_end_does_not_revoke_replacement_start(monkeypatch, explicit_target, via_cleanup):
    manager, created, clients = await make_full_manager(monkeypatch)
    first = asyncio.create_task(manager.start_session(manager.websocket, request_id="predecessor"))
    starting = None
    cleaning = None
    entered, release = asyncio.Event(), asyncio.Event()
    original_retire = manager._retire_session_resources_owned
    try:
        predecessor = await asyncio.wait_for(created.get(), 2)
        predecessor.allow_connect.set()
        await asyncio.wait_for(first, 2)

        async def retire(record, **kwargs):
            if record.session is predecessor:
                entered.set()
                await release.wait()
            await original_retire(record, **kwargs)

        monkeypatch.setattr(manager, "_retire_session_resources_owned", retire)
        starting = asyncio.create_task(manager.start_session(manager.websocket, new=True, request_id="replacement"))
        await asyncio.wait_for(entered.wait(), 2)
        operation = manager._start_operation
        retirement = manager._session_retirements[-1]
        assert manager.session is predecessor
        kwargs = {"expected_session": predecessor} if explicit_target else {}
        if via_cleanup:
            cleaning = asyncio.create_task(manager.cleanup(**kwargs))
            await asyncio.sleep(0)
        else:
            delayed_end = manager.request_end_session(by_server=True, **kwargs)
            assert delayed_end is retirement.task
        assert operation.valid, "a delayed predecessor end must not revoke the replacement"
        release.set()
        successor = await asyncio.wait_for(created.get(), 2)
        successor.allow_connect.set()
        await asyncio.wait_for(starting, 2)
        if cleaning is not None:
            await asyncio.wait_for(cleaning, 2)
        assert manager.session is successor and manager.is_active
        assert predecessor.closed.is_set()
        assert not any(message.get("type") == "session_failed" for message in manager.websocket.messages)
    finally:
        release.set()
        await drain_manager(manager, clients, first, *([starting] if starting else []))
        if cleaning is not None:
            await asyncio.gather(cleaning, return_exceptions=True)


@pytest.mark.parametrize("llm_release_delay", [0, 0.3])
async def test_late_tts_ready_survives_slow_llm_start(monkeypatch, llm_release_delay):
    manager, created, clients = await make_full_manager(monkeypatch)
    manager._config_manager.core["DISABLE_TTS"] = False
    monkeypatch.setattr(manager, "_resolve_session_use_tts", lambda *args: True)
    timed_out, ready_seen = asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    spoken_chunks = asyncio.Queue()
    original_start_tts = manager._start_session_start_tts_if_needed
    original_flush = manager._flush_tts_pending_chunks
    tts_start_task = None

    def worker(requests, responses, *_):
        # The test supplies the external readiness message only after the
        # real startup waiter has exhausted its optional TTS budget.
        while True:
            speech_id, text = requests.get()
            if speech_id == "__shutdown__":
                return
            if text is not None and speech_id is not None and not speech_id.startswith("__"):
                loop.call_soon_threadsafe(spoken_chunks.put_nowait, text)

    monkeypatch.setattr(lifecycle._core_facade, "get_tts_worker", lambda **kwargs: (worker, "key", "qwen"))
    def start_deadline():
        # Only the TTS waiter must exhaust a short budget. LLM connection is
        # released by the events below, so Windows scheduling cannot expire it
        # while the test is waiting for the late readiness message.
        budget = 0.2 if asyncio.current_task() is tts_start_task else 10
        return loop.time() + budget

    monkeypatch.setattr(manager, "_current_start_deadline", start_deadline)

    async def observe_tts_start():
        nonlocal tts_start_task
        tts_start_task = asyncio.current_task()
        try:
            return await original_start_tts()
        except TimeoutError:
            timed_out.set()
            raise

    async def observe_flush():
        await original_flush()
        if manager.tts_ready:
            ready_seen.set()

    monkeypatch.setattr(manager, "_start_session_start_tts_if_needed", observe_tts_start)
    monkeypatch.setattr(manager, "_flush_tts_pending_chunks", observe_flush)
    starting = asyncio.create_task(manager.start_session(manager.websocket, request_id="late-tts"))
    try:
        client = await asyncio.wait_for(created.get(), 2)
        await asyncio.wait_for(timed_out.wait(), 2)
        manager.tts_response_queue.put(("__ready__", True))
        await asyncio.wait_for(ready_seen.wait(), 2)
        assert manager.tts_ready and not starting.done()
        # Exercise a release later than the old shared 0.2-second budget.
        await asyncio.sleep(llm_release_delay)
        assert manager.tts_ready and not starting.done()
        client.allow_connect.set()
        await asyncio.wait_for(starting, 2)
        assert manager.tts_ready, "gather must not overwrite readiness already published by the handler"
        assert manager.tts_thread.is_alive()
        assert any(message.get("type") == "session_started" for message in manager.websocket.messages)
        await manager.mirror_assistant_speech("late ready speech", metadata={}, mirror_text=False, emit_turn_end_after=False)
        assert "late ready speech" in await asyncio.wait_for(spoken_chunks.get(), 2)
    finally:
        await drain_manager(manager, clients, starting)
        await asyncio.gather(*manager._tts_cleanup_tasks, return_exceptions=True)


@pytest.mark.parametrize("via_cleanup", [False, True])
async def test_server_end_without_target_preserves_pending_start(monkeypatch, via_cleanup):
    manager, created, clients = await make_full_manager(monkeypatch)
    manager._config_manager.core["DISABLE_TTS"] = False
    monkeypatch.setattr(manager, "_resolve_session_use_tts", lambda *args: True)

    def worker(requests, responses, *_):
        responses.put(("__ready__", True))
        while requests.get()[0] != "__shutdown__":
            pass

    monkeypatch.setattr(lifecycle._core_facade, "get_tts_worker", lambda **kwargs: (worker, "key", "qwen"))
    starting = asyncio.create_task(manager.start_session(manager.websocket, request_id="server-end"))
    try:
        client = await asyncio.wait_for(created.get(), 2)
        await asyncio.wait_for(client.connect_entered.wait(), 2)
        operation = manager._start_operation
        assert manager.session is None
        async with asyncio.timeout(2):
            while not manager.tts_ready:
                await asyncio.sleep(0)
        runtime = manager._tts_runtime
        handler = manager.tts_handler_task
        await asyncio.wait_for(manager.cleanup() if via_cleanup else manager.end_session(by_server=True), 2)
        assert operation.valid and not starting.done()
        assert manager._starting_session_count == 1
        assert not client.closed.is_set()
        assert manager._tts_runtime_is_current(runtime) and not handler.done()
        client.allow_connect.set()
        await asyncio.wait_for(starting, 2)
        assert manager.session is client and manager.is_active
        assert not any(message.get("type") == "session_failed" for message in manager.websocket.messages)
    finally:
        await drain_manager(manager, clients, starting)
        await asyncio.gather(*manager._tts_cleanup_tasks, return_exceptions=True)


@pytest.mark.parametrize("replaced_socket", [False, True])
async def test_disconnect_during_start_revokes_only_matching_socket(monkeypatch, replaced_socket):
    manager, created, clients = await make_full_manager(monkeypatch)
    disconnected = manager.websocket
    if replaced_socket:
        manager.websocket = ConnectedSocket()
    requester = manager.websocket
    manager._config_manager.core["DISABLE_TTS"] = False
    monkeypatch.setattr(manager, "_resolve_session_use_tts", lambda *args: True)

    def worker(requests, responses, *_):
        responses.put(("__ready__", True))
        while requests.get()[0] != "__shutdown__":
            pass

    monkeypatch.setattr(lifecycle._core_facade, "get_tts_worker", lambda **kwargs: (worker, "key", "qwen"))
    starting = asyncio.create_task(manager.start_session(requester, request_id="disconnect-start"))
    try:
        client = await asyncio.wait_for(created.get(), 2)
        await asyncio.wait_for(client.connect_entered.wait(), 2)
        async with asyncio.timeout(2):
            while not manager.tts_ready:
                await asyncio.sleep(0)
        operation = manager._start_operation
        runtime = manager._tts_runtime
        assert manager.session is None
        await asyncio.wait_for(manager.cleanup(expected_websocket=disconnected), 2)
        if replaced_socket:
            assert operation.valid and not starting.done()
            assert manager.websocket is requester
            assert manager._tts_runtime_is_current(runtime)
            client.allow_connect.set()
            await asyncio.wait_for(starting, 2)
            assert manager.session is client and manager.is_active
        else:
            assert not operation.valid
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(starting, 2)
            assert client.closed.is_set()
            assert manager.websocket is None and manager.session is None
            assert not manager.is_active and runtime.retired
            await asyncio.wait_for(runtime.cleanup_task, 2)
            assert not runtime.thread.is_alive()
            assert not any(message.get("type") == "session_started" for message in requester.messages)
    finally:
        await drain_manager(manager, clients, starting)
        await asyncio.gather(*manager._tts_cleanup_tasks, return_exceptions=True)


async def test_safe_close_failure_allows_text_rebuild_and_retains_capacity(monkeypatch):
    manager = make_manager()
    manager.session_start_failure_count = 0
    manager.session_start_max_failures = 3
    old = manager.session

    async def failed_close():
        raise RuntimeError("physical close uncertain")

    class Offline:
        pass

    async def start(*args, **kwargs):
        manager.session = Offline()
        manager.is_active = True

    old.close = failed_close
    manager.start_session = AsyncMock(side_effect=start)
    monkeypatch.setattr(streaming, "OmniOfflineClient", Offline)
    pending = list(manager.pending_input_data)
    assert await manager._rebuild_offline_session_for_text_input("text")
    manager.start_session.assert_awaited_once()
    assert manager.pending_input_data == pending
    record = manager._connection_record(old)
    assert record.retired and not record.closed
    with pytest.raises(RuntimeError, match="physical close uncertain"):
        record.close_task.result()


async def test_start_deadline_uses_existing_timeout_notice_with_nonempty_details(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    starting = asyncio.create_task(manager.start_session(
        manager.websocket, request_id="timeout-notice", _deadline=asyncio.get_running_loop().time() + 0.2,
    ))
    try:
        await asyncio.wait_for(created.get(), 2)
        await asyncio.wait_for(starting, 2)
        statuses = [message for message in manager.websocket.messages if message.get("type") == "status"]
        # send_status wraps its JSON code in a status envelope.
        notices = [json.loads(message["message"]) for message in statuses]
        assert any(notice["code"] == "CONNECTION_TIMEOUT" and notice["details"]["error"] for notice in notices)
        assert not any(notice["code"] == "CONNECTION_CLOSED_ABNORMAL" for notice in notices)
        assert manager.session_start_failure_count == 1
        assert any(message.get("type") == "session_failed" for message in manager.websocket.messages)
    finally:
        await drain_manager(manager, clients, starting)
