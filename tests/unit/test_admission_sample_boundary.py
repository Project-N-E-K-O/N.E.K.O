"""PCM ownership boundaries need not coincide with Silero inference windows."""

import asyncio

import pytest

from main_logic.asr_client.endpointing.admission_gate import AdmissionActivityGate
from main_logic.asr_client.endpointing.config import SmartTurnConfig
from main_logic.asr_client.endpointing.detector_runtime import DetectorRuntime
from main_logic.voice_turn.admission import AdmissionDecision
from tests.unit.test_voice_admission_integration import ProbabilityVad


@pytest.mark.parametrize("residual", [0, 1, 6, 14, 255, 489, 511])
@pytest.mark.parametrize("source_offset", [0, 160000])
def test_boundary_excludes_old_samples_without_resetting_model(residual, source_offset):
    vad = ProbabilityVad()
    gate = AdmissionActivityGate(vad, SmartTurnConfig(enabled=True))
    boundary = 21 * 512 + residual
    gate.feed_at(bytes(boundary * 2), source_offset + boundary)
    old_candidate = gate.evidence.candidate_id
    gate.seal_admission(start_sample=source_offset + boundary)
    assert vad.pending == residual
    assert not gate.recovery_boundary_ready  # raw speech context stays active

    # Complete seven model windows. Old residual duration cannot help this
    # candidate reach 224 ms, even though all windows have high probability.
    gate.feed_at(bytes((7 * 512 - residual) * 2), source_offset + 28 * 512)
    evidence = gate.evidence
    assert evidence.candidate_id != old_candidate
    assert evidence.audio_start_sample == source_offset + boundary
    assert gate.retained_start_sample == source_offset + boundary
    assert evidence.observed_samples == 7 * 512 - residual
    assert evidence.voiced_samples == evidence.observed_samples
    assert evidence.longest_speech_run_samples == evidence.observed_samples
    assert evidence.longest_gap_samples == 0
    assert evidence.probability_mean == pytest.approx(0.9)
    assert evidence.decision is (
        AdmissionDecision.PENDING if residual else AdmissionDecision.ADMIT
    )
    gate.feed_at(bytes(1024), source_offset + 29 * 512)
    assert gate.evidence.decision is AdmissionDecision.ADMIT


@pytest.mark.asyncio
async def test_detector_uses_owner_boundary_and_preserves_uploaded_overlap():
    vad = ProbabilityVad()
    gate = AdmissionActivityGate(vad, SmartTurnConfig(enabled=True))
    detector = DetectorRuntime(vad=vad, gate=gate, admission_enabled=True)
    boundary = 10766
    await detector.feed(bytes(boundary * 2), source_end_sample=boundary)
    fence = await detector.seal_provider_candidate(admission_start_sample=boundary)
    await detector.feed(bytes(980 * 2), source_end_sample=boundary + 980)
    assert gate.evidence.audio_start_sample == boundary
    snapshot = gate.evidence
    assert await detector.complete_provider_candidate(fence)
    await detector.seal_provider_candidate(
        preserve_admission=True, admission_start_sample=boundary + 980,
    )
    assert gate.evidence == snapshot
    await detector.close()


@pytest.mark.asyncio
async def test_detector_feed_accepts_absolute_position_after_new_detector():
    vad = ProbabilityVad()
    gate = AdmissionActivityGate(vad, SmartTurnConfig(enabled=True))
    detector = DetectorRuntime(vad=vad, gate=gate, admission_enabled=True)
    await detector.feed(bytes(1024), source_end_sample=160512)
    assert detector.submitted_samples == 160512
    assert gate.evidence.audio_start_sample == 160000
    assert gate.evidence.audio_end_sample == 160512
    await detector.close()


