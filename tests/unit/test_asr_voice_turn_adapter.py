from __future__ import annotations

import asyncio
import inspect
import threading
from collections import deque
from collections.abc import Iterable
from types import SimpleNamespace

import pytest

from main_logic.asr_client._voice_turn import _VoiceTurnAdapter
from main_logic.voice_turn.contracts import (
    EvaluationStatus,
    SpeechActivityEvent,
    TurnDecision,
    TurnEvaluation,
)
from main_logic.voice_turn.coordinator import CoordinatorState


async def _eventually(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not satisfied before timeout")
        await asyncio.sleep(0.001)


def _complete() -> TurnEvaluation:
    return TurnEvaluation(
        EvaluationStatus.OK,
        TurnDecision.COMPLETE,
        0.9,
        generation=0,
        activity_seq=1,
    )


def _incomplete() -> TurnEvaluation:
    return TurnEvaluation(
        EvaluationStatus.OK,
        TurnDecision.INCOMPLETE,
        0.1,
        generation=0,
        activity_seq=1,
    )


def _failed_evaluation(status: EvaluationStatus) -> TurnEvaluation:
    return TurnEvaluation(
        status,
        None,
        None,
        generation=0,
        activity_seq=1,
    )


class _FakeVad:
    def __init__(self, log: list[str] | None = None) -> None:
        self.load_calls = 0
        self.load_thread_ids: list[int] = []
        self.close_calls = 0
        self.log = log

    def load(self) -> bool:
        self.load_calls += 1
        self.load_thread_ids.append(threading.get_ident())
        if self.log is not None:
            self.log.append("vad-load")
        return True

    def close(self) -> None:
        self.close_calls += 1
        if self.log is not None:
            self.log.append("vad-close")


class _UnavailableVad(_FakeVad):
    def load(self) -> bool:
        super().load()
        return False


class _FakeGate:
    def __init__(
        self,
        outputs: Iterable[tuple[SpeechActivityEvent, ...]] = (),
        *,
        log: list[str] | None = None,
    ) -> None:
        self.outputs = deque(outputs)
        self.feed_calls: list[bytes] = []
        self.feed_thread_ids: list[int] = []
        self.reset_calls = 0
        self.log = log

    def feed(self, pcm16: bytes) -> tuple[SpeechActivityEvent, ...]:
        self.feed_calls.append(pcm16)
        self.feed_thread_ids.append(threading.get_ident())
        if self.log is not None:
            self.log.append(f"feed-{len(self.feed_calls) - 1}")
        if self.outputs:
            return self.outputs.popleft()
        return ()

    def reset(self) -> None:
        self.reset_calls += 1
        if self.log is not None:
            self.log.append("gate-reset")


class _BlockingGate(_FakeGate):
    def __init__(
        self,
        outputs: Iterable[tuple[SpeechActivityEvent, ...]] = (),
        *,
        blocked_indices: Iterable[int] = (0,),
        log: list[str] | None = None,
    ) -> None:
        super().__init__(outputs, log=log)
        self.started = {index: threading.Event() for index in blocked_indices}
        self.release = {index: threading.Event() for index in blocked_indices}

    def feed(self, pcm16: bytes) -> tuple[SpeechActivityEvent, ...]:
        index = len(self.feed_calls)
        self.feed_calls.append(pcm16)
        self.feed_thread_ids.append(threading.get_ident())
        if self.log is not None:
            self.log.append(f"feed-{index}-start")
        if index in self.started:
            self.started[index].set()
            assert self.release[index].wait(timeout=5)
        if self.log is not None:
            self.log.append(f"feed-{index}-end")
        if self.outputs:
            return self.outputs.popleft()
        return ()


class _FailingGate(_FakeGate):
    def feed(self, pcm16: bytes) -> tuple[SpeechActivityEvent, ...]:
        del pcm16
        raise RuntimeError("simulated VAD failure")


class _FakeCoordinator:
    def __init__(
        self,
        results: Iterable[TurnEvaluation] = (),
        *,
        block_evaluation: bool = False,
        log: list[str] | None = None,
    ) -> None:
        self.results = deque(results)
        self.pushed_audio: list[bytes] = []
        self.activity_events: list[SpeechActivityEvent] = []
        self.evaluate_calls = 0
        self.reset_calls = 0
        self.close_calls = 0
        self.unload_calls = 0
        self.state = CoordinatorState.IDLE
        self.evaluate_started = asyncio.Event()
        self.evaluate_release = asyncio.Event()
        if not block_evaluation:
            self.evaluate_release.set()
        self.log = log

    def push_audio(self, pcm16: bytes) -> None:
        self.pushed_audio.append(pcm16)

    async def on_activity_event(self, event: SpeechActivityEvent) -> None:
        self.activity_events.append(event)
        if event in (
            SpeechActivityEvent.SPEECH_STARTED,
            SpeechActivityEvent.SPEECH_RESUMED,
        ):
            self.state = CoordinatorState.SPEECH_ACTIVE
        elif event is SpeechActivityEvent.CANDIDATE_PAUSE:
            self.state = CoordinatorState.PAUSE_CANDIDATE

    async def evaluate_buffered(self) -> TurnEvaluation:
        self.evaluate_calls += 1
        self.state = CoordinatorState.EVALUATING
        self.evaluate_started.set()
        await self.evaluate_release.wait()
        result = self.results.popleft()
        self.state = (
            CoordinatorState.WAIT_CONTINUATION
            if result.status is EvaluationStatus.OK
            and result.decision is TurnDecision.INCOMPLETE
            else CoordinatorState.PAUSE_CANDIDATE
        )
        return result

    async def reset(self) -> None:
        self.reset_calls += 1
        self.state = CoordinatorState.IDLE
        if self.log is not None:
            self.log.append("coordinator-reset")

    async def close(self) -> None:
        self.close_calls += 1
        self.state = CoordinatorState.CLOSED
        if self.log is not None:
            self.log.append("coordinator-close")

    async def unload_predictor(self) -> None:
        self.unload_calls += 1


async def _noop_commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
    del generation, buffer_epoch, utterance_id


async def test_first_audio_lazy_loads_vad_off_loop_once() -> None:
    loop_thread_id = threading.get_ident()
    vad = _FakeVad()
    gate = _FakeGate()
    coordinator = _FakeCoordinator()
    adapter = _VoiceTurnAdapter(
        vad=vad,
        gate=gate,
        coordinator=coordinator,
        on_commit=_noop_commit,
    )

    await adapter.start()
    assert vad.load_calls == 0

    await adapter.push_audio(
        generation=0, buffer_epoch=0, utterance_id=1, pcm16=b"\x01\x00"
    )
    await _eventually(lambda: len(gate.feed_calls) == 1)
    await adapter.push_audio(
        generation=0, buffer_epoch=0, utterance_id=1, pcm16=b"\x02\x00"
    )
    await _eventually(lambda: len(gate.feed_calls) == 2)

    assert vad.load_calls == 1
    assert vad.load_thread_ids[0] != loop_thread_id
    assert all(thread_id != loop_thread_id for thread_id in gate.feed_thread_ids)
    assert coordinator.pushed_audio == [b"\x01\x00", b"\x02\x00"]
    await adapter.close()


async def test_reset_unloads_smart_turn_after_warm_ttl_and_audio_cancels_timer() -> None:
    coordinator = _FakeCoordinator()
    gate = _FakeGate()
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=coordinator,
        on_commit=_noop_commit,
        smart_turn_warm_seconds=0.01,
    )
    await adapter.start()

    await adapter.reset(generation=1, buffer_epoch=1, utterance_id=2)
    await _eventually(lambda: coordinator.unload_calls == 1)

    await adapter.reset(generation=1, buffer_epoch=1, utterance_id=3)
    await adapter.push_audio(
        generation=1,
        buffer_epoch=1,
        utterance_id=3,
        pcm16=b"\x01\x00",
    )
    await _eventually(lambda: len(gate.feed_calls) == 1)
    await asyncio.sleep(0.03)

    assert coordinator.unload_calls == 1
    await adapter.close()


