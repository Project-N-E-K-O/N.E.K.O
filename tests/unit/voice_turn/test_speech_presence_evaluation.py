from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts import evaluate_speech_presence as evaluation


class _OfflineDenoiser:
    def process_frame(self, frame: np.ndarray) -> tuple[np.ndarray, float]:
        return frame.copy(), 0.0


class _OfflineProcessor:
    RESET_TIMEOUT_SECONDS = 0.02
    RNNOISE_FRAME_SIZE = 480

    def __init__(self) -> None:
        self._denoiser = _OfflineDenoiser()
        self.reset_count = 0
        self.rnnoise_frame_count = 0
        self.rnnoise_probability_peak: float | None = None
        self.rnnoise_probability_mean: float | None = None
        self.rnnoise_probability_last: float | None = None
        self.rnnoise_probability_ema: float | None = None

    @property
    def rnnoise_available(self) -> bool:
        return True

    def process_chunk(self, audio_bytes: bytes) -> bytes:
        frame = np.frombuffer(audio_bytes, dtype=np.int16)
        _, probability = self._denoiser.process_frame(frame)
        self.rnnoise_frame_count = 1
        self.rnnoise_probability_peak = probability
        self.rnnoise_probability_mean = probability
        self.rnnoise_probability_last = probability
        self.rnnoise_probability_ema = probability
        return audio_bytes

    def reset(self) -> None:
        self.reset_count += 1
        self.rnnoise_frame_count = 0
        self.rnnoise_probability_peak = None
        self.rnnoise_probability_mean = None
        self.rnnoise_probability_last = None
        self.rnnoise_probability_ema = None


def test_offline_processor_matches_audio_processor_contract() -> None:
    offline_processor = _OfflineProcessor()
    for name in (
        "RESET_TIMEOUT_SECONDS",
        "RNNOISE_FRAME_SIZE",
        "rnnoise_available",
        "rnnoise_frame_count",
        "rnnoise_probability_peak",
        "rnnoise_probability_mean",
        "rnnoise_probability_last",
        "rnnoise_probability_ema",
    ):
        assert hasattr(evaluation.AudioProcessor, name)
        assert hasattr(offline_processor, name)


def _evaluated_clip(
    clip_id: str,
    *,
    label: bool,
    score: float,
    trigger_ms: float | None,
) -> evaluation.EvaluatedClip:
    return evaluation.EvaluatedClip(
        clip_id=clip_id,
        label=label,
        scenario="real_speech" if label else "real_negative",
        locale="zh-CN",
        snr_db=None,
        noise_kind=None,
        duration_seconds=1.0,
        rnnoise_chunk_peak=score,
        rnnoise_chunk_ema_peak=score / 2,
        rnnoise_online_trigger_ms=trigger_ms,
        rnnoise_offline_sustained_100ms_score=score,
        rnnoise_offline_sustained_100ms_trigger_ms=trigger_ms,
        silero_raw_score=score,
        silero_after_rnnoise_score=score,
        silero_raw_trigger_ms=trigger_ms,
        silero_after_rnnoise_trigger_ms=trigger_ms,
        speech_start_ms=100.0 if label else None,
        device_id="desktop-usb",
    )


def test_confusion_metrics_keep_recall_and_specificity_separate() -> None:
    confusion = evaluation.confusion_from_predictions(
        [True, True, False, False],
        [True, False, True, False],
    )

    metrics = evaluation.metrics_from_confusion(confusion)

    assert confusion == evaluation.Confusion(1, 1, 1, 1)
    assert metrics["accuracy"] == pytest.approx(0.5)
    assert metrics["balanced_accuracy"] == pytest.approx(0.5)
    assert metrics["speech_recall"] == pytest.approx(0.5)
    assert metrics["negative_specificity"] == pytest.approx(0.5)


def test_silero_score_and_trigger_require_sustained_windows() -> None:
    assert evaluation.silero_presence_score(
        [0.9, 0.1, 0.9],
        minimum_windows=2,
    ) == pytest.approx(0.1)
    assert evaluation.silero_presence_score(
        [0.2, 0.7, 0.8],
        minimum_windows=2,
    ) == pytest.approx(0.7)
    assert evaluation.first_silero_trigger_ms(
        [0.6, 0.4, 0.6],
        0.5,
        minimum_windows=2,
        offset_threshold=0.35,
    ) == pytest.approx(3 * evaluation.SILERO_WINDOW_MS)
    assert (
        evaluation.first_silero_trigger_ms(
            [0.6, 0.2, 0.6],
            0.5,
            minimum_windows=2,
            offset_threshold=0.35,
        )
        is None
    )


