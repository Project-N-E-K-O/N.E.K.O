import asyncio
import threading

import numpy as np
import pytest

from main_logic.voice_turn.contracts import (
    EvaluationStatus,
    SmartTurnConfig,
    SpeechActivityEvent,
    TurnDecision,
)
from main_logic.voice_turn.coordinator import CoordinatorState, TurnCoordinator


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

    def predict_probability(self, audio):
        self.calls += 1
        self.started.set()
        assert self.release.wait(timeout=5)
        return self.probability


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
