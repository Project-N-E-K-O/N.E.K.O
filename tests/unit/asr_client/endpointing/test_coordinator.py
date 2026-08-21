import asyncio
import threading

import numpy as np
import pytest

from main_logic.voice_turn.contracts import (
    EvaluationStatus,
    SpeechActivityEvent,
    TurnDecision,
)
from main_logic.asr_client.endpointing.config import SmartTurnConfig
from main_logic.asr_client.endpointing.coordinator import CoordinatorState, TurnCoordinator


def _pcm(sample_count=512):
    return np.ones(sample_count, dtype=np.int16).tobytes()


class _Predictor:
    def __init__(self, probability=0.8, *, available=True):
        self.probability = probability
        self.available = available
        self.calls = 0
        self.closed = False

    def load(self):
        return self.available

    def predict_probability(self, audio):
        self.calls += 1
        return self.probability

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_complete_and_incomplete_are_semantic_only():
    complete = TurnCoordinator(_Predictor(0.5), SmartTurnConfig(enabled=True))
    result = await complete.evaluate(_pcm())
    assert (result.status, result.decision) == (EvaluationStatus.OK, TurnDecision.COMPLETE)

    incomplete = TurnCoordinator(_Predictor(0.49), SmartTurnConfig(enabled=True))
    result = await incomplete.evaluate(_pcm())
    assert result.decision is TurnDecision.INCOMPLETE
    assert incomplete.state is CoordinatorState.WAIT_CONTINUATION


@pytest.mark.asyncio
async def test_unavailable_is_not_converted_to_incomplete():
    coordinator = TurnCoordinator(_Predictor(available=False), SmartTurnConfig(enabled=True))
    result = await coordinator.evaluate(_pcm())
    assert result.status is EvaluationStatus.UNAVAILABLE
    assert result.decision is None


class _BlockingPredictor(_Predictor):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self._active_lock = threading.Lock()
        self._active_calls = 0
        self.max_active_calls = 0

    def predict_probability(self, audio):
        with self._active_lock:
            self.calls += 1
            self._active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self._active_calls)
        self.started.set()
        try:
            assert self.release.wait(timeout=5)
            return self.probability
        finally:
            with self._active_lock:
                self._active_calls -= 1


@pytest.mark.asyncio
async def test_resumed_speech_makes_inflight_result_stale():
    predictor = _BlockingPredictor()
    coordinator = TurnCoordinator(predictor, SmartTurnConfig(enabled=True))
    task = asyncio.create_task(coordinator.evaluate(_pcm()))
    await asyncio.to_thread(predictor.started.wait, 2)
    await coordinator.on_activity_event(SpeechActivityEvent.SPEECH_RESUMED)
    predictor.release.set()
    result = await task
    assert result.status is EvaluationStatus.STALE


@pytest.mark.asyncio
async def test_latest_candidate_coalesces_without_parallel_inference():
    predictor = _BlockingPredictor()
    coordinator = TurnCoordinator(predictor, SmartTurnConfig(enabled=True))
    first = asyncio.create_task(coordinator.evaluate(_pcm(256)))
    await asyncio.to_thread(predictor.started.wait, 2)
    second = asyncio.create_task(coordinator.evaluate(_pcm(512)))
    await asyncio.sleep(0)
    predictor.release.set()
    first_result, second_result = await asyncio.gather(first, second)
    assert first_result.status is EvaluationStatus.STALE
    assert second_result.status is EvaluationStatus.OK
    assert predictor.calls == 2
    assert predictor.max_active_calls == 1


@pytest.mark.asyncio
async def test_close_invalidates_late_result_and_releases_runtime():
    predictor = _BlockingPredictor()
    coordinator = TurnCoordinator(predictor, SmartTurnConfig(enabled=True))
    task = asyncio.create_task(coordinator.evaluate(_pcm()))
    await asyncio.to_thread(predictor.started.wait, 2)
    close_task = asyncio.create_task(coordinator.close())
    predictor.release.set()
    result, _ = await asyncio.gather(task, close_task)
    assert result.status is EvaluationStatus.STALE
    assert coordinator.state is CoordinatorState.CLOSED
    assert predictor.closed is True


@pytest.mark.asyncio
async def test_cancelled_evaluation_keeps_lane_until_thread_finishes():
    predictor = _BlockingPredictor()
    coordinator = TurnCoordinator(predictor, SmartTurnConfig(enabled=True))
    evaluation = asyncio.create_task(coordinator.evaluate(_pcm()))
    await asyncio.to_thread(predictor.started.wait, 2)

    evaluation.cancel()
    await asyncio.sleep(0)
    close_task = asyncio.create_task(coordinator.close())
    await asyncio.sleep(0)

    assert evaluation.done() is False
    assert close_task.done() is False
    predictor.release.set()
    with pytest.raises(asyncio.CancelledError):
        await evaluation
    await asyncio.wait_for(close_task, 1)
    assert coordinator.state is CoordinatorState.CLOSED
    assert predictor.closed is True
    assert predictor.max_active_calls == 1