def test_neutral_sustained_score_is_not_silero_specific() -> None:
    assert evaluation.sustained_presence_score(
        [0.2, 0.7, 0.8],
        minimum_windows=2,
    ) == pytest.approx(0.7)


def test_offline_sustained_candidate_rejects_isolated_spikes() -> None:
    isolated = evaluation._first_sustained_trigger_ms(
        [0.9, 0.1, 0.9],
        0.8,
        2,
        20,
    )
    sustained = evaluation._first_sustained_trigger_ms(
        [0.1, 0.8, 0.9],
        0.8,
        2,
        20,
    )

    assert isolated is None
    assert sustained == 60


def test_online_replay_uses_chunk_shaped_adaptive_evidence() -> None:
    probabilities = [0.0] * 20 + [0.25, 0.25]

    assert (
        evaluation._current_rnnoise_policy_trigger_ms(
            probabilities,
            frames_per_chunk=2,
        )
        == 220
    )


def test_online_replay_defaults_to_browser_ten_millisecond_chunks() -> None:
    probabilities = [0.0] * 20 + [0.25]

    assert evaluation.RNNOISE_CHUNK_MS == 10
    assert evaluation._current_rnnoise_policy_trigger_ms(probabilities) == 210


def test_online_replay_derives_grouped_chunk_duration_from_shared_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evaluation, "RNNOISE_CHUNK_MS", 7)
    probabilities = [0.0] * 20 + [0.25, 0.25]

    assert (
        evaluation._current_rnnoise_policy_trigger_ms(
            probabilities,
            frames_per_chunk=2,
        )
        == 154
    )


def test_threshold_grid_has_provider_neutral_ownership() -> None:
    assert not hasattr(evaluation, "RNNOISE_THRESHOLDS")
    assert evaluation.PRESENCE_THRESHOLDS == (
        0.2,
        0.25,
        0.3,
        0.35,
        0.4,
        0.45,
        0.5,
        0.6,
        0.7,
        0.8,
        0.9,
        0.95,
        0.99,
    )


def test_online_replay_treats_fail_open_as_a_presence_trigger() -> None:
    trigger = evaluation._online_trigger_from_chunks(
        [evaluation.RnnoiseEvidence.unavailable()]
    )

    assert trigger == evaluation.RNNOISE_CHUNK_MS


def test_online_replay_omits_silero_metrics_when_activity_is_unavailable() -> None:
    trigger, metrics = evaluation._replay_rnnoise_policy(
        [
            evaluation.RnnoiseEvidence.from_legacy_probability(
                0.05,
                available=True,
            ),
            evaluation.RnnoiseEvidence.from_legacy_probability(
                0.9,
                available=True,
            ),
        ],
        chunk_ms=evaluation.RNNOISE_CHUNK_MS,
    )

    assert trigger == 2 * evaluation.RNNOISE_CHUNK_MS
    assert metrics.evidence_chunk_count == 2
    assert metrics.rnnoise_trigger_count == 1
    assert metrics.silero_trigger_count == 0
    assert metrics.rnnoise_silero_disagreement_count == 0


def test_online_replay_aligns_silero_activity_with_rnnoise_chunks() -> None:
    aligned_chunk_count = math.ceil(
        evaluation.SILERO_MINIMUM_WINDOWS
        * evaluation.SILERO_WINDOW_MS
        / evaluation.RNNOISE_CHUNK_MS
    )
    chunks = [
        evaluation.RnnoiseEvidence.from_legacy_probability(
            0.05,
            available=True,
        )
        for _ in range(aligned_chunk_count)
    ]

    trigger, metrics = evaluation._replay_rnnoise_policy(
        chunks,
        chunk_ms=evaluation.RNNOISE_CHUNK_MS,
        silero_probabilities=[0.9] * evaluation.SILERO_MINIMUM_WINDOWS,
    )

    assert trigger is None
    assert metrics.evidence_chunk_count == aligned_chunk_count
    assert metrics.rnnoise_trigger_count == 0
    assert metrics.silero_trigger_count == 1
    assert metrics.rnnoise_silero_disagreement_count == 1


