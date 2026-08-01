from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client.endpointing.detector_runtime import (
    DetectorFeedResult,
    DetectorRuntime,
    SmartTurnLease,
    SmartTurnReadiness,
    _AudioItem,
    _ResetItem,
    _VoiceTurnAdapter,
)
from main_logic.asr_client.endpointing.detector import (
    DetectorActivityEvent,
    DetectorIngressIdentity,
    DetectorPrewarmEvent,
    DetectorSubmitStatus,
    DetectorTurnEvent,
    ProviderCandidateFence,
)
from main_logic.asr_client.lifecycle import VoiceIngressToken, VoiceTurnToken
from main_logic.asr_client.provider_policy import AsrProviderPolicy
from main_logic.asr_client.endpointing.throttle_policy import (
    ThrottleAction,
    VoiceThrottlePolicy,
)
from main_logic.voice_turn.activity_evidence import RnnoiseEvidence
from main_logic.voice_turn.contracts import (
    EvaluationStatus,
    SpeechActivityEvent,
    TurnDecision,
)
from main_logic.asr_client.endpointing.coordinator import CoordinatorState


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

    def feed(self, pcm16: bytes):
        self.inputs.append(pcm16)
        return self.events

    def reset(self) -> None:
        return None


class _FailingVad(_Vad):
    def load(self) -> bool:
        raise RuntimeError("load failed")


class _FailingGate(_Gate):
    def feed(self, pcm16: bytes):
        raise RuntimeError("feed failed")


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
        self.prepare_calls += 1
        self.prepare_started.set()
        await self.prepare_release.wait()
        return True


class _ResetClearsSemanticCoordinator(_SemanticCoordinator):
    def __init__(self) -> None:
        super().__init__()
        self.reset_calls = 0

    async def reset(self) -> None:
        self.reset_calls += 1
        self.audio.clear()
        await super().reset()


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


def _smart_turn_policy() -> AsrProviderPolicy:
    return AsrProviderPolicy(
        transport="segmented",
        endpoint_authority="smart_turn",
        smart_turn_required=True,
        max_segment_ms=30_000,
        warm_transport_ms=0,
        replay_policy="none",
    )


def _provider_endpoint_policy() -> AsrProviderPolicy:
    return AsrProviderPolicy(
        transport="streaming",
        endpoint_authority="provider",
        smart_turn_required=False,
        max_segment_ms=None,
        warm_transport_ms=25_000,
        replay_policy="preconnect_only",
    )


def _ingress_token() -> VoiceIngressToken:
    return VoiceIngressToken(1, "socket", 1, 1, 1)


async def test_provider_prewarm_keeps_candidate_open_until_silero_confirms() -> None:
    gate = _Gate()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        provider_policy=_provider_endpoint_policy(),
    )
    ingress = _ingress_token()

    prewarm = await detector.feed(
        b"\x01\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.9,
            available=True,
        ),
        ingress_token=ingress,
    )
    followup = await detector.feed(
        b"\x02\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.05,
            available=True,
        ),
        ingress_token=ingress,
    )

    assert prewarm.throttle_action is ThrottleAction.PREWARM
    assert followup.throttle_action is ThrottleAction.KEEP_CANDIDATE_OPEN
    assert gate.inputs == [b"\x01\x00", b"\x02\x00"]
    assert detector.candidate_open is True
    await detector.close()


async def test_stale_provider_ingress_does_not_mutate_throttle_policy() -> None:
    policy = VoiceThrottlePolicy(
        resource_optimization_enabled=True,
        minimum_baseline_samples=1,
    )
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_provider_endpoint_policy(),
        throttle_policy=policy,
    )
    current_ingress = _ingress_token()
    await detector.feed(
        b"\x01\x00",
        rnnoise_evidence=RnnoiseEvidence.unavailable(),
        ingress_token=current_ingress,
    )
    before = policy.shadow_metrics

    stale = await detector.feed(
        b"\x02\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.05,
            available=True,
        ),
        ingress_token=VoiceIngressToken(1, "stale-socket", 1, 1, 1),
        allow_baseline_update=True,
    )

    assert stale.endpointing_available is False
    assert policy.shadow_metrics == before
    assert policy.baseline is None
    await detector.close()


