from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client.detector_runtime import (
    DetectorFeedResult,
    DetectorRuntime,
    SmartTurnLease,
    SmartTurnReadiness,
)
from main_logic.asr_client.activity_evidence import RnnoiseEvidence
from main_logic.asr_client.detector import (
    DetectorActivityEvent,
    DetectorCandidateKey,
    DetectorIngressIdentity,
    DetectorPrewarmEvent,
    DetectorTransportPrewarmEvent,
    DetectorSubmitStatus,
    DetectorTurnEvent,
    ProviderCandidateFence,
)
from main_logic.asr_client.lifecycle import VoiceIngressToken, VoiceTurnToken
from main_logic.asr_client.provider_policy import AsrProviderPolicy
from main_logic.asr_client.speaker_shadow import SpeakerShadowCandidateKey
from main_logic.asr_client.throttle_policy import ThrottleAction, VoiceThrottlePolicy
from main_logic.voice_turn.contracts import (
    EvaluationStatus,
    SpeechActivityEvent,
    TurnDecision,
)
from main_logic.voice_turn.coordinator import CoordinatorState


class _Vad:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.load_threads: list[int] = []
        self.closed = False

    def load(self) -> bool:
        self.load_threads.append(threading.get_ident())
        return self.available

    def close(self) -> None:
        self.closed = True


class _Gate:
    def __init__(self, events=()) -> None:
        self.events = tuple(events)
        self.inputs: list[bytes] = []
        self.reset_count = 0

    def feed(self, pcm16: bytes):
        self.inputs.append(pcm16)
        return self.events

    def reset(self) -> None:
        self.reset_count += 1


class _FailingVad(_Vad):
    def load(self) -> bool:
        raise RuntimeError("load failed")


class _FailingGate(_Gate):
    def feed(self, pcm16: bytes):
        raise RuntimeError("feed failed")


class _ResetFailingGate(_Gate):
    def reset(self) -> None:
        raise RuntimeError("reset failed")


class _SemanticCoordinator:
    def __init__(self, *, available: bool = True) -> None:
        self.state = CoordinatorState.IDLE
        self.audio: list[bytes] = []
        self.available = available
        self.prepare_calls = 0

    def push_audio(self, pcm16: bytes) -> None:
        self.audio.append(pcm16)

    async def on_activity_event(self, event: SpeechActivityEvent) -> None:
        if event is SpeechActivityEvent.CANDIDATE_PAUSE:
            self.state = CoordinatorState.PAUSE_CANDIDATE

    async def evaluate_buffered(self):
        self.state = CoordinatorState.PAUSE_CANDIDATE
        return SimpleNamespace(
            status=EvaluationStatus.OK,
            decision=TurnDecision.COMPLETE,
        )

    async def prepare_predictor(self) -> bool:
        self.prepare_calls += 1
        return self.available

    async def reset(self) -> None:
        self.state = CoordinatorState.IDLE

    async def close(self) -> None:
        self.state = CoordinatorState.CLOSED

    async def unload_predictor(self) -> None:
        return None


class _BlockingSemanticCoordinator(_SemanticCoordinator):
    def __init__(self, *, block_prepare: bool = False) -> None:
        super().__init__()
        self.evaluate_started = asyncio.Event()
        self.evaluate_release = asyncio.Event()
        self.prepare_started = asyncio.Event()
        self.prepare_release = asyncio.Event()
        if not block_prepare:
            self.prepare_release.set()

    async def evaluate_buffered(self):
        self.evaluate_started.set()
        await self.evaluate_release.wait()
        return await super().evaluate_buffered()

    async def prepare_predictor(self) -> bool:
        self.prepare_started.set()
        await self.prepare_release.wait()
        return True


class _RaisingPrepareCoordinator(_SemanticCoordinator):
    async def prepare_predictor(self) -> bool:
        raise RuntimeError("prepare failed")


class _OverflowAdapter:
    def __init__(self) -> None:
        self.failed = False
        self.throttle_available = True
        self.push_calls = 0
        self.reset_started = asyncio.Event()
        self.reset_release = asyncio.Event()
        self.closed = False

    async def push_audio(self, **_kwargs) -> None:
        self.push_calls += 1
        if self.push_calls == 1:
            raise asyncio.QueueFull

    async def reset(self, **_kwargs) -> None:
        self.reset_started.set()
        await self.reset_release.wait()

    async def close(self) -> None:
        self.closed = True

    async def wait_failure(self):
        await asyncio.Event().wait()

    def pin_smart_turn(self) -> None:
        return None

    def unpin_smart_turn(self) -> None:
        return None


class _SpeakerShadowSpy:
    def __init__(self) -> None:
        self.frames: list[tuple[bytes, int, object]] = []
        self.reset_calls = 0
        self.close_calls = 0
        self.finished: list[object] = []

    def submit(self, pcm16: bytes, *, sample_rate_hz: int, candidate) -> bool:
        self.frames.append((pcm16, sample_rate_hz, candidate))
        return True

    async def reset(self) -> None:
        self.reset_calls += 1

    def finish_candidate(self, candidate) -> bool:
        self.finished.append(candidate)
        return True

    async def close(self) -> None:
        self.close_calls += 1

    def snapshot(self) -> dict[str, int]:
        return {"submitted_frame_count": len(self.frames)}


class _ControlledCloseShadow(_SpeakerShadowSpy):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()

    async def close(self) -> None:
        self.close_calls += 1
        self.close_started.set()

    async def wait_closed(self) -> None:
        await self.close_release.wait()


def _smart_turn_policy() -> AsrProviderPolicy:
    return AsrProviderPolicy(
        transport="streaming",
        endpoint_authority="smart_turn",
        smart_turn_required=True,
        max_segment_ms=None,
        warm_transport_ms=25_000,
        replay_policy="preconnect_only",
    )


def _provider_endpoint_policy() -> AsrProviderPolicy:
    return AsrProviderPolicy(
        transport="streaming",
        endpoint_authority="provider",
        smart_turn_required=False,
        max_segment_ms=None,
        warm_transport_ms=25_000,
        replay_policy="none",
    )


def _ingress_token() -> VoiceIngressToken:
    return VoiceIngressToken(1, "socket", 1, 1, 1)