async def test_entire_gate_tuple_is_consumed_before_evaluation_decision() -> None:
    gate = _FakeGate(
        [
            (
                SpeechActivityEvent.CANDIDATE_PAUSE,
                SpeechActivityEvent.SPEECH_RESUMED,
            )
        ]
    )
    coordinator = _FakeCoordinator([_complete()])
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=coordinator,
        on_commit=_noop_commit,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=3, buffer_epoch=4, utterance_id=5, pcm16=b"\x01\x00"
    )
    await _eventually(lambda: len(coordinator.activity_events) == 2)
    await asyncio.sleep(0)

    assert coordinator.activity_events == [
        SpeechActivityEvent.CANDIDATE_PAUSE,
        SpeechActivityEvent.SPEECH_RESUMED,
    ]
    assert coordinator.evaluate_calls == 0
    await adapter.close()


async def test_activity_events_are_forwarded_to_runtime_in_order() -> None:
    observed: list[SpeechActivityEvent] = []

    async def on_activity(event: SpeechActivityEvent) -> None:
        observed.append(event)

    gate = _FakeGate(
        [
            (
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.SPEECH_RESUMED,
            )
        ]
    )
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=_FakeCoordinator(),
        on_commit=_noop_commit,
        on_activity=on_activity,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=3,
        buffer_epoch=4,
        utterance_id=5,
        pcm16=b"\x01\x00",
    )
    await _eventually(lambda: len(observed) == 2)

    assert observed == [
        SpeechActivityEvent.SPEECH_STARTED,
        SpeechActivityEvent.SPEECH_RESUMED,
    ]
    await adapter.close()


