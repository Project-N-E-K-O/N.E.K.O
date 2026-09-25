from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client.endpointing.admission_gate import AdmissionActivityGate
from main_logic.asr_client.endpointing.config import SmartTurnConfig
from main_logic.asr_client.endpointing.detector_runtime import DetectorRuntime
from main_logic.asr_client.endpointing.detector import CoreDetectorEventEnvelope
from main_logic.asr_client.lifecycle import (
    VoiceInputLifecycleController,
    VoiceRouteMode,
)
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.asr_client.runtime import AsrRuntimeCallbacks, IndependentAsrRuntime
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from main_logic.voice_turn.contracts import SpeechActivityEvent, VoiceIngressToken
from main_logic.voice_turn.admission import CandidateAdmission
from main_logic.voice_turn.transcript_admission import (
    assess_transcript,
    TranscriptDisposition,
)


class ProbabilityVad:
    """Only model inference is replaced; gate, runtime and queues are real."""

    def __init__(self):
        self.probability = 0.9
        self.pending = 0

    def load(self):
        return True

    def reset_stream(self):
        self.pending = 0

    def process_pcm16(self, pcm):
        self.pending += len(pcm) // 2
        count, self.pending = divmod(self.pending, 512)
        return [self.probability] * count

    def close(self):
        pass


def make_runtime(optimization, smart_turn=False):
    callbacks = AsrRuntimeCallbacks(
        display_name=lambda: "admission-test",
        on_prepare_turn=AsyncMock(return_value=True),
        on_partial=AsyncMock(),
        on_final=AsyncMock(),
        on_turn_abandoned=AsyncMock(),
        on_failure=AsyncMock(),
        on_status=AsyncMock(),
        on_lifecycle=AsyncMock(),
    )
    runtime = IndependentAsrRuntime(callbacks)
    policy = resolve_provider_policy("qwen", "manual" if smart_turn else "provider")
    runtime._asr_provider = "qwen"
    runtime._voice_input_resource_optimization_enabled = optimization
    lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        shadow_mode=False,
        resource_optimization_enabled=optimization,
    )
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle = lifecycle
    session = SimpleNamespace(
        is_ready=True,
        stream_audio=AsyncMock(),
        close=AsyncMock(),
        signal_user_activity_end=AsyncMock(),
    )
    runtime._asr_session = session
    vad = ProbabilityVad()
    gate = AdmissionActivityGate(vad, SmartTurnConfig(enabled=True))

    async def event_callback(event):
        assert runtime._asr_detector_dispatcher.submit_nowait(
            CoreDetectorEventEnvelope(
                event,
                detector,
                lifecycle,
                runtime._asr_session_epoch,
            )
        )

    if smart_turn:
        from tests.unit.test_asr_detector_runtime import (
            _SemanticCoordinator,
            _smart_turn_policy,
        )

        policy = _smart_turn_policy()
        lifecycle.provider_policy = policy
        coordinator = _SemanticCoordinator()
    else:
        coordinator = None
    detector = DetectorRuntime(
        vad=vad,
        gate=gate,
        provider_policy=policy,
        coordinator=coordinator,
        on_event=event_callback,
        resource_optimization_enabled=optimization,
        admission_enabled=True,
    )
    runtime._asr_detector = detector
    token = VoiceIngressToken(
        runtime._asr_session_epoch, "test", 1, 1, runtime._asr_audio_generation
    )
    return runtime, callbacks, session, vad, token


async def send(runtime, vad, token, probabilities):
    frames = []
    for index, probability in enumerate(probabilities):
        vad.probability = probability
        pcm = bytes([index % 127 + 1, 0]) * 512
        frames.append(pcm)
        await runtime.submit(ProcessedVoiceFrame(pcm, 16000, None), ingress_token=token)
        if (
            runtime._asr_detector is not None
            and runtime._asr_detector._semantic_adapter is not None
        ):
            await runtime._asr_detector._semantic_adapter.wait_idle()
        await runtime._asr_detector_dispatcher.wait_idle()
    await runtime._asr_audio_dispatcher.wait_idle()
    return b"".join(frames)