def test_online_replay_reports_rnnoise_count_from_trigger_trajectory() -> None:
    chunks = [
        evaluation.RnnoiseEvidence.from_legacy_probability(
            0.05,
            available=True,
        )
        for _ in range(20)
    ]
    chunks.extend(
        evaluation.RnnoiseEvidence.from_legacy_probability(
            0.19,
            available=True,
        )
        for _ in range(100)
    )
    chunks.append(
        evaluation.RnnoiseEvidence.from_legacy_probability(
            0.25,
            available=True,
        )
    )

    trigger, metrics = evaluation._replay_rnnoise_policy(
        chunks,
        chunk_ms=evaluation.RNNOISE_CHUNK_MS,
        silero_probabilities=[0.9] * evaluation.SILERO_MINIMUM_WINDOWS,
    )

    assert trigger is None
    assert metrics.rnnoise_trigger_count == 0
    assert metrics.silero_trigger_count > 0


def test_online_replay_delegates_to_production_throttle_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_policy = evaluation.VoiceThrottlePolicy
    decisions = 0

    class _RecordingPolicy(production_policy):
        def decide(self, *args, **kwargs):
            nonlocal decisions
            decisions += 1
            return super().decide(*args, **kwargs)

    monkeypatch.setattr(evaluation, "VoiceThrottlePolicy", _RecordingPolicy)

    assert evaluation._current_rnnoise_policy_trigger_ms([0.0, 0.9]) == 20
    assert decisions == 4


def test_offline_replay_resets_rnnoise_from_audio_time() -> None:
    processor = _OfflineProcessor()
    samples = np.zeros(4 * processor.RNNOISE_FRAME_SIZE, dtype=np.float32)

    evaluation._rnnoise_process(processor, samples)

    assert processor.reset_count == 1


def test_rnnoise_process_flushes_pending_frame_and_resampler_tail() -> None:
    class _TailResampler:
        def resample_chunk(
            self,
            samples: np.ndarray,
            *,
            last: bool = False,
        ) -> np.ndarray:
            assert samples.size == 0
            assert last is True
            return np.asarray([0.25, -0.25], dtype=np.float32)

    class _BufferedProcessor(_OfflineProcessor):
        def __init__(self) -> None:
            super().__init__()
            self._pending = np.empty(0, dtype=np.int16)
            self._frame_buffer_size = 0
            self._downsample_resampler = _TailResampler()

        def process_chunk(self, audio_bytes: bytes) -> bytes:
            incoming = np.frombuffer(audio_bytes, dtype=np.int16)
            combined = np.concatenate((self._pending, incoming))
            if combined.size < self.RNNOISE_FRAME_SIZE:
                self._pending = combined.copy()
                self._frame_buffer_size = combined.size
                self.rnnoise_frame_count = 0
                return b""
            frame = combined[: self.RNNOISE_FRAME_SIZE]
            self._pending = combined[self.RNNOISE_FRAME_SIZE :].copy()
            self._frame_buffer_size = self._pending.size
            denoised, probability = self._denoiser.process_frame(frame)
            self.rnnoise_frame_count = 1
            self.rnnoise_probability_peak = probability
            self.rnnoise_probability_mean = probability
            self.rnnoise_probability_last = probability
            self.rnnoise_probability_ema = probability
            return denoised.astype("<i2").tobytes()

    processor = _BufferedProcessor()
    (
        frame_probabilities,
        chunk_evidence,
        processed,
        _wall_elapsed,
        _cpu_elapsed,
    ) = evaluation._rnnoise_process(
        processor,
        np.full(100, 0.5, dtype=np.float32),
    )

    assert frame_probabilities == [0.0]
    assert [evidence.frame_count for evidence in chunk_evidence] == [0, 1]
    assert len(processed) == (processor.RNNOISE_FRAME_SIZE + 2) * 2
    assert processed[-4:] == evaluation._pcm16(
        np.asarray([0.25, -0.25], dtype=np.float32)
    )


def test_offline_replay_uses_production_rnnoise_speech_threshold() -> None:
    assert evaluation.AudioProcessor.RNNOISE_SPEECH_PROBABILITY_THRESHOLD == 0.2


def test_offline_replay_uses_audio_processor_rnnoise_ema_alpha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[evaluation.RnnoiseEvidence] = []

    def _capture(chunks, *, chunk_ms):
        assert chunk_ms == 10.0
        captured.extend(chunks)
        return None

    monkeypatch.setattr(evaluation.AudioProcessor, "RNNOISE_EMA_ALPHA", 0.5)
    monkeypatch.setattr(evaluation, "_replay_rnnoise_policy_trigger_ms", _capture)

    evaluation._current_rnnoise_policy_trigger_ms([0.2, 1.0])

    assert [chunk.ema for chunk in captured] == pytest.approx([0.2, 0.6])