async def test_bounded_queue_applies_backpressure_without_dropping_pcm() -> None:
    gate = _BlockingGate(blocked_indices=(0,))
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=_FakeCoordinator(),
        on_commit=_noop_commit,
        queue_maxsize=1,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=0, buffer_epoch=0, utterance_id=1, pcm16=b"\x01\x00"
    )
    assert await asyncio.to_thread(gate.started[0].wait, 1)
    await adapter.push_audio(
        generation=0, buffer_epoch=0, utterance_id=1, pcm16=b"\x02\x00"
    )
    blocked_producer = asyncio.create_task(
        adapter.push_audio(
            generation=0,
            buffer_epoch=0,
            utterance_id=1,
            pcm16=b"\x03\x00",
        )
    )
    await asyncio.sleep(0)
    assert blocked_producer.done() is False

    gate.release[0].set()
    await asyncio.wait_for(blocked_producer, 1)
    await _eventually(lambda: len(gate.feed_calls) == 3)
    assert gate.feed_calls == [b"\x01\x00", b"\x02\x00", b"\x03\x00"]
    await adapter.close()


async def test_reset_and_close_are_serialized_after_inflight_gate_feed() -> None:
    log: list[str] = []
    vad = _FakeVad(log)
    gate = _BlockingGate(blocked_indices=(0, 1), log=log)
    coordinator = _FakeCoordinator(log=log)
    adapter = _VoiceTurnAdapter(
        vad=vad,
        gate=gate,
        coordinator=coordinator,
        on_commit=_noop_commit,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=0, buffer_epoch=0, utterance_id=1, pcm16=b"\x01\x00"
    )
    assert await asyncio.to_thread(gate.started[0].wait, 1)
    reset_task = asyncio.create_task(
        adapter.reset(generation=0, buffer_epoch=1, utterance_id=2)
    )
    await asyncio.sleep(0)
    assert reset_task.done() is False
    assert "gate-reset" not in log
    assert "coordinator-reset" not in log

    gate.release[0].set()
    await asyncio.wait_for(reset_task, 1)
    assert log.index("feed-0-end") < log.index("gate-reset")
    assert log.index("feed-0-end") < log.index("coordinator-reset")

    await adapter.push_audio(
        generation=0, buffer_epoch=1, utterance_id=2, pcm16=b"\x02\x00"
    )
    assert await asyncio.to_thread(gate.started[1].wait, 1)
    close_task = asyncio.create_task(adapter.close())
    await asyncio.sleep(0)
    assert close_task.done() is False
    assert "vad-close" not in log
    assert "coordinator-close" not in log

    gate.release[1].set()
    await asyncio.wait_for(close_task, 1)
    assert log.index("feed-1-end") < log.index("vad-close")
    assert log.index("feed-1-end") < log.index("coordinator-close")


