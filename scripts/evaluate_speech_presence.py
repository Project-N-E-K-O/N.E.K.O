#!/usr/bin/env python3
"""Evaluate RNNoise and Silero as provider-neutral speech-presence evidence.

This benchmark answers only whether a clip might contain speech. It does not
measure semantic turn completion and cannot grant endpoint authority.

The production-policy replay consumes one evidence record per input chunk:
RNNoise frame count plus peak, mean, last, and EMA. Sustained-frame scores are
reported separately as offline candidates and are not production behavior.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import platform
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import psutil
import soundfile as sf
import soxr


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main_logic.asr_client.endpointing.config import SmartTurnConfig  # noqa: E402
from main_logic.asr_client.endpointing.silero_vad import (  # noqa: E402
    SileroActivityGate,
    SileroVad,
)
from main_logic.asr_client.endpointing.throttle_policy import (  # noqa: E402
    ThrottleAction,
    ThrottleShadowMetrics,
    VoiceThrottlePolicy,
)
from main_logic.voice_turn.activity_evidence import RnnoiseEvidence  # noqa: E402
from utils.audio_processor import AudioProcessor  # noqa: E402


SAMPLE_RATE_48K = 48_000
SAMPLE_RATE_16K = 16_000
RNNOISE_CHUNK_MS = 10
RNNOISE_CURRENT_THRESHOLD = 0.35
RNNOISE_OFFLINE_SUSTAINED_THRESHOLD = 0.7
RNNOISE_OFFLINE_SUSTAINED_FRAMES = 10
SILERO_CURRENT_THRESHOLD = 0.5
SILERO_OFFSET_THRESHOLD = 0.35
SILERO_MINIMUM_SPEECH_MS = 200
SILERO_WINDOW_MS = 1000 * SileroVad.WINDOW_SAMPLES / SileroVad.SAMPLE_RATE
SILERO_MINIMUM_WINDOWS = max(
    1,
    math.ceil(SILERO_MINIMUM_SPEECH_MS / SILERO_WINDOW_MS),
)
DEFAULT_SEED = 2398
DEFAULT_HOLDOUT_FRACTION = 0.25
PRESENCE_THRESHOLDS = (
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


@dataclass(frozen=True, slots=True)
class Confusion:
    true_positive: int
    false_negative: int
    true_negative: int
    false_positive: int


@dataclass(frozen=True, slots=True)
class CorpusClip:
    clip_id: str
    label: bool
    scenario: str
    samples_48k: np.ndarray
    locale: str | None = None
    snr_db: int | None = None
    noise_kind: str | None = None
    speech_start_ms: float | None = None
    device_id: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluatedClip:
    clip_id: str
    label: bool
    scenario: str
    locale: str | None
    snr_db: int | None
    noise_kind: str | None
    duration_seconds: float
    rnnoise_chunk_peak: float
    rnnoise_chunk_ema_peak: float
    rnnoise_online_trigger_ms: float | None
    rnnoise_offline_sustained_100ms_score: float
    rnnoise_offline_sustained_100ms_trigger_ms: float | None
    silero_raw_score: float
    silero_after_rnnoise_score: float
    silero_raw_trigger_ms: float | None
    silero_after_rnnoise_trigger_ms: float | None
    speech_start_ms: float | None
    device_id: str | None


def _project_relative_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return "<external>"


def source_group_id(clip_id: str) -> str:
    """Return a stable source identity so augmented variants stay together."""

    parts = str(clip_id).split("/")
    if len(parts) >= 4 and parts[0] == "speech":
        return "/".join(parts[:3])
    return str(clip_id)


def confusion_from_predictions(
    labels: Sequence[bool],
    predictions: Sequence[bool],
) -> Confusion:
    if len(labels) != len(predictions):
        raise ValueError("labels and predictions must have the same length")
    tp = fn = tn = fp = 0
    for label, predicted in zip(labels, predictions, strict=True):
        if label and predicted:
            tp += 1
        elif label:
            fn += 1
        elif predicted:
            fp += 1
        else:
            tn += 1
    return Confusion(tp, fn, tn, fp)


def metrics_from_confusion(confusion: Confusion) -> dict[str, float | int]:
    tp = confusion.true_positive
    fn = confusion.false_negative
    tn = confusion.true_negative
    fp = confusion.false_positive

    def ratio(numerator: float, denominator: float) -> float:
        return numerator / denominator if denominator else 0.0

    recall = ratio(tp, tp + fn)
    specificity = ratio(tn, tn + fp)
    precision = ratio(tp, tp + fp)
    return {
        **asdict(confusion),
        "accuracy": ratio(tp + tn, tp + fn + tn + fp),
        "balanced_accuracy": (recall + specificity) / 2,
        "speech_recall": recall,
        "speech_miss_rate": 1 - recall,
        "negative_specificity": specificity,
        "false_positive_rate": 1 - specificity,
        "precision": precision,
        "f1": ratio(2 * precision * recall, precision + recall),
    }


def split_calibration_holdout(
    clips: Sequence[Any],
    *,
    seed: int,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
) -> tuple[list[Any], list[Any]]:
    """Split by source and stratum without leaking augmented variants."""

    if not 0.0 < holdout_fraction < 1.0:
        raise ValueError("holdout_fraction must be within (0, 1)")
    groups: dict[str, list[Any]] = defaultdict(list)
    for clip in clips:
        groups[source_group_id(clip.clip_id)].append(clip)

    strata: dict[tuple[bool, str], list[str]] = defaultdict(list)
    for group_id, group in groups.items():
        labels = {bool(clip.label) for clip in group}
        locales = {str(getattr(clip, "locale", None) or "") for clip in group}
        devices = {
            str(getattr(clip, "device_id", None) or "") for clip in group
        }
        if len(labels) != 1 or len(locales) != 1 or len(devices) != 1:
            raise ValueError(
                f"source group is not label/locale/device homogeneous: {group_id}"
            )
        label = next(iter(labels))
        locale = next(iter(locales))
        device = next(iter(devices))
        scenario = str(getattr(group[0], "scenario", "") or "unspecified")
        if device:
            stratum = f"device:{device}:{scenario}:{locale}"
        elif label:
            stratum = f"locale:{locale}"
        else:
            stratum = f"scenario:{scenario}"
        strata[(label, stratum)].append(group_id)

    holdout_groups: set[str] = set()
    for stratum, group_ids in strata.items():
        ordered = sorted(
            group_ids,
            key=lambda value: hashlib.sha256(
                f"{seed}:{stratum}:{value}".encode()
            ).hexdigest(),
        )
        if len(ordered) < 2:
            continue
        count = max(1, round(len(ordered) * holdout_fraction))
        holdout_groups.update(ordered[: min(count, len(ordered) - 1)])

    calibration = [
        clip
        for clip in clips
        if source_group_id(clip.clip_id) not in holdout_groups
    ]
    holdout = [
        clip
        for clip in clips
        if source_group_id(clip.clip_id) in holdout_groups
    ]
    if not calibration or not holdout:
        raise ValueError("calibration/holdout split requires at least two source groups")
    return calibration, holdout


def select_presence_threshold(
    clips: Sequence[Any],
    *,
    score_name: str,
    thresholds: Sequence[float],
) -> dict[str, float | int]:
    """Select only from calibration data, with deterministic tie-breaking."""

    if not clips or not thresholds:
        raise ValueError("threshold selection requires clips and thresholds")
    rows: list[dict[str, float | int]] = []
    for threshold in thresholds:
        predictions = [
            float(getattr(clip, score_name)) >= threshold for clip in clips
        ]
        rows.append(
            {
                "threshold": float(threshold),
                **metrics_from_confusion(
                    confusion_from_predictions(
                        [bool(clip.label) for clip in clips],
                        predictions,
                    )
                ),
            }
        )
    return max(
        rows,
        key=lambda row: (
            float(row["balanced_accuracy"]),
            float(row["speech_recall"]),
            float(row["negative_specificity"]),
            -float(row["threshold"]),
        ),
    )


def sustained_presence_score(
    probabilities: Sequence[float],
    *,
    minimum_windows: int = SILERO_MINIMUM_WINDOWS,
) -> float:
    if minimum_windows <= 0:
        raise ValueError("minimum_windows must be positive")
    if len(probabilities) < minimum_windows:
        return 0.0
    values = np.asarray(probabilities, dtype=np.float64)
    return max(
        float(np.min(values[index : index + minimum_windows]))
        for index in range(len(values) - minimum_windows + 1)
    )


def silero_presence_score(
    probabilities: Sequence[float],
    *,
    minimum_windows: int = SILERO_MINIMUM_WINDOWS,
) -> float:
    return sustained_presence_score(
        probabilities,
        minimum_windows=minimum_windows,
    )


def first_silero_trigger_ms(
    probabilities: Sequence[float],
    threshold: float,
    *,
    minimum_windows: int = SILERO_MINIMUM_WINDOWS,
    offset_threshold: float = SILERO_OFFSET_THRESHOLD,
) -> float | None:
    speech_windows = 0
    for index, probability in enumerate(probabilities):
        if probability >= threshold:
            speech_windows += 1
            if speech_windows >= minimum_windows:
                return (index + 1) * SILERO_WINDOW_MS
        elif probability < offset_threshold:
            speech_windows = 0
    return None


def mix_at_snr(
    speech: np.ndarray,
    noise: np.ndarray,
    snr_db: float,
    *,
    speech_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Build an offline SNR variant; the manifest-only CLI does not call this."""

    if speech.shape != noise.shape:
        raise ValueError("speech and noise must have the same shape")
    active = speech if speech_mask is None else speech[speech_mask]
    speech_rms = float(np.sqrt(np.mean(np.square(active), dtype=np.float64)))
    noise_rms = float(np.sqrt(np.mean(np.square(noise), dtype=np.float64)))
    if speech_rms <= 1e-9 or noise_rms <= 1e-9:
        raise ValueError("speech and noise must both contain energy")
    target_noise_rms = speech_rms / (10 ** (snr_db / 20))
    mixed = speech + noise * (target_noise_rms / noise_rms)
    peak = float(np.max(np.abs(mixed)))
    if peak > 0.98:
        mixed *= 0.98 / peak
    return mixed.astype(np.float32)