async def test_completion_fence_replays_pcm_consumed_during_inference() -> None:
    coordinator = _BlockingSemanticCoordinator()
    gate = _Gate((SpeechActivityEvent.CANDIDATE_PAUSE,))
    completed = asyncio.Event()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        provider_policy=_smart_turn_policy(),
        coordinator=coordinator,
        on_turn_complete=lambda: completed.set() or asyncio.sleep(0),
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


async def test_successor_fence_uses_active_identity_for_queued_pause() -> None:
    coordinator = _BlockingSemanticCoordinator()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate((SpeechActivityEvent.CANDIDATE_PAUSE,)),
        provider_policy=_smart_turn_policy(),
        coordinator=coordinator,
        on_turn_complete=AsyncMock(),
    )
    adapter = detector._semantic_adapter
    assert isinstance(adapter, _VoiceTurnAdapter)
    await adapter.start()
    predecessor_identity = (0, 0, 1)
    active_identity = (1, 0, 2)
    adapter._identity = active_identity
    adapter._successor_audio_fence = (
        predecessor_identity,
        1,
        active_identity,
    )

    await adapter._process_audio(
        _AudioItem(
            identity=predecessor_identity,
            pcm16=b"\x01\x00" * 160,
            duration_us=10_000,
            detector_identity=DetectorIngressIdentity(
                _ingress_token(),
                detector_epoch=0,
                sequence_no=2,
            ),
        )
    )

    await asyncio.wait_for(coordinator.evaluate_started.wait(), 1)
    assert adapter._evaluation_task is not None
    coordinator.evaluate_release.set()
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
    successor = await detector.submit_audio(
        b"\x02\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    completion_release.set()
    await asyncio.wait_for(completion_published.wait(), 1)
    followup = await detector.submit_audio(
        b"\x03\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.0,
        rnnoise_available=True,
    )

    completed = [event for event in events if isinstance(event, DetectorTurnEvent)]
    assert first.candidate is not None
    assert successor.candidate is not None
    assert successor.candidate.candidate_generation == (
        first.candidate.candidate_generation + 1
    )
    assert followup.status is DetectorSubmitStatus.ACCEPTED
    assert detector.candidate_open is True
    assert len(completed) == 1
    assert completed[0].bound_turn.candidate == first.candidate
    await detector.close()


async def test_completion_fence_clears_activity_without_successor_pcm() -> None:
    completed = asyncio.Event()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate((SpeechActivityEvent.CANDIDATE_PAUSE,)),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_turn_complete=lambda: completed.set() or asyncio.sleep(0),
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


async def test_completed_candidate_bindings_stay_bounded() -> None:
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
            bytes((turn_index + 1, 0)) * 160,
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


async def test_provider_candidate_fence_preserves_post_discard_successor() -> None:
    gate = _Gate((SpeechActivityEvent.SPEECH_STARTED,))
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        provider_policy=_provider_endpoint_policy(),
    )
    evidence = RnnoiseEvidence.from_legacy_probability(0.9, available=True)
    await detector.feed(
        b"\x01\x00",
        rnnoise_evidence=evidence,
        ingress_token=_ingress_token(),
    )
    fence = await detector.seal_provider_candidate()
    assert isinstance(fence, ProviderCandidateFence)

    await detector.feed(
        b"\x02\x00",
        rnnoise_evidence=evidence,
        ingress_token=_ingress_token(),
    )
    assert await detector.discard_provider_successor(fence) is True
    await detector.feed(
        b"\x03\x00",
        rnnoise_evidence=evidence,
        ingress_token=_ingress_token(),
    )

    assert await detector.complete_provider_candidate(fence) is True
    assert await detector.complete_provider_candidate(fence) is None
    assert detector._speech_active is True
    await detector.close()