async def test_concurrent_close_is_idempotent() -> None:
    vad = _FakeVad()
    coordinator = _FakeCoordinator()
    adapter = _VoiceTurnAdapter(
        vad=vad,
        gate=_FakeGate(),
        coordinator=coordinator,
        on_commit=_noop_commit,
    )
    await adapter.start()

    await asyncio.wait_for(
        asyncio.gather(adapter.close(), adapter.close()),
        1,
    )

    assert (vad.close_calls, coordinator.close_calls) == (1, 1)


async def test_clear_rejects_late_complete_result_from_old_identity() -> None:
    commits: list[tuple[int, int, int]] = []
    current_identity = [7, 8, 9]
    operation_lock = asyncio.Lock()
    callback_finished = asyncio.Event()

    async def commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
        identity = (generation, buffer_epoch, utterance_id)
        async with operation_lock:
            if identity == tuple(current_identity):
                commits.append(identity)
        callback_finished.set()

    gate = _FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,)])
    coordinator = _FakeCoordinator([_complete()], block_evaluation=True)
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=coordinator,
        on_commit=commit,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=7, buffer_epoch=8, utterance_id=9, pcm16=b"\x01\x00"
    )
    await asyncio.wait_for(coordinator.evaluate_started.wait(), 1)
    await operation_lock.acquire()
    try:
        reset_task = asyncio.create_task(
            adapter.reset(generation=7, buffer_epoch=9, utterance_id=10)
        )
        await asyncio.sleep(0)
        assert reset_task.done() is False
        coordinator.evaluate_release.set()
        await asyncio.wait_for(reset_task, 1)
        current_identity[:] = [7, 9, 10]
    finally:
        operation_lock.release()
    await asyncio.wait_for(callback_finished.wait(), 1)

    assert commits == []
    await adapter.close()


async def test_complete_uses_original_identity_and_commit_callback_can_reenter() -> (
    None
):
    callback_finished = asyncio.Event()
    commits: list[tuple[int, int, int]] = []
    adapter: _VoiceTurnAdapter

    async def commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
        commits.append((generation, buffer_epoch, utterance_id))
        await adapter.reset(
            generation=generation,
            buffer_epoch=buffer_epoch + 1,
            utterance_id=utterance_id + 1,
        )
        callback_finished.set()

    gate = _FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,)])
    coordinator = _FakeCoordinator([_complete()])
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=coordinator,
        on_commit=commit,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=11, buffer_epoch=12, utterance_id=13, pcm16=b"\x01\x00"
    )
    await asyncio.wait_for(callback_finished.wait(), 1)

    assert commits == [(11, 12, 13)]
    assert coordinator.reset_calls == 1
    await adapter.close()