@pytest.mark.asyncio
@pytest.mark.parametrize("optimization", [True, False])
async def test_real_ingress_has_no_upload_or_prepare_before_admission_and_preserves_prefix(
    optimization,
):
    runtime, callbacks, session, vad, token = make_runtime(optimization)
    try:
        first = await send(runtime, vad, token, [0.9] * 6)
        callbacks.on_prepare_turn.assert_not_awaited()
        session.stream_audio.assert_not_awaited()
        last = await send(runtime, vad, token, [0.9])
        callbacks.on_prepare_turn.assert_awaited_once()
        delivered = b"".join(
            call.args[0] for call in session.stream_audio.await_args_list
        )
        assert delivered == first + last
        callbacks.on_failure.assert_not_awaited()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_32ms_after_final_does_not_prepare_or_upload_another_turn():
    runtime, callbacks, session, vad, token = make_runtime(False)
    try:
        await send(runtime, vad, token, [0.9] * 7 + [0.1] * 10)
        await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
        await runtime._handle_independent_asr_final(
            "sentence", runtime._asr_session_epoch, "qwen"
        )
        await runtime.wait_transcript_idle()
        uploaded = session.stream_audio.await_count
        prepared = callbacks.on_prepare_turn.await_count
        await send(runtime, vad, token, [0.9])
        assert callbacks.on_prepare_turn.await_count == prepared == 1
        assert session.stream_audio.await_count == uploaded
        assert callbacks.on_final.await_args.args[0].evidence.voiced_audio_ms == 224
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_rejected_candidates_do_not_leak_prefix_into_later_speech():
    runtime, callbacks, session, vad, token = make_runtime(True)
    try:
        await send(runtime, vad, token, ([0.9] + [0.4] * 4) * 5)
        callbacks.on_prepare_turn.assert_not_awaited()
        session.stream_audio.assert_not_awaited()
        speech = await send(runtime, vad, token, [0.9] * 7)
        callbacks.on_prepare_turn.assert_awaited_once()
        assert (
            b"".join(call.args[0] for call in session.stream_audio.await_args_list)
            == speech
        )
    finally:
        await runtime.close()


def test_raw_resume_still_reaches_semantic_detector_while_new_admission_waits():
    vad = ProbabilityVad()
    gate = AdmissionActivityGate(vad, SmartTurnConfig(enabled=True))
    gate.process_probabilities([0.9] * 7 + [0.1] * 10)
    gate.seal_admission()
    assert gate.process_probabilities([0.9]) == (SpeechActivityEvent.SPEECH_RESUMED,)
    assert gate.admission_events == ()
    gate.process_probabilities([0.9] * 6)
    assert gate.admission_events == (SpeechActivityEvent.SPEECH_STARTED,)


@pytest.mark.parametrize(
    "text", ["停", "停止。", "取消", "打断", "别说了", "coffee", "嗯，我想……"]
)
def test_transcript_policy_preserves_controls_and_content(text):
    evidence = CandidateAdmission("test").observe(0, 512, 0.9)
    assert (
        assess_transcript(text, evidence, is_voice_source=True, final=True).disposition
        is TranscriptDisposition.ALLOW
    )


def test_suspicious_filler_hold_final_reject_and_missing_evidence_allow():
    tracker = CandidateAdmission("test")
    weak = tracker.observe(0, 512, 0.9)
    assert (
        assess_transcript(" 嗯。 ", weak, is_voice_source=True, final=False).disposition
        is TranscriptDisposition.HOLD
    )
    assert (
        assess_transcript(" 嗯。 ", weak, is_voice_source=True, final=True).disposition
        is TranscriptDisposition.REJECT
    )
    assert (
        assess_transcript("嗯。", None, is_voice_source=True, final=True).disposition
        is TranscriptDisposition.ALLOW
    )
    assert (
        assess_transcript("嗯。", weak, is_voice_source=False, final=True).disposition
        is TranscriptDisposition.ALLOW
    )
    for i in range(1, 7):
        strong = tracker.observe(i * 512, (i + 1) * 512, 0.9)
    assert (
        assess_transcript("嗯。", strong, is_voice_source=True, final=True).disposition
        is TranscriptDisposition.ALLOW
    )


