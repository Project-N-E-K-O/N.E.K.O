from __future__ import annotations

import asyncio
import inspect
import math
import threading
from collections import deque
from collections.abc import Iterable
from types import SimpleNamespace

import pytest

from main_logic.asr_client.endpointing.detector_runtime import _VoiceTurnAdapter
from main_logic.asr_client.endpointing.detector import DetectorIngressIdentity
from main_logic.asr_client.lifecycle import VoiceIngressToken
from main_logic.voice_turn.contracts import (
    EvaluationStatus,
    SpeechActivityEvent,
    TurnDecision,
    TurnEvaluation,
)
from main_logic.asr_client.endpointing.coordinator import CoordinatorState


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


class _EvidenceSpy:
    def __init__(self) -> None:
        self.accepted: list[bytes] = []
        self.current: list[bytes] = []
        self.completed: list[tuple[bytes, ...]] = []

    @property
    def enabled(self) -> bool:
        return True

    def accepted_audio(self, *, identity, pcm16: bytes) -> None:
        del identity
        self.accepted.append(pcm16)
        self.current.append(pcm16)

    def complete(self, *, identity, reason, probability, threshold) -> None:
        del identity, reason, probability, threshold
        self.completed.append(tuple(self.current))
        self.current.clear()

    def discard(self) -> None:
        self.current.clear()

    async def close(self) -> None:
        return None


async def _noop_commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
    del generation, buffer_epoch, utterance_id


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("candidate_complete_confirmation_seconds", math.nan),
        ("candidate_complete_confirmation_seconds", math.inf),
        ("strict_complete_confirmation_seconds", math.nan),
        ("strict_complete_confirmation_seconds", math.inf),
    ],
)
def test_confirmation_delays_must_be_finite(argument: str, value: float) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        _VoiceTurnAdapter(
            vad=_FakeVad(),
            gate=_FakeGate(),
            coordinator=_FakeCoordinator(),
            on_commit=_noop_commit,
            **{argument: value},
        )


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


async def test_voice_turn_rejects_non_16khz_pcm() -> None:
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate(),
        coordinator=_FakeCoordinator(),
        on_commit=_noop_commit,
    )

    with pytest.raises(ValueError, match="requires 16 kHz"):
        await adapter.push_audio(
            generation=0,
            buffer_epoch=0,
            utterance_id=1,
            pcm16=b"\x01\x00",
            sample_rate_hz=48_000,
        )

    await adapter.close()


async def test_silent_audio_does_not_extend_smart_turn_warm_ttl() -> None:
    coordinator = _FakeCoordinator()
    gate = _FakeGate()
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=coordinator,
        on_commit=_noop_commit,
        # TTL 必须远大于「一帧音频被消费完」所需的时间：取 0.01s 时卸载往往在
        # 音频进入消费路径之前就已触发，断言就证明不了「静音音频没有续期」。
        smart_turn_warm_seconds=0.2,
    )
    await adapter.start()

    await adapter.reset(generation=1, buffer_epoch=1, utterance_id=2)
    # TTL 现在是 0.2s，默认 1.0s 的 deadline 只有 5 倍余量，CI 比本机慢一到两个
    # 数量级，显式给到 2.0s。
    await _eventually(lambda: coordinator.unload_calls == 1, timeout=2.0)

    await adapter.reset(generation=1, buffer_epoch=1, utterance_id=3)
    armed = adapter._smart_turn_unload_task  # reset() 排下的这一轮 TTL 卸载
    assert armed is not None
    await adapter.push_audio(
        generation=1,
        buffer_epoch=1,
        utterance_id=3,
        pcm16=b"\x01\x00",
    )
    await _eventually(lambda: len(gate.feed_calls) == 1)
    # wait_idle 排空队列，保证这帧静音音频已被完整消费——若消费路径错误地
    # 取消了 TTL 卸载，此刻取消就已经发生了，下面的断言不会变成空过判定。
    await adapter.wait_idle()
    # 本用例的主张是「静音音频不给 TTL 续期」，光数 unload_calls 到 2 证明不了它：
    # 续期就是 _cancel_smart_turn_unload + _schedule_smart_turn_unload，计数照样会
    # 到 2、只是晚一轮 TTL。所以要断言 reset() 排下的**同一个**任务还挂着、没被取消。
    assert adapter._smart_turn_unload_task is armed, "静音音频重排了 TTL 卸载任务"
    assert not armed.cancelled()
    # 不能用固定 sleep 等 TTL 到点：Windows 事件循环 15.625ms 的时钟粒度下
    # sleep(0.03) 可能只等零毫秒（断言提前落地）。
    await _eventually(lambda: coordinator.unload_calls == 2, timeout=2.0)

    assert coordinator.unload_calls == 2
    await adapter.close()