async def test_incomplete_waits_for_continuation_and_resume_cancels_fallback() -> None:
    commits: list[tuple[int, int, int]] = []

    async def commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
        commits.append((generation, buffer_epoch, utterance_id))

    gate = _FakeGate(
        [
            (SpeechActivityEvent.CANDIDATE_PAUSE,),
            (SpeechActivityEvent.SPEECH_RESUMED,),
            (SpeechActivityEvent.CANDIDATE_PAUSE,),
        ]
    )
    coordinator = _FakeCoordinator([_incomplete(), _complete()])
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=coordinator,
        on_commit=commit,
        continuation_timeout_seconds=0.02,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=1, buffer_epoch=2, utterance_id=3, pcm16=b"\x01\x00"
    )
    await _eventually(lambda: coordinator.evaluate_calls == 1)
    await adapter.push_audio(
        generation=1, buffer_epoch=2, utterance_id=3, pcm16=b"\x02\x00"
    )
    await _eventually(
        lambda: SpeechActivityEvent.SPEECH_RESUMED in coordinator.activity_events
    )
    await asyncio.sleep(0.04)
    assert commits == []

    await adapter.push_audio(
        generation=1, buffer_epoch=2, utterance_id=3, pcm16=b"\x03\x00"
    )
    await _eventually(lambda: commits == [(1, 2, 3)])
    await adapter.close()


async def test_incomplete_falls_back_to_same_identity_after_default_two_second_policy() -> (
    None
):
    assert (
        inspect.signature(_VoiceTurnAdapter)
        .parameters["continuation_timeout_seconds"]
        .default
        == 2.0
    )
    committed = asyncio.Event()
    commits: list[tuple[int, int, int]] = []

    async def commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
        commits.append((generation, buffer_epoch, utterance_id))
        committed.set()

    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,)]),
        coordinator=_FakeCoordinator([_incomplete()]),
        on_commit=commit,
        continuation_timeout_seconds=0.02,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=21, buffer_epoch=22, utterance_id=23, pcm16=b"\x01\x00"
    )
    await asyncio.wait_for(committed.wait(), 1)
    assert commits == [(21, 22, 23)]
    await adapter.close()


@pytest.mark.parametrize(
    "result",
    [
        _failed_evaluation(EvaluationStatus.UNAVAILABLE),
        _failed_evaluation(EvaluationStatus.ERROR),
        SimpleNamespace(status=EvaluationStatus.OK, decision=None),
    ],
)
async def test_semantic_failure_latches_silero_only_endpointing(result) -> None:
    commits: list[tuple[int, int, int]] = []

    async def commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
        commits.append((generation, buffer_epoch, utterance_id))

    coordinator = _FakeCoordinator([result])
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate(
            [
                (SpeechActivityEvent.CANDIDATE_PAUSE,),
                (SpeechActivityEvent.CANDIDATE_PAUSE,),
            ]
        ),
        coordinator=coordinator,
        on_commit=commit,
        continuation_timeout_seconds=0.01,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=1, buffer_epoch=2, utterance_id=3, pcm16=b"\x01\x00"
    )
    await _eventually(lambda: commits == [(1, 2, 3)])
    await adapter.reset(generation=1, buffer_epoch=3, utterance_id=4)
    await adapter.push_audio(
        generation=1, buffer_epoch=3, utterance_id=4, pcm16=b"\x02\x00"
    )
    await _eventually(lambda: commits == [(1, 2, 3), (1, 3, 4)])

    assert coordinator.evaluate_calls == 1
    await adapter.close()


