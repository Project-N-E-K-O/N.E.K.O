"""Strict-review probes: cancelled policy cleanup must not replay rejected text."""

import asyncio
from queue import Empty, Queue
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from main_logic.core.proactive import ProactiveMixin
from main_logic.core.tts_runtime import TtsRuntimeMixin
from tests.unit.test_core_game_route_memory_contract import _make_manager


class ObservedLock:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.waiting = asyncio.Event()
        self.fail = False

    async def __aenter__(self):
        self.waiting.set()
        await self.lock.acquire()
        if self.fail:
            self.lock.release()
            raise RuntimeError("injected cache-lock failure")

    async def __aexit__(self, *_args):
        self.lock.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["interrupt_wait", "cache_lock"])
@pytest.mark.parametrize("entry,termination", [
    ("policy", "cancel"), ("existing_clear", "cancel"),
    ("policy", "repeat_cancel"), ("policy", "exception"),
    ("policy", "takeover"),
])
async def test_policy_retirement_survives_interrupted_cleanup_and_preserves_next_turn(monkeypatch, boundary, entry, termination):
    manager = _make_manager()
    manager.tts_request_queue = Queue()
    manager.tts_response_queue = Queue()
    manager.tts_pending_chunks = []
    manager.tts_cache_lock = lock = ObservedLock()
    manager.use_tts = True
    manager.tts_ready = False
    manager._tts_done_pending_until_ready = True
    stop = Event()
    observed_interrupt = Event()
    observed_barrier = Event()
    submitted = []

    def worker():
        while not stop.is_set():
            try:
                item = manager.tts_request_queue.get(timeout=0.01)
            except Empty:
                continue
            if item[0] == "__interrupt__":
                observed_interrupt.set()
            elif item[0] == "__review_barrier__":
                observed_barrier.set()
            else:
                submitted.append(item)

    manager.tts_thread = Thread(target=worker, daemon=True)
    manager.tts_thread.start()
    queued = await ProactiveMixin.feed_tts_chunk(
        manager, "Rejected pending recommendation", expected_speech_id=manager.current_speech_id,
        delivery_allowed=lambda: True,
    )
    assert queued and manager.tts_pending_chunks
    rejected_speech_id = manager.current_speech_id
    manager._remember_tts_replay_chunk(rejected_speech_id, "Rejected replay text")
    manager.tts_response_queue.put((manager.current_speech_id, b"queued synthetic PCM"))
    release_sleep = asyncio.Event()
    if boundary == "interrupt_wait":
        entered = asyncio.Event()

        async def sleep(_seconds):
            entered.set()
            await release_sleep.wait()
            if termination == "exception":
                raise RuntimeError("injected interrupt-wait failure")

        from main_logic.core import tts_runtime
        monkeypatch.setattr(tts_runtime, "asyncio", SimpleNamespace(sleep=sleep))
    else:
        # The real _let_tts_interrupt_land wait runs before the real cache-lock
        # acquisition. Only the lock exposes an observation, not a stub clear.
        await lock.lock.acquire()
        entered = lock.waiting
        lock.waiting.clear()

    clear = (
        ProactiveMixin.discard_proactive_delivery(manager, expected_speech_id=manager.current_speech_id)
        if entry == "policy" else TtsRuntimeMixin._clear_tts_pipeline(manager)
    )
    task = asyncio.create_task(clear)
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert await asyncio.to_thread(observed_interrupt.wait, 2)
        if termination == "exception":
            lock.fail = boundary == "cache_lock"
            release_sleep.set()
            if lock.lock.locked():
                lock.lock.release()
            with pytest.raises(RuntimeError, match="injected"):
                await asyncio.wait_for(task, 2)
            lock.fail = False
        elif termination == "takeover":
            # These writes represent the new turn's synchronous cache section.
            # The old cleanup must preserve all three, even after lock waiting.
            manager.current_speech_id = "new-user-speech"
            manager.tts_pending_chunks.append((manager.current_speech_id, "New user reply"))
            manager._tts_done_pending_until_ready = True
            manager._remember_tts_replay_chunk(manager.current_speech_id, "New user reply")
            release_sleep.set()
            if lock.lock.locked():
                lock.lock.release()
            await asyncio.wait_for(task, 2)
        else:
            task.cancel()
            if termination == "repeat_cancel":
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert task.cancelling() == (2 if termination == "repeat_cancel" else 1)
        assert not manager._tts_done_queued_for_turn
        assert manager.tts_response_queue.empty()
        if lock.lock.locked():
            lock.lock.release()
        pending_after_cancel = list(manager.tts_pending_chunks)
        if entry == "policy":
            assert all(sid != rejected_speech_id for sid, _text in pending_after_cancel)
            if termination == "takeover":
                assert pending_after_cancel == [("new-user-speech", "New user reply")]
                assert manager._tts_done_pending_until_ready
                assert manager._tts_replay_speech_id == "new-user-speech"
            else:
                assert not manager._tts_done_pending_until_ready
                assert manager._tts_replay_speech_id is None
        # A ready notification is a normal later event. Keep the real pending
        # flush and text enqueue protocol, rather than mocking those decisions.
        manager.tts_ready = True
        await TtsRuntimeMixin._flush_tts_pending_chunks(manager)
        assert not manager.tts_pending_chunks
        manager.tts_request_queue.put(("__review_barrier__", None))
        assert await asyncio.to_thread(observed_barrier.wait, 2)
        if entry == "existing_clear":
            # Baseline-control evidence: the old clear path shares the gap.
            assert pending_after_cancel
            assert any(text == "Rejected pending recommendation" for _speech_id, text in submitted)
        else:
            assert not any(sid == rejected_speech_id for sid, _text in submitted)
            if termination == "takeover":
                assert ("new-user-speech", "New user reply") in submitted
            # The cancelled cleanup must also leave the real lock and done
            # protocol usable by the following turn, without a replacement worker.
            manager.current_speech_id = "following-turn"
            manager._interrupt_tts_now()
            assert await asyncio.wait_for(ProactiveMixin.feed_tts_chunk(
                manager, "Following turn reply", expected_speech_id="following-turn",
            ), 2)
            async with lock:
                assert manager._request_tts_done_locked() == "queued"
            observed_barrier.clear()
            manager.tts_request_queue.put(("__review_barrier__", None))
            assert await asyncio.to_thread(observed_barrier.wait, 2)
            assert ("following-turn", "Following turn reply") in submitted
            assert submitted[-1] == (None, None)
    finally:
        release_sleep.set()
        if lock.lock.locked():
            lock.lock.release()
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
        stop.set()
        await asyncio.to_thread(manager.tts_thread.join, 2)
        assert not manager.tts_thread.is_alive()
