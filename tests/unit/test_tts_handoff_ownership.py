import asyncio
from queue import Queue
from threading import Event, Thread

import pytest

from main_logic.core.tts_runtime import TtsRuntimeMixin
from main_logic.core.tts_lifecycle import TtsLifecycleMixin
from main_logic.core.tts_records import TtsCapacityError, tts_output_runtime


class Manager(TtsRuntimeMixin, TtsLifecycleMixin):
    def __init__(self):
        self._init_tts_lifecycle_state()
        self.tts_thread = None
        self.tts_request_queue = Queue()
        self.tts_response_queue = Queue()
        self.tts_handler_task = None
        self.tts_ready = False
        self.tts_cache_lock = asyncio.Lock()
        self.tts_pending_chunks = []
        self._speech_output_total = 0


def install(manager, release):
    manager.tts_request_queue = Queue()
    manager.tts_response_queue = Queue()
    manager.tts_thread = Thread(target=release.wait, daemon=True)
    manager.tts_thread.start()
    manager._tts_runtime = None
    return manager._snapshot_tts_runtime()


@pytest.mark.asyncio
async def test_retired_live_workers_keep_both_slots_until_real_exit():
    manager = Manager()
    releases = [Event(), Event()]
    first = install(manager, releases[0])
    manager._retire_tts_runtime(first)
    second = install(manager, releases[1])
    manager._retire_tts_runtime(second)
    try:
        assert manager._live_tts_runtime_count() == 2
        with pytest.raises(TtsCapacityError):
            await manager._wait_tts_capacity(asyncio.get_running_loop().time() + 0.03)
        assert not first.cleanup_complete.is_set()
        assert not second.cleanup_complete.is_set()
        releases[0].set()
        await asyncio.wait_for(first.cleanup_complete.wait(), 1)
        await manager._wait_tts_capacity(asyncio.get_running_loop().time() + 0.1)
        assert manager._live_tts_runtime_count() == 1
    finally:
        for release in releases:
            release.set()
        await asyncio.gather(first.cleanup_task, second.cleanup_task)


@pytest.mark.asyncio
async def test_cancelling_teardown_caller_does_not_cancel_owned_cleanup():
    manager = Manager()
    release = Event()
    runtime = install(manager, release)
    caller = asyncio.create_task(manager._teardown_tts_runtime(
        None, runtime.thread, runtime.request_queue, runtime.response_queue
    ))
    await asyncio.sleep(0)
    caller.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert runtime.retired
        assert not runtime.cleanup_task.cancelled()
        assert manager._live_tts_runtime_count() == 1
    finally:
        release.set()
        await asyncio.gather(runtime.cleanup_task, return_exceptions=True)
    assert runtime.cleanup_complete.is_set()


@pytest.mark.asyncio
async def test_old_ready_waiting_for_cache_lock_cannot_publish_to_new_runtime():
    manager = Manager()
    releases = [Event(), Event()]
    old = install(manager, releases[0])
    old.response_queue.put(("__ready__", True))
    await manager.tts_cache_lock.acquire()
    task = manager._start_tts_response_handler()
    records = [old]
    try:
        for _ in range(30):
            await asyncio.sleep(0.01)
            if old.response_queue.empty():
                break
        assert old.response_queue.empty(), "old handler never consumed the ready event"
        new = install(manager, releases[1])
        records.append(new)
        manager.tts_pending_chunks = [("new", "keep")]
        manager.tts_cache_lock.release()
        await asyncio.wait_for(task, 1)
        assert manager.tts_ready is False
        assert manager.tts_pending_chunks == [("new", "keep")]
        assert new.request_queue.empty()
    finally:
        if manager.tts_cache_lock.locked():
            manager.tts_cache_lock.release()
        for record, release in zip(records, releases):
            manager._retire_tts_runtime(record)
            release.set()
        await asyncio.gather(*(record.cleanup_task for record in records))