async def test_detector_loads_silero_off_loop_and_returns_activity() -> None:
    vad = _Vad()
    gate = _Gate((SpeechActivityEvent.SPEECH_STARTED,))
    detector = DetectorRuntime(vad=vad, gate=gate)

    result = await detector.feed(b"\x01\x00" * 160)

    assert result == DetectorFeedResult(
        events=(SpeechActivityEvent.SPEECH_STARTED,),
        throttle_available=True,
        throttle_action=ThrottleAction.OPEN_CANDIDATE,
    )
    assert gate.inputs == [b"\x01\x00" * 160]
    assert vad.load_threads and vad.load_threads[0] != threading.get_ident()
    await detector.close()
    assert vad.closed is True


async def test_detector_failure_requests_independent_asr_fail_open() -> None:
    detector = DetectorRuntime(vad=_Vad(available=False), gate=_Gate())

    first = await detector.feed(b"\x01\x00")
    second = await detector.feed(b"\x02\x00")

    assert first.throttle_available is False
    assert second.throttle_available is False
    assert first.events == second.events == ()


async def test_rnnoise_soft_gate_skips_silero_until_probable_voice() -> None:
    gate = _Gate((SpeechActivityEvent.SPEECH_STARTED,))
    detector = DetectorRuntime(vad=_Vad(), gate=gate, rnnoise_onset_probability=0.4)

    quiet = await detector.feed(b"\x01\x00", speech_probability=0.1)
    speech = await detector.feed(b"\x02\x00", speech_probability=0.8)

    assert quiet.events == ()
    assert quiet.throttle_available is True
    assert gate.inputs == [b"\x02\x00"]
    assert speech.events == (SpeechActivityEvent.SPEECH_STARTED,)


async def test_provider_feed_propagates_transport_prewarm_once() -> None:
    emitted = []

    async def on_event(event) -> None:
        emitted.append(event)

    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_provider_endpoint_policy(),
        on_event=on_event,
    )
    evidence = RnnoiseEvidence.from_legacy_probability(0.9, available=True)

    first = await detector.feed(
        b"\x01\x00",
        rnnoise_evidence=evidence,
        ingress_token=_ingress_token(),
    )
    second = await detector.feed(
        b"\x02\x00",
        rnnoise_evidence=evidence,
        ingress_token=_ingress_token(),
    )

    assert first.throttle_action is ThrottleAction.PREWARM
    assert second.throttle_action is ThrottleAction.PREWARM
    assert len(emitted) == 1
    assert isinstance(emitted[0], DetectorTransportPrewarmEvent)
    assert emitted[0].ingress.ingress_token == _ingress_token()
    assert detector.smart_turn_readiness is SmartTurnReadiness.UNLOADED
    await detector.close()