def _read_mono_48k(path: Path) -> np.ndarray:
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = np.mean(samples, axis=1, dtype=np.float32)
    if sample_rate != SAMPLE_RATE_48K:
        mono = soxr.resample(mono, sample_rate, SAMPLE_RATE_48K, quality="HQ")
    return np.asarray(mono, dtype=np.float32)


def load_real_device_manifest(
    manifest_path: Path,
) -> tuple[list[CorpusClip], dict[str, Any]]:
    """Load labeled recordings while excluding local paths from report data."""

    resolved_manifest = manifest_path.resolve()
    payload = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("real-device manifest schema_version must be 1")
    entries = payload.get("clips")
    if not isinstance(entries, list) or not entries:
        raise ValueError("real-device manifest clips must be a non-empty list")

    clips: list[CorpusClip] = []
    seen_ids: set[str] = set()
    device_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("real-device manifest clip must be an object")
        clip_id = str(entry.get("id") or "").strip()
        device_id = str(entry.get("device_id") or "").strip()
        if (
            not clip_id
            or not device_id
            or "/" in clip_id
            or "\\" in clip_id
            or "/" in device_id
            or "\\" in device_id
        ):
            raise ValueError("real-device id and device_id must be path atoms")
        report_id = f"real/{device_id}/{clip_id}"
        if report_id in seen_ids:
            raise ValueError(f"duplicate real-device clip id: {report_id}")
        seen_ids.add(report_id)
        device_ids.add(device_id)
        label = entry.get("label")
        if not isinstance(label, bool):
            raise ValueError(f"real-device label must be boolean: {report_id}")
        relative_path = entry.get("path")
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError(f"real-device path is required: {report_id}")
        declared_path = Path(relative_path)
        audio_path = (resolved_manifest.parent / declared_path).resolve()
        if declared_path.is_absolute() or not audio_path.is_relative_to(
            resolved_manifest.parent
        ):
            raise ValueError(
                "real-device path must stay inside the manifest directory: "
                f"{report_id}"
            )
        if not audio_path.is_file():
            raise FileNotFoundError(audio_path)
        speech_start = entry.get("speech_start_ms")
        if speech_start is not None and float(speech_start) < 0:
            raise ValueError("speech_start_ms must not be negative")
        clips.append(
            CorpusClip(
                clip_id=report_id,
                label=label,
                scenario=str(
                    entry.get("scenario")
                    or ("real_device_speech" if label else "real_device_negative")
                ),
                samples_48k=_read_mono_48k(audio_path),
                locale=(
                    str(entry["locale"]).strip() if entry.get("locale") else None
                ),
                speech_start_ms=(
                    float(speech_start) if speech_start is not None else None
                ),
                device_id=device_id,
            )
        )
    return clips, {
        "manifest_schema_version": 1,
        "clip_count": len(clips),
        "device_ids": sorted(device_ids),
    }