@pytest.mark.asyncio
async def test_clear_pipeline_waiting_for_lock_preserves_replacement_cache():
    manager = Manager()
    release = Event()
    old = install(manager, release)
    manager._tts_done_queued_for_turn = False
    manager._tts_done_pending_until_ready = False
    manager._cancel_tts_soft_flush = lambda: None
    manager._cancel_game_speech_completion_wait = lambda: None
    manager._clear_game_speech_correlation = lambda: None
    manager._reset_tts_stream_normalizer = lambda: None
    await manager.tts_cache_lock.acquire()
    task = asyncio.create_task(manager._clear_tts_pipeline())
    await asyncio.sleep(0.03)
    manager.tts_request_queue = Queue()
    manager.tts_response_queue = Queue()
    manager.tts_thread = None
    manager._tts_runtime = None
    manager.tts_pending_chunks = [("new", "keep")]
    manager.tts_cache_lock.release()
    try:
        await task
        assert manager.tts_pending_chunks == [("new", "keep")]
    finally:
        manager._retire_tts_runtime(old)
        release.set()
        await old.cleanup_task


@pytest.mark.asyncio
async def test_nonoverlapping_worker_waits_for_real_retired_exit():
    manager = Manager()
    release = Event()
    runtime = install(manager, release)
    runtime.supports_runtime_overlap = False
    manager._retire_tts_runtime(runtime)
    try:
        with pytest.raises(TtsCapacityError):
            await manager._wait_tts_capacity(asyncio.get_running_loop().time() + 0.03)
        release.set()
        await runtime.cleanup_task
        await manager._wait_tts_capacity(asyncio.get_running_loop().time() + 0.1)
    finally:
        release.set()
        await runtime.cleanup_task


@pytest.mark.asyncio
async def test_fallback_cannot_create_third_worker(monkeypatch):
    from main_logic.core import tts_runtime as module

    manager = Manager()
    releases = [Event(), Event()]
    first = install(manager, releases[0])
    manager._retire_tts_runtime(first)
    second = install(manager, releases[1])
    manager._tts_active_provider_key = "configured"
    manager._tts_excluded_provider_keys = frozenset()
    monkeypatch.setattr(module._core_facade, "tts_provider_falls_back_on_failure", lambda _: True)
    try:
        with pytest.raises(TtsCapacityError):
            manager._activate_configured_tts_fallback("test")
        assert manager._live_tts_runtime_count() == 2
        assert second.request_queue.empty()
        assert manager._tts_capacity_exhausted
    finally:
        manager._retire_tts_runtime(second)
        for release in releases:
            release.set()
        await asyncio.gather(first.cleanup_task, second.cleanup_task)


@pytest.mark.asyncio
async def test_old_runtime_audio_waiting_for_frame_lock_is_not_sent():
    from tests.unit.test_tts_audio_done_forward import _RecordingWebsocket

    manager = Manager()
    release = Event()
    old = install(manager, release)
    manager.websocket = _RecordingWebsocket()
    manager.current_speech_id = "old"
    manager.sync_message_queue = Queue()
    await manager._ensure_audio_frame_send_lock().acquire()
    token = tts_output_runtime.set(old)
    try:
        sending = asyncio.create_task(manager.send_speech(b"obsolete", "old"))
    finally:
        tts_output_runtime.reset(token)
    await asyncio.sleep(0)
    manager._retire_tts_runtime(old)
    manager._ensure_audio_frame_send_lock().release()
    try:
        assert await sending is False
        assert manager.websocket.calls == []
    finally:
        release.set()
        await old.cleanup_task


@pytest.mark.asyncio
async def test_native_start_retires_orphan_tts_without_waiting_for_thread_exit():
    from main_logic.core.lifecycle import LifecycleMixin

    manager = Manager()
    release = Event()
    runtime = install(manager, release)
    manager.use_tts = False
    manager._check_start_operation = lambda: None
    try:
        assert await LifecycleMixin._start_session_start_tts_if_needed(manager)
        assert runtime.retired
        assert runtime.shutdown_sent
        assert not runtime.cleanup_complete.is_set()
    finally:
        release.set()
        await runtime.cleanup_task


@pytest.mark.asyncio
async def test_retirement_during_audio_frame_keeps_header_and_payload_together():
    from tests.unit.test_tts_audio_done_forward import _RecordingWebsocket

    entered = asyncio.Event()
    release = asyncio.Event()

    class Socket(_RecordingWebsocket):
        async def send_bytes(self, payload):
            entered.set()
            await release.wait()
            await super().send_bytes(payload)

    manager = Manager()
    manager.websocket = Socket()
    manager.sync_message_queue = Queue()
    manager.current_speech_id = "old"
    sending = asyncio.create_task(manager.send_speech(b"pcm", "old"))
    await entered.wait()
    sending.cancel()
    await asyncio.sleep(0)
    assert not sending.done()
    assert manager._ensure_audio_frame_send_lock().locked()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await sending
    assert manager.websocket.calls == [
        ("json", {"type": "audio_chunk", "speech_id": "old"}),
        ("bytes", b"pcm"),
    ]
    assert not manager._ensure_audio_frame_send_lock().locked()


