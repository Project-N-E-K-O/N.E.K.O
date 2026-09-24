import asyncio
from queue import Queue
from threading import Event, Thread
from unittest.mock import AsyncMock, MagicMock

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
async def test_handler_retries_fallback_after_retired_worker_releases_capacity():
    manager = Manager()
    releases = [Event(), Event(), Event()]
    first = install(manager, releases[0])
    manager._retire_tts_runtime(first)
    second = install(manager, releases[1])
    manager._tts_active_provider_key = "configured"
    manager._last_tts_error_code = ""
    manager._tts_retry_notify_count = 0
    manager.send_status = AsyncMock()
    fallback_attempted = asyncio.Event()
    fallback_ready = asyncio.Event()
    replacements = []

    def activate(_stage):
        if manager._live_tts_runtime_count() >= 2:
            manager._tts_capacity_exhausted = True
            fallback_attempted.set()
            raise TtsCapacityError("retired worker still occupies capacity")
        manager._retire_tts_runtime(second, stop_handler=False)
        replacement = install(manager, releases[2])
        replacements.append(replacement)
        replacement.response_queue.put(("__ready__", True))
        return True

    async def flush_pending():
        fallback_ready.set()

    manager._activate_configured_tts_fallback = activate
    manager._flush_tts_pending_chunks = flush_pending
    second.response_queue.put(("__ready__", False))
    handler = manager._start_tts_response_handler()
    try:
        await asyncio.wait_for(fallback_attempted.wait(), 1)
        assert not handler.done()
        assert not fallback_ready.is_set()
        releases[0].set()
        await asyncio.wait_for(fallback_ready.wait(), 1)
        assert not manager._tts_capacity_exhausted
        assert manager.tts_ready
        manager.send_status.assert_not_awaited()
    finally:
        handler.cancel()
        await asyncio.gather(handler, return_exceptions=True)
        for runtime in [first, second, *replacements]:
            manager._retire_tts_runtime(runtime)
        for release in releases:
            release.set()
        await asyncio.gather(
            *(runtime.cleanup_task for runtime in [first, second, *replacements]
              if runtime.cleanup_task is not None),
            return_exceptions=True,
        )


@pytest.mark.asyncio
async def test_handler_finishes_nonoverlapping_fallback_after_own_worker_exits():
    manager = Manager()
    releases = [Event(), Event()]
    old = install(manager, releases[0])
    manager._last_tts_error_code = ""
    manager._tts_retry_notify_count = 0
    manager.send_status = AsyncMock()
    fallback_attempted = asyncio.Event()
    fallback_ready = asyncio.Event()
    replacements = []

    def activate(_stage):
        manager._retire_tts_runtime(old, stop_handler=False)
        manager._tts_capacity_exhausted = True
        fallback_attempted.set()
        raise TtsCapacityError("replacement cannot overlap the old worker")

    def start_replacement(*, preserve_provider_exclusions):
        assert preserve_provider_exclusions
        manager._tts_capacity_exhausted = False
        replacement = install(manager, releases[1])
        replacements.append(replacement)
        replacement.response_queue.put(("__ready__", True))

    async def flush_pending():
        fallback_ready.set()

    manager._activate_configured_tts_fallback = activate
    manager._start_tts_thread = start_replacement
    manager._flush_tts_pending_chunks = flush_pending
    old.response_queue.put(("__ready__", False))
    handler = manager._start_tts_response_handler()
    try:
        await asyncio.wait_for(fallback_attempted.wait(), 1)
        assert not handler.done()
        releases[0].set()
        await asyncio.wait_for(fallback_ready.wait(), 1)
        assert not manager._tts_capacity_exhausted
        assert manager.tts_ready
        manager.send_status.assert_not_awaited()
    finally:
        handler.cancel()
        await asyncio.gather(handler, return_exceptions=True)
        for runtime in [old, *replacements]:
            manager._retire_tts_runtime(runtime)
        for release in releases:
            release.set()
        await asyncio.gather(
            *(runtime.cleanup_task for runtime in [old, *replacements]
              if runtime.cleanup_task is not None),
            return_exceptions=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["timeout", "takeover"])