def _pcm16(samples: np.ndarray) -> bytes:
    return (
        np.clip(samples, -1.0, 1.0) * 32767
    ).round().astype("<i2").tobytes()


def _first_sustained_trigger_ms(
    scores: Sequence[float],
    threshold: float,
    minimum_windows: int,
    frame_ms: float,
) -> float | None:
    """Offline-only sustained-window candidate."""

    sustained = 0
    for index, score in enumerate(scores):
        sustained = sustained + 1 if score >= threshold else 0
        if sustained >= minimum_windows:
            return (index + 1) * frame_ms
    return None


def _current_rnnoise_policy_trigger_ms(
    frame_probabilities: Sequence[float],
    *,
    frames_per_chunk: int = 1,
) -> float | None:
    """Replay production-shaped, chunk-level evidence from offline frames."""

    if frames_per_chunk <= 0:
        raise ValueError("frames_per_chunk must be positive")
    alpha = AudioProcessor.RNNOISE_EMA_ALPHA
    ema: float | None = None
    chunks: list[RnnoiseEvidence] = []
    for start in range(0, len(frame_probabilities), frames_per_chunk):
        values = frame_probabilities[start : start + frames_per_chunk]
        if not values:
            continue
        for probability in values:
            ema = (
                probability
                if ema is None
                else alpha * probability + (1.0 - alpha) * ema
            )
        mean = sum(values) / len(values)
        evidence = RnnoiseEvidence(
            available=True,
            frame_count=len(values),
            peak=max(values),
            mean=mean,
            last=values[-1],
            ema=ema,
        )
        chunks.append(evidence)
    return _replay_rnnoise_policy_trigger_ms(
        chunks,
        chunk_ms=float(RNNOISE_CHUNK_MS * frames_per_chunk),
    )