async def test_smart_turn_pin_prevents_unload_until_release() -> None:
    coordinator = _FakeCoordinator()
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate(),
        coordinator=coordinator,
        on_commit=_noop_commit,
        smart_turn_warm_seconds=0.01,
    )
    await adapter.start()
    adapter.pin_smart_turn()

    await adapter.reset(generation=1, buffer_epoch=1, utterance_id=2)
    await asyncio.sleep(0.03)
    assert coordinator.unload_calls == 0

    adapter.unpin_smart_turn()
    await _eventually(lambda: coordinator.unload_calls == 1)
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


async def test_bounded_queue_reports_backpressure_without_blocking_producer() -> None:
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
    with pytest.raises(asyncio.QueueFull):
        await adapter.push_audio(
            generation=0,
            buffer_epoch=0,
            utterance_id=1,
            pcm16=b"\x03\x00",
        )

    gate.release[0].set()
    await _eventually(lambda: len(gate.feed_calls) == 2)
    assert gate.feed_calls == [b"\x01\x00", b"\x02\x00"]
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

    async def commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
        identity = (generation, buffer_epoch, utterance_id)
        async with operation_lock:
            if identity == tuple(current_identity):
                commits.append(identity)

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
        await asyncio.wait_for(reset_task, 1)
        current_identity[:] = [7, 9, 10]
        coordinator.evaluate_release.set()
    finally:
        operation_lock.release()
    await asyncio.sleep(0)

    assert commits == []
    await adapter.close()


async def test_silero_keeps_consuming_while_smart_turn_is_blocked() -> None:
    gate = _FakeGate(
        [
            (SpeechActivityEvent.CANDIDATE_PAUSE,),
            (SpeechActivityEvent.SPEECH_RESUMED,),
        ]
    )
    coordinator = _FakeCoordinator(
        [_failed_evaluation(EvaluationStatus.STALE)], block_evaluation=True
    )
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=coordinator,
        on_commit=_noop_commit,
        smart_turn_required=True,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=1, buffer_epoch=1, utterance_id=1, pcm16=b"\x01\x00"
    )
    await asyncio.wait_for(coordinator.evaluate_started.wait(), 1)
    await adapter.push_audio(
        generation=1, buffer_epoch=1, utterance_id=1, pcm16=b"\x02\x00"
    )
    await _eventually(lambda: len(gate.feed_calls) == 2)

    assert coordinator.evaluate_calls == 1
    assert coordinator.evaluate_release.is_set() is False
    coordinator.evaluate_release.set()
    await adapter.wait_idle()
    await adapter.close()