async def test_semantic_degraded_fallback_is_cancelled_by_speech_resume() -> None:
    commits: list[tuple[int, int, int]] = []

    async def commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
        commits.append((generation, buffer_epoch, utterance_id))

    coordinator = _FakeCoordinator(
        [_failed_evaluation(EvaluationStatus.UNAVAILABLE)]
    )
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate(
            [
                (SpeechActivityEvent.CANDIDATE_PAUSE,),
                (SpeechActivityEvent.SPEECH_RESUMED,),
                (SpeechActivityEvent.CANDIDATE_PAUSE,),
            ]
        ),
        coordinator=coordinator,
        on_commit=commit,
        continuation_timeout_seconds=0.02,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=5, buffer_epoch=6, utterance_id=7, pcm16=b"\x01\x00"
    )
    await _eventually(lambda: coordinator.evaluate_calls == 1)
    await adapter.push_audio(
        generation=5, buffer_epoch=6, utterance_id=7, pcm16=b"\x02\x00"
    )
    await asyncio.sleep(0.04)
    assert commits == []

    await adapter.push_audio(
        generation=5, buffer_epoch=6, utterance_id=7, pcm16=b"\x03\x00"
    )
    await _eventually(lambda: commits == [(5, 6, 7)])
    assert coordinator.evaluate_calls == 1
    await adapter.close()


async def test_required_smart_turn_failure_blocks_without_silero_commit() -> None:
    commits: list[tuple[int, int, int]] = []

    async def commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
        commits.append((generation, buffer_epoch, utterance_id))

    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,)]),
        coordinator=_FakeCoordinator(
            [_failed_evaluation(EvaluationStatus.UNAVAILABLE)]
        ),
        on_commit=commit,
        continuation_timeout_seconds=0.01,
        smart_turn_required=True,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=31,
        buffer_epoch=32,
        utterance_id=33,
        pcm16=b"\x01\x00",
    )
    failure = await asyncio.wait_for(adapter.wait_failure(), 1)
    await asyncio.sleep(0.03)

    assert commits == []
    assert (failure.kind, failure.stage) == ("unavailable", "smart_turn")
    with pytest.raises(RuntimeError, match="ASR_VOICE_TURN_FAILED"):
        await adapter.push_audio(
            generation=31,
            buffer_epoch=32,
            utterance_id=33,
            pcm16=b"\x02\x00",
        )
    await adapter.close()


async def test_stale_semantic_result_does_not_degrade_future_turns() -> None:
    committed = asyncio.Event()

    async def commit(*_identity: int) -> None:
        committed.set()

    coordinator = _FakeCoordinator(
        [_failed_evaluation(EvaluationStatus.STALE), _complete()]
    )
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate(
            [
                (SpeechActivityEvent.CANDIDATE_PAUSE,),
                (SpeechActivityEvent.CANDIDATE_PAUSE,),
            ]
        ),
        coordinator=coordinator,
        on_commit=commit,
        continuation_timeout_seconds=0.01,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=8, buffer_epoch=9, utterance_id=10, pcm16=b"\x01\x00"
    )
    await _eventually(lambda: coordinator.evaluate_calls == 1)
    await adapter.reset(generation=8, buffer_epoch=10, utterance_id=11)
    await adapter.push_audio(
        generation=8, buffer_epoch=10, utterance_id=11, pcm16=b"\x02\x00"
    )
    await asyncio.wait_for(committed.wait(), 1)

    assert coordinator.evaluate_calls == 2
    await adapter.close()


async def test_vad_load_failure_is_terminal_and_rejects_future_input() -> None:
    vad = _UnavailableVad()
    coordinator = _FakeCoordinator()
    adapter = _VoiceTurnAdapter(
        vad=vad,
        gate=_FakeGate(),
        coordinator=coordinator,
        on_commit=_noop_commit,
    )
    await adapter.start()
    await adapter.push_audio(
        generation=0, buffer_epoch=0, utterance_id=1, pcm16=b"\x00\x00"
    )

    failure = await asyncio.wait_for(adapter.wait_failure(), 1)

    assert (failure.kind, failure.stage) == ("unavailable", "vad_load")
    with pytest.raises(RuntimeError, match="ASR_VOICE_TURN_FAILED"):
        await adapter.push_audio(
            generation=0, buffer_epoch=0, utterance_id=1, pcm16=b"\x00\x00"
        )
    with pytest.raises(RuntimeError, match="ASR_VOICE_TURN_FAILED"):
        await adapter.reset(generation=0, buffer_epoch=1, utterance_id=2)
    await adapter.close()
    assert (vad.close_calls, coordinator.close_calls) == (1, 1)