def _rnnoise_process(
    processor: AudioProcessor,
    samples_48k: np.ndarray,
) -> tuple[list[float], list[RnnoiseEvidence], bytes, float, float]:
    """Capture raw frames for offline candidates and chunk evidence for online replay."""

    chunk_samples = SAMPLE_RATE_48K * RNNOISE_CHUNK_MS // 1000
    frame_probabilities: list[float] = []
    chunk_evidence: list[RnnoiseEvidence] = []
    processed_chunks: list[bytes] = []
    denoiser = processor._denoiser  # noqa: SLF001 - benchmark instrumentation
    if denoiser is None:
        raise RuntimeError("RNNoise native runtime is unavailable")
    original_process_frame = denoiser.process_frame

    def capture_process_frame(frame: np.ndarray) -> tuple[np.ndarray, float]:
        denoised, probability = original_process_frame(frame)
        frame_probabilities.append(float(probability))
        return denoised, probability

    denoiser.process_frame = capture_process_frame
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    silence_audio_seconds = 0.0
    try:
        for start in range(0, samples_48k.size, chunk_samples):
            if silence_audio_seconds >= processor.RESET_TIMEOUT_SECONDS:
                processor.reset()
                silence_audio_seconds = 0.0
            frame_start = len(frame_probabilities)
            input_chunk = samples_48k[start : start + chunk_samples]
            processed = processor.process_chunk(
                _pcm16(input_chunk)
            )
            if processed:
                processed_chunks.append(processed)
            count = processor.rnnoise_frame_count
            chunk_evidence.append(
                RnnoiseEvidence(
                    available=processor.rnnoise_available,
                    frame_count=count,
                    peak=processor.rnnoise_probability_peak if count else None,
                    mean=processor.rnnoise_probability_mean if count else None,
                    last=processor.rnnoise_probability_last if count else None,
                    ema=processor.rnnoise_probability_ema if count else None,
                )
            )
            chunk_probabilities = frame_probabilities[frame_start:]
            last_speech_frame = next(
                (
                    index
                    for index in range(len(chunk_probabilities) - 1, -1, -1)
                    if (
                        chunk_probabilities[index]
                        > AudioProcessor.RNNOISE_SPEECH_PROBABILITY_THRESHOLD
                    )
                ),
                None,
            )
            if last_speech_frame is None:
                silence_audio_seconds += input_chunk.size / SAMPLE_RATE_48K
            else:
                trailing_frames = len(chunk_probabilities) - last_speech_frame - 1
                silence_audio_seconds = (
                    trailing_frames * RNNOISE_CHUNK_MS / 1000.0
                )
    finally:
        cpu_elapsed = time.process_time() - cpu_started
        wall_elapsed = time.perf_counter() - wall_started
        denoiser.process_frame = original_process_frame
    return (
        frame_probabilities,
        chunk_evidence,
        b"".join(processed_chunks),
        wall_elapsed,
        cpu_elapsed,
    )


