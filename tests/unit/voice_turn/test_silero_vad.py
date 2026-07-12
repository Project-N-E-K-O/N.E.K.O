import numpy as np
import pytest

from main_logic.voice_turn.contracts import SmartTurnConfig, SpeechActivityEvent
from main_logic.voice_turn.onnx_runtime import RuntimeState
from main_logic.voice_turn.silero_vad import SileroActivityGate, SileroVad


class _Session:
    def __init__(self):
        self.inputs = []

    def run(self, output_names, inputs):
        self.inputs.append({key: np.asarray(value).copy() for key, value in inputs.items()})
        state = inputs["state"] + 1
        return [np.asarray([[0.9]], dtype=np.float32), state]


def _ready_vad():
    vad = SileroVad(enabled=True)
    vad._session = _Session()
    vad._state = RuntimeState.READY
    return vad


def test_silero_preserves_context_and_lstm_state_across_windows():
    vad = _ready_vad()
    values = np.arange(1024, dtype=np.int16)
    assert vad.process_pcm16(values.tobytes()) == pytest.approx([0.9, 0.9])
    first, second = vad._session.inputs
    assert first["input"].shape == (1, 576)
    assert np.all(first["input"][0, :64] == 0)
    assert np.allclose(second["input"][0, :64], values[448:512] / 32768.0)
    assert np.all(second["state"] == 1)


def test_silero_reset_clears_context_state_and_pending_audio():
    vad = _ready_vad()
    vad.process_pcm16(np.ones(700, dtype=np.int16).tobytes())
    vad.reset_stream()
    assert not vad._pending.size
    assert np.all(vad._context == 0)
    assert np.all(vad._lstm_state == 0)


class _NoopVad:
    def reset_stream(self):
        pass


def test_activity_gate_emits_pause_once_and_resume_without_force_commit():
    config = SmartTurnConfig(
        enabled=True,
        minimum_speech_ms=32,
        candidate_silence_ms=32,
    )
    gate = SileroActivityGate(_NoopVad(), config)
    assert gate.process_probabilities([0.9]) is SpeechActivityEvent.SPEECH_STARTED
    assert gate.process_probabilities([0.1]) is SpeechActivityEvent.CANDIDATE_PAUSE
    assert gate.process_probabilities([0.1] * 100) is SpeechActivityEvent.NONE
    assert gate.process_probabilities([0.9]) is SpeechActivityEvent.SPEECH_RESUMED
    assert {event.value for event in SpeechActivityEvent}.isdisjoint({"force_end", "turn_complete"})