@pytest.mark.asyncio
async def test_smart_turn_uses_same_admission_and_keeps_ordered_prefix():
    runtime, callbacks, session, vad, token = make_runtime(False, smart_turn=True)
    try:
        first = await send(runtime, vad, token, [0.9] * 6)
        callbacks.on_prepare_turn.assert_not_awaited()
        session.stream_audio.assert_not_awaited()
        last = await send(runtime, vad, token, [0.9])
        callbacks.on_prepare_turn.assert_awaited_once()
        assert (
            b"".join(c.args[0] for c in session.stream_audio.await_args_list)
            == first + last
        )
        callbacks.on_failure.assert_not_awaited()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_pending_successor_survives_old_final_and_owns_its_evidence():
    runtime, callbacks, session, vad, token = make_runtime(False)
    try:
        await send(runtime, vad, token, [0.9] * 7 + [0.1] * 10)
        await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
        successor = await send(runtime, vad, token, [0.9] * 7)
        callbacks.on_prepare_turn.assert_awaited_once()
        await runtime._handle_independent_asr_final(
            "first", runtime._asr_session_epoch, "qwen"
        )
        await runtime.wait_transcript_idle()
        await runtime._asr_audio_dispatcher.wait_idle()
        assert callbacks.on_prepare_turn.await_count == 2
        old_evidence = callbacks.on_final.await_args.args[0].evidence
        current = runtime._asr_admission_evidence[runtime._asr_prepared_turn_token]
        assert old_evidence.candidate_id != current.candidate_id
        assert current.audio_start_sample >= old_evidence.audio_end_sample
        assert session.stream_audio.await_args.args[0] == successor
        callbacks.on_failure.assert_not_awaited()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_detection_unavailable_does_not_enable_continuous_upload():
    runtime, callbacks, session, vad, token = make_runtime(False)
    vad.load = lambda: False
    try:
        await send(runtime, vad, token, [0.9])
        callbacks.on_prepare_turn.assert_not_awaited()
        session.stream_audio.assert_not_awaited()
        callbacks.on_failure.assert_awaited_once()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_unadmitted_semantic_completion_does_not_wait_for_a_nonexistent_final():
    runtime, callbacks, session, vad, token = make_runtime(False, smart_turn=True)
    try:
        await send(runtime, vad, token, [0.9, 0.4, 0.4] * 7 + [0.1] * 10)
        await runtime._asr_detector._semantic_adapter.wait_idle()
        await runtime._asr_detector_dispatcher.wait_idle()
        callbacks.on_prepare_turn.assert_not_awaited()
        session.stream_audio.assert_not_awaited()
        assert not runtime._asr_detector._defer_turn_complete
        await send(runtime, vad, token, [0.9] * 7)
        callbacks.on_prepare_turn.assert_awaited_once()
        assert session.stream_audio.await_count > 0
        callbacks.on_failure.assert_not_awaited()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_partial_hold_then_expanded_content_releases_only_latest_preview():
    runtime, callbacks, session, vad, token = make_runtime(False)
    try:
        await send(runtime, vad, token, [0.9] * 7)
        turn = runtime._asr_prepared_turn_token
        # Exercise the defensive policy with a provider result whose own
        # snapshot is weak; no fake low-confidence value is synthesized.
        runtime._asr_admission_evidence[turn] = CandidateAdmission("weak").observe(
            0, 512, 0.9
        )
        await runtime._send_independent_asr_preview("嗯。", runtime._asr_session_epoch)
        callbacks.on_partial.assert_not_awaited()
        assert runtime._asr_held_preview.turn_token == turn
        await runtime._send_independent_asr_preview(
            "嗯，我想换一个", runtime._asr_session_epoch
        )
        callbacks.on_partial.assert_awaited_once()
        assert callbacks.on_partial.await_args.args[0].text == "嗯，我想换一个"
        assert runtime._asr_held_preview is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("samples_per_chunk", [160, 320, 512])