async def test_provider_candidate_completion_restores_idle_throttle() -> None:
    policy = VoiceThrottlePolicy(
        resource_optimization_enabled=True,
        minimum_baseline_samples=1,
    )
    gate = _Gate((SpeechActivityEvent.SPEECH_STARTED,))
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        provider_policy=_provider_endpoint_policy(),
        throttle_policy=policy,
    )
    active = await detector.feed(
        b"\x01\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.9,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )
    assert active.events == (SpeechActivityEvent.SPEECH_STARTED,)

    fence = await detector.seal_provider_candidate()
    assert isinstance(fence, ProviderCandidateFence)
    assert await detector.complete_provider_candidate(fence) is False
    gate.events = ()
    before = len(gate.inputs)
    baseline_before_quiet = policy.baseline

    quiet = await detector.feed(
        b"\x02\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.05,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )

    assert quiet.throttle_action is ThrottleAction.SKIP_IDLE_PCM
    assert len(gate.inputs) == before
    assert baseline_before_quiet is not None
    assert policy.baseline is not None
    assert policy.baseline < baseline_before_quiet
    await detector.close()


async def test_provider_completion_preserves_successor_prewarm() -> None:
    emitted = []

    async def on_event(event) -> None:
        emitted.append(event)

    gate = _Gate((SpeechActivityEvent.SPEECH_STARTED,))
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        provider_policy=_provider_endpoint_policy(),
        on_event=on_event,
    )
    await detector.feed(
        b"\x01\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.9,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )
    fence = await detector.seal_provider_candidate()
    assert fence is not None
    emitted.clear()
    gate.events = ()

    successor = await detector.feed(
        b"\x02\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.9,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )
    assert successor.throttle_action is ThrottleAction.PREWARM
    assert len(emitted) == 1

    gate.events = (SpeechActivityEvent.SPEECH_STARTED,)
    confirmed = await detector.feed(
        b"\x03\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.9,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )
    assert confirmed.events == (SpeechActivityEvent.SPEECH_STARTED,)
    stale = ProviderCandidateFence(
        fence.detector_epoch + 1,
        fence.candidate_generation,
        fence.through_sequence_no,
    )
    assert await detector.complete_provider_candidate(stale) is None

    assert await detector.complete_provider_candidate(fence) is True
    assert await detector.complete_provider_candidate(fence) is None
    assert emitted == [emitted[0]]
    gate.events = ()
    before = len(gate.inputs)
    kept_open = await detector.feed(
        b"\x04\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.05,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )
    assert kept_open.throttle_action is ThrottleAction.KEEP_CANDIDATE_OPEN
    assert len(gate.inputs) == before + 1
    await detector.close()


async def test_provider_successor_discard_preserves_old_fence() -> None:
    policy = VoiceThrottlePolicy(
        resource_optimization_enabled=True,
        minimum_baseline_samples=1,
    )
    gate = _Gate((SpeechActivityEvent.SPEECH_STARTED,))
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        provider_policy=_provider_endpoint_policy(),
        throttle_policy=policy,
    )
    await detector.feed(
        b"\x00\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.05,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )
    await detector.feed(
        b"\x01\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.9,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )
    fence = await detector.seal_provider_candidate()
    assert fence is not None
    await detector.feed(
        b"\x02\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.9,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )
    detector_epoch = detector._detector_epoch
    ingress_token = detector._ingress_token
    baseline = policy.baseline

    assert await detector.discard_provider_successor(fence) is True

    assert detector._provider_candidate_fence == fence
    assert detector._provider_discarded_through_sequence_no == detector._sequence_no
    assert detector._detector_epoch == detector_epoch
    assert detector._ingress_token == ingress_token
    assert detector._speech_active is False
    assert gate.reset_count == 1
    assert policy.baseline == baseline
    assert await detector.complete_provider_candidate(fence) is False
    assert detector._provider_discarded_through_sequence_no is None
    await detector.close()


async def test_provider_successor_after_discard_survives_old_final() -> None:
    gate = _Gate((SpeechActivityEvent.SPEECH_STARTED,))
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        provider_policy=_provider_endpoint_policy(),
    )
    await detector.feed(
        b"\x01\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.9,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )
    fence = await detector.seal_provider_candidate()
    assert fence is not None
    await detector.feed(
        b"\x02\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.9,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )
    assert await detector.discard_provider_successor(fence) is True

    await detector.feed(
        b"\x03\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.9,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )

    assert await detector.complete_provider_candidate(fence) is True
    assert detector._speech_active is True
    await detector.close()


async def test_provider_successor_discard_rejects_stale_fence_without_mutation() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_provider_endpoint_policy(),
    )
    fence = await detector.seal_provider_candidate()
    assert fence is not None
    stale = ProviderCandidateFence(
        fence.detector_epoch + 1,
        fence.candidate_generation,
        fence.through_sequence_no,
    )
    generation = detector._candidate_generation

    assert await detector.discard_provider_successor(stale) is False

    assert detector._provider_candidate_fence == fence
    assert detector._provider_discarded_through_sequence_no is None
    assert detector._candidate_generation == generation
    await detector.close()


async def test_repeated_provider_successor_discard_advances_one_watermark() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate((SpeechActivityEvent.SPEECH_STARTED,)),
        provider_policy=_provider_endpoint_policy(),
    )
    fence = await detector.seal_provider_candidate()
    assert fence is not None
    await detector.feed(
        b"\x01\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.9,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )
    assert await detector.discard_provider_successor(fence) is True
    first_watermark = detector._provider_discarded_through_sequence_no
    await detector.feed(
        b"\x02\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.9,
            available=True,
        ),
        ingress_token=_ingress_token(),
    )

    assert await detector.discard_provider_successor(fence) is True
    assert first_watermark is not None
    assert detector._provider_discarded_through_sequence_no > first_watermark
    assert detector._provider_candidate_fence == fence
    assert await detector.complete_provider_candidate(fence) is False
    await detector.close()


async def test_provider_successor_discard_propagates_gate_reset_failure() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_ResetFailingGate(),
        provider_policy=_provider_endpoint_policy(),
    )
    fence = await detector.seal_provider_candidate()
    assert fence is not None

    with pytest.raises(RuntimeError, match="reset failed"):
        await detector.discard_provider_successor(fence)

    assert detector._provider_candidate_fence == fence
    assert detector._provider_discarded_through_sequence_no is None
    await detector.close()


async def test_provider_discard_watermark_clears_on_reset_and_close() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_provider_endpoint_policy(),
    )
    fence = await detector.seal_provider_candidate()
    assert fence is not None
    assert await detector.discard_provider_successor(fence) is True
    assert detector._provider_discarded_through_sequence_no is not None

    await detector.reset()

    assert detector._provider_discarded_through_sequence_no is None
    detector._provider_discarded_through_sequence_no = 1
    await detector.close()
    assert detector._provider_discarded_through_sequence_no is None


async def test_provider_candidate_fence_stays_bounded_across_100_turns() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_provider_endpoint_policy(),
    )

    for _ in range(100):
        fence = await detector.seal_provider_candidate()
        assert fence is not None
        assert await detector.discard_provider_successor(fence) is True
        assert await detector.complete_provider_candidate(fence) is False

    assert detector._provider_candidate_fence is None
    assert detector._provider_discarded_through_sequence_no is None
    assert detector._bound_turns == {}
    assert detector._completion_fences == {}
    await detector.close()


async def test_rnnoise_unavailable_does_not_look_like_zero_probability() -> None:
    gate = _Gate((SpeechActivityEvent.SPEECH_STARTED,))
    detector = DetectorRuntime(vad=_Vad(), gate=gate, rnnoise_onset_probability=0.4)

    result = await detector.feed(
        b"\x01\x00",
        speech_probability=0.0,
        rnnoise_available=False,
    )

    assert gate.inputs == [b"\x01\x00"]
    assert result.events == (SpeechActivityEvent.SPEECH_STARTED,)


async def test_active_speech_still_feeds_silero_when_rnnoise_probability_drops() -> (
    None
):
    gate = _Gate((SpeechActivityEvent.SPEECH_STARTED,))
    detector = DetectorRuntime(vad=_Vad(), gate=gate)

    await detector.feed(b"\x01\x00", speech_probability=0.8)
    await detector.feed(b"\x02\x00", speech_probability=0.0)

    assert gate.inputs == [b"\x01\x00", b"\x02\x00"]


async def test_unified_detector_owns_smart_turn_and_emits_semantic_completion() -> None:
    completed = asyncio.Event()
    completion_count = 0

    async def on_complete() -> None:
        nonlocal completion_count
        completion_count += 1
        completed.set()

    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate((SpeechActivityEvent.CANDIDATE_PAUSE,)),
        provider_policy=AsrProviderPolicy(
            transport="segmented",
            endpoint_authority="smart_turn",
            smart_turn_required=True,
            max_segment_ms=27_000,
            warm_transport_ms=0,
            replay_policy="none",
        ),
        coordinator=_SemanticCoordinator(),
        on_turn_complete=on_complete,
    )

    result = await detector.feed(b"\x01\x00" * 160)
    await asyncio.wait_for(completed.wait(), 1)

    assert result.events == (SpeechActivityEvent.CANDIDATE_PAUSE,)
    assert result.throttle_available is True

    await detector.feed(b"\x02\x00" * 160)
    assert completion_count == 1
    await detector.release_deferred_turn()
    assert completion_count == 2
    await detector.close()


async def test_submit_audio_does_not_wait_for_smart_turn_inference() -> None:
    coordinator = _BlockingSemanticCoordinator()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate((SpeechActivityEvent.CANDIDATE_PAUSE,)),
        provider_policy=_smart_turn_policy(),
        coordinator=coordinator,
        on_turn_complete=AsyncMock(),
    )

    first = await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    await asyncio.wait_for(coordinator.evaluate_started.wait(), 1)
    second = await asyncio.wait_for(
        detector.submit_audio(
            b"\x02\x00" * 160,
            ingress_token=_ingress_token(),
            sample_rate_hz=16_000,
            speech_probability=0.1,
            rnnoise_available=True,
        ),
        0.1,
    )

    assert first.status is DetectorSubmitStatus.ACCEPTED
    assert second.status is DetectorSubmitStatus.ACCEPTED
    assert detector.candidate_open is True
    coordinator.evaluate_release.set()
    await detector.close()


async def test_candidate_open_prevents_rnnoise_from_skipping_followup_pcm() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_turn_complete=AsyncMock(),
    )

    quiet = await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.1,
        rnnoise_available=True,
    )
    onset = await detector.submit_audio(
        b"\x02\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    followup = await detector.submit_audio(
        b"\x03\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.0,
        rnnoise_available=True,
    )

    assert quiet.status is DetectorSubmitStatus.SKIPPED_QUIET
    assert onset.status is DetectorSubmitStatus.ACCEPTED
    assert followup.status is DetectorSubmitStatus.ACCEPTED
    await detector.close()