def test_evaluate_corpus_closes_processor_when_rnnoise_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    closed: list[str] = []

    class _UnavailableProcessor:
        def __init__(self, **_kwargs) -> None:
            self._denoiser = None

        def close(self) -> None:
            closed.append("processor")

    monkeypatch.setattr(evaluation, "AudioProcessor", _UnavailableProcessor)

    with pytest.raises(RuntimeError, match="RNNoise native runtime is unavailable"):
        evaluation.evaluate_corpus([], tmp_path)

    assert closed == ["processor"]


def test_evaluate_corpus_closes_runtimes_when_silero_load_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    closed: list[str] = []

    class _LoadProcessor:
        def __init__(self, **_kwargs) -> None:
            self._denoiser = object()

        def close(self) -> None:
            closed.append("processor")

    class _LoadVad:
        def __init__(self, **_kwargs) -> None:
            self.unavailable_reason = "load failed"

        def load(self) -> bool:
            return False

        def close(self) -> None:
            closed.append("vad")

    monkeypatch.setattr(evaluation, "AudioProcessor", _LoadProcessor)
    monkeypatch.setattr(evaluation, "SileroVad", _LoadVad)

    with pytest.raises(RuntimeError, match="Silero failed to load: load failed"):
        evaluation.evaluate_corpus([], tmp_path)

    assert closed == ["vad", "processor"]


def test_evaluate_corpus_closes_runtimes_when_silero_warmup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    closed: list[str] = []

    class _WarmupProcessor:
        def __init__(self, **_kwargs) -> None:
            self._denoiser = object()

        def close(self) -> None:
            closed.append("processor")

    class _WarmupVad:
        WINDOW_SAMPLES = 512

        def __init__(self, **_kwargs) -> None:
            self.unavailable_reason = None

        def load(self) -> bool:
            return True

        def process_pcm16(self, _pcm16: bytes) -> None:
            raise RuntimeError("warmup failed")

        def reset_stream(self) -> None:
            return None

        def close(self) -> None:
            closed.append("vad")

    monkeypatch.setattr(evaluation, "AudioProcessor", _WarmupProcessor)
    monkeypatch.setattr(evaluation, "SileroVad", _WarmupVad)

    with pytest.raises(RuntimeError, match="warmup failed"):
        evaluation.evaluate_corpus([], tmp_path)

    assert closed == ["processor", "vad"]


def test_mix_at_snr_preserves_requested_rms_ratio() -> None:
    rng = np.random.default_rng(123)
    speech = rng.normal(size=48_000).astype(np.float32) * 0.02
    noise = rng.normal(size=48_000).astype(np.float32)

    mixed = evaluation.mix_at_snr(speech, noise, 10)
    added_noise = mixed - speech
    speech_rms = np.sqrt(np.mean(np.square(speech), dtype=np.float64))
    noise_rms = np.sqrt(np.mean(np.square(added_noise), dtype=np.float64))

    assert 20 * np.log10(speech_rms / noise_rms) == pytest.approx(10, abs=0.05)


def test_calibration_holdout_keeps_source_variants_together() -> None:
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

    calibration, holdout = evaluation.split_calibration_holdout(
        clips,
        seed=2398,
    )

    calibration_groups = {
        evaluation.source_group_id(clip.clip_id) for clip in calibration
    }
    holdout_groups = {
        evaluation.source_group_id(clip.clip_id) for clip in holdout
    }
    assert calibration_groups.isdisjoint(holdout_groups)
    assert {clip.locale for clip in holdout if clip.label} == {"en", "zh"}
    assert {clip.scenario for clip in holdout if not clip.label} == {
        "negative_fan",
        "negative_game_sfx",
    }


def test_calibration_holdout_preserves_truthy_label_conversion() -> None:
    clips = [
        SimpleNamespace(
            clip_id=f"compat/{label}/{index}",
            label=label,
            locale="en" if label else None,
            scenario="speech" if label else "idle",
            device_id=None,
        )
        for label in (0, 1)
        for index in range(2)
    ]

    calibration, holdout = evaluation.split_calibration_holdout(
        clips,
        seed=2398,
    )

    assert {bool(clip.label) for clip in calibration} == {False, True}
    assert {bool(clip.label) for clip in holdout} == {False, True}