@pytest.mark.asyncio
async def test_cancelled_close_keeps_cleanup_owned_until_lane_is_drained():
    predictor = _Predictor()
    coordinator = TurnCoordinator(predictor, SmartTurnConfig(enabled=True))
    await coordinator._evaluation_lock.acquire()
    close_impl_started = asyncio.Event()
    original_close_impl = coordinator._close_impl

    async def observed_close_impl() -> None:
        close_impl_started.set()
        await original_close_impl()

    coordinator._close_impl = observed_close_impl

    close_task = asyncio.create_task(coordinator.close())
    await close_impl_started.wait()
    close_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close_task

    assert coordinator.state is CoordinatorState.CLOSED
    assert predictor.closed is False

    coordinator._evaluation_lock.release()
    await asyncio.wait_for(coordinator._close_task, 1)
    assert predictor.closed is True


class _SlowUnloadPredictor(_Predictor):
    def __init__(self):
        super().__init__()
        self.session_ready = True
        self.unload_started = threading.Event()
        self.unload_release = threading.Event()
        self.events = []

    def load(self):
        self.events.append("load")
        self.session_ready = True
        return True

    def unload(self):
        self.unload_started.set()
        assert self.unload_release.wait(timeout=5)
        self.session_ready = False
        self.events.append("unload_finished")
        return True


@pytest.mark.asyncio
async def test_cancelled_unload_keeps_lane_until_thread_finishes():
    predictor = _SlowUnloadPredictor()
    coordinator = TurnCoordinator(predictor, SmartTurnConfig(enabled=True))
    unload_task = asyncio.create_task(coordinator.unload_predictor())
    await asyncio.to_thread(predictor.unload_started.wait, 2)

    unload_task.cancel()
    await asyncio.sleep(0)
    prepare_task = asyncio.create_task(coordinator.prepare_predictor())
    await asyncio.sleep(0.05)

    # Cancellation must not release the lane while unload() still runs in its
    # thread; otherwise prepare observes a session the detached thread clears.
    assert unload_task.done() is False
    assert prepare_task.done() is False

    predictor.unload_release.set()
    with pytest.raises(asyncio.CancelledError):
        await unload_task
    assert await asyncio.wait_for(prepare_task, 1) is True
    assert predictor.events == ["unload_finished", "load"]
    assert predictor.session_ready is True


@pytest.mark.asyncio
async def test_unload_predictor_normal_path_unchanged():
    predictor = _SlowUnloadPredictor()
    predictor.unload_release.set()
    coordinator = TurnCoordinator(predictor, SmartTurnConfig(enabled=True))
    await coordinator.unload_predictor()
    assert predictor.events == ["unload_finished"]
    assert predictor.session_ready is False
    # A predictor without an unload hook keeps unload_predictor a no-op.
    await TurnCoordinator(_Predictor(), SmartTurnConfig(enabled=True)).unload_predictor()


@pytest.mark.asyncio
async def test_reset_during_inflight_evaluation_is_stale():
    predictor = _BlockingPredictor()
    coordinator = TurnCoordinator(predictor, SmartTurnConfig(enabled=True))
    task = asyncio.create_task(coordinator.evaluate(_pcm()))
    await asyncio.to_thread(predictor.started.wait, 2)
    await coordinator.reset()
    predictor.release.set()
    result = await task
    assert result.status is EvaluationStatus.STALE
    assert coordinator.generation == 1
    assert coordinator.state is CoordinatorState.IDLE


@pytest.mark.asyncio
async def test_empty_audio_is_unavailable_not_incomplete():
    coordinator = TurnCoordinator(_Predictor(), SmartTurnConfig(enabled=True))
    result = await coordinator.evaluate(b"")
    assert result.status is EvaluationStatus.UNAVAILABLE
    assert result.decision is None


@pytest.mark.asyncio
async def test_buffered_audio_evaluation_uses_bounded_buffer():
    predictor = _Predictor(0.8)
    coordinator = TurnCoordinator(predictor, SmartTurnConfig(enabled=True))
    coordinator.push_audio(_pcm())
    result = await coordinator.evaluate_buffered()
    assert result.status is EvaluationStatus.OK
    assert result.decision is TurnDecision.COMPLETE
    assert predictor.calls == 1


@pytest.mark.asyncio
async def test_close_is_idempotent():
    predictor = _Predictor()
    coordinator = TurnCoordinator(predictor, SmartTurnConfig(enabled=True))
    await coordinator.close()
    await coordinator.close()
    assert coordinator.state is CoordinatorState.CLOSED
    assert predictor.closed is True


async def test_cancelled_predictor_call_reports_cancellation_not_the_worker_error():
    # Codex P2. The loop caught only CancelledError, so a worker thread that
    # raised AFTER the call was cancelled propagated its own exception straight
    # out and skipped the cancelled branch entirely: the caller saw an
    # inference/runtime error instead of the cancellation it asked for, which
    # misclassifies reset and teardown work -- a cancelled idle unload surfaced
    # as a runtime failure.

    started = asyncio.Event()
    release = threading.Event()

    def _worker():
        started.set()
        # Block until the test has cancelled the awaiting task, then fail.
        release.wait(5)
        raise RuntimeError("predictor blew up after cancellation")

    task = asyncio.create_task(TurnCoordinator._run_predictor_call(_worker))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    # Let the cancellation land while the thread is still running...
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_uncancelled_predictor_failure_still_reports_the_worker_error():
    # The other side: with no cancellation recorded, the worker's own exception
    # must reach the caller unchanged.

    def _worker():
        raise RuntimeError("plain predictor failure")

    with pytest.raises(RuntimeError, match="plain predictor failure"):
        await TurnCoordinator._run_predictor_call(_worker)
