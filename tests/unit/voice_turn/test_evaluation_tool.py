import json
import wave

import numpy as np
import pytest

from tools.voice_eval.evaluate_smart_turn_v3 import evaluate_cases, load_cases


def _wav(path, samples):
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(np.asarray(samples, dtype="<i2").tobytes())


class _Predictor:
    def __init__(self, probabilities):
        self.probabilities = iter(probabilities)

    def predict_probability(self, audio):
        return next(self.probabilities)


def test_evaluation_reports_all_four_confusion_cells(tmp_path):
    labels = []
    for index, expected in enumerate(("complete", "complete", "incomplete", "incomplete")):
        name = f"case-{index}.wav"
        _wav(tmp_path / name, [index])
        labels.append(json.dumps({"path": name, "expected": expected, "language": "zh"}))
    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text("\n".join(labels), encoding="utf-8")
    report = evaluate_cases(
        load_cases(tmp_path, labels_path), _Predictor([0.9, 0.1, 0.9, 0.1])
    )
    assert report["confusion_matrix"] == {
        "true_complete": 1,
        "false_incomplete": 1,
        "false_complete": 1,
        "true_incomplete": 1,
    }


def test_labels_cannot_escape_fixture_directory(tmp_path):
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({"path": "../outside.wav", "expected": "complete"}))
    with pytest.raises(ValueError, match="escapes"):
        load_cases(tmp_path, labels)