async def test_provider_fence_preserves_quiet_pcm_then_closes_candidate() -> None:
    gate = _Gate()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        provider_policy=_provider_endpoint_policy(),
    )
    ingress = _ingress_token()
    await detector.feed(
        b"\x01\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.9,
            available=True,
        ),
        ingress_token=ingress,
    )
    fence = await detector.seal_provider_candidate()
    assert fence is not None

    successor = await detector.feed(
        b"\x02\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.05,
            available=True,
        ),
        ingress_token=ingress,
    )

    assert successor.throttle_action is ThrottleAction.KEEP_CANDIDATE_OPEN
    assert gate.inputs == [b"\x01\x00", b"\x02\x00"]
    assert await detector.complete_provider_candidate(fence) is True
    assert detector.candidate_open is False
    quiet = await detector.feed(
        b"\x03\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.05,
            available=True,
        ),
        ingress_token=ingress,
        allow_baseline_update=True,
    )
    assert quiet.throttle_action is ThrottleAction.SKIP_IDLE_PCM
    assert gate.inputs == [b"\x01\x00", b"\x02\x00"]
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
    assert await detector.complete_provider_candidate(fence) is False
    gate.events = ()
    before = len(gate.inputs)

    quiet = await detector.feed(
        b"\x02\x00",
        rnnoise_evidence=RnnoiseEvidence.from_legacy_probability(
            0.05,
            available=True,
        ),
        ingress_token=_ingress_token(),
        allow_baseline_update=True,
    )

    assert quiet.throttle_action is ThrottleAction.SKIP_IDLE_PCM
    assert len(gate.inputs) == before
    await detector.close()


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


async def test_disabled_resource_optimization_never_skips_quiet_silero_pcm() -> None:
    gate = _Gate((SpeechActivityEvent.SPEECH_STARTED,))
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        rnnoise_onset_probability=0.4,
        resource_optimization_enabled=False,
    )

    result = await detector.feed(
        b"\x01\x00",
        speech_probability=0.1,
        rnnoise_available=True,
    )

    assert gate.inputs == [b"\x01\x00"]
    assert result.events == (SpeechActivityEvent.SPEECH_STARTED,)


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


async def test_active_speech_still_feeds_silero_when_rnnoise_probability_drops() -> None:
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


async def test_smart_turn_activity_updates_throttle_shadow_metrics() -> None:
    policy = VoiceThrottlePolicy(resource_optimization_enabled=True)
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate((SpeechActivityEvent.SPEECH_STARTED,)),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_turn_complete=AsyncMock(),
        throttle_policy=policy,
    )

    result = await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    adapter = detector._semantic_adapter
    assert adapter is not None
    await adapter.wait_idle()
    second_result = await detector.submit_audio(
        b"\x02\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    await adapter.wait_idle()

    assert result.status is DetectorSubmitStatus.ACCEPTED
    assert second_result.status is DetectorSubmitStatus.ACCEPTED
    assert detector.throttle_shadow_metrics.rnnoise_trigger_count == 2
    assert detector.throttle_shadow_metrics.silero_trigger_count == 1
    assert detector.throttle_shadow_metrics.rnnoise_silero_disagreement_count == 1
    await detector.close()


async def test_disabled_resource_optimization_never_skips_quiet_smart_turn_pcm() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_turn_complete=AsyncMock(),
        resource_optimization_enabled=False,
    )

    result = await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.1,
        rnnoise_available=True,
    )

    assert result.status is DetectorSubmitStatus.ACCEPTED
    await detector.close()


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
    assert not detector._bound_turns
    await lease.release()
    await detector.close()


async def test_late_candidate_binding_retires_deferred_completion() -> None:
    events: list[DetectorActivityEvent | DetectorTurnEvent] = []
    candidate = None
    turn_token = VoiceTurnToken(_ingress_token(), turn_id=8)

    async def on_event(event) -> None:
        nonlocal candidate
        events.append(event)
        if (
            isinstance(event, DetectorActivityEvent)
            and event.activity is SpeechActivityEvent.SPEECH_STARTED
        ):
            candidate = event.candidate

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
        if candidate is not None and candidate in detector._deferred_completions:
            break
        await asyncio.sleep(0.001)

    assert candidate is not None
    assert candidate in detector._deferred_completions
    bound = await detector.bind_candidate(candidate, turn_token)

    assert bound is not None
    assert bound.turn_token == turn_token
    assert candidate not in detector._bound_turns
    assert candidate not in detector._deferred_completions
    assert sum(isinstance(event, DetectorTurnEvent) for event in events) == 1
    await lease.release()
    await detector.close()


