"""Offline evaluator preserves real window positions and never fabricates tails."""

from scripts.evaluate_short_voice_admission import probability_trace, replay_policy
from main_logic.voice_turn.admission import AdmissionConfig


class WindowVad:
    def __init__(self):
        self.pending = 0
        self.pcm = bytearray()
        self.closed = False
        self.resets = 0

    def reset_stream(self):
        self.resets += 1

    def process_pcm16(self, pcm):
        self.pcm.extend(pcm)
        self.pending += len(pcm) // 2
        count, self.pending = divmod(self.pending, 512)
        return [.9] * count

    def close(self):
        self.closed = True


def trace(probabilities):
    return dict(windows=[dict(start_sample=i * 512, end_sample=(i + 1) * 512,
        probability=p, available_at_input_seconds=(i + 1) * .032,
        normalized_ingress_end_sample=(i + 1) * 512) for i, p in enumerate(probabilities)])


def test_direct_stream_preserves_samples_and_unfinished_model_window():
    vad = WindowVad()
    pcm = bytes(range(200)) * 11
    result = probability_trace(pcm, 16000, vad=vad)
    assert bytes(vad.pcm) == pcm
    assert vad.resets == 1 and vad.closed
    assert result["processed_model_samples"] == 1024
    assert result["pending_model_samples"] == 76
    assert [window["end_sample"] for window in result["windows"]] == [512, 1024]
    assert result["windows"][0]["available_at_input_seconds"] == .04
    assert result["rnnoise_available"] is None


def test_48k_stream_uses_processor_once_and_does_not_flush_or_pad():
    class Processor:
        rnnoise_available = True
        _frame_buffer_size = 0

        def __init__(self):
            self.calls = []
            self.closed = False

        def process_chunk(self, pcm):
            self.calls.append(pcm)
            return bytes(len(pcm) // 3)

        def close(self):
            self.closed = True

    vad, processor = WindowVad(), Processor()
    pcm = bytes(4800)
    result = probability_trace(pcm, 48000, vad=vad, processor=processor)
    assert b"".join(processor.calls) == pcm
    assert len(processor.calls) == 5
    assert result["normalized_output_samples"] == 800
    assert result["pending_model_samples"] == 288
    assert result["rnnoise_available"] is True
    assert processor.closed and vad.closed


def test_identical_probabilities_exercise_real_legacy_and_short_gate_policy():
    recording = trace([.95] * 4 + [.1] * 4)
    baseline = replay_policy(recording, AdmissionConfig())
    short = replay_policy(recording, AdmissionConfig(
        experimental_short_speech=True, short_minimum_voiced_ms=128))
    assert baseline["admission_count"] == 0
    assert short["admission_count"] == 1
    admitted = short["admissions"][0]
    assert admitted["evidence"]["admission_path"] == "short"
    assert admitted["evidence"]["reason"] == "short_speech_end"
    assert admitted["observed_model_end_sample"] == 4096
    assert admitted["available_at_input_seconds"] == .256
    assert recording == trace([.95] * 4 + [.1] * 4)


def test_eof_without_low_tail_does_not_become_short_speech_end():
    result = replay_policy(trace([.95] * 4), AdmissionConfig(
        experimental_short_speech=True, short_minimum_voiced_ms=128))
    assert result["admission_count"] == 0
    assert result["final_evidence"]["decision"] == "pending"