async def test_fallback_capacity_wait_preserves_cleanup_and_session_owner(outcome):
    manager = Manager()
    releases = [Event(), Event()]
    first = install(manager, releases[0])
    manager._retire_tts_runtime(first)
    second = install(manager, releases[1])
    manager.session = object()
    manager.use_tts = True
    attempted = asyncio.Event()
    attempts = 0

    def activate(_stage):
        nonlocal attempts
        attempts += 1
        attempted.set()
        raise TtsCapacityError("older worker still alive")

    manager._activate_configured_tts_fallback = activate
    if outcome == "timeout":
        manager._current_start_deadline = (
            lambda: asyncio.get_running_loop().time() + 0.03
        )
    token = tts_output_runtime.set(second)
    try:
        task = asyncio.create_task(
            manager._activate_configured_tts_fallback_after_capacity(
                "test", second
            )
        )
        await asyncio.wait_for(attempted.wait(), 1)
        if outcome == "timeout":
            with pytest.raises(TtsCapacityError):
                await asyncio.wait_for(task, 1)
            assert first.cleanup_task is not None
            assert not first.cleanup_task.cancelled()
        else:
            manager.session = object()
            releases[0].set()
            assert await asyncio.wait_for(task, 1) is False
        assert attempts == 1
    finally:
        tts_output_runtime.reset(token)
        manager._retire_tts_runtime(second)
        for release in releases:
            release.set()
        await asyncio.gather(first.cleanup_task, second.cleanup_task)


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_session", [False, True])
async def test_respawn_capacity_failure_retries_only_for_its_session(replace_session):
    """Capacity release restarts the worker unless another session took over."""
    manager = Manager()
    releases = [Event(), Event()]
    first = install(manager, releases[0])
    manager._retire_tts_runtime(first)
    second = install(manager, releases[1])
    manager._retire_tts_runtime(second)
    manager._tts_runtime = None
    manager.tts_thread = None
    manager.session = object()
    manager.use_tts = True
    manager.is_active = True
    manager._tts_capacity_exhausted = False
    manager._last_tts_error_code = None
    manager._last_tts_respawn_time = 0.0
    manager._tts_respawn_task = None
    manager._tts_excluded_provider_keys = frozenset()
    started = asyncio.Event()
    new_release = Event()

    def start_worker(*, preserve_provider_exclusions):
        if manager._live_tts_runtime_count() >= 2:
            raise TtsCapacityError("retired workers still occupy capacity")
        manager.tts_request_queue = Queue()
        manager.tts_response_queue = Queue()
        manager.tts_thread = Thread(target=new_release.wait, daemon=True)
        manager.tts_thread.start()
        manager._snapshot_tts_runtime()
        started.set()

    manager._start_tts_thread = MagicMock(side_effect=start_worker)
    manager._start_tts_response_handler = MagicMock()
    try:
        manager._respawn_tts_worker()
        assert not manager._tts_capacity_exhausted
        assert manager._live_tts_runtime_count() == 2
        assert manager._tts_respawn_task is not None

        releases[0].set()
        await asyncio.wait_for(first.cleanup_task, 1)
        assert not started.is_set()
        if replace_session:
            manager.session = object()
        manager._last_tts_respawn_time -= 12.0
        retry_task = manager._tts_respawn_task
        releases[1].set()
        await asyncio.wait_for(retry_task, 1)
        if replace_session:
            assert not started.is_set()
            assert manager._start_tts_thread.call_count == 1
        else:
            assert started.is_set()
            assert manager.tts_thread.is_alive()
            assert manager._start_tts_thread.call_count == 2
            manager._start_tts_response_handler.assert_called_once_with()
    finally:
        for release in releases:
            release.set()
        await asyncio.gather(first.cleanup_task, second.cleanup_task)
        new_release.set()
        if manager.tts_thread is not None:
            await asyncio.to_thread(manager.tts_thread.join, 1)


@pytest.mark.asyncio
async def test_capacity_retry_can_replace_a_retired_dead_current_runtime():
    """The failed admission can retire the dead owner before capacity clears."""
    manager = Manager()
    release = Event()
    blocking = install(manager, release)
    blocking.supports_runtime_overlap = False
    manager._retire_tts_runtime(blocking)

    dead_thread = Thread(target=lambda: None)
    dead_thread.start()
    dead_thread.join()
    manager.tts_thread = dead_thread
    manager.tts_request_queue = Queue()
    manager.tts_response_queue = Queue()
    manager._tts_runtime = None
    dead = manager._snapshot_tts_runtime()
    manager.session = object()
    manager.use_tts = True
    manager.is_active = True
    manager._tts_capacity_exhausted = False
    manager._last_tts_error_code = None
    manager._last_tts_respawn_time = 0.0
    manager._tts_respawn_task = None
    manager._tts_excluded_provider_keys = frozenset()
    started = asyncio.Event()
    new_release = Event()

    def start_worker(*, preserve_provider_exclusions):
        if blocking.thread.is_alive():
            manager._retire_tts_runtime(dead)
            raise TtsCapacityError("exclusive worker still occupies capacity")
        assert tts_output_runtime.get() is None
        manager.tts_request_queue = Queue()
        manager.tts_response_queue = Queue()
        manager.tts_thread = Thread(target=new_release.wait, daemon=True)
        manager.tts_thread.start()
        manager._snapshot_tts_runtime()
        started.set()

    manager._start_tts_thread = MagicMock(side_effect=start_worker)
    manager._start_tts_response_handler = MagicMock()
    token = tts_output_runtime.set(dead)
    try:
        manager._respawn_tts_worker()
    finally:
        tts_output_runtime.reset(token)
    try:
        assert dead.retired
        retry_task = manager._tts_respawn_task
        assert retry_task is not None
        manager._last_tts_respawn_time -= 12.0
        release.set()
        await asyncio.wait_for(retry_task, 1)
        assert started.is_set()
        assert manager._start_tts_thread.call_count == 2
    finally:
        release.set()
        await asyncio.gather(blocking.cleanup_task, dead.cleanup_task)
        new_release.set()
        if manager.tts_thread is not None:
            await asyncio.to_thread(manager.tts_thread.join, 1)


@pytest.mark.asyncio
async def test_cancelled_capacity_retry_keeps_worker_cleanup_owned():
    manager = Manager()
    releases = [Event(), Event()]
    first = install(manager, releases[0])
    manager._retire_tts_runtime(first)
    second = install(manager, releases[1])
    manager._retire_tts_runtime(second)
    manager._tts_runtime = None
    manager.tts_thread = None
    manager._tts_capacity_exhausted = False
    manager._last_tts_error_code = None
    manager._last_tts_respawn_time = 0.0
    manager._tts_respawn_task = None
    manager._tts_excluded_provider_keys = frozenset()
    manager._start_tts_thread = MagicMock(
        side_effect=TtsCapacityError("retired workers still occupy capacity")
    )
    try:
        manager._respawn_tts_worker()
        retry_task = manager._tts_respawn_task
        assert retry_task is not None
        await asyncio.sleep(0)
        retry_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await retry_task
        assert not first.cleanup_task.cancelled()
        assert not second.cleanup_task.cancelled()
    finally:
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