async def test_deferred_completion_retires_candidate_before_third_turn() -> None:
    events: list[DetectorActivityEvent | DetectorTurnEvent] = []
    candidates = []
    detector: DetectorRuntime
    turn_tokens = [
        VoiceTurnToken(_ingress_token(), turn_id=turn_id)
        for turn_id in range(1, 4)
    ]

    async def on_event(event) -> None:
        events.append(event)
        if (
            isinstance(event, DetectorActivityEvent)
            and event.activity is SpeechActivityEvent.SPEECH_STARTED
        ):
            turn_token = turn_tokens[len(candidates)]
            candidates.append(event.candidate)
            bound = await detector.bind_candidate(event.candidate, turn_token)
            assert bound is not None
            assert bound.turn_token == turn_token

    gate = _Gate(
        (
            SpeechActivityEvent.SPEECH_STARTED,
            SpeechActivityEvent.CANDIDATE_PAUSE,
        )
    )
    coordinator = _ResetClearsSemanticCoordinator()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=gate,
        provider_policy=_smart_turn_policy(),
        coordinator=coordinator,
        on_event=on_event,
    )

    await detector.feed(b"\x01\x00" * 160)
    assert detector._candidate_generation == 1

    await detector.feed(b"\x02\x00" * 160)
    async with asyncio.timeout(1):
        while not detector._deferred_turn_complete:
            await asyncio.sleep(0)
    deferred_candidate = candidates[1]
    assert detector._candidate_generation == 2
    assert deferred_candidate not in detector._bound_turns

    gate.events = ()
    third_pcm = b"\x03\x00" * 160
    await detector.feed(third_pcm)
    reset_calls = coordinator.reset_calls

    await detector.release_deferred_turn()
    assert detector._candidate_generation == 2
    assert deferred_candidate not in detector._bound_turns
    assert deferred_candidate not in detector._deferred_completions
    assert coordinator.reset_calls == reset_calls
    assert third_pcm in coordinator.audio

    gate.events = (
        SpeechActivityEvent.SPEECH_STARTED,
        SpeechActivityEvent.CANDIDATE_PAUSE,
    )
    await detector.feed(b"\x04\x00" * 160)

    assert [candidate.candidate_generation for candidate in candidates] == [0, 1, 2]
    assert [
        event.bound_turn.turn_token.turn_id
        for event in events
        if isinstance(event, DetectorTurnEvent)
    ] == [1, 2, 3]
    await detector.close()


async def test_smart_turn_readiness_is_pinned_to_one_logical_turn() -> None:
    coordinator = _SemanticCoordinator()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
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


async def test_provider_authority_streaming_never_builds_or_prepares_smart_turn() -> None:
    coordinator = _SemanticCoordinator()
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate((SpeechActivityEvent.CANDIDATE_PAUSE,)),
        provider_policy=AsrProviderPolicy(
            transport="streaming",
            endpoint_authority="provider",
            smart_turn_required=False,
            max_segment_ms=None,
            warm_transport_ms=25_000,
            replay_policy="preconnect_only",
        ),
        coordinator=coordinator,
        on_turn_complete=AsyncMock(),
    )

    result = await detector.feed(b"\x01\x00" * 160)

    assert result.events == (SpeechActivityEvent.CANDIDATE_PAUSE,)
    assert detector._semantic_adapter is None
    assert detector.smart_turn_readiness is SmartTurnReadiness.UNLOADED
    assert coordinator.prepare_calls == 0
    assert coordinator.audio == []
    assert coordinator.state is CoordinatorState.IDLE
    await detector.close()
    assert coordinator.state is CoordinatorState.IDLE


