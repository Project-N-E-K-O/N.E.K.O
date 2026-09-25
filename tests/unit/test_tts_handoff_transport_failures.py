"""Bounded transport failure and queue cancellation during TTS handoff."""

import asyncio
from queue import Queue
from threading import Event

import pytest

from main_logic.core import tts_runtime as runtime_module
from tests.unit.test_tts_handoff_ownership import Manager, install
from tests.unit.test_tts_audio_done_forward import _RecordingWebsocket


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