async def test_completed_turn_does_not_clear_successor_candidate_activity() -> None:
    completion_started = asyncio.Event()
    completion_release = asyncio.Event()
    completion_published = asyncio.Event()
    events: list[object] = []
    detector: DetectorRuntime

    async def on_event(event: object) -> None:
        events.append(event)
        if isinstance(event, DetectorPrewarmEvent):
            await detector.bind_candidate(
                event.candidate,
                VoiceTurnToken(event.ingress.ingress_token, 1),
            )
        elif isinstance(event, DetectorTurnEvent):
            completion_started.set()
            await completion_release.wait()

    async def on_complete() -> None:
        completion_published.set()

    gate = _Gate((SpeechActivityEvent.CANDIDATE_PAUSE,))
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_event=on_event,
        on_turn_complete=on_complete,
    )

    first = await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    await asyncio.wait_for(completion_started.wait(), 1)

    gate.events = ()
    successor_onset = await detector.submit_audio(
        b"\x02\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    completion_release.set()
    await asyncio.wait_for(completion_published.wait(), 1)

    successor_followup = await detector.submit_audio(
        b"\x03\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.0,
        rnnoise_available=True,
    )

    completed_turns = [
        event for event in events if isinstance(event, DetectorTurnEvent)
    ]
    assert first.candidate is not None
    assert successor_onset.candidate is not None
    assert successor_onset.candidate.candidate_generation == (
        first.candidate.candidate_generation + 1
    )
    assert successor_followup.status is DetectorSubmitStatus.ACCEPTED
    assert detector.candidate_open is True
    assert len(completed_turns) == 1
    assert completed_turns[0].bound_turn.candidate == first.candidate
    await detector.close()


async def test_completion_fence_replays_pcm_consumed_during_inference() -> None:
    coordinator = _BlockingSemanticCoordinator()
    gate = _Gate((SpeechActivityEvent.CANDIDATE_PAUSE,))
    completed = asyncio.Event()

    async def on_complete() -> None:
        completed.set()

    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        provider_policy=_smart_turn_policy(),
        coordinator=coordinator,
        on_turn_complete=on_complete,
    )

    await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    await asyncio.wait_for(coordinator.evaluate_started.wait(), 1)

    gate.events = ()
    successor_pcm = b"\x02\x00" * 160
    successor = await detector.submit_audio(
        successor_pcm,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    async with asyncio.timeout(1):
        while gate.inputs.count(successor_pcm) < 1:
            await asyncio.sleep(0)

    coordinator.evaluate_release.set()
    await asyncio.wait_for(completed.wait(), 1)
    async with asyncio.timeout(1):
        while gate.inputs.count(successor_pcm) < 2:
            await asyncio.sleep(0)

    assert successor.candidate is not None
    assert successor.candidate.candidate_generation == 0
    assert gate.inputs.count(successor_pcm) == 2
    assert coordinator.audio.count(successor_pcm) == 2
    await detector.close()


async def test_completion_fence_clears_activity_without_successor_pcm() -> None:
    completed = asyncio.Event()

    async def on_complete() -> None:
        completed.set()

    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate((SpeechActivityEvent.CANDIDATE_PAUSE,)),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_turn_complete=on_complete,
    )
    await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    await asyncio.wait_for(completed.wait(), 1)

    quiet = await detector.submit_audio(
        b"\x02\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.0,
        rnnoise_available=True,
    )

    assert quiet.status is DetectorSubmitStatus.SKIPPED_QUIET
    assert detector.candidate_open is False
    await detector.close()


async def test_disabled_resource_optimization_never_skips_quiet_rnnoise_pcm() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        resource_optimization_enabled=False,
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_turn_complete=AsyncMock(),
    )

    quiet = await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.0,
        rnnoise_available=True,
    )

    assert quiet.status is DetectorSubmitStatus.ACCEPTED
    assert quiet.identity is not None
    assert detector.candidate_open is True
    await detector.close()


async def test_submit_audio_uses_chunk_peak_for_prewarm_and_emits_once() -> None:
    events: list[DetectorPrewarmEvent] = []

    async def on_event(event) -> None:
        if isinstance(event, DetectorPrewarmEvent):
            events.append(event)

    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_event=on_event,
    )
    evidence = RnnoiseEvidence(
        available=True,
        frame_count=3,
        peak=0.8,
        mean=0.35,
        last=0.05,
        ema=0.3,
    )

    first = await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.05,
        rnnoise_available=True,
        rnnoise_evidence=evidence,
    )
    second = await detector.submit_audio(
        b"\x02\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.0,
        rnnoise_available=True,
        rnnoise_evidence=RnnoiseEvidence(
            available=True,
            frame_count=1,
            peak=0.0,
            mean=0.0,
            last=0.0,
            ema=0.0,
        ),
    )

    assert first.status is DetectorSubmitStatus.ACCEPTED
    assert first.throttle_action is ThrottleAction.PREWARM
    assert second.status is DetectorSubmitStatus.ACCEPTED
    assert second.throttle_action is ThrottleAction.KEEP_CANDIDATE_OPEN
    assert [event.kind for event in events] == ["prewarm"]
    assert events[0].candidate == first.candidate == second.candidate
    await detector.close()