@pytest.mark.asyncio
async def test_runtime_handoff_drains_status_blocked_inside_websocket_send():
    from main_logic.core.notify import NotifyMixin
    from tests.unit.test_tts_audio_done_forward import _RecordingWebsocket

    entered = asyncio.Event()
    cancelled = asyncio.Event()
    release_status = asyncio.Event()

    class Socket(_RecordingWebsocket):
        async def send_text(self, payload):
            entered.set()
            # Model a transport write that cannot abort after accepting bytes.
            while not release_status.is_set():
                try:
                    await release_status.wait()
                except asyncio.CancelledError:
                    cancelled.set()
            self.calls.append(("status", payload))

    class StatusManager(Manager, NotifyMixin):
        def _fire_task(self, coro):
            task = asyncio.create_task(coro)
            self.dispatched.add(task)
            return task

    manager = StatusManager()
    manager.dispatched = set()
    manager.websocket = Socket()
    manager.sync_message_queue = Queue()
    release_thread = Event()
    runtime = install(manager, release_thread)
    runtime.response_queue.put(("__warning__", '{"code":"TTS_RECONNECTING"}'))
    handler = manager._start_tts_response_handler()
    await asyncio.wait_for(entered.wait(), 1)
    manager._retire_tts_runtime(runtime)
    try:
        # The handler itself must own the write cancellation. A detached
        # notification leaves this event unset and incorrectly releases handoff.
        cancellation_seen = asyncio.create_task(cancelled.wait())
        handoff_seen = asyncio.create_task(runtime.handoff_safe.wait())
        await asyncio.wait(
            {cancellation_seen, handoff_seen}, timeout=1,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for observation in (cancellation_seen, handoff_seen):
            observation.cancel()
        await asyncio.gather(cancellation_seen, handoff_seen, return_exceptions=True)
        assert cancelled.is_set(), "retirement left a detached status transport uncancelled"
        assert not runtime.handoff_safe.is_set()
        assert not handler.done()
        release_status.set()
        await asyncio.wait_for(runtime.handoff_safe.wait(), 1)
        assert len(manager.websocket.calls) == 1
        assert manager.sync_message_queue.empty(), "retired status cannot mirror after delivery"
        manager.websocket.calls.append(("new_session", None))
        await asyncio.sleep(0)
        assert manager.websocket.calls[-1] == ("new_session", None)
    finally:
        release_status.set()
        release_thread.set()
        await asyncio.gather(handler, *manager.dispatched, return_exceptions=True)
        await runtime.cleanup_task


@pytest.mark.asyncio
async def test_retirement_keeps_wakeup_until_real_queue_consumer_exits():
    consumer_entered = Event()
    allow_consume = Event()
    consumer_exited = Event()

    class PausedQueue(Queue):
        def get(self, block=True, timeout=None):
            if block:
                consumer_entered.set()
                allow_consume.wait()
            result = super().get(block=block, timeout=timeout)
            if block:
                consumer_exited.set()
            return result

    manager = Manager()
    release_thread = Event()
    runtime = install(manager, release_thread)
    runtime.response_queue = manager.tts_response_queue = PausedQueue()
    handler = manager._start_tts_response_handler()
    await asyncio.to_thread(consumer_entered.wait)
    manager._retire_tts_runtime(runtime)
    release_thread.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    try:
        assert not handler.done(), "handler returned while its executor consumer was still alive"
        assert not runtime.cleanup_complete.is_set()
        allow_consume.set()
        await asyncio.wait_for(runtime.cleanup_task, 1)
        assert consumer_exited.is_set()
        assert handler.done()
    finally:
        allow_consume.set()
        # Ensure a failed mutant also releases the real executor thread.
        runtime.response_queue.put(("__handler_exit__", None))
        await asyncio.gather(handler, runtime.cleanup_task, return_exceptions=True)