async def test_evaluation_tail_overflow_uses_backpressure_without_failure() -> None:
    coordinator = _FakeCoordinator([_complete()], block_evaluation=True)
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate(
            [
                (SpeechActivityEvent.CANDIDATE_PAUSE,),
                (),
                (),
                (),
            ]
        ),
        coordinator=coordinator,
        on_commit=_noop_commit,
        queue_capacity_ms=20,
    )
    await adapter.start()
    try:
        await adapter.push_audio(
            generation=1,
            buffer_epoch=1,
            utterance_id=1,
            pcm16=b"\x01\x00" * 160,
        )
        await asyncio.wait_for(coordinator.evaluate_started.wait(), 1)

        for value in (2, 3):
            await adapter.push_audio(
                generation=1,
                buffer_epoch=1,
                utterance_id=1,
                pcm16=bytes((value, 0)) * 160,
            )
            await _eventually(lambda: len(coordinator.pushed_audio) >= value)

        with pytest.raises(asyncio.QueueFull):
            await adapter.push_audio(
                generation=1,
                buffer_epoch=1,
                utterance_id=1,
                pcm16=b"\x04\x00" * 160,
            )

        assert adapter.failed is False
        assert coordinator.pushed_audio == [
            b"\x01\x00" * 160,
            b"\x02\x00" * 160,
            b"\x03\x00" * 160,
        ]
        coordinator.evaluate_release.set()
        await adapter.wait_idle()
        assert adapter.failed is False
    finally:
        coordinator.evaluate_release.set()
        await adapter.close()


async def test_confirmation_observes_evaluation_tail_without_refeeding_audio() -> None:
    first_pcm = b"\x01\x00" * 160
    tail_pcm = b"\x02\x00" * 160
    coordinator = _FakeCoordinator([_complete()], block_evaluation=True)
    gate = _FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,), ()])
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=coordinator,
        on_commit=_noop_commit,
        candidate_complete_confirmation_seconds=10.0,
        smart_turn_required=True,
    )
    await adapter.start()
    try:
        await adapter.push_audio(
            generation=1,
            buffer_epoch=1,
            utterance_id=1,
            pcm16=first_pcm,
        )
        await asyncio.wait_for(coordinator.evaluate_started.wait(), 1)
        await adapter.push_audio(
            generation=1,
            buffer_epoch=1,
            utterance_id=1,
            pcm16=tail_pcm,
        )
        await _eventually(lambda: len(gate.feed_calls) == 2)

        coordinator.evaluate_release.set()
        await adapter.wait_idle()

        assert coordinator.evaluate_calls == 1
        assert adapter.failed is False
        assert coordinator.pushed_audio == [first_pcm, tail_pcm]
        assert gate.feed_calls == [first_pcm, tail_pcm]
    finally:
        coordinator.evaluate_release.set()
        await adapter.close()


async def test_confirmation_expiry_waits_for_inflight_continuation() -> None:
    commits: list[tuple[int, int, int]] = []
    first_pcm = b"\x01\x00"
    continuation_pcm = b"\x02\x00"
    observed: list[bytes] = []
    ingress = VoiceIngressToken(1, "socket", 1, 1, 1)

    async def commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
        commits.append((generation, buffer_epoch, utterance_id))

    gate = _BlockingGate(
        [
            (SpeechActivityEvent.CANDIDATE_PAUSE,),
            (SpeechActivityEvent.SPEECH_RESUMED,),
        ],
        blocked_indices=(1,),
    )
    coordinator = _FakeCoordinator([_complete()])
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=coordinator,
        on_commit=commit,
        on_accepted_audio=lambda pcm16, *_args: observed.append(pcm16),
        candidate_complete_confirmation_seconds=0.05,
        smart_turn_required=True,
    )
    evidence = _EvidenceSpy()
    adapter._smart_turn_audio_evidence = evidence
    await adapter.start()
    try:
        await adapter.push_audio(
            generation=1,
            buffer_epoch=1,
            utterance_id=1,
            pcm16=first_pcm,
            detector_identity=DetectorIngressIdentity(ingress, 1, 1),
        )
        await adapter.wait_idle()
        await adapter.push_audio(
            generation=1,
            buffer_epoch=1,
            utterance_id=1,
            pcm16=continuation_pcm,
            detector_identity=DetectorIngressIdentity(ingress, 1, 2),
        )
        assert await asyncio.to_thread(gate.started[1].wait, 1)

        await asyncio.sleep(0.1)
        assert commits == []
        assert observed == [first_pcm]
        assert evidence.accepted == [first_pcm]

        gate.release[1].set()
        await adapter.wait_idle()
        assert commits == []
        assert observed == [first_pcm, continuation_pcm]
        assert evidence.accepted == [first_pcm, continuation_pcm]
        assert evidence.completed == []
        assert evidence.current == [first_pcm, continuation_pcm]
    finally:
        gate.release[1].set()
        await adapter.close()