async def test_segmented_submit_mirrors_only_accepted_audio_to_speaker_shadow() -> None:
    shadow = _SpeakerShadowSpy()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        speaker_shadow=shadow,
        on_turn_complete=AsyncMock(),
    )
    pcm16 = b"\x01\x00" * 160

    result = await detector.submit_audio(
        pcm16,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )

    assert result.status is DetectorSubmitStatus.ACCEPTED
    assert len(shadow.frames) == 1
    shadow_candidate = shadow.frames[0][2]
    assert shadow.frames[0][:2] == (pcm16, 16_000)
    assert shadow_candidate == SpeakerShadowCandidateKey(
        detector_epoch=0,
        shadow_generation=0,
        scope="smart_turn_turn",
    )
    assert detector.speaker_shadow_metrics == {"submitted_frame_count": 1}
    await detector.reset()
    await detector.close()
    assert shadow.reset_calls == 1
    assert shadow.close_calls == 1


async def test_replace_speaker_verifier_invalidates_candidate_without_endpoint_change() -> (
    None
):
    previous = _SpeakerShadowSpy()
    replacement = _SpeakerShadowSpy()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate((SpeechActivityEvent.SPEECH_STARTED,)),
        provider_policy=_provider_endpoint_policy(),
        speaker_shadow=previous,
    )

    await detector.feed(
        b"\x01\x00" * 160,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    generation = detector._speaker_shadow_generation
    candidate_generation = detector._candidate_generation

    assert await detector.replace_speaker_verifier(replacement) is True

    assert previous.close_calls == 1
    assert detector._speaker_shadow is replacement
    assert detector._speaker_shadow_candidate is None
    assert detector._speaker_shadow_generation == generation + 1
    assert detector._candidate_generation == candidate_generation
    assert detector._semantic_adapter is None

    await detector.close()
    assert replacement.close_calls == 1


async def test_replace_speaker_verifier_installs_new_only_after_old_is_fully_closed() -> (
    None
):
    previous = _ControlledCloseShadow()
    replacement = _SpeakerShadowSpy()
    gate = _Gate((SpeechActivityEvent.SPEECH_STARTED,))
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        provider_policy=_provider_endpoint_policy(),
        speaker_shadow=previous,
    )

    replace_task = asyncio.create_task(
        detector.replace_speaker_verifier(replacement)
    )
    await asyncio.wait_for(previous.close_started.wait(), 1)

    assert detector._speaker_shadow is None
    await detector.feed(
        b"\x01\x00" * 160,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    assert replacement.frames == []
    assert replace_task.done() is False

    previous.close_release.set()
    assert await asyncio.wait_for(replace_task, 1) is True
    assert detector._speaker_shadow is replacement

    await detector.close()


async def test_replace_speaker_verifier_timeout_stays_disabled_and_fail_open(
    monkeypatch,
) -> None:
    import main_logic.asr_client.detector_runtime as detector_module

    previous = _ControlledCloseShadow()
    replacement = _SpeakerShadowSpy()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_provider_endpoint_policy(),
        speaker_shadow=previous,
    )
    monkeypatch.setattr(
        detector_module,
        "_SPEAKER_VERIFIER_REPLACEMENT_CLOSE_SECONDS",
        0.01,
    )

    assert await detector.replace_speaker_verifier(replacement) is False

    assert detector._speaker_shadow is None
    assert replacement.close_calls == 1
    assert detector.speaker_shadow_metrics["replacement_failure_count"] == 1
    await detector.close()


async def test_detector_close_during_verifier_replacement_never_installs_new() -> None:
    previous = _ControlledCloseShadow()
    replacement = _SpeakerShadowSpy()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_provider_endpoint_policy(),
        speaker_shadow=previous,
    )

    replace_task = asyncio.create_task(
        detector.replace_speaker_verifier(replacement)
    )
    await asyncio.wait_for(previous.close_started.wait(), 1)
    await detector.close()

    previous.close_release.set()
    assert await asyncio.wait_for(replace_task, 1) is False
    assert detector._speaker_shadow is None
    assert replacement.close_calls == 1


async def test_shadow_toggle_preserves_provider_pcm_bytes_order_and_detector_results() -> None:
    frames = [b"\x01\x00" * 160, b"\x02\x00" * 160, b"\x03\x00" * 160]

    async def run(shadow):
        gate = _Gate((SpeechActivityEvent.SPEECH_STARTED,))
        detector = DetectorRuntime(vad=_Vad(), gate=gate, speaker_shadow=shadow)
        results = []
        for index, frame in enumerate(frames):
            if index == 1:
                gate.events = ()
            results.append(
                await detector.feed(
                    frame,
                    speech_probability=0.9,
                    rnnoise_available=True,
                )
            )
        await detector.close()
        return gate.inputs, results

    without_shadow = await run(None)
    with_shadow = await run(_SpeakerShadowSpy())

    assert without_shadow == with_shadow
    assert with_shadow[0] == frames


async def test_provider_shadow_finishes_at_pause_and_ignores_following_silence() -> (
    None
):
    shadow = _SpeakerShadowSpy()
    gate = _Gate((SpeechActivityEvent.SPEECH_STARTED,))
    detector = DetectorRuntime(vad=_Vad(), gate=gate, speaker_shadow=shadow)
    asr_generation = detector._candidate_generation

    await detector.feed(b"\x01\x00" * 160, speech_probability=0.9)
    gate.events = (SpeechActivityEvent.CANDIDATE_PAUSE,)
    await detector.feed(b"\x02\x00" * 160, speech_probability=0.1)
    gate.events = ()
    for _ in range(200):
        await detector.feed(b"\x03\x00" * 160, speech_probability=0.0)

    first_candidate = SpeakerShadowCandidateKey(0, 0, "provider_pause")
    assert [frame[0] for frame in shadow.frames] == [
        b"\x01\x00" * 160,
        b"\x02\x00" * 160,
    ]
    assert [frame[2] for frame in shadow.frames] == [first_candidate] * 2
    assert shadow.finished == [first_candidate]
    assert detector._candidate_generation == asr_generation

    gate.events = (SpeechActivityEvent.SPEECH_RESUMED,)
    await detector.feed(b"\x05\x00" * 160, speech_probability=0.0)
    assert shadow.frames[-1][2] == SpeakerShadowCandidateKey(0, 1, "provider_pause")
    await detector.close()