def test_calibration_holdout_rejects_singleton_only_strata_explicitly() -> None:
    clips = [
        SimpleNamespace(
            clip_id="real/mic-a/speech-a",
            label=True,
            locale="en",
            scenario="speech-a",
            device_id="mic-a",
        ),
        SimpleNamespace(
            clip_id="real/mic-b/speech-b",
            label=True,
            locale="zh",
            scenario="speech-b",
            device_id="mic-b",
        ),
        SimpleNamespace(
            clip_id="real/mic-a/idle-a",
            label=False,
            locale=None,
            scenario="idle-a",
            device_id="mic-a",
        ),
        SimpleNamespace(
            clip_id="real/mic-b/idle-b",
            label=False,
            locale=None,
            scenario="idle-b",
            device_id="mic-b",
        ),
    ]

    with pytest.raises(ValueError, match="singleton-only strata"):
        evaluation.split_calibration_holdout(clips, seed=2398)


@pytest.mark.parametrize(
    "singleton_label, missing_class",
    ((False, "negative"), (True, "speech")),
)
def test_calibration_holdout_rejects_a_split_missing_one_class(
    singleton_label: bool,
    missing_class: str,
) -> None:
    paired_label = not singleton_label
    clips = [
        SimpleNamespace(
            clip_id=f"paired/{index:02d}",
            label=paired_label,
            locale="en" if paired_label else None,
            scenario="speech" if paired_label else "idle",
            device_id=None,
        )
        for index in range(2)
    ]
    clips.append(
        SimpleNamespace(
            clip_id="singleton/00",
            label=singleton_label,
            locale="en" if singleton_label else None,
            scenario="speech" if singleton_label else "idle",
            device_id=None,
        )
    )

    with pytest.raises(
        ValueError,
        match=rf"both splits; holdout missing {missing_class}",
    ):
        evaluation.split_calibration_holdout(clips, seed=2398)


@pytest.mark.parametrize(
    "label, missing_class",
    ((True, "negative"), (False, "speech")),
)
def test_calibration_holdout_rejects_a_single_class_corpus(
    label: bool,
    missing_class: str,
) -> None:
    clips = [
        SimpleNamespace(
            clip_id=f"single-class/{index:02d}",
            label=label,
            locale="en" if label else None,
            scenario="speech" if label else "idle",
            device_id=None,
        )
        for index in range(2)
    ]

    with pytest.raises(
        ValueError,
        match=(
            rf"both splits; calibration missing {missing_class}, "
            rf"holdout missing {missing_class}"
        ),
    ):
        evaluation.split_calibration_holdout(clips, seed=2398)


def test_threshold_is_selected_from_calibration_metrics_only() -> None:
    clips = [
        SimpleNamespace(label=True, score=0.80),
        SimpleNamespace(label=True, score=0.75),
        SimpleNamespace(label=False, score=0.60),
        SimpleNamespace(label=False, score=0.10),
    ]

    selected = evaluation.select_presence_threshold(
        clips,
        score_name="score",
        thresholds=(0.6, 0.7, 0.8),
    )

    assert selected["threshold"] == pytest.approx(0.7)
    assert selected["balanced_accuracy"] == pytest.approx(1.0)