async def test_manual_streaming_provider_builds_and_prepares_smart_turn() -> None:
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
    token = VoiceTurnToken(_ingress_token(), turn_id=1)

    lease = await detector.prepare_endpointing(token)

    assert lease is not None
    assert detector.smart_turn_readiness is SmartTurnReadiness.READY
    assert detector.endpointing_ready(token) is True
    assert coordinator.prepare_calls == 1
    await lease.release()
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


async def test_reset_during_inflight_prepare_does_not_block_next_turn() -> None:
    coordinator = _BlockingSemanticCoordinator(block_prepare=True)
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=coordinator,
        on_turn_complete=AsyncMock(),
    )
    first_token = VoiceTurnToken(_ingress_token(), turn_id=1)
    first = asyncio.create_task(detector.prepare_endpointing(first_token))
    await asyncio.wait_for(coordinator.prepare_started.wait(), 1)

    await detector.reset()
    assert detector._prepare_task is not None
    assert detector._prepare_token is None

    next_token = VoiceTurnToken(_ingress_token(), turn_id=2)
    second = asyncio.create_task(detector.prepare_endpointing(next_token))
    await asyncio.sleep(0)
    coordinator.prepare_release.set()

    lease = await asyncio.wait_for(second, 1)
    assert lease is not None
    assert detector.smart_turn_readiness is SmartTurnReadiness.READY
    assert detector.endpointing_ready(next_token) is True
    # The superseded waiter loses its turn without raising or going fatal.
    assert await asyncio.wait_for(first, 1) is None
    assert detector._semantic_adapter._smart_turn_pin_count == 1
    await lease.release()
    assert detector._semantic_adapter._smart_turn_pin_count == 0
    await detector.close()


async def test_concurrent_same_token_prepare_calls_coalesce() -> None:
    coordinator = _BlockingSemanticCoordinator(block_prepare=True)
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=coordinator,
        on_turn_complete=AsyncMock(),
    )
    token = VoiceTurnToken(_ingress_token(), turn_id=1)
    first = asyncio.create_task(detector.prepare_endpointing(token))
    await asyncio.wait_for(coordinator.prepare_started.wait(), 1)
    second = asyncio.create_task(detector.prepare_endpointing(token))
    await asyncio.sleep(0)
    coordinator.prepare_release.set()

    first_lease = await asyncio.wait_for(first, 1)
    second_lease = await asyncio.wait_for(second, 1)
    assert first_lease is not None
    assert second_lease is not None
    assert coordinator.prepare_calls == 1
    await first_lease.release()
    await detector.close()


async def test_close_while_waiting_for_stale_prepare_cleans_up() -> None:
    coordinator = _BlockingSemanticCoordinator(block_prepare=True)
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=coordinator,
        on_turn_complete=AsyncMock(),
    )
    first_token = VoiceTurnToken(_ingress_token(), turn_id=1)
    first = asyncio.create_task(detector.prepare_endpointing(first_token))
    await asyncio.wait_for(coordinator.prepare_started.wait(), 1)
    await detector.reset()
    next_token = VoiceTurnToken(_ingress_token(), turn_id=2)
    second = asyncio.create_task(detector.prepare_endpointing(next_token))
    await asyncio.sleep(0)

    await detector.close()

    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(
        result is None or isinstance(result, asyncio.CancelledError)
        for result in results
    )
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


async def test_overflow_epoch_bump_clears_deferred_completion_flags() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_turn_complete=AsyncMock(),
    )
    adapter = _OverflowAdapter()
    adapter.reset_release.set()
    detector._semantic_adapter = adapter
    detector._semantic_started = True
    # A sealed turn is waiting for the provider final when the queue overflows.
    detector._defer_turn_complete = True
    detector._deferred_turn_complete = True

    result = await detector.submit_audio(
        b"\x01\x00" * 160,
        ingress_token=_ingress_token(),
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )

    assert result.status is DetectorSubmitStatus.BACKPRESSURE
    assert detector._defer_turn_complete is False
    assert detector._deferred_turn_complete is False
    overflow_reset = detector._overflow_reset_task
    assert overflow_reset is not None
    await asyncio.wait_for(overflow_reset, 1)

    # The stale deferred flag must not let release_deferred_turn advance the
    # fresh epoch's first candidate or generation.
    semantic_generation = detector._semantic_generation
    await detector.release_deferred_turn()
    assert detector._candidate_generation == 0
    assert detector._semantic_generation == semantic_generation
    await detector.close()