async def test_provider_shadow_discards_unconfirmed_prewarm_on_idle() -> None:
    shadow = _SpeakerShadowSpy()
    detector = DetectorRuntime(vad=_Vad(), gate=_Gate(), speaker_shadow=shadow)

    await detector.feed(b"\x01\x00" * 160, speech_probability=0.9)
    await detector.feed(b"\x02\x00" * 160, speech_probability=0.0)

    candidate = SpeakerShadowCandidateKey(0, 0, "provider_pause")
    assert [frame[2] for frame in shadow.frames] == [candidate]
    assert shadow.finished == [candidate]
    await detector.close()


async def test_disabled_throttling_does_not_make_shadow_collect_idle_pcm() -> None:
    shadow = _SpeakerShadowSpy()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        speaker_shadow=shadow,
        resource_optimization_enabled=False,
    )

    result = await detector.feed(b"\x01\x00" * 160, speech_probability=0.0)

    assert result.throttle_available is True
    assert shadow.frames == []
    await detector.close()


async def test_segmented_completion_fence_finishes_only_old_shadow_candidate() -> None:
    shadow = _SpeakerShadowSpy()
    coordinator = _BlockingSemanticCoordinator()
    completion_started = asyncio.Event()
    completion_release = asyncio.Event()

    async def on_complete() -> None:
        completion_started.set()
        await completion_release.wait()

    gate = _Gate((SpeechActivityEvent.CANDIDATE_PAUSE,))
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        provider_policy=_smart_turn_policy(),
        coordinator=coordinator,
        speaker_shadow=shadow,
        on_turn_complete=on_complete,
    )

    await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    await asyncio.wait_for(coordinator.evaluate_started.wait(), 1)
    coordinator.evaluate_release.set()
    await asyncio.wait_for(completion_started.wait(), 1)

    gate.events = ()
    await detector.submit_audio(
        b"\x02\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )

    first = SpeakerShadowCandidateKey(0, 0, "smart_turn_turn")
    successor = SpeakerShadowCandidateKey(0, 1, "smart_turn_turn")
    assert [frame[2] for frame in shadow.frames] == [first, successor]
    assert shadow.finished == [first]
    completion_release.set()
    await asyncio.sleep(0)
    assert shadow.finished == [first]
    await detector.close()


async def test_session_smart_turn_pin_survives_turn_lease_then_reset_releases_it() -> (
    None
):
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_turn_complete=AsyncMock(),
    )
    token = VoiceTurnToken(_ingress_token(), turn_id=1)

    assert await detector.pin_endpointing_session() is True
    assert detector._semantic_adapter._smart_turn_pin_count == 1
    lease = await detector.prepare_endpointing(token)
    assert lease is not None
    assert detector._semantic_adapter._smart_turn_pin_count == 2

    await lease.release()
    assert detector._semantic_adapter._smart_turn_pin_count == 1
    await detector.reset()
    assert detector._semantic_adapter._smart_turn_pin_count == 0
    assert detector.smart_turn_readiness is SmartTurnReadiness.UNLOADED
    await detector.close()


async def test_reset_does_not_wait_for_blocked_session_smart_turn_load() -> None:
    coordinator = _BlockingSemanticCoordinator(block_prepare=True)
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=coordinator,
        on_turn_complete=AsyncMock(),
    )
    pin_task = asyncio.create_task(detector.pin_endpointing_session())
    await asyncio.wait_for(coordinator.prepare_started.wait(), 1)

    await asyncio.wait_for(detector.reset(), 0.1)

    assert detector._semantic_adapter._smart_turn_pin_count == 0
    coordinator.prepare_release.set()
    assert await asyncio.wait_for(pin_task, 1) is False
    assert detector.smart_turn_readiness is SmartTurnReadiness.UNLOADED
    await detector.close()


async def test_close_releases_session_and_turn_smart_turn_pins() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_turn_complete=AsyncMock(),
    )
    token = VoiceTurnToken(_ingress_token(), turn_id=1)
    assert await detector.pin_endpointing_session() is True
    lease = await detector.prepare_endpointing(token)
    assert lease is not None
    adapter = detector._semantic_adapter
    assert adapter._smart_turn_pin_count == 2

    await detector.close()

    assert adapter._smart_turn_pin_count == 0
    assert detector.smart_turn_readiness is SmartTurnReadiness.UNLOADED


async def test_smart_turn_loading_does_not_hold_detector_audio_submission() -> None:
    coordinator = _BlockingSemanticCoordinator(block_prepare=True)
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=coordinator,
        on_turn_complete=AsyncMock(),
    )
    turn_token = VoiceTurnToken(_ingress_token(), turn_id=1)
    prepare_task = asyncio.create_task(detector.prepare_endpointing(turn_token))
    await asyncio.wait_for(coordinator.prepare_started.wait(), 1)

    submitted = await asyncio.wait_for(
        detector.submit_audio(
            b"\x01\x00" * 160,
            ingress_token=_ingress_token(),
            sample_rate_hz=16_000,
            speech_probability=0.9,
            rnnoise_available=True,
        ),
        0.1,
    )

    assert submitted.status is DetectorSubmitStatus.ACCEPTED
    assert detector.smart_turn_readiness is SmartTurnReadiness.LOADING
    assert detector.endpointing_ready(turn_token) is False
    coordinator.prepare_release.set()
    lease = await asyncio.wait_for(prepare_task, 1)
    assert lease is not None
    await lease.release()
    await detector.close()


async def test_reset_cancels_turn_prepare_without_waiting_for_model_load() -> None:
    coordinator = _BlockingSemanticCoordinator(block_prepare=True)
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=coordinator,
        on_turn_complete=AsyncMock(),
    )
    token = VoiceTurnToken(_ingress_token(), turn_id=1)
    prepare = asyncio.create_task(detector.prepare_endpointing(token))
    await asyncio.wait_for(coordinator.prepare_started.wait(), 1)

    await asyncio.wait_for(detector.reset(), 0.1)

    with pytest.raises(asyncio.CancelledError):
        await prepare
    assert detector._prepare_task is None
    assert detector._semantic_adapter._smart_turn_pin_count == 0
    assert detector.smart_turn_readiness is SmartTurnReadiness.UNLOADED
    await detector.close()


