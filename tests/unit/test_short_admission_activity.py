"""An admitted short word owns one activity start and the existing pause."""

from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client.endpointing.admission_gate import AdmissionActivityGate
from main_logic.asr_client.endpointing.config import SmartTurnConfig
from main_logic.asr_client.endpointing.detector_runtime import _VoiceTurnAdapter
from main_logic.voice_turn.admission import AdmissionConfig, AdmissionDecision
from main_logic.voice_turn.contracts import SpeechActivityEvent as Event
from tests.unit.test_asr_voice_turn_adapter import _FakeCoordinator, _eventually, _incomplete
from tests.unit.test_voice_admission_integration import ProbabilityVad


def make_gate(*, enabled=True, pause_ms=300, shadow=False):
    vad = ProbabilityVad()
    gate = AdmissionActivityGate(
        vad, SmartTurnConfig(enabled=True, candidate_silence_ms=pause_ms),
        admission_config=AdmissionConfig(experimental_short_speech=enabled),
        admission_shadow_config=(
            AdmissionConfig(experimental_short_speech=True) if shadow else None
        ),
    )
    return vad, gate


def test_short_word_starts_only_after_end_evidence_then_uses_original_pause():
    _, gate = make_gate()
    assert gate.process_probabilities([.95] * 6) == ()
    assert gate.admission_events == ()
    assert gate.process_probabilities([.1] * 3) == ()
    assert gate.process_probabilities([.1]) == (Event.SPEECH_STARTED,)
    assert gate.admission_events == (Event.SPEECH_STARTED,)
    assert gate.admission_records[0].evidence.admission_path == "short"
    assert not gate.recovery_boundary_ready
    assert gate.process_probabilities([.1] * 5) == ()
    assert gate.process_probabilities([.1]) == (Event.CANDIDATE_PAUSE,)
    assert gate.admission_events == (Event.CANDIDATE_PAUSE,)
    assert gate.recovery_boundary_ready
    assert gate.process_probabilities([.1] * 5) == ()


@pytest.mark.parametrize("pause_ms", [96, 128, 300])
def test_one_packet_preserves_start_pause_order_and_candidate_identity(pause_ms):
    _, gate = make_gate(pause_ms=pause_ms)
    assert gate.process_probabilities([.95] * 6 + [.1] * 10) == (
        Event.SPEECH_STARTED, Event.CANDIDATE_PAUSE,
    )
    assert gate.admission_events == (Event.SPEECH_STARTED, Event.CANDIDATE_PAUSE)
    started, paused = gate.admission_records
    assert started.evidence.candidate_id == paused.evidence.candidate_id
    assert started.audio_start_sample == paused.audio_start_sample == 0
    assert started.evidence.terminal_audio_end_sample == 10 * 512


def test_continuation_after_short_admission_does_not_duplicate_start():
    _, gate = make_gate()
    assert gate.process_probabilities([.95] * 6 + [.1] * 4) == (Event.SPEECH_STARTED,)
    assert gate.process_probabilities([.95] * 4) == ()
    assert gate.process_probabilities([.1] * 10) == (Event.CANDIDATE_PAUSE,)
    # A single resumed window stays visible to SmartTurn, without granting a
    # successor user turn admission.
    assert gate.process_probabilities([.95]) == (Event.SPEECH_RESUMED,)
    assert gate.admission_events == ()
    assert gate.evidence.decision is AdmissionDecision.PENDING


def test_low_internal_window_admitted_ordinary_activity_can_still_pause():
    _, gate = make_gate()
    # The low internal window resets the original raw minimum duration. The
    # accepted ordinary candidate must still establish activity for SmartTurn.
    assert gate.process_probabilities([.95] * 4 + [.1] + [.95] * 3) == (
        Event.SPEECH_STARTED,
    )
    assert gate.evidence.admission_path == "ordinary"
    assert gate.process_probabilities([.1] * 10) == (Event.CANDIDATE_PAUSE,)


def test_single_pulse_and_legacy_short_word_never_gain_activity():
    _, gate = make_gate()
    assert gate.process_probabilities([.95] + [.1] * 20) == ()
    assert gate.admission_events == ()
    _, legacy = make_gate(enabled=False)
    assert legacy.process_probabilities([.95] * 6 + [.1] * 20) == ()
    assert legacy.admission_events == ()


def test_owner_seal_and_stale_seal_preserve_successor_evidence_and_model():
    vad, gate = make_gate()
    gate.feed_at(bytes(6 * 1024), 6 * 512)
    vad.probability = .1
    gate.feed_at(bytes(4 * 1024), 10 * 512)
    first = gate.evidence
    gate.seal_admission(start_sample=10 * 512)
    vad.probability = .95
    gate.feed_at(bytes(1024), 11 * 512)
    successor = gate.evidence
    assert successor.candidate_id != first.candidate_id
    with pytest.raises(ValueError, match="precedes observed"):
        gate.seal_admission(start_sample=10 * 512)
    assert gate.evidence == successor
    assert gate.admission_events == ()


def test_shadow_short_admission_has_no_effect_on_authoritative_events(caplog):
    _, gate = make_gate(enabled=False, shadow=True)
    with caplog.at_level("INFO"):
        events = gate.process_probabilities([.95] * 6 + [.1] * 4)
    assert events == gate.admission_events == ()
    assert gate.evidence.decision is AdmissionDecision.REJECT
    assert gate.shadow_evidence.decision is AdmissionDecision.ADMIT
    assert "voice-admission-shadow" in caplog.text


def test_shadow_can_observe_successive_short_candidates_without_raw_start():
    _, gate = make_gate(enabled=False, shadow=True)
    assert gate.process_probabilities([.95] * 6 + [.1] * 4) == ()
    first = gate.shadow_evidence
    assert gate.process_probabilities([.1] * 6) == ()
    assert gate.shadow_evidence is None
    assert gate.process_probabilities([.95] * 6 + [.1] * 4) == ()
    assert gate.shadow_evidence.candidate_id != first.candidate_id
    assert gate.shadow_evidence.admission_path == "short"
    assert gate.admission_events == ()


@pytest.mark.asyncio
async def test_smart_turn_observes_short_start_pause_and_evaluates_once():
    vad, gate = make_gate()
    coordinator = _FakeCoordinator([_incomplete()])
    admitted = []

    async def activity(event):
        admitted.append(event)

    adapter = _VoiceTurnAdapter(
        vad=vad, gate=gate, coordinator=coordinator,
        on_activity=activity, on_commit=AsyncMock(),
    )
    await adapter.start()
    try:
        await adapter.push_audio(
            generation=0, buffer_epoch=0, utterance_id=1,
            pcm16=bytes(6 * 1024),
        )
        await _eventually(lambda: gate._sample_cursor == 6 * 512)
        assert not admitted
        vad.probability = .1
        await adapter.push_audio(
            generation=0, buffer_epoch=0, utterance_id=1,
            pcm16=bytes(10 * 1024),
        )
        await _eventually(lambda: coordinator.evaluate_calls == 1)
        assert admitted == [Event.SPEECH_STARTED, Event.CANDIDATE_PAUSE]
        assert coordinator.activity_events == admitted
        assert coordinator.pushed_audio == [bytes(6 * 1024), bytes(10 * 1024)]
    finally:
        await adapter.close()