def _online_trigger_from_chunks(
    chunks: Sequence[RnnoiseEvidence],
) -> float | None:
    return _replay_rnnoise_policy_trigger_ms(
        chunks,
        chunk_ms=RNNOISE_CHUNK_MS,
    )


def _replay_rnnoise_policy(
    chunks: Sequence[RnnoiseEvidence],
    *,
    chunk_ms: float,
    silero_probabilities: Sequence[float] | None = None,
) -> tuple[float | None, ThrottleShadowMetrics]:
    rnnoise_policy = VoiceThrottlePolicy(
        resource_optimization_enabled=True,
        bootstrap_onset=RNNOISE_CURRENT_THRESHOLD,
    )
    shadow_policy = VoiceThrottlePolicy(
        resource_optimization_enabled=True,
        bootstrap_onset=RNNOISE_CURRENT_THRESHOLD,
    )
    replay_vad = SileroVad(enabled=False)
    silero_gate = SileroActivityGate(
        replay_vad,
        SmartTurnConfig(
            enabled=True,
            onset_probability=SILERO_CURRENT_THRESHOLD,
            offset_probability=SILERO_OFFSET_THRESHOLD,
            minimum_speech_ms=SILERO_MINIMUM_SPEECH_MS,
        ),
    )
    trigger_ms: float | None = None
    rnnoise_candidate_open = False
    shadow_candidate_open = False
    silero_index = 0
    positive_actions = {
        ThrottleAction.PREWARM,
        ThrottleAction.OPEN_CANDIDATE,
    }
    try:
        for index, evidence in enumerate(chunks):
            rnnoise_decision = rnnoise_policy.decide(
                evidence,
                candidate_open=rnnoise_candidate_open,
                allow_baseline_update=not rnnoise_candidate_open,
            )
            if rnnoise_decision.action in positive_actions:
                if trigger_ms is None:
                    trigger_ms = (index + 1) * chunk_ms
                rnnoise_candidate_open = True

            shadow_decision = shadow_policy.decide(
                evidence,
                candidate_open=shadow_candidate_open,
                allow_baseline_update=not shadow_candidate_open,
            )
            if shadow_decision.action in positive_actions:
                shadow_candidate_open = True

            chunk_end_ms = (index + 1) * chunk_ms
            while (
                silero_probabilities is not None
                and silero_index < len(silero_probabilities)
                and (silero_index + 1) * SILERO_WINDOW_MS <= chunk_end_ms
            ):
                probability = silero_probabilities[silero_index]
                for event in silero_gate.process_probabilities((probability,)):
                    shadow_policy.observe_silero(
                        event,
                        probability=probability,
                    )
                silero_index += 1
    finally:
        replay_vad.close()

    rnnoise_metrics = rnnoise_policy.shadow_metrics
    shadow_metrics = shadow_policy.shadow_metrics
    metrics = ThrottleShadowMetrics(
        evidence_chunk_count=rnnoise_metrics.evidence_chunk_count,
        incomplete_chunk_count=rnnoise_metrics.incomplete_chunk_count,
        rnnoise_trigger_count=rnnoise_metrics.rnnoise_trigger_count,
        silero_trigger_count=(
            shadow_metrics.silero_trigger_count if silero_probabilities else 0
        ),
        rnnoise_silero_disagreement_count=(
            shadow_metrics.rnnoise_silero_disagreement_count
            if silero_probabilities
            else 0
        ),
    )
    return trigger_ms, metrics


def _replay_rnnoise_policy_trigger_ms(
    chunks: Sequence[RnnoiseEvidence],
    *,
    chunk_ms: float,
) -> float | None:
    trigger_ms, _ = _replay_rnnoise_policy(chunks, chunk_ms=chunk_ms)
    return trigger_ms