async def test_scoped_detector_events_bind_before_logical_complete() -> None:
    events: list[DetectorActivityEvent | DetectorTurnEvent] = []
    detector: DetectorRuntime
    turn_token = VoiceTurnToken(_ingress_token(), turn_id=7)

    async def on_event(event) -> None:
        events.append(event)
        if (
            isinstance(event, DetectorActivityEvent)
            and event.activity is SpeechActivityEvent.SPEECH_STARTED
        ):
            assert await detector.bind_candidate(event.candidate, turn_token)

    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(
            (
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.CANDIDATE_PAUSE,
            )
        ),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_event=on_event,
    )
    lease = await detector.prepare_endpointing(turn_token)
    assert lease is not None

    submitted = await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    assert submitted.status is DetectorSubmitStatus.ACCEPTED
    for _ in range(100):
        if any(isinstance(event, DetectorTurnEvent) for event in events):
            break
        await asyncio.sleep(0.001)

    assert [type(event) for event in events] == [
        DetectorPrewarmEvent,
        DetectorActivityEvent,
        DetectorActivityEvent,
        DetectorTurnEvent,
    ]
    complete = events[-1]
    assert isinstance(complete, DetectorTurnEvent)
    assert complete.bound_turn.turn_token == turn_token
    assert detector._bound_turns == {}
    assert detector._deferred_completions == {}
    assert await detector.bind_candidate(complete.bound_turn.candidate, turn_token) is None
    await lease.release()
    await detector.close()


async def test_deferred_completion_consumes_binding_when_candidate_binds_late() -> None:
    events: list[object] = []

    async def on_event(event: object) -> None:
        events.append(event)

    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(
            (
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.CANDIDATE_PAUSE,
            )
        ),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_event=on_event,
    )
    submitted = await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    assert submitted.candidate is not None
    async with asyncio.timeout(1):
        while submitted.candidate not in detector._deferred_completions:
            await asyncio.sleep(0)

    turn_token = VoiceTurnToken(_ingress_token(), turn_id=11)
    bound = await detector.bind_candidate(submitted.candidate, turn_token)

    assert bound is not None
    completed = [event for event in events if isinstance(event, DetectorTurnEvent)]
    assert len(completed) == 1
    assert completed[0].bound_turn == bound
    assert detector._bound_turns == {}
    assert detector._deferred_completions == {}
    assert await detector.bind_candidate(submitted.candidate, turn_token) is None
    await detector.close()


async def test_bound_completion_is_idempotent_and_rejects_stale_epoch() -> None:
    events: list[DetectorTurnEvent] = []

    async def on_event(event: object) -> None:
        if isinstance(event, DetectorTurnEvent):
            events.append(event)

    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_event=on_event,
    )
    candidate = DetectorCandidateKey(detector.detector_epoch, 0)
    turn_token = VoiceTurnToken(_ingress_token(), turn_id=12)
    identity = DetectorIngressIdentity(
        _ingress_token(),
        detector.detector_epoch,
        sequence_no=1,
    )
    assert await detector.bind_candidate(candidate, turn_token) is not None

    assert await detector._publish_bound_completion(candidate, identity) is True
    assert await detector._publish_bound_completion(candidate, identity) is False
    stale = DetectorCandidateKey(detector.detector_epoch + 1, 0)
    assert await detector.bind_candidate(stale, turn_token) is None

    assert len(events) == 1
    assert detector._bound_turns == {}
    assert detector._deferred_completions == {}
    await detector.close()


async def test_completion_callback_error_does_not_retain_binding() -> None:
    async def on_event(event: object) -> None:
        if isinstance(event, DetectorTurnEvent):
            raise RuntimeError("completion consumer failed")

    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_event=on_event,
    )
    candidate = DetectorCandidateKey(detector.detector_epoch, 0)
    turn_token = VoiceTurnToken(_ingress_token(), turn_id=13)
    identity = DetectorIngressIdentity(
        _ingress_token(),
        detector.detector_epoch,
        sequence_no=1,
    )
    assert await detector.bind_candidate(candidate, turn_token) is not None

    with pytest.raises(RuntimeError, match="completion consumer failed"):
        await detector._publish_bound_completion(candidate, identity)

    assert detector._bound_turns == {}
    assert detector._deferred_completions == {}
    await detector.close()


async def test_completed_candidate_bindings_stay_bounded_across_100_turns() -> None:
    completed: list[DetectorTurnEvent] = []
    detector: DetectorRuntime
    next_turn_id = 0

    async def on_event(event: object) -> None:
        nonlocal next_turn_id
        if (
            isinstance(event, DetectorActivityEvent)
            and event.activity is SpeechActivityEvent.SPEECH_STARTED
        ):
            next_turn_id += 1
            bound = await detector.bind_candidate(
                event.candidate,
                VoiceTurnToken(_ingress_token(), turn_id=next_turn_id),
            )
            assert bound is not None
        elif isinstance(event, DetectorTurnEvent):
            completed.append(event)

    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(
            (
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.CANDIDATE_PAUSE,
            )
        ),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_event=on_event,
    )

    for turn_index in range(100):
        await detector.submit_audio(
            bytes((turn_index % 255 + 1, 0)) * 160,
            ingress_token=_ingress_token(),
            sample_rate_hz=16_000,
            speech_probability=0.9,
            rnnoise_available=True,
        )
        async with asyncio.timeout(1):
            while len(completed) <= turn_index:
                await asyncio.sleep(0)
        assert detector._bound_turns == {}
        assert detector._deferred_completions == {}
        await detector.release_deferred_turn()

    assert len(completed) == 100
    await detector.close()


async def test_smart_turn_readiness_is_pinned_to_one_logical_turn() -> None:
    coordinator = _SemanticCoordinator()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=AsrProviderPolicy(
            transport="streaming",
            endpoint_authority="smart_turn",
            smart_turn_required=True,
            max_segment_ms=None,
            warm_transport_ms=25_000,
            replay_policy="preconnect_only",
        ),
        coordinator=coordinator,
        on_turn_complete=AsyncMock(),
    )
    token = VoiceTurnToken(
        VoiceIngressToken(1, "socket", 1, 1, 1),
        turn_id=1,
    )

    lease = await detector.prepare_endpointing(token)

    assert lease is not None
    assert detector.smart_turn_readiness is SmartTurnReadiness.READY
    assert detector.endpointing_ready(token) is True
    await lease.release()
    assert detector.endpointing_ready(token) is False
    assert detector.smart_turn_readiness is SmartTurnReadiness.UNLOADED

    next_token = VoiceTurnToken(token.ingress, turn_id=2)
    next_lease = await detector.prepare_endpointing(next_token)

    assert next_lease is not None
    assert coordinator.prepare_calls == 2
    await detector.reset()
    assert detector.smart_turn_readiness is SmartTurnReadiness.UNLOADED
    await next_lease.release()
    await detector.close()