async def test_confirmation_replays_newer_pcm_once_for_successor() -> None:
    first_pcm = b"\x01\x00" * 160
    evaluation_tail_pcm = b"\x02\x00" * 160
    confirmation_pcm = b"\x03\x00" * 160
    ingress = VoiceIngressToken(1, "socket", 1, 1, 1)
    coordinator = _FakeCoordinator([_complete()], block_evaluation=True)
    gate = _FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,), (), (), (), ()])
    successor = (2, 1, 2)
    attribution_log: list[tuple[str, bytes | None]] = []

    def observe_audio(pcm16, _sample_rate_hz, _detector_identity) -> None:
        attribution_log.append(("audio", pcm16))

    def advance_fence(*_args):
        attribution_log.append(("fence", None))
        return successor

    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=coordinator,
        on_commit=_noop_commit,
        on_accepted_audio=observe_audio,
        on_completion_fence=advance_fence,
        candidate_complete_confirmation_seconds=10.0,
        smart_turn_required=True,
    )
    evidence = _EvidenceSpy()
    adapter._smart_turn_audio_evidence = evidence
    await adapter.start()
    try:
        await adapter.push_audio(
            generation=1,
            buffer_epoch=1,
            utterance_id=1,
            pcm16=first_pcm,
            detector_identity=DetectorIngressIdentity(ingress, 1, 1),
        )
        await asyncio.wait_for(coordinator.evaluate_started.wait(), 1)
        await adapter.push_audio(
            generation=1,
            buffer_epoch=1,
            utterance_id=1,
            pcm16=evaluation_tail_pcm,
            detector_identity=DetectorIngressIdentity(ingress, 1, 2),
        )
        await _eventually(lambda: len(gate.feed_calls) == 2)
        coordinator.evaluate_release.set()
        await adapter.wait_idle()
        assert adapter._pending_complete_confirmation is not None
        assert attribution_log == [("audio", first_pcm)]
        assert evidence.accepted == [first_pcm]

        await adapter.push_audio(
            generation=1,
            buffer_epoch=1,
            utterance_id=1,
            pcm16=confirmation_pcm,
            detector_identity=DetectorIngressIdentity(ingress, 1, 3),
        )
        await adapter.wait_idle()
        pending = adapter._pending_complete_confirmation
        assert pending is not None
        assert attribution_log == [("audio", first_pcm)]
        assert evidence.accepted == [first_pcm]

        assert await adapter._publish_pending_complete_confirmation(pending)

        assert adapter._identity == successor
        assert attribution_log == [
            ("audio", first_pcm),
            ("fence", None),
            ("audio", evaluation_tail_pcm),
            ("audio", confirmation_pcm),
        ]
        assert evidence.accepted == [
            first_pcm,
            evaluation_tail_pcm,
            confirmation_pcm,
        ]
        assert evidence.completed == [(first_pcm,)]
        assert evidence.current == [evaluation_tail_pcm, confirmation_pcm]
        assert coordinator.pushed_audio == [
            first_pcm,
            evaluation_tail_pcm,
            confirmation_pcm,
            evaluation_tail_pcm,
            confirmation_pcm,
        ]
        assert gate.feed_calls == coordinator.pushed_audio
    finally:
        coordinator.evaluate_release.set()
        await adapter.close()


