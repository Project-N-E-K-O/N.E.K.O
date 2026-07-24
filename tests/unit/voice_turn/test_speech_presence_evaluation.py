from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

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


def test_current_rnnoise_policy_uses_adaptive_threshold_after_baseline() -> None:
    probabilities = [0.0] * 20 + [0.25, 0.25]

    trigger = MODULE._current_rnnoise_policy_trigger_ms(probabilities)

    assert trigger == 220


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


def test_calibration_holdout_split_keeps_source_variants_together() -> None:
    clips = []
    for locale in ("en", "zh"):
        for source in range(4):
            for variant in ("clean", "snr_+10"):
                clips.append(
                    SimpleNamespace(
                        clip_id=f"speech/{locale}/{source:02d}/{variant}",
                        label=True,
                        locale=locale,
                    )
                )
    for scenario in ("negative_fan", "negative_game_sfx"):
        clips.extend(
            SimpleNamespace(
                clip_id=f"negative/{scenario}/{index:02d}",
                label=False,
                locale=None,
                scenario=scenario,
                device_id=None,
            )
            for index in range(2)
        )

    for seed in range(10):
        calibration, holdout = MODULE.split_calibration_holdout(
            clips,
            seed=seed,
            holdout_fraction=0.25,
        )

        calibration_groups = {
            MODULE.source_group_id(clip.clip_id) for clip in calibration
        }
        holdout_groups = {MODULE.source_group_id(clip.clip_id) for clip in holdout}
        assert calibration_groups.isdisjoint(holdout_groups)
        assert {clip.locale for clip in holdout if clip.label} == {"en", "zh"}
        assert {clip.scenario for clip in holdout if not clip.label} == {
            "negative_fan",
            "negative_game_sfx",
        }


def test_threshold_is_selected_from_calibration_metrics_only() -> None:
    clips = [
        SimpleNamespace(label=True, score=0.80),
        SimpleNamespace(label=True, score=0.75),
        SimpleNamespace(label=False, score=0.60),
        SimpleNamespace(label=False, score=0.10),
    ]

    selected = MODULE.select_presence_threshold(
        clips,
        score_name="score",
        thresholds=(0.6, 0.7, 0.8),
    )

    assert selected["threshold"] == pytest.approx(0.7)
    assert selected["balanced_accuracy"] == pytest.approx(1.0)


def test_real_device_manifest_loads_audio_without_exposing_paths(tmp_path: Path) -> None:
    audio_path = tmp_path / "desk-mic.wav"
    MODULE.sf.write(
        audio_path,
        np.zeros(MODULE.SAMPLE_RATE_48K, dtype=np.float32),
        MODULE.SAMPLE_RATE_48K,
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "clips": [
                    {
                        "id": "idle-fan-01",
                        "path": audio_path.name,
                        "label": False,
                        "device_id": "desktop-usb",
                        "scenario": "real_idle_fan",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    clips, summary = MODULE.load_real_device_manifest(manifest_path)

    assert len(clips) == 1
    assert clips[0].clip_id == "real/desktop-usb/idle-fan-01"
    assert clips[0].device_id == "desktop-usb"
    assert summary == {
        "manifest_schema_version": 1,
        "clip_count": 1,
        "device_ids": ["desktop-usb"],
    }
    assert str(audio_path) not in json.dumps(summary)