def evaluate_corpus(
    clips: Iterable[CorpusClip],
    asset_dir: Path,
) -> tuple[list[EvaluatedClip], dict[str, Any]]:
    process = psutil.Process(os.getpid())
    gc.collect()
    rss_before = process.memory_info().rss
    processor = AudioProcessor(
        input_sample_rate=SAMPLE_RATE_48K,
        output_sample_rate=SAMPLE_RATE_16K,
        noise_reduce_enabled=True,
        agc_enabled=True,
        limiter_enabled=True,
    )
    if processor._denoiser is None:  # noqa: SLF001 - benchmark fails closed
        processor.close()
        raise RuntimeError("RNNoise native runtime is unavailable")
    rss_after_rnnoise = process.memory_info().rss
    vad = SileroVad(enabled=True, asset_dir=asset_dir, intra_op_threads=1)
    if not vad.load():
        vad.close()
        processor.close()
        raise RuntimeError(f"Silero failed to load: {vad.unavailable_reason}")

    evaluated: list[EvaluatedClip] = []
    total_audio_seconds = 0.0
    rnnoise_wall_seconds = 0.0
    rnnoise_cpu_seconds = 0.0
    silero_wall_seconds = 0.0
    silero_cpu_seconds = 0.0
    throttle_shadow_metrics: defaultdict[str, int] = defaultdict(int)
    try:
        vad.process_pcm16(np.zeros(SileroVad.WINDOW_SAMPLES, dtype="<i2").tobytes())
        vad.reset_stream()
        rss_after_silero = process.memory_info().rss
        for clip in clips:
            processor.reset()
            vad.reset_stream()
            (
                frame_probabilities,
                chunk_evidence,
                processed_16k,
                rnnoise_wall,
                rnnoise_cpu,
            ) = _rnnoise_process(processor, clip.samples_48k)
            raw_16k = soxr.resample(
                clip.samples_48k,
                SAMPLE_RATE_48K,
                SAMPLE_RATE_16K,
                quality="HQ",
            )
            started_wall = time.perf_counter()
            started_cpu = time.process_time()
            raw_probabilities = vad.process_pcm16(_pcm16(raw_16k))
            vad.reset_stream()
            denoised_probabilities = vad.process_pcm16(processed_16k)
            silero_wall_seconds += time.perf_counter() - started_wall
            silero_cpu_seconds += time.process_time() - started_cpu
            duration = clip.samples_48k.size / SAMPLE_RATE_48K
            total_audio_seconds += duration
            rnnoise_wall_seconds += rnnoise_wall
            rnnoise_cpu_seconds += rnnoise_cpu
            online_trigger_ms, clip_shadow_metrics = _replay_rnnoise_policy(
                chunk_evidence,
                chunk_ms=RNNOISE_CHUNK_MS,
                silero_probabilities=denoised_probabilities,
            )
            for name, value in asdict(clip_shadow_metrics).items():
                throttle_shadow_metrics[name] += int(value)
            evaluated.append(
                EvaluatedClip(
                    clip_id=clip.clip_id,
                    label=clip.label,
                    scenario=clip.scenario,
                    locale=clip.locale,
                    snr_db=clip.snr_db,
                    noise_kind=clip.noise_kind,
                    duration_seconds=duration,
                    rnnoise_chunk_peak=max(
                        (
                            value.peak
                            for value in chunk_evidence
                            if value.peak is not None
                        ),
                        default=0.0,
                    ),
                    rnnoise_chunk_ema_peak=max(
                        (
                            value.ema
                            for value in chunk_evidence
                            if value.ema is not None
                        ),
                        default=0.0,
                    ),
                    rnnoise_online_trigger_ms=online_trigger_ms,
                    rnnoise_offline_sustained_100ms_score=(
                        sustained_presence_score(
                            frame_probabilities,
                            minimum_windows=RNNOISE_OFFLINE_SUSTAINED_FRAMES,
                        )
                    ),
                    rnnoise_offline_sustained_100ms_trigger_ms=(
                        _first_sustained_trigger_ms(
                            frame_probabilities,
                            RNNOISE_OFFLINE_SUSTAINED_THRESHOLD,
                            RNNOISE_OFFLINE_SUSTAINED_FRAMES,
                            RNNOISE_CHUNK_MS,
                        )
                    ),
                    silero_raw_score=silero_presence_score(raw_probabilities),
                    silero_after_rnnoise_score=silero_presence_score(
                        denoised_probabilities
                    ),
                    silero_raw_trigger_ms=first_silero_trigger_ms(
                        raw_probabilities,
                        SILERO_CURRENT_THRESHOLD,
                    ),
                    silero_after_rnnoise_trigger_ms=first_silero_trigger_ms(
                        denoised_probabilities,
                        SILERO_CURRENT_THRESHOLD,
                    ),
                    speech_start_ms=clip.speech_start_ms,
                    device_id=clip.device_id,
                )
            )
    finally:
        processor.close()
        vad.close()

    mib = 1024 * 1024
    denominator = total_audio_seconds or 1.0
    zero_shadow_metrics = asdict(
        ThrottleShadowMetrics(
            evidence_chunk_count=0,
            incomplete_chunk_count=0,
            rnnoise_trigger_count=0,
            silero_trigger_count=0,
            rnnoise_silero_disagreement_count=0,
        )
    )
    normalized_shadow_metrics = {
        name: throttle_shadow_metrics.get(name, 0)
        for name in zero_shadow_metrics
    }
    return evaluated, {
        "total_audio_seconds": total_audio_seconds,
        "rnnoise_pipeline_wall_realtime_factor": (
            rnnoise_wall_seconds / denominator
        ),
        "rnnoise_pipeline_cpu_realtime_factor": rnnoise_cpu_seconds / denominator,
        "silero_pair_wall_realtime_factor": silero_wall_seconds / denominator,
        "silero_pair_cpu_realtime_factor": silero_cpu_seconds / denominator,
        "rnnoise_rss_delta_mib": (rss_after_rnnoise - rss_before) / mib,
        "silero_rss_delta_mib": (rss_after_silero - rss_after_rnnoise) / mib,
        "throttle_shadow_metrics": normalized_shadow_metrics,
    }