def test_real_device_manifest_does_not_expose_paths(tmp_path: Path) -> None:
    audio_path = tmp_path / "desk-mic.wav"
    evaluation.sf.write(
        audio_path,
        np.zeros(evaluation.SAMPLE_RATE_48K, dtype=np.float32),
        evaluation.SAMPLE_RATE_48K,
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

    clips, summary = evaluation.load_real_device_manifest(manifest_path)

    assert clips[0].clip_id == "real/desktop-usb/idle-fan-01"
    assert summary == {
        "manifest_schema_version": 1,
        "clip_count": 1,
        "device_ids": ["desktop-usb"],
    }
    assert str(audio_path) not in json.dumps(summary)


def test_real_device_manifest_summary_omits_audio_filename(tmp_path: Path) -> None:
    audio_path = tmp_path / "private-device-name.wav"
    evaluation.sf.write(
        audio_path,
        np.zeros(evaluation.SAMPLE_RATE_48K, dtype=np.float32),
        evaluation.SAMPLE_RATE_48K,
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "clips": [
                    {
                        "id": "idle-01",
                        "path": audio_path.name,
                        "label": False,
                        "device_id": "desktop-usb",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    _, summary = evaluation.load_real_device_manifest(manifest_path)

    assert audio_path.name not in json.dumps(summary)


@pytest.mark.parametrize("path_kind", ["absolute", "traversal"])
def test_real_device_manifest_rejects_paths_outside_its_directory(
    tmp_path: Path,
    path_kind: str,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    declared_path = (
        str((tmp_path / "absolute.wav").resolve())
        if path_kind == "absolute"
        else str(Path("..") / "traversal.wav")
    )
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "clips": [
                    {
                        "id": "idle-01",
                        "path": declared_path,
                        "label": False,
                        "device_id": "desktop-usb",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="real-device path must stay inside the manifest directory",
    ):
        evaluation.load_real_device_manifest(manifest_path)


@pytest.mark.parametrize(
    ("status_output", "expected"),
    [
        ("", "abc123"),
        (" M scripts/evaluate_speech_presence.py", "abc123-dirty"),
    ],
)
def test_revision_uses_repository_commit_and_dirty_marker(
    monkeypatch: pytest.MonkeyPatch,
    status_output: str,
    expected: str,
) -> None:
    outputs = iter(("abc123\n", status_output))
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.setattr(
        evaluation.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=next(outputs)),
    )

    assert evaluation._resolve_revision() == expected


def test_report_includes_silero_latency_and_private_asset_contract(
    tmp_path: Path,
) -> None:
    clips = [
        _evaluated_clip("real/desktop-usb/speech-1", label=True, score=0.9, trigger_ms=150),
        _evaluated_clip("real/desktop-usb/speech-2", label=True, score=0.8, trigger_ms=200),
        _evaluated_clip("real/desktop-usb/noise-1", label=False, score=0.1, trigger_ms=None),
        _evaluated_clip("real/desktop-usb/noise-2", label=False, score=0.05, trigger_ms=None),
    ]
    external_assets = tmp_path / "private-user" / "models"
    shadow_metrics = {
        "evidence_chunk_count": 8,
        "incomplete_chunk_count": 1,
        "rnnoise_trigger_count": 3,
        "silero_trigger_count": 2,
        "rnnoise_silero_disagreement_count": 1,
    }

    report = evaluation.build_report(
        clips,
        performance={"throttle_shadow_metrics": shadow_metrics},
        seed=2398,
        asset_dir=external_assets,
        corpus_manifest={"real_device": {"clip_count": len(clips)}},
    )

    assert report["asset_contract"] == {
        "silero_directory": "<external>",
        "default_directory": "main_logic/asr_client/endpointing/models",
        "uses_default_directory": False,
    }
    assert set(report["silero_paths"]) == {"raw", "after_rnnoise"}
    assert set(report["onset_latency_ms"]) == {
        "rnnoise_online",
        "rnnoise_offline_sustained_100ms",
        "silero_raw",
        "silero_after_rnnoise",
    }
    assert report["corpus_summary"]["duration_seconds"] == pytest.approx(4.0)
    assert report["corpus_summary"]["locales"] == ["zh-CN"]
    assert report["corpus_summary"]["device_ids"] == ["desktop-usb"]
    assert report["corpus_summary"]["rnnoise_chunk_peak"]["p95"] > 0
    online_contract = report["online_contract"]
    assert set(online_contract) == {
        "granularity",
        "rnnoise_fields",
        "shadow_metric_kinds",
        "shadow_metrics",
        "calibration",
        "holdout",
    }
    assert online_contract["granularity"] == "one_input_chunk"
    assert online_contract["rnnoise_fields"] == [
        "frame_count",
        "peak",
        "mean",
        "last",
        "ema",
    ]
    assert report["online_contract"]["shadow_metric_kinds"] == [
        "action_count",
        "evidence_chunk_count",
        "disagreement_count",
    ]
    assert report["online_contract"]["shadow_metrics"] == shadow_metrics
    assert (
        report["offline_candidates"]["rnnoise_continuous_100ms"][
            "production_behavior"
        ]
        is False
    )
    assert any(
        "AGC" in limitation and "limiter" in limitation
        for limitation in report["limitations"]
    )
    assert any(
        "fixed 0.7 threshold" in limitation
        and "calibrated threshold" in limitation
        for limitation in report["limitations"]
    )
    assert any(
        "onset/offset hysteresis" in limitation
        and "strictly consecutive windows" in limitation
        for limitation in report["limitations"]
    )
    serialized = json.dumps(report)
    assert str(tmp_path) not in serialized
    assert "private-user" not in serialized
    assert all(
        "private-user" not in json.dumps(value)
        for value in report["silero_paths"].values()
    )