@pytest.mark.asyncio
async def test_stale_boundary_cannot_erase_newer_observed_evidence():
    vad = ProbabilityVad()
    gate = AdmissionActivityGate(vad, SmartTurnConfig(enabled=True))
    detector = DetectorRuntime(vad=vad, gate=gate, admission_enabled=True)
    await detector.feed(bytes(1024), source_end_sample=512)
    snapshot = gate.evidence
    with pytest.raises(ValueError, match="precedes observed audio"):
        await detector.seal_provider_candidate(admission_start_sample=511)
    assert gate.evidence == snapshot
    assert detector._provider_candidate_fence is None
    await detector.close()


@pytest.mark.parametrize("delta", [-512, -1, 1, 512])
def test_source_hole_or_overlap_does_not_shift_existing_evidence(delta):
    gate = AdmissionActivityGate(ProbabilityVad(), SmartTurnConfig(enabled=True))
    gate.feed_at(bytes(1024), 160512)
    snapshot = gate.evidence
    with pytest.raises(ValueError, match="discontinuous"):
        gate.feed_at(bytes(1024), 161024 + delta)
    assert gate.evidence == snapshot
    gate.feed_at(bytes(1024), 161024)
    assert gate.evidence.observed_samples == 1024
    assert gate.evidence.audio_start_sample == 160000


def test_gate_reset_starts_new_absolute_range():
    gate = AdmissionActivityGate(ProbabilityVad(), SmartTurnConfig(enabled=True))
    gate.feed_at(bytes(1024), 160512)
    gate.reset()
    gate.feed_at(bytes(1024), 320512)
    assert gate.evidence.audio_start_sample == 320000
    assert gate.evidence.observed_samples == 512


@pytest.mark.asyncio
async def test_seal_resolves_boundary_after_waiting_for_detector_lock():
    vad = ProbabilityVad()
    gate = AdmissionActivityGate(vad, SmartTurnConfig(enabled=True))
    detector = DetectorRuntime(vad=vad, gate=gate, admission_enabled=True)
    await detector.feed(bytes(1024), source_end_sample=512)
    boundary = 512
    calls = []

    def owner_boundary():
        assert detector._lock.locked()
        calls.append(boundary)
        return boundary

    async with detector._lock:
        task = asyncio.create_task(detector.seal_provider_candidate(
            admission_boundary=owner_boundary,
        ))
        await asyncio.sleep(0)
        assert not calls
        boundary = 1024
    assert await task is not None
    assert calls == [1024]
    await detector.feed(bytes(2048), source_end_sample=1536)
    assert gate.evidence.audio_start_sample == 1024
    assert gate.evidence.observed_samples == 512
    await detector.close()


@pytest.mark.asyncio
async def test_boundary_owner_failure_leaves_candidate_untouched():
    vad = ProbabilityVad()
    gate = AdmissionActivityGate(vad, SmartTurnConfig(enabled=True))
    detector = DetectorRuntime(vad=vad, gate=gate, admission_enabled=True)
    await detector.feed(bytes(1024), source_end_sample=512)
    snapshot = gate.evidence

    def retired_owner():
        raise RuntimeError("retired owner")

    with pytest.raises(RuntimeError, match="retired owner"):
        await detector.seal_provider_candidate(admission_boundary=retired_owner)
    assert gate.evidence == snapshot
    assert detector._provider_candidate_fence is None
    await detector.close()


@pytest.mark.asyncio
async def test_smart_turn_source_gap_fails_without_no_vad_fallback():
    from tests.unit.test_voice_admission_integration import make_runtime

    runtime, callbacks, session, vad, token = make_runtime(False, smart_turn=True)
    detector = runtime._asr_detector
    adapter = detector._semantic_adapter
    try:
        for end in (512, 1025):
            await detector.submit_audio(
                bytes(1024), ingress_token=token, sample_rate_hz=16000,
                speech_probability=0.9, rnnoise_available=False,
                source_end_sample=end,
            )
            await adapter.wait_idle()
        assert adapter.failed
        assert adapter.failure.stage == "vad_feed"
        assert not adapter._vad_degraded
        session.stream_audio.assert_not_awaited()
        callbacks.on_prepare_turn.assert_not_awaited()
    finally:
        await runtime.close()
