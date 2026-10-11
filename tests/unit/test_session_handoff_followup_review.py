"""Failed state settlement and runtime recovery must retain their owners."""

import asyncio
from threading import Event
from unittest.mock import AsyncMock

import pytest

from tests.unit.session_handoff_harness import ConnectedSocket, drain_manager, make_full_manager
from tests.unit.test_session_handoff_lifecycle import make_manager
from tests.unit.test_tts_handoff_ownership import install

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_completed_start_context_does_not_revoke_inherited_callback(monkeypatch):
    manager, _, clients = await make_full_manager(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    operation, token = manager._claim_start_operation(
        manager.websocket, "completed-start", "text", asyncio.get_running_loop().time() + 15,
    )

    async def inherited_callback():
        assert manager._current_start_request() is operation
        entered.set()
        await release.wait()
        assert manager._current_start_request() is None
        manager._check_start_operation()
        assert manager._current_start_deadline() > asyncio.get_running_loop().time()

    callback = asyncio.create_task(inherited_callback())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        manager._finish_start_operation(operation, token)
        token = None
        operation.valid = False
        operation.deadline = asyncio.get_running_loop().time() - 1
        release.set()
        await asyncio.wait_for(callback, 1)
    finally:
        release.set()
        callback.cancel()
        await asyncio.gather(callback, return_exceptions=True)
        if token is not None:
            manager._finish_start_operation(operation, token)
        await drain_manager(manager, clients)


@pytest.mark.parametrize("path", ["start", "fallback"])
@pytest.mark.parametrize("live_count", [2, 5])
async def test_tts_admission_uses_configured_capacity_with_retired_workers(monkeypatch, path, live_count):
    from main_logic import core as core_module
    from main_logic.core.tts_records import TtsCapacityError, tts_output_runtime

    manager, _, clients = await make_full_manager(monkeypatch)
    manager.session = object()
    manager.is_active = True
    manager.use_tts = True
    releases = [Event() for _ in range(live_count)]
    runtimes = [install(manager, release) for release in releases]
    for runtime in runtimes[:-1]:
        manager._retire_tts_runtime(runtime)

    def worker(requests, responses, key, voice):
        while requests.get()[0] != "__shutdown__":
            pass

    manager._resolve_tts_worker_spec = lambda: (worker, "key", "voice", "openai", False, {})
    manager._build_tts_runtime_key = lambda: ("test",)
    manager._tts_active_provider_key = "openai"
    monkeypatch.setattr(core_module, "tts_provider_falls_back_on_failure", lambda provider: True)
    monkeypatch.setattr(core_module, "tts_provider_uses_configured_preset_voice", lambda provider: False)
    token = tts_output_runtime.set(runtimes[-1])
    try:
        assert manager._tts_capacity_limit() == 5
        if live_count == 5:
            with pytest.raises(TtsCapacityError):
                if path == "start":
                    manager._start_tts_thread()
                else:
                    manager._activate_configured_tts_fallback("test")
            assert manager._live_tts_runtime_count() == live_count
        else:
            if path == "start":
                manager._start_tts_thread()
            else:
                assert manager._activate_configured_tts_fallback("test")
            assert manager._live_tts_runtime_count() == live_count + 1
            assert all(runtime.thread.is_alive() for runtime in runtimes)
    finally:
        tts_output_runtime.reset(token)
        for release in releases:
            release.set()
        for runtime in tuple(manager._tts_runtimes):
            manager._retire_tts_runtime(runtime)
        await asyncio.gather(*manager._tts_cleanup_tasks, return_exceptions=True)
        manager.session = None
        await drain_manager(manager, clients)


async def test_runtime_recovery_handles_worker_specific_capacity_rejection(monkeypatch):
    from main_logic.core.tts_records import TtsCapacityError
    from unittest.mock import Mock

    manager, _, clients = await make_full_manager(monkeypatch)
    manager.session = object()
    manager.is_active = True
    manager.use_tts = True
    release = Event()
    runtime = install(manager, release)
    manager._retire_tts_runtime(runtime)
    manager._wait_tts_capacity = AsyncMock()
    manager._start_tts_thread = Mock(side_effect=TtsCapacityError("exclusive replacement"))
    manager._schedule_tts_capacity_recovery = Mock()
    manager.tts_pending_chunks = [("speech", "pending")]
    try:
        await manager.ensure_tts_pipeline_alive()
        assert not manager.tts_ready
        assert manager.tts_pending_chunks == [("speech", "pending")]
        manager._schedule_tts_capacity_recovery.assert_called_once()
    finally:
        release.set()
        await asyncio.gather(*manager._tts_cleanup_tasks, return_exceptions=True)
        manager.session = None
        await drain_manager(manager, clients)


async def test_retired_failure_notification_reaches_original_request_socket(monkeypatch):
    manager, _, clients = await make_full_manager(monkeypatch)
    requester = manager.websocket
    operation, token = manager._claim_start_operation(requester, "old-request", "audio", asyncio.get_running_loop().time() + 15)
    operation.valid = False
    manager.websocket = ConnectedSocket()
    try:
        with pytest.raises(asyncio.CancelledError):
            await manager.send_session_failed("audio", request_id="old-request", also_notify=requester)
        await manager.send_session_failed("audio", request_id="old-request", also_notify=requester, allow_retired_operation=True)
        assert requester.messages == [{"type": "session_failed", "input_mode": "audio", "request_id": "old-request"}]
        assert manager._current_start_request() is operation
    finally:
        manager._finish_start_operation(operation, token)
        await drain_manager(manager, clients)


@pytest.mark.parametrize("failure", ["handoff", "abandon"])
async def test_failed_reservation_discards_its_inputs_and_context(monkeypatch, failure):
    manager, _, clients = await make_full_manager(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = {"input_type": "text", "data": "previous owner"}
    manager.pending_input_data = [original]

    async def wait(_deadline):
        entered.set()
        await release.wait()
        if failure == "handoff":
            raise RuntimeError("handoff failed")

    manager._wait_session_handoff = wait
    starting = asyncio.create_task(manager.start_session(manager.websocket, request_id="failed"))
    try:
        await entered.wait()
        manager.pending_input_data.append({"input_type": "text", "data": "abandoned reservation"})
        manager.pending_context_appends = [{"text": "reservation context"}]
        if failure == "abandon":
            manager._user_session_abandon_epoch = getattr(manager, "_user_session_abandon_epoch", 0) + 1
        release.set()
        await starting
        assert manager.pending_input_data == [original]
        assert manager.pending_context_appends == []
        assert any(message.get("type") == "session_failed" for message in manager.websocket.messages)
    finally:
        release.set()
        await drain_manager(manager, clients, starting)


async def test_memory_callback_failure_blocks_until_successful_retry():
    manager = make_manager()
    manager.session.allow_close.set()
    calls = []
    queued = []

    async def callback():
        calls.append("clear")
        if len(calls) == 1:
            raise OSError("context clear failed")

    def queue(cb):
        queued.append(cb)
        return asyncio.create_task(cb())

    async def wait(completion, _callback, **kwargs):
        await completion

    manager._queue_session_end_memory_barrier = queue
    manager._wait_for_session_end_memory_barrier = wait
    ending = manager.request_end_session(by_server=True, after_memory_settlement=callback)
    record = manager._session_retirements[-1]
    with pytest.raises(RuntimeError, match="handoff failed"):
        await ending
    assert record.state_detached
    assert not record.handoff_safe.is_set()
    await manager._wait_session_handoff(asyncio.get_running_loop().time() + 1)
    await record.task
    assert calls == ["clear", "clear"]
    assert len(queued) == 1
    assert record.handoff_safe.is_set()


async def test_failure_after_detachment_resumes_state_cleanup():
    manager = make_manager()
    manager.session.allow_close.set()
    calls = []

    def reset():
        calls.append("reset")
        if len(calls) == 1:
            raise RuntimeError("after detach")

    manager._reset_proactive_gate = reset
    manager._init_renew_status = AsyncMock()
    ending = manager.request_end_session(by_server=True)
    record = manager._session_retirements[-1]
    with pytest.raises(RuntimeError, match="after detach"):
        await ending
    assert manager.session is None
    assert record.state_detached
    await manager._wait_session_handoff(asyncio.get_running_loop().time() + 1)
    await record.task
    manager._init_renew_status.assert_awaited_once()
    assert not manager.session_ready
    assert [item["data"] for item in manager.sync_message_queue.queue] == ["session end"]
    assert record.handoff_safe.is_set()


@pytest.mark.parametrize("superseded", [False, True])
async def test_tts_capacity_release_recovers_only_captured_session(monkeypatch, superseded):
    manager, _, clients = await make_full_manager(monkeypatch)
    manager._tts_capacity_limit = lambda worker=None: 2
    manager.session = object()
    manager.use_tts = True
    manager.is_active = True
    manager._last_tts_respawn_time = 0
    releases = [Event(), Event()]
    runtimes = [install(manager, release) for release in releases]
    for runtime in runtimes:
        manager._retire_tts_runtime(runtime)
    calls = []
    recovered = asyncio.Event()
    manager.tts_pending_chunks = [("reply", "waiting text")]

    def respawn():
        calls.append(list(manager.tts_pending_chunks))
        recovered.set()

    # Exercise the real respawn guards with the current retired pointer still
    # alive. Only provider construction/handler I/O is replaced.
    def start_worker(**kwargs):
        assert manager._live_tts_runtime_count() < manager._tts_capacity_limit()
        respawn()

    manager._start_tts_thread = start_worker
    manager._start_tts_response_handler = lambda: None
    try:
        await asyncio.wait_for(manager.ensure_tts_pipeline_alive(), .1)
        retry = manager._tts_respawn_task
        await manager.ensure_tts_pipeline_alive()
        assert manager._tts_respawn_task is retry
        if superseded:
            manager.session = object()
        releases[0].set()
        await asyncio.wait_for(retry, 2)
        assert runtimes[1].thread.is_alive()
        assert calls == ([] if superseded else [[("reply", "waiting text")]])
    finally:
        for release in releases:
            release.set()
        await asyncio.gather(*(runtime.cleanup_task for runtime in runtimes), return_exceptions=True)
        manager.session = None
        await drain_manager(manager, clients)


@pytest.mark.parametrize("external", [False, True])
async def test_receive_loop_consumes_only_retirement_cancel(external):
    manager = make_manager()

    async def receive():
        task = asyncio.current_task()
        task.cancel("external shutdown" if external else "session start retired")
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError as exc:
            if not manager._consume_start_retirement_cancellation(exc):
                raise
        await asyncio.sleep(0)
        return "next message"

    task = asyncio.create_task(receive())
    if external:
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert await task == "next message"


async def test_late_memory_error_keeps_record_retryable():
    manager = make_manager()
    manager.session.allow_close.set()
    completions = []

    def queue(_callback):
        future = asyncio.get_running_loop().create_future()
        completions.append(future)
        if len(completions) > 1:
            future.set_result(None)
        return future

    manager._queue_session_end_memory_barrier = queue
    manager._wait_for_session_end_memory_barrier = AsyncMock()
    ending = manager.request_end_session(by_server=True, after_memory_settlement=lambda: None)
    record = manager._session_retirements[-1]
    await ending
    completions[0].set_exception(OSError("late callback failure"))
    with pytest.raises(RuntimeError, match="memory settlement failed"):
        await manager._wait_session_handoff(asyncio.get_running_loop().time() + 1)
    assert not record.handoff_safe.is_set()
    await manager._wait_session_handoff(asyncio.get_running_loop().time() + 1)
    await record.task
    assert len(completions) == 2
    assert record.handoff_safe.is_set()


async def test_game_speech_registers_correlation_after_recovery():
    from tests.unit.test_core_game_route_memory_contract import _make_manager, _FakeAliveThread
    manager = _make_manager()
    release = Event()
    release.set()
    runtime = install(manager, release)

    async def recover():
        manager._retire_tts_runtime(runtime)
        manager.tts_thread = _FakeAliveThread()
        manager.tts_ready = True

    manager.ensure_tts_pipeline_alive = recover
    try:
        result = await manager.mirror_assistant_speech(
            "game line", metadata={}, mirror_text=False, emit_turn_end_after=False,
            speech_correlation_id="game-request",
        )
        assert result["audio_queued"]
        assert manager._audio_chunk_header(result["speech_id"])["sdk_speech_correlation_id"] == "game-request"
    finally:
        if runtime.cleanup_task is None:
            manager._retire_tts_runtime(runtime)
        await runtime.cleanup_task


@pytest.mark.parametrize("path", ["rebuild", "stream", "raw_stream"])
@pytest.mark.parametrize("external", [False, True])
async def test_streaming_boundaries_keep_loop_only_for_retired_start(monkeypatch, path, external):
    manager, _, clients = await make_full_manager(monkeypatch)

    async def cancelled_start(*args, **kwargs):
        asyncio.current_task().cancel("shutdown" if external else "session start retired")
        await asyncio.sleep(0)

    manager.start_session = cancelled_start

    async def receive():
        if path == "rebuild":
            assert not await manager._rebuild_offline_session_for_text_input("text")
        elif path == "stream":
            await manager._process_stream_data_internal({"input_type": "text", "data": "input"})
        else:
            for _ in range(2):
                await manager._stream_data_now({"input_type": "text", "data": "input"})
                assert asyncio.current_task().cancelling() == 0
        await asyncio.sleep(0)
        return "receive loop alive"

    caller = asyncio.create_task(receive())
    try:
        if external:
            with pytest.raises(asyncio.CancelledError):
                await caller
        else:
            assert await caller == "receive loop alive"
    finally:
        await drain_manager(manager, clients, caller)


@pytest.mark.parametrize("external", [False, True])
async def test_agent_callbacks_requeued_on_start_cancellation(external):
    from tests.unit.test_proactive_sm_integration import _make_mgr
    manager = _make_mgr()
    manager.websocket = ConnectedSocket()
    callback = {"_callback_delivery_id": "still-pending", "status": "completed", "summary": "task finished"}
    manager.pending_agent_callbacks = [callback]

    async def cancelled_start(*args, **kwargs):
        asyncio.current_task().cancel("shutdown" if external else "session start retired")
        await asyncio.sleep(0)

    manager.start_session = cancelled_start
    task = asyncio.create_task(manager.trigger_agent_callbacks())
    if external:
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert not await task
    assert manager.pending_agent_callbacks == [callback]


@pytest.mark.parametrize("committed", [False, True])
async def test_text_callback_cancel_requeues_only_uncommitted_batch(committed):
    from main_logic.proactive_delivery import DELIVERY_ACK_FUTURE_KEY
    from tests.unit.test_proactive_sm_integration import _FakeOmniOffline, _make_mgr

    entered = asyncio.Event()

    class Session(_FakeOmniOffline):
        async def prompt_ephemeral(self, instruction, *, images=None, on_committed=None, on_committed_text=None):
            if committed:
                if on_committed_text:
                    on_committed_text("shown")
                on_committed()
            entered.set()
            await asyncio.Event().wait()

    manager = _make_mgr(session=Session())
    ack = asyncio.get_running_loop().create_future()
    callback = {"_callback_delivery_id": "cancelled", "status": "completed", "summary": "result", DELIVERY_ACK_FUTURE_KEY: ack}
    manager.pending_agent_callbacks = [callback]
    extra = {"_callback_delivery_id": "cancelled", "summary": "result"}
    manager.pending_extra_replies = [extra]
    task = asyncio.create_task(manager.trigger_agent_callbacks())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel("external shutdown")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.pending_agent_callbacks == ([] if committed else [callback])
    assert manager.pending_extra_replies == ([] if committed else [extra])
    assert (ack.done() and ack.result() is True) == committed
