from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "tools" / "voice_eval" / "evaluate_speech_presence.py"
SPEC = importlib.util.spec_from_file_location("evaluate_speech_presence", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_confusion_metrics_keep_recall_and_specificity_separate() -> None:
    confusion = MODULE.confusion_from_predictions(
        [True, True, False, False],
        [True, False, True, False],
    )

    metrics = MODULE.metrics_from_confusion(confusion)

    assert confusion == MODULE.Confusion(1, 1, 1, 1)
    assert metrics["accuracy"] == pytest.approx(0.5)
    assert metrics["balanced_accuracy"] == pytest.approx(0.5)
    assert metrics["speech_recall"] == pytest.approx(0.5)
    assert metrics["negative_specificity"] == pytest.approx(0.5)


def test_confusion_rejects_mismatched_inputs() -> None:
    with pytest.raises(ValueError, match="same length"):
        MODULE.confusion_from_predictions([True], [])


def test_silero_presence_score_requires_sustained_windows() -> None:
    assert MODULE.silero_presence_score([0.9, 0.1, 0.9], minimum_windows=2) == 0.1
    assert MODULE.silero_presence_score([0.2, 0.7, 0.8], minimum_windows=2) == 0.7
    assert MODULE.silero_presence_score([0.9], minimum_windows=2) == 0.0


def test_first_silero_trigger_matches_offset_reset_behavior() -> None:
    trigger = MODULE.first_silero_trigger_ms(
        [0.6, 0.4, 0.6],
        0.5,
        minimum_windows=2,
        offset_threshold=0.35,
    )
    reset_trigger = MODULE.first_silero_trigger_ms(
        [0.6, 0.2, 0.6],
        0.5,
        minimum_windows=2,
        offset_threshold=0.35,
    )

    assert trigger == pytest.approx(3 * MODULE.SILERO_WINDOW_MS)
    assert reset_trigger is None


def test_sustained_trigger_rejects_isolated_rnnoise_spikes() -> None:
    isolated = MODULE._first_sustained_trigger_ms([0.9, 0.1, 0.9], 0.8, 2, 20)
    sustained = MODULE._first_sustained_trigger_ms([0.1, 0.8, 0.9], 0.8, 2, 20)

    assert isolated is None
    assert sustained == 60


def test_mix_at_snr_preserves_requested_rms_ratio() -> None:
    rng = np.random.default_rng(123)
    speech = rng.normal(size=48_000).astype(np.float32) * 0.02
    noise = rng.normal(size=48_000).astype(np.float32)

    mixed = MODULE.mix_at_snr(speech, noise, 10)
    added_noise = mixed - speech
    speech_rms = np.sqrt(np.mean(np.square(speech), dtype=np.float64))
    noise_rms = np.sqrt(np.mean(np.square(added_noise), dtype=np.float64))

    assert 20 * np.log10(speech_rms / noise_rms) == pytest.approx(10, abs=0.05)


def test_mix_at_snr_rejects_silent_inputs() -> None:
    silence = np.zeros(100, dtype=np.float32)
    noise = np.ones(100, dtype=np.float32)

    with pytest.raises(ValueError, match="contain energy"):
        MODULE.mix_at_snr(silence, noise, 0)
