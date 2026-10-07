"""Bounded transport failure and queue cancellation during TTS handoff."""

import asyncio
from queue import Queue
from threading import Event
from unittest.mock import MagicMock

import pytest

from main_logic.core import tts_runtime as runtime_module
from tests.unit.test_tts_handoff_ownership import Manager, install
from tests.unit.test_tts_audio_done_forward import _RecordingWebsocket


@pytest.mark.asyncio
@pytest.mark.parametrize("startup_path", ["ensure", "native"])
async def test_startup_handler_stop_respects_deadline_and_retains_cleanup(startup_path):
    from main_logic.core.lifecycle import LifecycleMixin

    entered = asyncio.Event()
    release = asyncio.Event()
    manager = Manager()
    release_worker = Event()
    runtime = install(manager, release_worker)
    release_worker.set()
    await asyncio.to_thread(runtime.thread.join)

    async def handler():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    task = asyncio.create_task(handler())
    manager.tts_handler_task = runtime.handler = task
    manager._tts_handler_response_queue = runtime.response_queue
    await entered.wait()
    deadline = asyncio.get_running_loop().time() + 0.13
    manager.use_tts = False
    manager._check_start_operation = lambda: None
    manager._current_start_deadline = lambda: deadline + 0.1
    try:
        starting = (
            manager.ensure_tts_pipeline_alive(deadline=deadline)
            if startup_path == "ensure"
            else LifecycleMixin._start_session_start_tts_if_needed(manager)
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(starting, 0.5)
        assert asyncio.get_running_loop().time() < deadline + 0.15
        assert runtime.retired
        assert not task.done()
        assert runtime.cleanup_task is not None
        release.set()
        await asyncio.wait_for(runtime.cleanup_task, 1)
        assert runtime.cleanup_complete.is_set()
    finally:
        release.set()
        manager._retire_tts_runtime(runtime)
        await asyncio.gather(task, runtime.cleanup_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_timed_out_handler_does_not_block_successor_tts_start():
    from main_logic.core.lifecycle import LifecycleMixin

    manager = Manager()
    release_worker, release_next_worker = Event(), Event()
    old = install(manager, release_worker)
    release_worker.set()
    await asyncio.to_thread(old.thread.join)
    entered, release_handler = asyncio.Event(), asyncio.Event()

    async def handler():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release_handler.wait()

    old_handler = asyncio.create_task(handler())
    manager.tts_handler_task = old.handler = old_handler
    manager._tts_handler_response_queue = old.response_queue
    manager.use_tts = False
    manager._check_start_operation = lambda: None
    manager._current_start_deadline = lambda: asyncio.get_running_loop().time() + 0.13
    await entered.wait()
    successor = None
    try:
        with pytest.raises(TimeoutError):
            await LifecycleMixin._start_session_start_tts_if_needed(manager)
        assert old.retired and not old_handler.done()
        assert old.handler is old_handler
        assert old.cleanup_task is not None and not old.cleanup_task.done()
        manager.use_tts = True
        manager._start_tts_thread = lambda **kwargs: install(manager, release_next_worker)
        manager._start_tts_response_handler = MagicMock()
        await manager.ensure_tts_pipeline_alive(deadline=asyncio.get_running_loop().time() + 0.1)
        successor = manager._tts_runtime
        assert successor is not old and not successor.retired
        manager._start_tts_response_handler.assert_called_once()
        assert not old_handler.done(), "successor startup must not join retired handler cleanup"
        release_handler.set()
        await asyncio.wait_for(old.cleanup_task, 1)
    finally:
        release_handler.set()
        release_worker.set()
        release_next_worker.set()
        if successor is not None:
            manager._retire_tts_runtime(successor)
        await asyncio.gather(old_handler, *manager._tts_cleanup_tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_part", ["header", "payload"])
async def test_stalled_frame_closes_only_captured_socket_and_releases_lock(monkeypatch, blocked_part):
    entered = asyncio.Event()
    stopped = asyncio.Event()
    release = asyncio.Event()

    class StalledSocket(_RecordingWebsocket):
        def __init__(self):
            super().__init__()
            self.closed = []

        async def stall(self):
            entered.set()
            try:
                await release.wait()
            finally:
                stopped.set()

        async def send_json(self, data):
            if blocked_part == "header":
                await self.stall()
            await super().send_json(data)

        async def send_bytes(self, data):
            if blocked_part == "payload":
                await self.stall()
            await super().send_bytes(data)

        async def close(self, *, code):
            self.closed.append(code)

    monkeypatch.setattr(runtime_module, "TTS_FRAME_WRITE_TIMEOUT_SECONDS", 0.05, raising=False)
    manager = Manager()
    old = StalledSocket()
    successor = StalledSocket()
    manager.websocket = old
    manager.sync_message_queue = Queue()
    sending = asyncio.create_task(manager.send_speech(b"pcm", "old"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        manager.websocket = successor
        done, _ = await asyncio.wait({sending}, timeout=0.5)
        assert sending in done, "stalled frame kept the shared audio lock indefinitely"
        assert await sending is False
        assert stopped.is_set()
        assert old.closed == [1011]
        assert successor.closed == []
        assert manager.sync_message_queue.empty()
        assert not manager._ensure_audio_frame_send_lock().locked()
    finally:
        # Also release the unbounded baseline writer when a regression fails.
        release.set()
        sending.cancel()
        await asyncio.gather(sending, return_exceptions=True)


@pytest.mark.asyncio
async def test_stalled_frame_retirement_waits_for_writer_then_reclaims_runtime(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    closed = asyncio.Event()

    class StalledSocket(_RecordingWebsocket):
        async def send_bytes(self, data):
            entered.set()
            await release.wait()

        async def close(self, *, code):
            assert code == 1011
            closed.set()

    monkeypatch.setattr(runtime_module, "TTS_FRAME_WRITE_TIMEOUT_SECONDS", 0.05)
    manager = Manager()
    manager.websocket = StalledSocket()
    manager.sync_message_queue = Queue()
    release_worker = Event()
    runtime = install(manager, release_worker)
    runtime.response_queue.put(("__audio__", "old", b"pcm"))
    handler = manager._start_tts_response_handler()
    try:
        await asyncio.wait_for(entered.wait(), 1)
        manager._retire_tts_runtime(runtime)
        await asyncio.sleep(0)
        assert not runtime.handoff_safe.is_set()
        assert manager._ensure_audio_frame_send_lock().locked()
        await asyncio.wait_for(runtime.handoff_safe.wait(), 0.5)
        assert closed.is_set()
        assert handler.done()
        assert not manager._ensure_audio_frame_send_lock().locked()
        assert manager.sync_message_queue.empty()
        assert not runtime.cleanup_complete.is_set()
        assert manager._live_tts_runtime_count() == 1
        release_worker.set()
        await asyncio.wait_for(runtime.cleanup_task, 1)
        assert runtime.cleanup_complete.is_set()
        assert manager._live_tts_runtime_count() == 0
    finally:
        release.set()
        release_worker.set()
        manager._retire_tts_runtime(runtime)
        await asyncio.gather(handler, runtime.cleanup_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_stopping_handler_waits_for_the_frame_write_deadline(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowSocket(_RecordingWebsocket):
        async def send_bytes(self, data):
            entered.set()
            await release.wait()
            await super().send_bytes(data)

    monkeypatch.setattr(runtime_module, "TTS_FRAME_WRITE_TIMEOUT_SECONDS", 2.0)
    manager = Manager()
    manager.websocket = SlowSocket()
    manager.sync_message_queue = Queue()
    release_worker = Event()
    runtime = install(manager, release_worker)
    runtime.response_queue.put(("__audio__", "old", b"pcm"))
    handler = manager._start_tts_response_handler()
    stopping = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        stopping = asyncio.create_task(manager._stop_tts_response_handler())
        await asyncio.sleep(1.1)
        assert not stopping.done(), "handler stop ignored the active frame deadline"
        release.set()
        await asyncio.wait_for(stopping, 1)
        assert handler.done()
    finally:
        release.set()
        release_worker.set()
        manager._retire_tts_runtime(runtime)
        await asyncio.gather(
            handler,
            *(task for task in (stopping, runtime.cleanup_task) if task is not None),
            return_exceptions=True,
        )


@pytest.mark.asyncio
async def test_stopping_handler_covers_frame_close_and_cancel_grace(monkeypatch):
    entered = asyncio.Event()

    class SlowCloseSocket(_RecordingWebsocket):
        def __init__(self):
            super().__init__()
            self.closed_codes = []

        async def send_bytes(self, data):
            entered.set()
            await asyncio.sleep(10)

        async def close(self, *, code):
            await asyncio.sleep(1.45)
            self.closed_codes.append(code)

    monkeypatch.setattr(runtime_module, "TTS_FRAME_WRITE_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(runtime_module, "TTS_SOCKET_CLOSE_TIMEOUT_SECONDS", 1.5)
    monkeypatch.setattr(runtime_module, "TTS_HANDLER_CANCEL_GRACE_SECONDS", 2.0)
    manager = Manager()
    socket = SlowCloseSocket()
    manager.websocket = socket
    manager.sync_message_queue = Queue()

    async def handler_body():
        try:
            await manager._write_audio_frame(
                socket, {"type": "audio_chunk", "speech_id": "old"}, b"pcm"
            )
        except Exception:
            # The real response handler catches transport disconnects after the
            # frame writer has closed the captured socket.
            pass

    handler = asyncio.create_task(handler_body())
    manager.tts_handler_task = handler
    manager._tts_handler_response_queue = manager.tts_response_queue
    stopping = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        stopping = asyncio.create_task(manager._stop_tts_response_handler())
        await asyncio.wait_for(stopping, 5)
        assert handler.done()
        assert socket.closed_codes == [1011]
    finally:
        for task in (stopping, handler):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(stopping, handler, return_exceptions=True)


@pytest.mark.asyncio
async def test_pipeline_clear_cannot_steal_stopping_handlers_wakeup():
    entered, release_consumer, exited = Event(), Event(), Event()

    class PausedQueue(Queue):
        def get(self, block=True, timeout=None):
            if block:
                entered.set()
                release_consumer.wait()
            result = super().get(block=block, timeout=timeout)
            if block:
                exited.set()
            return result

    manager = Manager()
    release_worker = Event()
    runtime = install(manager, release_worker)
    runtime.response_queue = manager.tts_response_queue = PausedQueue()
    manager._cancel_tts_soft_flush = lambda: None
    manager._reset_tts_stream_normalizer = lambda: None
    manager._discard_pending_ai_voice_echo = lambda: None
    handler = manager._start_tts_response_handler()
    stopping = None
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        stopping = asyncio.create_task(manager._stop_tts_response_handler())
        for _ in range(100):
            if not runtime.response_queue.empty():
                break
            await asyncio.sleep(0.001)
        assert not runtime.response_queue.empty(), "handler did not enqueue its wakeup"
        await manager._clear_tts_pipeline()
        assert not runtime.response_queue.empty(), "clear stole the executor wakeup"
        release_consumer.set()
        await asyncio.wait_for(stopping, 1)
        assert exited.is_set()
        manager._retire_tts_runtime(runtime)
        release_worker.set()
        await asyncio.wait_for(runtime.cleanup_task, 1)
        assert runtime.cleanup_complete.is_set()
    finally:
        runtime.response_queue.put(("__handler_exit__", None))
        release_consumer.set()
        release_worker.set()
        manager._retire_tts_runtime(runtime)
        await asyncio.gather(handler, *(t for t in (stopping, runtime.cleanup_task) if t), return_exceptions=True)