def _metrics(
    clips: Sequence[EvaluatedClip],
    predictions: Sequence[bool],
) -> dict[str, float | int]:
    return metrics_from_confusion(
        confusion_from_predictions(
            [clip.label for clip in clips],
            predictions,
        )
    )


def build_report(
    clips: Sequence[EvaluatedClip],
    *,
    performance: dict[str, Any],
    seed: int,
    asset_dir: Path,
    corpus_manifest: dict[str, Any],
) -> dict[str, Any]:
    calibration, holdout = split_calibration_holdout(clips, seed=seed)
    selection = select_presence_threshold(
        calibration,
        score_name="rnnoise_offline_sustained_100ms_score",
        thresholds=PRESENCE_THRESHOLDS,
    )
    offline_threshold = float(selection["threshold"])

    def online_predictions(values: Sequence[EvaluatedClip]) -> list[bool]:
        return [clip.rnnoise_online_trigger_ms is not None for clip in values]

    def offline_predictions(values: Sequence[EvaluatedClip]) -> list[bool]:
        return [
            clip.rnnoise_offline_sustained_100ms_score >= offline_threshold
            for clip in values
        ]

    def distribution(values: Sequence[float]) -> dict[str, float | int | None]:
        if not values:
            return {"count": 0, "median": None, "p95": None}
        samples = np.asarray(values, dtype=np.float64)
        return {
            "count": len(values),
            "median": float(np.median(samples)),
            "p95": float(np.percentile(samples, 95)),
        }

    def score_report(score_name: str) -> dict[str, Any]:
        selected = select_presence_threshold(
            calibration,
            score_name=score_name,
            thresholds=PRESENCE_THRESHOLDS,
        )
        threshold = float(selected["threshold"])

        def predictions(values: Sequence[EvaluatedClip]) -> list[bool]:
            return [
                float(getattr(clip, score_name)) >= threshold
                for clip in values
            ]

        return {
            "selection": selected,
            "calibration": _metrics(calibration, predictions(calibration)),
            "holdout": _metrics(holdout, predictions(holdout)),
        }

    def onset_latency(trigger_name: str) -> dict[str, float | int | None]:
        values = [
            float(trigger) - float(clip.speech_start_ms)
            for clip in clips
            if clip.label
            and clip.speech_start_ms is not None
            and (trigger := getattr(clip, trigger_name)) is not None
        ]
        return distribution(values)

    default_asset_dir = (
        PROJECT_ROOT
        / "main_logic"
        / "asr_client"
        / "endpointing"
        / "models"
    ).resolve()
    resolved_asset_dir = asset_dir.resolve()
    reported_asset_dir = _project_relative_path(resolved_asset_dir)
    default_asset_relative = _project_relative_path(default_asset_dir)
    shadow_metrics = performance.get("throttle_shadow_metrics")
    if not isinstance(shadow_metrics, dict):
        shadow_metrics = asdict(
            ThrottleShadowMetrics(
                evidence_chunk_count=0,
                incomplete_chunk_count=0,
                rnnoise_trigger_count=0,
                silero_trigger_count=0,
                rnnoise_silero_disagreement_count=0,
            )
        )

    return {
        "schema_version": 2,
        "scope": "speech_presence_only",
        "revision": os.environ.get("GIT_COMMIT", "working-tree"),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "corpus": corpus_manifest,
        "corpus_summary": {
            "clip_count": len(clips),
            "duration_seconds": sum(clip.duration_seconds for clip in clips),
            "locales": sorted(
                {clip.locale for clip in clips if clip.locale is not None}
            ),
            "device_ids": sorted(
                {clip.device_id for clip in clips if clip.device_id is not None}
            ),
            "rnnoise_chunk_peak": distribution(
                [clip.rnnoise_chunk_peak for clip in clips]
            ),
            "rnnoise_chunk_ema_peak": distribution(
                [clip.rnnoise_chunk_ema_peak for clip in clips]
            ),
        },
        "asset_contract": {
            "silero_directory": reported_asset_dir,
            "default_directory": default_asset_relative,
            "uses_default_directory": resolved_asset_dir == default_asset_dir,
        },
        "online_contract": {
            "granularity": "one_input_chunk",
            "rnnoise_fields": ["frame_count", "peak", "mean", "last", "ema"],
            "shadow_metric_kinds": [
                "action_count",
                "evidence_chunk_count",
                "disagreement_count",
            ],
            "shadow_metrics": shadow_metrics,
            "calibration": _metrics(
                calibration,
                online_predictions(calibration),
            ),
            "holdout": _metrics(holdout, online_predictions(holdout)),
        },
        "offline_candidates": {
            "rnnoise_continuous_100ms": {
                "production_behavior": False,
                "selection": selection,
                "calibration": _metrics(
                    calibration,
                    offline_predictions(calibration),
                ),
                "holdout": _metrics(holdout, offline_predictions(holdout)),
            }
        },
        "silero_paths": {
            "raw": score_report("silero_raw_score"),
            "after_rnnoise": score_report("silero_after_rnnoise_score"),
        },
        "onset_latency_ms": {
            "rnnoise_online": onset_latency("rnnoise_online_trigger_ms"),
            "rnnoise_offline_sustained_100ms": onset_latency(
                "rnnoise_offline_sustained_100ms_trigger_ms"
            ),
            "silero_raw": onset_latency("silero_raw_trigger_ms"),
            "silero_after_rnnoise": onset_latency(
                "silero_after_rnnoise_trigger_ms"
            ),
        },
        "performance": performance,
        "limitations": [
            "Presence evidence never publishes a provider final.",
            "The continuous 100ms RNNoise result is offline-only.",
            (
                "Its onset latency uses the fixed 0.7 threshold, not the "
                "calibrated threshold reported for that offline candidate."
            ),
            (
                "Silero onset latency uses onset/offset hysteresis counting, "
                "not strictly consecutive windows at or above the onset "
                "threshold used by the sustained score."
            ),
            "Repository TTS and synthetic noise do not replace device holdout.",
            (
                "Raw Silero uses one-shot soxr.resample on unprocessed audio, "
                "while the RNNoise path uses AudioProcessor's streaming "
                "resampler with RNNoise, AGC, and limiter enabled, so their "
                "score difference is not attributable to RNNoise alone or "
                "solely to resampling."
            ),
            "Reports contain aggregate identities, not local recording paths.",
        ],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-device-manifest",
        required=True,
        type=Path,
        help="JSON manifest of labeled recordings",
    )
    parser.add_argument(
        "--asset-dir",
        type=Path,
        default=(
            PROJECT_ROOT
            / "main_logic"
            / "asr_client"
            / "endpointing"
            / "models"
        ),
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    clips, manifest = load_real_device_manifest(args.real_device_manifest)
    evaluated, performance = evaluate_corpus(clips, args.asset_dir.resolve())
    report = build_report(
        evaluated,
        performance=performance,
        seed=args.seed,
        asset_dir=args.asset_dir.resolve(),
        corpus_manifest={"real_device": manifest},
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