async def test_confirmation_does_not_refeed_pcm_without_identity_advance() -> None:
    first_pcm = b"\x01\x00" * 160
    continuation_pcm = b"\x02\x00" * 160
    ingress = VoiceIngressToken(1, "socket", 1, 1, 1)
    coordinator = _FakeCoordinator([_complete()])
    gate = _FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,), ()])
    observed: list[bytes] = []
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=coordinator,
        on_commit=_noop_commit,
        on_accepted_audio=lambda pcm16, *_args: observed.append(pcm16),
        candidate_complete_confirmation_seconds=10.0,
        smart_turn_required=True,
    )
    evidence = _EvidenceSpy()
    adapter._smart_turn_audio_evidence = evidence
    await adapter.start()
    try:
        for sequence_no, pcm16 in ((1, first_pcm), (2, continuation_pcm)):
            await adapter.push_audio(
                generation=1,
                buffer_epoch=1,
                utterance_id=1,
                pcm16=pcm16,
                detector_identity=DetectorIngressIdentity(ingress, 1, sequence_no),
            )
            await adapter.wait_idle()
        pending = adapter._pending_complete_confirmation
        assert pending is not None
        assert observed == [first_pcm]
        assert evidence.accepted == [first_pcm]

        assert await adapter._publish_pending_complete_confirmation(pending)

        assert observed == [first_pcm, continuation_pcm]
        assert evidence.accepted == [first_pcm, continuation_pcm]
        assert evidence.completed == [(first_pcm, continuation_pcm)]
        assert evidence.current == []
        assert coordinator.pushed_audio == [first_pcm, continuation_pcm]
        assert gate.feed_calls == [first_pcm, continuation_pcm]
    finally:
        await adapter.close()


async def test_confirmation_tail_overflow_uses_shared_backpressure_budget() -> None:
    ingress = VoiceIngressToken(1, "socket", 1, 1, 1)
    coordinator = _FakeCoordinator([_complete()])
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,), (), ()]),
        coordinator=coordinator,
        on_commit=_noop_commit,
        queue_capacity_ms=20,
        candidate_complete_confirmation_seconds=10.0,
        smart_turn_required=True,
    )
    await adapter.start()
    try:
        for sequence_no in (1, 2, 3):
            await adapter.push_audio(
                generation=1,
                buffer_epoch=1,
                utterance_id=1,
                pcm16=bytes((sequence_no, 0)) * 160,
                detector_identity=DetectorIngressIdentity(ingress, 1, sequence_no),
            )
            await adapter.wait_idle()

        with pytest.raises(asyncio.QueueFull):
            await adapter.push_audio(
                generation=1,
                buffer_epoch=1,
                utterance_id=1,
                pcm16=b"\x04\x00" * 160,
                detector_identity=DetectorIngressIdentity(ingress, 1, 4),
            )

        assert adapter.failed is False
        assert len(coordinator.pushed_audio) == 3
    finally:
        await adapter.close()


async def test_close_bounds_stalled_pending_completion_callback() -> None:
    scoped_started = asyncio.Event()
    never_release = asyncio.Event()
    ingress = VoiceIngressToken(1, "socket", 1, 1, 1)
    vad = _FakeVad()
    coordinator = _FakeCoordinator([_complete()])

    async def stalled_scoped_commit(*_args) -> None:
        scoped_started.set()
        await never_release.wait()

    adapter = _VoiceTurnAdapter(
        vad=vad,
        gate=_FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,)]),
        coordinator=coordinator,
        on_commit=_noop_commit,
        on_scoped_commit=stalled_scoped_commit,
        candidate_complete_confirmation_seconds=10.0,
        smart_turn_required=True,
    )
    await adapter.start()
    await adapter.push_audio(
        generation=1,
        buffer_epoch=1,
        utterance_id=1,
        pcm16=b"\x01\x00" * 160,
        detector_identity=DetectorIngressIdentity(ingress, 1, 1),
    )
    await adapter.wait_idle()
    assert adapter._pending_complete_confirmation is not None

    await asyncio.wait_for(adapter.close(), 1.5)

    assert scoped_started.is_set()
    assert (vad.close_calls, coordinator.close_calls) == (1, 1)


