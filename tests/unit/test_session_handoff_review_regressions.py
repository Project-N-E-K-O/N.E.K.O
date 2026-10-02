"""Interleavings reported during review of the session handoff change."""

import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.core import LLMSessionManager
from main_logic.core.game_speech_audio_cache import GAME_SPEECH_AUDIO_CACHE
from tests.unit.session_handoff_harness import drain_manager, make_full_manager
from tests.unit.test_session_handoff_lifecycle import make_manager
from tests.unit.test_tts_handoff_ownership import Manager, install
from tests.unit.test_realtime_close_ownership import _make_client
from main_logic.omni_realtime_client._response_arbiter import RealtimeResponseArbiter

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_handoff_wait_reserves_start_and_queues_ingress(monkeypatch):
    manager, _, clients = await make_full_manager(monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def wait(_deadline):
        entered.set()
        await release.wait()

    manager._wait_session_handoff = wait
    manager.send_session_failed = AsyncMock()
    start = asyncio.create_task(manager.start_session(manager.websocket, new=True, request_id="waiting"))
    try:
        await entered.wait()
        await asyncio.wait_for(manager._stream_data_now({"input_type": "user_image", "data": "image"}), .5)
        assert manager._starting_session_count == 1
        assert manager._session_generation == 0
        assert len(manager.pending_input_data) == 1
        assert not clients
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start
        manager.send_session_failed.assert_awaited_once_with("audio", request_id="waiting", also_notify=manager.websocket, allow_retired_operation=True)
    finally:
        release.set()
        await drain_manager(manager, clients, start)


async def test_memory_callback_error_notifies_waiting_start(monkeypatch):
    manager, _, clients = await make_full_manager(monkeypatch)

    async def fail(_deadline):
        raise OSError("memory callback failed")

    manager._wait_session_handoff = fail
    manager.send_session_failed = AsyncMock()
    await manager.start_session(manager.websocket, request_id="memory-error")
    manager.send_session_failed.assert_awaited_once()
    assert manager._starting_session_count == 0
    assert manager.session_start_failure_count == 0
    await drain_manager(manager, clients)


async def test_failed_renewal_does_not_pin_retirement(monkeypatch):
    manager = make_manager()
    old = manager.session
    old.allow_close.set()
    manager._init_renew_status = AsyncMock(side_effect=OSError("renew failed"))
    ending = manager.request_end_session(by_server=True)
    record = manager._session_retirements[-1]
    with pytest.raises(RuntimeError, match="handoff failed"):
        await asyncio.wait_for(ending, 1)
    assert old.closed.is_set()
    assert not record.handoff_safe.is_set()
    assert record.cleanup_complete.is_set()
    manager._init_renew_status.side_effect = None
    await manager._wait_session_handoff(asyncio.get_running_loop().time() + .5)
    await record.task
    assert record.handoff_safe.is_set()
    assert manager._init_renew_status.await_count == 2
    await manager.end_session(by_server=True)


async def test_unexpected_isolation_failure_can_retry_without_false_release(monkeypatch):
    manager = make_manager()
    old = manager.session
    old.allow_close.set()
    original = manager._retire_session_resources_owned
    calls = 0

    async def fail_once(record, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("early isolation failure")
        return await original(record, **kwargs)

    monkeypatch.setattr(manager, "_retire_session_resources_owned", fail_once)
    ending = manager.request_end_session(by_server=True)
    record = manager._session_retirements[-1]
    with pytest.raises(OSError):
        await ending
    assert not record.handoff_safe.is_set()
    assert not record.cleanup_complete.is_set()
    await manager._wait_session_handoff(asyncio.get_running_loop().time() + .5)
    await record.task
    assert calls == 2
    assert old.closed.is_set()


async def test_retirement_joins_start_checkpoint_before_receive_loop_cleanup():
    manager = make_manager()
    manager.session.allow_close.set()
    entered = asyncio.Event()
    caller_cleanup = asyncio.Event()

    async def receive_loop():
        # This operation owns the installed client; it is not replacing a
        # predecessor. Capture an empty slot, then model its publication.
        installed = manager.session
        manager.session = None
        operation, token = manager._claim_start_operation(manager.websocket, None, "audio", asyncio.get_running_loop().time() + 15)
        manager.session = installed
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            manager._finish_start_operation(operation, token)
            caller_cleanup.set()
            await manager.end_session(by_server=True)

    caller = asyncio.create_task(receive_loop())
    await entered.wait()
    ending = manager.request_end_session(by_server=True)
    await asyncio.wait_for(caller_cleanup.wait(), .5)
    await asyncio.wait_for(ending, .5)
    await asyncio.gather(caller, return_exceptions=True)


async def test_deferred_flush_stays_idle_until_owner_releases_it():
    manager = make_manager()
    manager._bg_tasks = set()
    manager.pending_input_data = [{"input_type": "text", "data": "later"}]
    manager._deferred_pending_input_flush_count = 1
    manager._flush_pending_input_data = AsyncMock()
    reservation = manager._pending_input_flush_scheduled = object()
    task = manager._schedule_session_input_flush(reservation)
    await task
    for _ in range(5):
        await asyncio.sleep(0)
    assert manager._flush_pending_input_data.await_count == 1
    assert manager._pending_input_flush_scheduled is None
    assert not manager._bg_tasks


async def test_runtime_tts_capacity_failure_does_not_block_turn():
    manager = Manager()
    manager._tts_capacity_limit = lambda worker=None: 2
    releases = [Event(), Event()]
    runtimes = [install(manager, release) for release in releases]
    for runtime in runtimes:
        manager._retire_tts_runtime(runtime)
    manager._stop_tts_response_handler = AsyncMock(side_effect=AssertionError("turn must not join old handler"))
    try:
        await asyncio.wait_for(manager.ensure_tts_pipeline_alive(), .1)
        assert not manager.tts_ready
        assert manager._live_tts_runtime_count() == 2
        manager._stop_tts_response_handler.assert_not_awaited()
    finally:
        retry = getattr(manager, "_tts_respawn_task", None)
        if retry is not None:
            retry.cancel()
            await asyncio.gather(retry, return_exceptions=True)
        for release in releases:
            release.set()
        await asyncio.gather(*(runtime.cleanup_task for runtime in runtimes))


async def test_retirement_cancels_preloads_and_discards_current_capture(monkeypatch):
    manager = Manager()
    release = Event()
    runtime = install(manager, release)
    calls = []
    manager.cancel_game_speech_preloads = lambda: calls.append("cancel")
    discarded = []
    monkeypatch.setattr(GAME_SPEECH_AUDIO_CACHE, "discard_owner", lambda owner: discarded.append(owner))
    try:
        manager._retire_tts_runtime(runtime)
        manager._retire_tts_runtime(runtime)
        assert calls == ["cancel"]
        assert discarded == [manager]
    finally:
        release.set()
        await runtime.cleanup_task


@pytest.mark.parametrize("via_quarantine", [False, True])
async def test_gemini_failed_close_can_retry_through_public_paths(via_quarantine):
    client = _make_client()
    client._is_gemini = True
    calls = []

    class Context:
        async def __aexit__(self, *args):
            calls.append("exit")
            if len(calls) == 1:
                raise RuntimeError("exit failed")

    class Session:
        async def close(self):
            if len(calls) == 1:
                raise RuntimeError("close failed")

    client._gemini_context_manager = Context()
    client._gemini_session = Session()
    client.ws = client._gemini_session
    if via_quarantine:
        client._gemini_external_outcome_token = object()
        task = asyncio.create_task(client._quarantine_gemini_external_submit(None, client._gemini_external_outcome_token))
        client._gemini_external_quarantine_task = task
    else:
        task = asyncio.create_task(client._close_gemini())
    with pytest.raises(RuntimeError):
        await task
    if via_quarantine:
        await client._await_gemini_external_quarantine()
        assert client._gemini_external_quarantine_task is None
    else:
        await client.close()
    assert calls == ["exit", "exit"]
    assert client._gemini_session is None
    assert not client._gemini_close_retry_contexts


async def test_pause_rearm_does_not_cancel_teardown_after_expiry():
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def abort(_reason):
        entered.set()
        await release.wait()
        finished.set()

    arbiter = RealtimeResponseArbiter(AsyncMock(), abort_transport=abort)
    arbiter._dispatch_pause_timeout = .01
    arbiter.begin_turn_preparation("stuck")
    expiry = arbiter._pause_expiry
    try:
        await asyncio.wait_for(entered.wait(), 1)
        arbiter.pause_dispatch("late cleanup")
        arbiter.resume_dispatch()
        release.set()
        await asyncio.wait_for(expiry, 1)
        assert finished.is_set()
    finally:
        release.set()
        await arbiter.shutdown()


@pytest.mark.parametrize("source,expected_cancels", [("proactive", 1), ("external_asr", 0)])
async def test_prepare_preserves_only_parked_external_asr(source, expected_cancels):
    client = _make_client()
    arbiter = client._ensure_response_arbiter()
    arbiter._current = SimpleNamespace(source=source, response_send_started=False, terminal=None)
    arbiter.cancel_current = AsyncMock()
    client.handle_interruption = AsyncMock()
    try:
        await client.prepare_external_voice_turn(turn_id="new-user-turn")
        assert arbiter.cancel_current.await_count == expected_cancels
    finally:
        arbiter._current = None
        await client.close()


async def test_memory_settlement_receives_its_own_budget(monkeypatch):
    from main_logic.core import session_lifecycle
    monkeypatch.setattr(session_lifecycle, "FRONTEND_START_SESSION_TIMEOUT_SECONDS", .03)
    manager = make_manager()
    manager.session.allow_close.set()
    completion = asyncio.get_running_loop().create_future()
    manager._queue_session_end_memory_barrier = lambda _callback: completion

    async def wait(future, _callback, **kwargs):
        await future

    manager._wait_for_session_end_memory_barrier = wait
    timer = asyncio.get_running_loop().call_later(.05, completion.set_result, None)
    try:
        await manager.end_session(by_server=True, after_memory_settlement=lambda: None, memory_settlement_timeout=.1)
        assert completion.done()
    finally:
        timer.cancel()


async def test_retirement_keeps_inputs_of_reserved_successor():
    manager = make_manager()
    manager.session.allow_close.set()
    manager.pending_input_data = [{"data": "old"}]
    entered, release = asyncio.Event(), asyncio.Event()

    async def close_asr(**kwargs):
        entered.set()
        await release.wait()

    manager._close_independent_asr = close_asr
    ending = manager.request_end_session(by_server=True)
    await entered.wait()
    operation, token = manager._claim_start_operation(manager.websocket, "new", "audio", asyncio.get_running_loop().time() + 15, advance_generation=False)
    new_input = {"data": "new"}
    manager.pending_input_data.append(new_input)
    try:
        release.set()
        await ending
        assert manager.pending_input_data == [new_input]
        assert manager._starting_session_count == 1
    finally:
        manager._finish_start_operation(operation, token)