async def test_vad_feed_failure_reports_fixed_terminal_classification() -> None:
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FailingGate(),
        coordinator=_FakeCoordinator(),
        on_commit=_noop_commit,
    )
    await adapter.start()
    await adapter.push_audio(
        generation=0, buffer_epoch=0, utterance_id=1, pcm16=b"\x00\x00"
    )

    failure = await asyncio.wait_for(adapter.wait_failure(), 1)

    assert (failure.kind, failure.stage) == ("runtime_error", "vad_feed")
    await adapter.close()


async def test_unexpected_consumer_failure_is_terminal() -> None:
    coordinator = _FakeCoordinator()

    def fail_push(_pcm16: bytes) -> None:
        raise RuntimeError("unexpected coordinator failure")

    coordinator.push_audio = fail_push
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate(),
        coordinator=coordinator,
        on_commit=_noop_commit,
    )
    await adapter.start()
    await adapter.push_audio(
        generation=0, buffer_epoch=0, utterance_id=1, pcm16=b"\x00\x00"
    )

    failure = await asyncio.wait_for(adapter.wait_failure(), 1)

    assert (failure.kind, failure.stage) == ("runtime_error", "consumer")
    await adapter.close()


async def test_each_session_owns_independent_lazy_runtime_lifecycle() -> None:
    vad_a, vad_b = _FakeVad(), _FakeVad()
    gate_a, gate_b = _FakeGate(), _FakeGate()
    coordinator_a, coordinator_b = _FakeCoordinator(), _FakeCoordinator()
    adapter_a = _VoiceTurnAdapter(
        vad=vad_a,
        gate=gate_a,
        coordinator=coordinator_a,
        on_commit=_noop_commit,
    )
    adapter_b = _VoiceTurnAdapter(
        vad=vad_b,
        gate=gate_b,
        coordinator=coordinator_b,
        on_commit=_noop_commit,
    )
    await adapter_a.start()
    await adapter_b.start()

    await adapter_a.push_audio(
        generation=0, buffer_epoch=0, utterance_id=1, pcm16=b"\x01\x00"
    )
    await adapter_b.push_audio(
        generation=0, buffer_epoch=0, utterance_id=1, pcm16=b"\x02\x00"
    )
    await _eventually(lambda: len(gate_a.feed_calls) == 1)
    await _eventually(lambda: len(gate_b.feed_calls) == 1)
    assert (vad_a.load_calls, vad_b.load_calls) == (1, 1)

    await adapter_a.close()
    assert (vad_a.close_calls, coordinator_a.close_calls) == (1, 1)
    assert (vad_b.close_calls, coordinator_b.close_calls) == (0, 0)

    await adapter_b.push_audio(
        generation=0, buffer_epoch=0, utterance_id=1, pcm16=b"\x03\x00"
    )
    await _eventually(lambda: len(gate_b.feed_calls) == 2)
    assert vad_b.load_calls == 1
    await adapter_b.close()
    assert (vad_b.close_calls, coordinator_b.close_calls) == (1, 1)


async def test_close_cleans_up_after_consumer_failure() -> None:
    vad = _FakeVad()
    coordinator = _FakeCoordinator()
    adapter = _VoiceTurnAdapter(
        vad=vad,
        gate=_FailingGate(),
        coordinator=coordinator,
        on_commit=_noop_commit,
    )
    await adapter.start()
    await adapter.push_audio(
        generation=0,
        buffer_epoch=0,
        utterance_id=1,
        pcm16=b"\x00\x00",
    )
    await _eventually(
        lambda: adapter._consumer_task is not None and adapter._consumer_task.done()
    )

    await asyncio.wait_for(adapter.close(), 1)
    assert (vad.close_calls, coordinator.close_calls) == (1, 1)
