"""Recovery boundaries use existing raw VAD activity, not admission ownership."""

from types import SimpleNamespace

import pytest

from main_logic.asr_client.endpointing.admission_gate import AdmissionActivityGate
from main_logic.asr_client.endpointing.config import SmartTurnConfig
from main_logic.asr_client.endpointing.detector_runtime import DetectorRuntime
from main_logic.asr_client.endpointing.silero_vad import SileroActivityGate
from main_logic.voice_turn.contracts import SpeechActivityEvent


class _Vad:
    def reset_stream(self):
        pass


def _runtime(gate_type=SileroActivityGate, *, semantic=False):
    vad = _Vad()
    gate = gate_type(vad, SmartTurnConfig(enabled=True))
    runtime = DetectorRuntime(vad=vad, gate=gate, admission_enabled=False)
    runtime._load_attempted = True
    if semantic:
        runtime._semantic_adapter = SimpleNamespace(
            failed=False, _vad_available=True, throttle_available=True,
        )
    return runtime, gate


@pytest.mark.parametrize("gate_type", [SileroActivityGate, AdmissionActivityGate])
@pytest.mark.parametrize("semantic", [False, True])
def test_raw_pause_controls_boundary_independent_of_stale_candidate_flags(gate_type, semantic):
    runtime, gate = _runtime(gate_type, semantic=semantic)
    assert runtime.recovery_boundary_ready
    gate.process_probabilities([1.0] * gate._minimum_speech_windows)
    assert not runtime.recovery_boundary_ready
    # Runtime ownership flags are deliberately unrelated to a raw pause.
    runtime._candidate_open = True
    runtime._speech_active = True
    events = gate.process_probabilities([0.0] * gate._candidate_silence_windows)
    assert SpeechActivityEvent.CANDIDATE_PAUSE in events
    assert runtime.recovery_boundary_ready
    gate.process_probabilities([1.0])
    assert not runtime.recovery_boundary_ready


def test_pause_followed_by_resume_in_one_packet_is_not_a_safe_boundary():
    runtime, gate = _runtime()
    gate.process_probabilities([1.0] * gate._minimum_speech_windows)
    events = gate.process_probabilities([0.0] * gate._candidate_silence_windows + [1.0])
    assert events == (SpeechActivityEvent.CANDIDATE_PAUSE, SpeechActivityEvent.SPEECH_RESUMED)
    assert not runtime.recovery_boundary_ready


def test_sealing_admission_does_not_hide_active_raw_speech():
    runtime, gate = _runtime(AdmissionActivityGate)
    gate.process_probabilities([1.0] * gate._minimum_speech_windows)
    gate.seal_admission()
    assert not runtime.recovery_boundary_ready
    gate.process_probabilities([0.0] * gate._candidate_silence_windows)
    assert runtime.recovery_boundary_ready


@pytest.mark.parametrize("state", ["unloaded", "unavailable", "closed", "no_gate"])
def test_provider_missing_detection_is_not_a_boundary(state):
    runtime, _ = _runtime()
    if state == "unloaded":
        runtime._load_attempted = False
    elif state == "unavailable":
        runtime._available = False
    elif state == "closed":
        runtime._closed = True
    else:
        runtime._gate = object()
    assert not runtime.recovery_boundary_ready


@pytest.mark.parametrize("field", ["failed", "_vad_available", "throttle_available"])
def test_smartturn_missing_detection_is_not_a_boundary(field):
    runtime, _ = _runtime(semantic=True)
    setattr(runtime._semantic_adapter, field, field == "failed")
    assert not runtime.recovery_boundary_ready