async def test_multiple_pauses_coalesce_to_one_followup_evaluation() -> None:
    coordinator = _FakeCoordinator(
        [_failed_evaluation(EvaluationStatus.STALE), _complete()],
        block_evaluation=True,
    )
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,)] * 3),
        coordinator=coordinator,
        on_commit=_noop_commit,
        smart_turn_required=True,
    )
    await adapter.start()

    for value in (1, 2, 3):
        await adapter.push_audio(
            generation=1,
            buffer_epoch=1,
            utterance_id=1,
            pcm16=bytes((value, 0)),
        )
    await asyncio.wait_for(coordinator.evaluate_started.wait(), 1)
    await _eventually(lambda: len(coordinator.pushed_audio) == 3)
    coordinator.evaluate_release.set()
    await adapter.wait_idle()

    assert coordinator.evaluate_calls == 2
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


async def test_reset_invalidates_scoped_commit_before_core_callback() -> None:
    scoped_started = asyncio.Event()
    release_scoped = asyncio.Event()
    core_commits: list[tuple[int, int, int]] = []

    async def scoped_commit(*_args) -> None:
        scoped_started.set()
        try:
            await release_scoped.wait()
        except asyncio.CancelledError:
            # A defensive consumer may suppress cancellation. The identity
            # barrier must still stop it from reaching the Core callback.
            await release_scoped.wait()

    async def core_commit(*identity: int) -> None:
        core_commits.append(identity)

    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate(),
        coordinator=_FakeCoordinator(),
        on_commit=core_commit,
        on_scoped_commit=scoped_commit,
    )
    await adapter.start()
    old_identity = (4, 5, 6)
    adapter._identity = old_identity
    adapter._dispatch_commit(
        old_identity,
        DetectorIngressIdentity(
            ingress_token=VoiceIngressToken(1, "socket", 1, 1, 1),
            detector_epoch=1,
            sequence_no=1,
        ),
    )
    await asyncio.wait_for(scoped_started.wait(), 1)

    reset_task = asyncio.create_task(
        adapter.reset(generation=4, buffer_epoch=7, utterance_id=8)
    )
    await asyncio.sleep(0)
    release_scoped.set()
    await asyncio.wait_for(reset_task, 1)

    assert core_commits == []
    await adapter.close()


async def test_stale_reset_cannot_regress_adapter_identity() -> None:
    gate = _FakeGate()
    coordinator = _FakeCoordinator()
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=gate,
        coordinator=coordinator,
        on_commit=lambda *_args: None,
    )
    adapter._identity = (3, 4, 5)

    await adapter._process_reset((3, 4, 4))

    assert adapter._identity == (3, 4, 5)
    assert coordinator.reset_calls == 0
    assert gate.reset_calls == 0


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


async def test_required_incomplete_rechecks_and_only_complete_commits() -> None:
    assert (
        inspect.signature(_VoiceTurnAdapter)
        .parameters["continuation_timeout_seconds"]
        .default
        == 2.0
    )
    commits: list[tuple[int, int, int]] = []

    async def commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
        commits.append((generation, buffer_epoch, utterance_id))

    coordinator = _FakeCoordinator([_incomplete(), _complete()])

    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,)]),
        coordinator=coordinator,
        on_commit=commit,
        # 复查窗口取 0.5s（原为 0.02s）：Windows 时钟粒度 15.625ms，窗口太窄时
        # 「还没复查」的断言点和复查定时器会落在同一格 ready，靠回调顺序决定输赢。
        continuation_timeout_seconds=0.5,
        smart_turn_required=True,
        max_endpoint_wait_seconds=5.0,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=21, buffer_epoch=22, utterance_id=23, pcm16=b"\x01\x00"
    )
    await _eventually(lambda: coordinator.evaluate_calls == 1)
    # wait_idle 是确定性同步点：队列排空 + 评估任务与 commit 回调都已跑完，
    # 所以 incomplete 若被错误地 commit，此刻 commits 一定非空——断言不会空过。
    await adapter.wait_idle()
    assert commits == []
    await _eventually(lambda: coordinator.evaluate_calls == 2, timeout=3.0)
    await _eventually(lambda: commits == [(21, 22, 23)], timeout=3.0)
    assert commits == [(21, 22, 23)]
    await adapter.close()