async def test_cancelled_smart_turn_release_can_be_retried() -> None:
    token = VoiceTurnToken(
        VoiceIngressToken(1, "socket", 1, 1, 1),
        turn_id=1,
    )
    runtime = SimpleNamespace(
        release_endpointing=AsyncMock(
            side_effect=[asyncio.CancelledError(), None],
        )
    )
    lease = SmartTurnLease(token, runtime)

    with pytest.raises(asyncio.CancelledError):
        await lease.release()
    assert lease._released is False

    await lease.release()

    assert lease._released is True
    assert runtime.release_endpointing.await_count == 2


async def test_smart_turn_prepare_failure_never_becomes_ready() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=AsrProviderPolicy(
            transport="segmented",
            endpoint_authority="smart_turn",
            smart_turn_required=True,
            max_segment_ms=27_000,
            warm_transport_ms=0,
            replay_policy="none",
        ),
        coordinator=_SemanticCoordinator(available=False),
        on_turn_complete=AsyncMock(),
    )
    token = VoiceTurnToken(
        VoiceIngressToken(1, "socket", 1, 1, 1),
        turn_id=1,
    )

    assert await detector.prepare_endpointing(token) is None
    assert detector.smart_turn_readiness is SmartTurnReadiness.FAILED
    assert detector.endpointing_ready(token) is False
    await detector.close()


async def test_smart_turn_prepare_exception_cleans_task_and_pin() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=_RaisingPrepareCoordinator(),
        on_turn_complete=AsyncMock(),
    )
    token = VoiceTurnToken(_ingress_token(), turn_id=1)

    assert await detector.prepare_endpointing(token) is None
    assert detector.smart_turn_readiness is SmartTurnReadiness.FAILED
    assert detector._prepare_task is None
    assert detector._prepare_token is None
    assert detector._semantic_adapter._smart_turn_pin_count == 0
    await detector.close()


async def test_close_cancels_prepare_after_cleanup() -> None:
    coordinator = _BlockingSemanticCoordinator(block_prepare=True)
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=coordinator,
        on_turn_complete=AsyncMock(),
    )
    token = VoiceTurnToken(_ingress_token(), turn_id=1)
    prepare = asyncio.create_task(detector.prepare_endpointing(token))
    await asyncio.wait_for(coordinator.prepare_started.wait(), 1)

    await detector.close()

    with pytest.raises(asyncio.CancelledError):
        await prepare
    assert detector._prepare_task is None
    assert detector._prepare_token is None
    assert detector._semantic_adapter._smart_turn_pin_count == 0
    assert detector.smart_turn_readiness is SmartTurnReadiness.UNLOADED


async def test_overflow_reset_rejects_audio_until_barrier_finishes() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_turn_complete=AsyncMock(),
    )
    adapter = _OverflowAdapter()
    detector._semantic_adapter = adapter
    detector._semantic_started = True

    first = await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    await asyncio.wait_for(adapter.reset_started.wait(), 1)
    second = await detector.submit_audio(
        b"\x02\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )

    assert first.status is DetectorSubmitStatus.BACKPRESSURE
    assert second.status is DetectorSubmitStatus.BACKPRESSURE
    assert adapter.push_calls == 1

    overflow_reset = detector._overflow_reset_task
    assert overflow_reset is not None
    adapter.reset_release.set()
    await asyncio.wait_for(overflow_reset, 1)
    third = await detector.submit_audio(
        b"\x03\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    assert third.status is DetectorSubmitStatus.ACCEPTED
    assert adapter.push_calls == 2
    await detector.close()


async def test_silero_unavailable_keeps_periodic_smart_turn_authority() -> None:
    completed = asyncio.Event()
    detector = DetectorRuntime(
        vad=_Vad(available=False),
        gate=_Gate(),
        provider_policy=AsrProviderPolicy(
            transport="streaming",
            endpoint_authority="smart_turn",
            smart_turn_required=True,
            max_segment_ms=None,
            warm_transport_ms=25_000,
            replay_policy="preconnect_only",
        ),
        coordinator=_SemanticCoordinator(),
        on_turn_complete=lambda: completed.set() or asyncio.sleep(0),
    )
    token = VoiceTurnToken(
        VoiceIngressToken(1, "socket", 1, 1, 1),
        turn_id=1,
    )
    lease = await detector.prepare_endpointing(token)
    assert lease is not None

    result = await detector.feed(b"\x01\x00" * 8_000)
    await detector.feed(b"\x02\x00" * 160)
    await asyncio.wait_for(completed.wait(), 1)

    assert result.throttle_available is False
    assert result.endpointing_available is True
    assert result.events == (SpeechActivityEvent.SPEECH_STARTED,)
    assert detector.endpointing_ready(token) is True
    await lease.release()
    await detector.close()


def test_custom_vad_requires_matching_gate() -> None:
    with pytest.raises(ValueError, match="gate is required"):
        DetectorRuntime(vad=_Vad())


async def test_detector_validates_pcm_and_accepts_empty_frames() -> None:
    detector = DetectorRuntime(vad=_Vad(), gate=_Gate())

    with pytest.raises(ValueError, match="complete PCM16"):
        await detector.feed(b"\x00")
    assert await detector.feed(b"") == DetectorFeedResult((), True)


async def test_detector_latches_load_and_inference_failures() -> None:
    load_failed = DetectorRuntime(vad=_FailingVad(), gate=_Gate())
    inference_failed = DetectorRuntime(vad=_Vad(), gate=_FailingGate())

    assert (await load_failed.feed(b"\x00\x00")).throttle_available is False
    assert (await inference_failed.feed(b"\x00\x00")).throttle_available is False
    assert (await inference_failed.feed(b"\x00\x00")).events == ()


async def test_detector_reset_and_close_are_idempotent() -> None:
    vad = _Vad()
    gate = _Gate()
    detector = DetectorRuntime(vad=vad, gate=gate)

    await detector.reset()
    await detector.close()
    await detector.reset()
    await detector.close()

    assert vad.closed is True
    assert (await detector.feed(b"\x00\x00")).throttle_available is False