async def test_chunk_boundaries_and_rnnoise_disagreement_preserve_exact_audio(
    samples_per_chunk,
):
    runtime, callbacks, session, vad, token = make_runtime(True)
    sent = []
    try:
        while callbacks.on_prepare_turn.await_count == 0:
            pcm = b"\x11\x00" * samples_per_chunk
            sent.append(pcm)
            await runtime.submit(
                ProcessedVoiceFrame(pcm, 16000, 0.0, True), ingress_token=token
            )
            await runtime._asr_detector_dispatcher.wait_idle()
            assert len(sent) <= 24
        await runtime._asr_audio_dispatcher.wait_idle()
        assert b"".join(
            c.args[0] for c in session.stream_audio.await_args_list
        ) == b"".join(sent)
        callbacks.on_prepare_turn.assert_awaited_once()
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("second_windows,expected_turns", [(1, 1), (7, 2)])
async def test_delayed_provider_endpoints_redeem_only_fresh_overlap_evidence(
    second_windows, expected_turns
):
    runtime, callbacks, session, vad, token = make_runtime(False)
    try:
        audio = await send(
            runtime,
            vad,
            token,
            [0.9] * 7 + [0.1] * 10 + [0.9] * second_windows + [0.1] * 10,
        )
        assert (
            b"".join(c.args[0] for c in session.stream_audio.await_args_list) == audio
        )
        for text in ("first", "second"):
            await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
            await runtime._handle_independent_asr_final(
                text, runtime._asr_session_epoch, "qwen"
            )
        await runtime.wait_transcript_idle()
        assert callbacks.on_prepare_turn.await_count == expected_turns
        assert callbacks.on_final.await_count == expected_turns
        if expected_turns == 2:
            first, second = (
                call.args[0].evidence for call in callbacks.on_final.await_args_list
            )
            assert first.candidate_id != second.candidate_id
            assert second.voiced_audio_ms == 224
        callbacks.on_failure.assert_not_awaited()
    finally:
        await runtime.close()


def test_coalesced_network_packet_keeps_each_candidate_evidence_boundary():
    probabilities = [0.9] * 7 + [0.1] * 10 + [0.9] * 7
    combined = AdmissionActivityGate(ProbabilityVad(), SmartTurnConfig(enabled=True))
    combined.process_probabilities(probabilities)
    split = AdmissionActivityGate(ProbabilityVad(), SmartTurnConfig(enabled=True))
    split_records = []
    for probability in probabilities:
        split.process_probabilities([probability])
        split_records.extend(split.admission_records)

    def signature(record):
        return (
            record.activity,
            record.evidence.candidate_id,
            record.evidence.audio_start_sample,
            record.evidence.audio_end_sample,
        )

    assert list(map(signature, combined.admission_records)) == list(
        map(signature, split_records)
    )
    assert (
        combined.admission_records[0].evidence.candidate_id
        != combined.admission_records[-1].evidence.candidate_id
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("interleave_audio", [False, True])
async def test_buffered_third_turn_waits_for_already_uploaded_second_turn(
    interleave_audio,
):
    runtime, callbacks, session, vad, token = make_runtime(False)
    try:
        await send(runtime, vad, token, [0.9] * 7 + [0.1] * 10 + [0.9] * 7 + [0.1] * 10)
        await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
        third = await send(runtime, vad, token, [0.9] * 7)
        await runtime._handle_independent_asr_final(
            "first", runtime._asr_session_epoch, "qwen"
        )
        assert callbacks.on_prepare_turn.await_count == 1
        if interleave_audio:

            async def during_replay(notification):
                nonlocal third
                if (
                    notification.state == "active"
                    and callbacks.on_prepare_turn.await_count == 1
                ):
                    third += await send(runtime, vad, token, [0.9])

            callbacks.on_lifecycle.side_effect = during_replay
        await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
        await runtime._handle_independent_asr_final(
            "second", runtime._asr_session_epoch, "qwen"
        )
        await runtime.wait_transcript_idle()
        await runtime._asr_audio_dispatcher.wait_idle()
        assert callbacks.on_prepare_turn.await_count == 3
        first, second = (c.args[0].evidence for c in callbacks.on_final.await_args_list)
        current = runtime._asr_admission_evidence[runtime._asr_prepared_turn_token]
        assert (
            first.audio_start_sample
            < second.audio_start_sample
            < current.audio_start_sample
        )
        assert session.stream_audio.await_args.args[0] == third
        assert len(third) == (8 if interleave_audio else 7) * 1024
        callbacks.on_failure.assert_not_awaited()
    finally:
        await runtime.close()