async def test_required_incomplete_blocks_after_max_endpoint_wait_without_commit() -> (
    None
):
    commits: list[tuple[int, int, int]] = []

    async def commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
        commits.append((generation, buffer_epoch, utterance_id))

    coordinator = _FakeCoordinator([_incomplete()] * 20)
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,)]),
        coordinator=coordinator,
        on_commit=commit,
        continuation_timeout_seconds=0.01,
        smart_turn_required=True,
        max_endpoint_wait_seconds=0.035,
    )
    await adapter.start()

    await adapter.push_audio(
        generation=31, buffer_epoch=32, utterance_id=33, pcm16=b"\x01\x00"
    )
    failure = await asyncio.wait_for(adapter.wait_failure(), 1)

    assert failure.stage == "smart_turn"
    assert commits == []
    assert coordinator.evaluate_calls >= 2
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

    coordinator = _FakeCoordinator([_failed_evaluation(EvaluationStatus.UNAVAILABLE)])
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


async def test_commit_callback_failure_is_terminal_and_consumed() -> None:
    async def fail_commit(*_identity: int) -> None:
        raise RuntimeError("commit failed")

    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,)]),
        coordinator=_FakeCoordinator([_complete()]),
        on_commit=fail_commit,
    )
    await adapter.start()
    await adapter.push_audio(
        generation=1,
        buffer_epoch=2,
        utterance_id=3,
        pcm16=b"\x00\x00",
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


async def test_wait_idle_resolves_when_consumer_fails_with_queued_audio() -> None:
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=_FailingGate(),
        coordinator=_FakeCoordinator(),
        on_commit=_noop_commit,
    )
    await adapter.start()
    # Enqueue several frames before the consumer runs so the failure on the
    # first frame leaves items behind that must not deadlock join().
    for frame in (b"\x01\x00", b"\x02\x00", b"\x03\x00"):
        await adapter.push_audio(
            generation=0, buffer_epoch=0, utterance_id=1, pcm16=frame
        )

    await asyncio.wait_for(adapter.wait_idle(), 1)

    assert adapter.failed is True
    await adapter.close()


async def test_tiny_frames_advance_periodic_evaluation_without_vad() -> None:
    commits: list[tuple[int, int, int]] = []

    async def commit(generation: int, buffer_epoch: int, utterance_id: int) -> None:
        commits.append((generation, buffer_epoch, utterance_id))

    adapter = _VoiceTurnAdapter(
        vad=_UnavailableVad(),
        gate=_FakeGate(),
        coordinator=_FakeCoordinator([_complete()]),
        on_commit=commit,
        smart_turn_required=True,
        fallback_evaluation_interval_ms=1,
    )
    await adapter.start()
    # 16-byte frames are 0.5 ms each; a per-frame integer millisecond count
    # would floor to zero and never trigger the periodic evaluation.
    for _ in range(2):
        await adapter.push_audio(
            generation=0, buffer_epoch=0, utterance_id=1, pcm16=b"\x01\x00" * 8
        )
    await asyncio.wait_for(adapter.wait_idle(), 1)

    assert commits == [(0, 0, 1)]
    await adapter.close()


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