async def test_invalidate_clears_deferred_completion_flags() -> None:
    detector = DetectorRuntime(
        vad=_Vad(),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
        coordinator=_SemanticCoordinator(),
        on_turn_complete=AsyncMock(),
    )
    token = VoiceTurnToken(_ingress_token(), turn_id=1)
    lease = await detector.prepare_endpointing(token)
    assert lease is not None
    detector._defer_turn_complete = True
    detector._deferred_turn_complete = True
    epoch_before = detector.detector_epoch

    await detector.invalidate(token)

    assert detector.detector_epoch == epoch_before + 1
    assert detector._defer_turn_complete is False
    assert detector._deferred_turn_complete is False
    semantic_generation = detector._semantic_generation
    await detector.release_deferred_turn()
    assert detector._candidate_generation == 0
    assert detector._semantic_generation == semantic_generation
    await detector.close()


async def test_gate_reset_serializes_with_concurrent_feed() -> None:
    reset_started = threading.Event()
    reset_release = threading.Event()

    class _BlockingResetGate(_Gate):
        def reset(self) -> None:
            reset_started.set()
            reset_release.wait(timeout=5)

    gate = _BlockingResetGate()
    detector = DetectorRuntime(vad=_Vad(), gate=gate)
    reset_task = asyncio.create_task(detector.reset())
    await asyncio.to_thread(reset_started.wait, 1)
    feed_task = asyncio.create_task(detector.feed(b"\x01\x00" * 160))
    await asyncio.sleep(0.05)

    # The gate counters are unlocked, so feed must not run while reset is
    # still inside the gate.
    assert gate.inputs == []
    reset_release.set()
    await asyncio.wait_for(asyncio.gather(reset_task, feed_task), 1)
    assert gate.inputs == [b"\x01\x00" * 160]
    await detector.close()


async def test_stale_reset_consumed_after_newer_reset_keeps_new_identity() -> None:
    gate = _Gate()
    adapter = _VoiceTurnAdapter(
        vad=_Vad(),
        gate=gate,
        coordinator=_SemanticCoordinator(),
        on_commit=AsyncMock(),
    )
    await adapter.start()
    await adapter.push_audio(
        generation=1, buffer_epoch=0, utterance_id=1, pcm16=b"\x01\x00" * 160
    )
    await adapter.wait_idle()
    assert adapter._identity == (1, 0, 1)

    loop = asyncio.get_running_loop()
    stale_completed: asyncio.Future[None] = loop.create_future()
    newer_completed: asyncio.Future[None] = loop.create_future()
    # Racing resets both enqueue with priority, so the later-enqueued newer
    # reset is consumed first and the stale one afterwards.
    adapter._queue.put_control_nowait(
        _ResetItem((2, 0, 2), stale_completed), priority=True
    )
    adapter._queue.put_control_nowait(
        _ResetItem((3, 0, 3), newer_completed), priority=True
    )
    await asyncio.wait_for(asyncio.gather(newer_completed, stale_completed), 1)

    assert adapter._identity == (3, 0, 3)
    gate.inputs.clear()
    await adapter.push_audio(
        generation=3, buffer_epoch=0, utterance_id=3, pcm16=b"\x02\x00" * 160
    )
    await adapter.wait_idle()
    assert gate.inputs == [b"\x02\x00" * 160]
    await adapter.close()


async def test_silero_unavailable_keeps_periodic_smart_turn_authority() -> None:
    completed = asyncio.Event()
    detector = DetectorRuntime(
        vad=_Vad(available=False),
        gate=_Gate(),
        provider_policy=_smart_turn_policy(),
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
