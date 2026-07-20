#!/usr/bin/env python3
"""Benchmark RNNoise and Silero as speech-presence evidence.

This tool deliberately evaluates only the cheap question "might somebody be
speaking?".  It does not evaluate semantic turn completion and must not be
used to grant provider-native endpoint authority.

The default corpus is reproducible and repository-local:

* positive clips: multilingual tutorial TTS with deterministic SNR mixing;
* negative clips: synthetic silence/noise and explicitly selected non-speech
  badminton sound effects;
* no raw PCM, embeddings, or user recordings are written to the report.
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


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_logic.voice_turn.silero_vad import SileroVad  # noqa: E402
from utils.audio_processor import AudioProcessor  # noqa: E402


SAMPLE_RATE_48K = 48_000
SAMPLE_RATE_16K = 16_000
RNNOISE_CHUNK_MS = 20
RNNOISE_CURRENT_THRESHOLD = 0.35
RNNOISE_EXPLORATORY_SUSTAINED_THRESHOLD = 0.7
RNNOISE_EXPLORATORY_SUSTAINED_FRAMES = 10
SILERO_CURRENT_THRESHOLD = 0.5
SILERO_OFFSET_THRESHOLD = 0.35
SILERO_MINIMUM_SPEECH_MS = 200
SILERO_WINDOW_MS = 1000 * SileroVad.WINDOW_SAMPLES / SileroVad.SAMPLE_RATE
SILERO_MINIMUM_WINDOWS = max(
    1, math.ceil(SILERO_MINIMUM_SPEECH_MS / SILERO_WINDOW_MS)
)
DEFAULT_SEED = 2398
DEFAULT_HOLDOUT_FRACTION = 0.25
DEFAULT_SNR_DB = (20, 10, 5, 0, -5)
RNNOISE_THRESHOLDS = (
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
SILERO_THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7)


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
    rnnoise_peak: float
    rnnoise_ema_peak: float
    rnnoise_sustained_100ms_score: float
    rnnoise_sustained_200ms_score: float
    silero_raw_score: float
    silero_after_rnnoise_score: float
    rnnoise_trigger_ms: float | None
    rnnoise_current_policy_trigger_ms: float | None
    rnnoise_sustained_100ms_trigger_ms: float | None
    silero_raw_trigger_ms: float | None
    silero_after_rnnoise_trigger_ms: float | None
    speech_start_ms: float | None
    device_id: str | None


def source_group_id(clip_id: str) -> str:
    """Return the underlying source identity used to prevent split leakage."""

    parts = str(clip_id).split("/")
    if len(parts) >= 4 and parts[0] == "speech":
        return "/".join(parts[:3])
    return str(clip_id)


def confusion_from_predictions(
    labels: Sequence[bool], predictions: Sequence[bool]
) -> Confusion:
    """Return binary confusion counts for equally-sized inputs."""

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
    """Calculate metrics without letting class imbalance hide miss rates."""

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
    holdout_fraction: float = 0.25,
) -> tuple[list[Any], list[Any]]:
    """Split by source group so augmented variants never cross partitions."""

    if not 0.0 < holdout_fraction < 1.0:
        raise ValueError("holdout_fraction must be within (0, 1)")
    groups: dict[str, list[Any]] = defaultdict(list)
    for clip in clips:
        groups[source_group_id(clip.clip_id)].append(clip)
    strata: dict[tuple[bool, str], list[str]] = defaultdict(list)
    for group_id, group in groups.items():
        labels = {bool(clip.label) for clip in group}
        locales = {str(getattr(clip, "locale", None) or "") for clip in group}
        device_ids = {
            str(getattr(clip, "device_id", None) or "") for clip in group
        }
        if len(labels) != 1 or len(locales) != 1 or len(device_ids) != 1:
            raise ValueError(
                f"source group is not label/locale/device homogeneous: {group_id}"
            )
        label = next(iter(labels))
        locale = next(iter(locales)) if label else "negative"
        device_id = next(iter(device_ids))
        if device_id:
            scenario = str(getattr(group[0], "scenario", "") or "unspecified")
            stratum = f"device:{device_id}:{scenario}:{locale}"
        elif label:
            stratum = f"locale:{locale}"
        else:
            scenario = str(getattr(group[0], "scenario", "") or "negative")
            stratum = f"scenario:{scenario}"
        strata[(label, stratum)].append(group_id)

    holdout_groups: set[str] = set()
    for stratum, group_ids in strata.items():
        ordered = sorted(
            group_ids,
            key=lambda group_id: hashlib.sha256(
                f"{seed}:{stratum}:{group_id}".encode()
            ).hexdigest(),
        )
        if len(ordered) < 2:
            continue
        count = max(1, round(len(ordered) * holdout_fraction))
        holdout_groups.update(ordered[: min(count, len(ordered) - 1)])

    calibration = [
        clip for clip in clips if source_group_id(clip.clip_id) not in holdout_groups
    ]
    holdout = [
        clip for clip in clips if source_group_id(clip.clip_id) in holdout_groups
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
    """Choose a threshold using calibration metrics only."""

    if not clips or not thresholds:
        raise ValueError("threshold selection requires clips and thresholds")
    rows: list[dict[str, float | int]] = []
    for threshold in thresholds:
        predictions = [getattr(clip, score_name) >= threshold for clip in clips]
        row = {
            "threshold": float(threshold),
            **metrics_from_confusion(
                confusion_from_predictions(
                    [bool(clip.label) for clip in clips], predictions
                )
            ),
        }
        rows.append(row)
    return max(
        rows,
        key=lambda row: (
            float(row["balanced_accuracy"]),
            float(row["speech_recall"]),
            float(row["negative_specificity"]),
            -float(row["threshold"]),
        ),
    )


def silero_presence_score(
    probabilities: Sequence[float],
    *,
    minimum_windows: int = SILERO_MINIMUM_WINDOWS,
) -> float:
    """Score the strongest sustained Silero run, not a one-window spike."""

    if minimum_windows <= 0:
        raise ValueError("minimum_windows must be positive")
    if len(probabilities) < minimum_windows:
        return 0.0
    values = np.asarray(probabilities, dtype=np.float64)
    return max(
        float(np.min(values[index : index + minimum_windows]))
        for index in range(len(values) - minimum_windows + 1)
    )


def first_silero_trigger_ms(
    probabilities: Sequence[float],
    threshold: float,
    *,
    minimum_windows: int = SILERO_MINIMUM_WINDOWS,
    offset_threshold: float = SILERO_OFFSET_THRESHOLD,
) -> float | None:
    """Mirror the production onset counter, including its offset reset rule."""

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
    """Mix float mono audio at a requested RMS SNR without clipping."""

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
        mixed = mixed * (0.98 / peak)
    return mixed.astype(np.float32)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_mono_48k(path: Path) -> np.ndarray:
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = np.mean(samples, axis=1, dtype=np.float32)
    if sample_rate != SAMPLE_RATE_48K:
        mono = soxr.resample(mono, sample_rate, SAMPLE_RATE_48K, quality="HQ")
    return np.asarray(mono, dtype=np.float32)


def _highest_energy_segment(samples: np.ndarray, seconds: float) -> np.ndarray:
    target = round(seconds * SAMPLE_RATE_48K)
    if samples.size <= target:
        return np.pad(samples, (0, target - samples.size)).astype(np.float32)
    squared = np.square(samples, dtype=np.float64)
    cumulative = np.concatenate(([0.0], np.cumsum(squared)))
    energies = cumulative[target:] - cumulative[:-target]
    start = int(np.argmax(energies))
    segment = samples[start : start + target].copy()
    fade = min(round(0.01 * SAMPLE_RATE_48K), target // 2)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, endpoint=False, dtype=np.float32)
        segment[:fade] *= ramp
        segment[-fade:] *= ramp[::-1]
    return segment


def _unit_noise(
    kind: str, sample_count: int, rng: np.random.Generator
) -> np.ndarray:
    if kind == "silence":
        return np.zeros(sample_count, dtype=np.float32)
    if kind == "white":
        values = rng.normal(size=sample_count)
    elif kind == "pink":
        frequencies = np.fft.rfftfreq(sample_count)
        spectrum = rng.normal(size=frequencies.size) + 1j * rng.normal(
            size=frequencies.size
        )
        scale = np.ones_like(frequencies)
        scale[1:] = 1.0 / np.sqrt(frequencies[1:])
        spectrum *= scale
        spectrum[0] = 0
        values = np.fft.irfft(spectrum, n=sample_count)
    elif kind == "fan":
        timeline = np.arange(sample_count, dtype=np.float64) / SAMPLE_RATE_48K
        base = float(rng.choice((50.0, 60.0)))
        values = sum(
            (1.0 / harmonic)
            * np.sin(2 * np.pi * base * harmonic * timeline + rng.uniform(0, 2 * np.pi))
            for harmonic in range(1, 7)
        )
        broadband = rng.normal(size=sample_count)
        kernel = np.ones(96, dtype=np.float64) / 96
        values += 0.6 * np.convolve(broadband, kernel, mode="same")
    elif kind == "impulse":
        values = 0.015 * rng.normal(size=sample_count)
        burst_count = max(1, round(sample_count / SAMPLE_RATE_48K * 5))
        for _ in range(burst_count):
            width = int(rng.integers(96, 960))
            start = int(rng.integers(0, max(1, sample_count - width)))
            values[start : start + width] += (
                rng.uniform(0.4, 1.0)
                * np.hanning(width)
                * rng.choice((-1.0, 1.0))
            )
    else:
        raise ValueError(f"unsupported noise kind: {kind}")
    rms = float(np.sqrt(np.mean(np.square(values), dtype=np.float64)))
    if rms <= 1e-12:
        raise ValueError(f"generated {kind} noise has no energy")
    return np.asarray(values / rms, dtype=np.float32)


def _negative_noise(
    kind: str,
    sample_count: int,
    dbfs: float,
    rng: np.random.Generator,
) -> np.ndarray:
    if kind == "silence":
        return np.zeros(sample_count, dtype=np.float32)
    values = _unit_noise(kind, sample_count, rng)
    values *= 10 ** (dbfs / 20)
    peak = float(np.max(np.abs(values)))
    if peak > 0.98:
        values *= 0.98 / peak
    return values.astype(np.float32)


def _selected_speech_files(repo_root: Path, per_locale: int) -> dict[str, list[Path]]:
    root = repo_root / "static" / "assets" / "tutorial" / "guide-audio"
    selected: dict[str, list[Path]] = {}
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        files = sorted(directory.glob("*.mp3"))
        if not files:
            continue
        count = min(per_locale, len(files))
        indices = np.linspace(0, len(files) - 1, count, dtype=int)
        selected[directory.name] = [files[int(index)] for index in indices]
    if not selected:
        raise FileNotFoundError(f"no tutorial speech assets found under {root}")
    return selected


def build_corpus(
    repo_root: Path,
    *,
    speech_per_locale: int,
    negative_per_kind: int,
    seed: int,
) -> tuple[list[CorpusClip], dict[str, Any]]:
    """Build deterministic 2.5-second labeled clips entirely in memory."""

    if speech_per_locale <= 0 or negative_per_kind <= 0:
        raise ValueError("corpus counts must be positive")
    rng = np.random.default_rng(seed)
    speech_seconds = 1.5
    lead_seconds = 0.5
    tail_seconds = 0.5
    clip_seconds = lead_seconds + speech_seconds + tail_seconds
    sample_count = round(clip_seconds * SAMPLE_RATE_48K)
    lead_samples = round(lead_seconds * SAMPLE_RATE_48K)
    speech_samples = round(speech_seconds * SAMPLE_RATE_48K)
    speech_mask = np.zeros(sample_count, dtype=bool)
    speech_mask[lead_samples : lead_samples + speech_samples] = True
    clips: list[CorpusClip] = []
    selected_files = _selected_speech_files(repo_root, speech_per_locale)
    noise_kinds = ("white", "pink", "fan", "impulse")
    corpus_manifest: dict[str, Any] = {"speech_files": {}}

    for locale, paths in selected_files.items():
        corpus_manifest["speech_files"][locale] = [
            path.relative_to(repo_root).as_posix() for path in paths
        ]
        for file_index, path in enumerate(paths):
            speech = _highest_energy_segment(_read_mono_48k(path), speech_seconds)
            clean = np.zeros(sample_count, dtype=np.float32)
            clean[lead_samples : lead_samples + speech_samples] = speech
            clip_prefix = f"speech/{locale}/{file_index:02d}"
            clips.append(
                CorpusClip(
                    clip_id=f"{clip_prefix}/clean",
                    label=True,
                    scenario="clean_speech",
                    samples_48k=clean,
                    locale=locale,
                    speech_start_ms=lead_seconds * 1000,
                )
            )
            for snr_index, snr_db in enumerate(DEFAULT_SNR_DB):
                noise_kind = noise_kinds[(file_index + snr_index) % len(noise_kinds)]
                noise = _unit_noise(noise_kind, sample_count, rng)
                mixed = mix_at_snr(
                    clean,
                    noise,
                    snr_db,
                    speech_mask=speech_mask,
                )
                clips.append(
                    CorpusClip(
                        clip_id=f"{clip_prefix}/snr_{snr_db:+d}",
                        label=True,
                        scenario=f"speech_snr_{snr_db:+d}",
                        samples_48k=mixed,
                        locale=locale,
                        snr_db=snr_db,
                        noise_kind=noise_kind,
                        speech_start_ms=lead_seconds * 1000,
                    )
                )

    levels = (-45.0, -35.0, -25.0, -15.0)
    for kind in ("silence", "white", "pink", "fan", "impulse"):
        for index in range(negative_per_kind):
            level = levels[index % len(levels)]
            clips.append(
                CorpusClip(
                    clip_id=f"negative/{kind}/{index:02d}",
                    label=False,
                    scenario=f"negative_{kind}",
                    samples_48k=_negative_noise(kind, sample_count, level, rng),
                    noise_kind=kind,
                )
            )

    sfx_root = repo_root / "static" / "game" / "games" / "badminton" / "audio"
    sfx_paths = sorted(sfx_root.glob("badminton-racket-shuttlecock*.mp3"))
    sfx_paths.extend(sorted(sfx_root.glob("zapsplat_sport_badminton*.mp3")))
    corpus_manifest["negative_sfx_files"] = [
        path.relative_to(repo_root).as_posix() for path in sfx_paths
    ]
    for index, path in enumerate(sfx_paths):
        clips.append(
            CorpusClip(
                clip_id=f"negative/game_sfx/{index:02d}",
                label=False,
                scenario="negative_game_sfx",
                samples_48k=_highest_energy_segment(
                    _read_mono_48k(path), clip_seconds
                ),
                noise_kind="game_sfx",
            )
        )
    return clips, corpus_manifest


def load_real_device_manifest(
    manifest_path: Path,
) -> tuple[list[CorpusClip], dict[str, Any]]:
    """Load labeled real-device recordings without reporting local file paths."""

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
            raise ValueError("real-device id and device_id must be non-empty path atoms")
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
        audio_path = (resolved_manifest.parent / relative_path).resolve()
        if not audio_path.is_file():
            raise FileNotFoundError(audio_path)
        scenario = str(
            entry.get("scenario")
            or ("real_device_speech" if label else "real_device_negative")
        ).strip()
        speech_start = entry.get("speech_start_ms")
        if speech_start is not None:
            speech_start = float(speech_start)
            if speech_start < 0:
                raise ValueError("speech_start_ms must not be negative")
        locale = entry.get("locale")
        clips.append(
            CorpusClip(
                clip_id=report_id,
                label=label,
                scenario=scenario,
                samples_48k=_read_mono_48k(audio_path),
                locale=str(locale).strip() if locale else None,
                speech_start_ms=speech_start,
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


def _rnnoise_process(
    processor: AudioProcessor, samples_48k: np.ndarray
) -> tuple[list[float], list[float], bytes, float, float]:
    chunk_samples = SAMPLE_RATE_48K * RNNOISE_CHUNK_MS // 1000
    frame_probabilities: list[float] = []
    ema_values: list[float] = []
    processed_chunks: list[bytes] = []
    denoiser = processor._denoiser  # noqa: SLF001 - benchmark-only instrumentation
    if denoiser is None:
        raise RuntimeError("RNNoise denoiser disappeared during evaluation")
    original_process_frame = denoiser.process_frame

    def capture_process_frame(frame: np.ndarray) -> tuple[np.ndarray, float]:
        denoised, probability = original_process_frame(frame)
        frame_probabilities.append(float(probability))
        return denoised, probability

    denoiser.process_frame = capture_process_frame
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    try:
        for start in range(0, samples_48k.size, chunk_samples):
            chunk = samples_48k[start : start + chunk_samples]
            processed = processor.process_chunk(_pcm16(chunk))
            if processed:
                processed_chunks.append(processed)
            ema = processor.rnnoise_probability_ema
            if ema is not None:
                ema_values.append(float(ema))
    finally:
        cpu_elapsed = time.process_time() - cpu_started
        wall_elapsed = time.perf_counter() - wall_started
        denoiser.process_frame = original_process_frame
    return (
        frame_probabilities,
        ema_values,
        b"".join(processed_chunks),
        wall_elapsed,
        cpu_elapsed,
    )


def _first_threshold_trigger_ms(
    scores: Sequence[float], threshold: float, frame_ms: float
) -> float | None:
    for index, score in enumerate(scores):
        if score >= threshold:
            return (index + 1) * frame_ms
    return None


def _first_sustained_trigger_ms(
    scores: Sequence[float], threshold: float, minimum_windows: int, frame_ms: float
) -> float | None:
    sustained = 0
    for index, score in enumerate(scores):
        sustained = sustained + 1 if score >= threshold else 0
        if sustained >= minimum_windows:
            return (index + 1) * frame_ms
    return None


def _current_rnnoise_policy_trigger_ms(
    frame_probabilities: Sequence[float],
    *,
    frames_per_chunk: int = 2,
) -> float | None:
    """Simulate the fresh-session #2398 adaptive RNNoise onset policy."""

    if frames_per_chunk <= 0:
        raise ValueError("frames_per_chunk must be positive")
    baseline: float | None = None
    baseline_samples = 0
    for start in range(0, len(frame_probabilities), frames_per_chunk):
        chunk = frame_probabilities[start : start + frames_per_chunk]
        if not chunk:
            continue
        mean = sum(chunk) / len(chunk)
        baseline = mean if baseline is None else 0.05 * mean + 0.95 * baseline
        baseline_samples += len(chunk)
        threshold = (
            RNNOISE_CURRENT_THRESHOLD
            if baseline_samples < 20
            else min(0.65, max(0.20, baseline + 0.12))
        )
        if max(chunk) >= threshold:
            return (start + len(chunk)) * 10.0
    return None


def evaluate_corpus(
    clips: Iterable[CorpusClip], asset_dir: Path
) -> tuple[list[EvaluatedClip], dict[str, float]]:
    process = psutil.Process(os.getpid())
    gc.collect()
    rss_before_rnnoise = process.memory_info().rss
    processor = AudioProcessor(
        input_sample_rate=SAMPLE_RATE_48K,
        output_sample_rate=SAMPLE_RATE_16K,
        noise_reduce_enabled=True,
        agc_enabled=True,
        limiter_enabled=True,
    )
    if processor._denoiser is None:  # noqa: SLF001 - benchmark must fail closed
        raise RuntimeError("RNNoise native runtime is unavailable")
    rss_after_rnnoise = process.memory_info().rss
    import onnxruntime  # noqa: F401, PLC0415 - isolate shared runtime RSS

    rss_after_onnxruntime_import = process.memory_info().rss
    vad = SileroVad(enabled=True, asset_dir=asset_dir, intra_op_threads=1)
    if not vad.load():
        raise RuntimeError(f"Silero failed to load: {vad.unavailable_reason}")
    vad.process_pcm16(np.zeros(SileroVad.WINDOW_SAMPLES, dtype="<i2").tobytes())
    vad.reset_stream()
    rss_after_silero_warm = process.memory_info().rss

    evaluated: list[EvaluatedClip] = []
    rnnoise_wall_seconds = 0.0
    rnnoise_cpu_seconds = 0.0
    silero_raw_wall_seconds = 0.0
    silero_raw_cpu_seconds = 0.0
    silero_after_rnnoise_wall_seconds = 0.0
    silero_after_rnnoise_cpu_seconds = 0.0
    audio_seconds = 0.0
    try:
        for index, clip in enumerate(clips, start=1):
            processor.reset()
            vad.reset_stream()
            (
                frame_probabilities,
                ema_values,
                processed_16k,
                rnnoise_wall_elapsed,
                rnnoise_cpu_elapsed,
            ) = _rnnoise_process(processor, clip.samples_48k)
            raw_16k = soxr.resample(
                clip.samples_48k, SAMPLE_RATE_48K, SAMPLE_RATE_16K, quality="HQ"
            )
            raw_wall_started = time.perf_counter()
            raw_cpu_started = time.process_time()
            raw_probabilities = vad.process_pcm16(_pcm16(raw_16k))
            raw_cpu_elapsed = time.process_time() - raw_cpu_started
            raw_wall_elapsed = time.perf_counter() - raw_wall_started
            vad.reset_stream()
            denoised_wall_started = time.perf_counter()
            denoised_cpu_started = time.process_time()
            denoised_probabilities = vad.process_pcm16(processed_16k)
            denoised_cpu_elapsed = time.process_time() - denoised_cpu_started
            denoised_wall_elapsed = time.perf_counter() - denoised_wall_started
            duration = clip.samples_48k.size / SAMPLE_RATE_48K
            audio_seconds += duration
            rnnoise_wall_seconds += rnnoise_wall_elapsed
            rnnoise_cpu_seconds += rnnoise_cpu_elapsed
            silero_raw_wall_seconds += raw_wall_elapsed
            silero_raw_cpu_seconds += raw_cpu_elapsed
            silero_after_rnnoise_wall_seconds += denoised_wall_elapsed
            silero_after_rnnoise_cpu_seconds += denoised_cpu_elapsed
            evaluated.append(
                EvaluatedClip(
                    clip_id=clip.clip_id,
                    label=clip.label,
                    scenario=clip.scenario,
                    locale=clip.locale,
                    snr_db=clip.snr_db,
                    noise_kind=clip.noise_kind,
                    duration_seconds=duration,
                    rnnoise_peak=max(frame_probabilities, default=0.0),
                    rnnoise_ema_peak=max(ema_values, default=0.0),
                    rnnoise_sustained_100ms_score=silero_presence_score(
                        frame_probabilities, minimum_windows=10
                    ),
                    rnnoise_sustained_200ms_score=silero_presence_score(
                        frame_probabilities, minimum_windows=20
                    ),
                    silero_raw_score=silero_presence_score(raw_probabilities),
                    silero_after_rnnoise_score=silero_presence_score(
                        denoised_probabilities
                    ),
                    rnnoise_trigger_ms=_first_threshold_trigger_ms(
                        frame_probabilities, RNNOISE_CURRENT_THRESHOLD, 10
                    ),
                    rnnoise_current_policy_trigger_ms=(
                        _current_rnnoise_policy_trigger_ms(frame_probabilities)
                    ),
                    rnnoise_sustained_100ms_trigger_ms=_first_sustained_trigger_ms(
                        frame_probabilities,
                        RNNOISE_EXPLORATORY_SUSTAINED_THRESHOLD,
                        RNNOISE_EXPLORATORY_SUSTAINED_FRAMES,
                        10,
                    ),
                    silero_raw_trigger_ms=first_silero_trigger_ms(
                        raw_probabilities, SILERO_CURRENT_THRESHOLD
                    ),
                    silero_after_rnnoise_trigger_ms=first_silero_trigger_ms(
                        denoised_probabilities, SILERO_CURRENT_THRESHOLD
                    ),
                    speech_start_ms=clip.speech_start_ms,
                    device_id=clip.device_id,
                )
            )
            if index % 25 == 0:
                print(f"evaluated {index} clips", file=sys.stderr)
    finally:
        processor.close()
        vad.close()
    mib = 1024 * 1024
    return evaluated, {
        "total_audio_seconds": audio_seconds,
        "rnnoise_pipeline_wall_seconds": rnnoise_wall_seconds,
        "rnnoise_pipeline_cpu_seconds": rnnoise_cpu_seconds,
        "silero_raw_wall_seconds": silero_raw_wall_seconds,
        "silero_raw_cpu_seconds": silero_raw_cpu_seconds,
        "silero_after_rnnoise_wall_seconds": silero_after_rnnoise_wall_seconds,
        "silero_after_rnnoise_cpu_seconds": silero_after_rnnoise_cpu_seconds,
        "rnnoise_pipeline_wall_realtime_factor": (
            rnnoise_wall_seconds / audio_seconds
        ),
        "rnnoise_pipeline_cpu_realtime_factor": rnnoise_cpu_seconds / audio_seconds,
        "silero_raw_wall_realtime_factor": silero_raw_wall_seconds / audio_seconds,
        "silero_raw_cpu_realtime_factor": silero_raw_cpu_seconds / audio_seconds,
        "silero_after_rnnoise_wall_realtime_factor": (
            silero_after_rnnoise_wall_seconds / audio_seconds
        ),
        "silero_after_rnnoise_cpu_realtime_factor": (
            silero_after_rnnoise_cpu_seconds / audio_seconds
        ),
        "rnnoise_rss_delta_mib": (rss_after_rnnoise - rss_before_rnnoise) / mib,
        "onnxruntime_import_rss_delta_mib": (
            rss_after_onnxruntime_import - rss_after_rnnoise
        )
        / mib,
        "silero_session_warm_rss_delta_mib": (
            rss_after_silero_warm - rss_after_onnxruntime_import
        )
        / mib,
        "silero_combined_rss_delta_mib": (
            rss_after_silero_warm - rss_after_rnnoise
        )
        / mib,
    }


def _metric_row(
    clips: Sequence[EvaluatedClip],
    predictions: Sequence[bool],
) -> dict[str, float | int]:
    return metrics_from_confusion(
        confusion_from_predictions([clip.label for clip in clips], predictions)
    )


def _threshold_curve(
    clips: Sequence[EvaluatedClip],
    score_name: str,
    thresholds: Sequence[float],
) -> list[dict[str, float | int]]:
    rows = []
    for threshold in thresholds:
        predictions = [getattr(clip, score_name) >= threshold for clip in clips]
        rows.append({"threshold": threshold, **_metric_row(clips, predictions)})
    return rows


def _metrics_at_threshold(
    clips: Sequence[EvaluatedClip], score_name: str, threshold: float
) -> dict[str, float | int]:
    return _metric_row(
        clips,
        [getattr(clip, score_name) >= threshold for clip in clips],
    )


def _real_device_metrics(
    clips: Sequence[EvaluatedClip],
    *,
    score_name: str,
    threshold: float,
) -> dict[str, Any]:
    groups: dict[str, list[EvaluatedClip]] = defaultdict(list)
    for clip in clips:
        if clip.device_id:
            groups[clip.device_id].append(clip)
    if not groups:
        return {
            "available": False,
            "reason": "no_real_device_manifest_supplied",
            "devices": {},
        }
    return {
        "available": True,
        "devices": {
            device_id: {
                "clips": len(group),
                **_metrics_at_threshold(group, score_name, threshold),
            }
            for device_id, group in sorted(groups.items())
        },
    }


def _current_strategy_metrics(
    clips: Sequence[EvaluatedClip],
) -> dict[str, dict[str, float | int]]:
    rnnoise_fixed = [
        clip.rnnoise_peak >= RNNOISE_CURRENT_THRESHOLD for clip in clips
    ]
    rnnoise_current_policy = [
        clip.rnnoise_current_policy_trigger_ms is not None for clip in clips
    ]
    rnnoise_100ms = [
        clip.rnnoise_sustained_100ms_score >= RNNOISE_CURRENT_THRESHOLD
        for clip in clips
    ]
    rnnoise_200ms = [
        clip.rnnoise_sustained_200ms_score >= RNNOISE_CURRENT_THRESHOLD
        for clip in clips
    ]
    silero_raw = [clip.silero_raw_trigger_ms is not None for clip in clips]
    silero_after = [
        clip.silero_after_rnnoise_trigger_ms is not None for clip in clips
    ]
    return {
        "rnnoise_fixed_peak_0.35": _metric_row(clips, rnnoise_fixed),
        "rnnoise_current_adaptive_policy_fresh_session": _metric_row(
            clips, rnnoise_current_policy
        ),
        "rnnoise_sustained_100ms_0.35": _metric_row(clips, rnnoise_100ms),
        "rnnoise_sustained_200ms_0.35": _metric_row(clips, rnnoise_200ms),
        "silero_raw_0.5_200ms": _metric_row(clips, silero_raw),
        "silero_after_rnnoise_0.5_200ms": _metric_row(clips, silero_after),
        "rnnoise_and_silero_after_rnnoise": _metric_row(
            clips,
            [
                left and right
                for left, right in zip(
                    rnnoise_current_policy, silero_after, strict=True
                )
            ],
        ),
        "rnnoise_or_silero_raw": _metric_row(
            clips,
            [
                left or right
                for left, right in zip(
                    rnnoise_current_policy, silero_raw, strict=True
                )
            ],
        ),
    }


def _group_metrics(
    clips: Sequence[EvaluatedClip], score_name: str, threshold: float
) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[EvaluatedClip]] = defaultdict(list)
    for clip in clips:
        groups[clip.scenario].append(clip)
    result: dict[str, dict[str, float | int]] = {}
    for name, group in sorted(groups.items()):
        predictions = [getattr(clip, score_name) >= threshold for clip in group]
        labels = [clip.label for clip in group]
        if all(labels):
            result[name] = {
                "clips": len(group),
                "speech_recall": sum(predictions) / len(predictions),
            }
        elif not any(labels):
            result[name] = {
                "clips": len(group),
                "false_positive_rate": sum(predictions) / len(predictions),
            }
        else:
            result[name] = _metric_row(group, predictions)
    return result


def _group_trigger_metrics(
    clips: Sequence[EvaluatedClip], trigger_name: str
) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[EvaluatedClip]] = defaultdict(list)
    for clip in clips:
        groups[clip.scenario].append(clip)
    result: dict[str, dict[str, float | int]] = {}
    for name, group in sorted(groups.items()):
        predictions = [getattr(clip, trigger_name) is not None for clip in group]
        labels = [clip.label for clip in group]
        if all(labels):
            result[name] = {
                "clips": len(group),
                "speech_recall": sum(predictions) / len(predictions),
            }
        elif not any(labels):
            result[name] = {
                "clips": len(group),
                "false_positive_rate": sum(predictions) / len(predictions),
            }
        else:
            result[name] = _metric_row(group, predictions)
    return result


def _locale_recall(
    clips: Sequence[EvaluatedClip], score_name: str, threshold: float
) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[EvaluatedClip]] = defaultdict(list)
    for clip in clips:
        if clip.label and clip.locale:
            groups[clip.locale].append(clip)
    return {
        locale: {
            "clips": len(group),
            "speech_recall": sum(
                getattr(clip, score_name) >= threshold for clip in group
            )
            / len(group),
        }
        for locale, group in sorted(groups.items())
    }


def _locale_trigger_recall(
    clips: Sequence[EvaluatedClip], trigger_name: str
) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[EvaluatedClip]] = defaultdict(list)
    for clip in clips:
        if clip.label and clip.locale:
            groups[clip.locale].append(clip)
    return {
        locale: {
            "clips": len(group),
            "speech_recall": sum(
                getattr(clip, trigger_name) is not None for clip in group
            )
            / len(group),
        }
        for locale, group in sorted(groups.items())
    }


def _score_distributions(
    clips: Sequence[EvaluatedClip], score_name: str
) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for clip in clips:
        groups[clip.scenario].append(float(getattr(clip, score_name)))
    return {
        name: {
            "clips": len(values),
            "minimum": min(values),
            "median": float(np.median(values)),
            "p95": float(np.percentile(values, 95)),
            "maximum": max(values),
        }
        for name, values in sorted(groups.items())
    }


def _trigger_summary(
    clips: Sequence[EvaluatedClip], trigger_name: str
) -> dict[str, float | int | None]:
    positive = [clip for clip in clips if clip.label]
    triggered = [clip for clip in positive if getattr(clip, trigger_name) is not None]
    delays = [
        max(0.0, float(getattr(clip, trigger_name)) - float(clip.speech_start_ms))
        for clip in triggered
        if clip.speech_start_ms is not None
    ]
    pre_onset = sum(
        float(getattr(clip, trigger_name)) < float(clip.speech_start_ms)
        for clip in triggered
        if clip.speech_start_ms is not None
    )
    return {
        "positive_clips": len(positive),
        "triggered_clips": len(triggered),
        "missed_clips": len(positive) - len(triggered),
        "pre_onset_trigger_rate": pre_onset / len(positive) if positive else 0.0,
        "onset_delay_median_ms": float(np.median(delays)) if delays else None,
        "onset_delay_p95_ms": float(np.percentile(delays, 95)) if delays else None,
    }


def build_report(
    clips: Sequence[EvaluatedClip],
    *,
    corpus_manifest: dict[str, Any],
    performance: dict[str, float],
    seed: int,
    asset_dir: Path,
) -> dict[str, Any]:
    silero_path = asset_dir / "silero_vad.onnx"
    calibration, holdout = split_calibration_holdout(
        clips,
        seed=seed,
        holdout_fraction=DEFAULT_HOLDOUT_FRACTION,
    )
    selected_rnnoise = select_presence_threshold(
        calibration,
        score_name="rnnoise_sustained_100ms_score",
        thresholds=RNNOISE_THRESHOLDS,
    )
    selected_threshold = float(selected_rnnoise["threshold"])
    limitations = [
        "Repository TTS and synthetic noise are a pre-benchmark, not a room recording study.",
        "RNNoise evidence is measured on the desktop 48 kHz pipeline only.",
        "Silero is measured both on raw 16 kHz audio and after the production RNNoise/AGC/limiter pipeline.",
        "Speech presence cannot replace the logical endpoint: streaming ASR uses provider-native endpointing; segmented ASR uses SmartTurn.",
    ]
    if not any(clip.device_id for clip in clips):
        limitations.append(
            "No labeled real-device manifest was supplied; room, echo, far-field, and overlapping-speaker claims remain blocked."
        )
    return {
        "schema_version": 2,
        "scope": "speech_presence_resource_evidence_only",
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "seed": seed,
            "silero_asset": str(silero_path),
            "silero_sha256": _sha256(silero_path),
        },
        "corpus": {
            "clip_count": len(clips),
            "positive_clips": sum(clip.label for clip in clips),
            "negative_clips": sum(not clip.label for clip in clips),
            "duration_seconds": sum(clip.duration_seconds for clip in clips),
            **corpus_manifest,
        },
        "current_strategy_metrics": _current_strategy_metrics(clips),
        "calibration_holdout": {
            "method": "deterministic_stratified_source_group_split",
            "holdout_fraction": DEFAULT_HOLDOUT_FRACTION,
            "source_group_overlap": False,
            "calibration_clips": len(calibration),
            "holdout_clips": len(holdout),
            "calibration_source_groups": len(
                {source_group_id(clip.clip_id) for clip in calibration}
            ),
            "holdout_source_groups": len(
                {source_group_id(clip.clip_id) for clip in holdout}
            ),
            "rnnoise_sustained_100ms": {
                "selection_rule": (
                    "maximize balanced_accuracy, then recall, specificity, "
                    "then prefer the lower threshold"
                ),
                "selected_on_calibration": selected_rnnoise,
                "holdout_metrics": _metrics_at_threshold(
                    holdout,
                    "rnnoise_sustained_100ms_score",
                    selected_threshold,
                ),
                "holdout_scenario_metrics": _group_metrics(
                    holdout,
                    "rnnoise_sustained_100ms_score",
                    selected_threshold,
                ),
                "holdout_locale_recall": _locale_recall(
                    holdout,
                    "rnnoise_sustained_100ms_score",
                    selected_threshold,
                ),
                "fixed_0.7_holdout_metrics": _metrics_at_threshold(
                    holdout,
                    "rnnoise_sustained_100ms_score",
                    RNNOISE_EXPLORATORY_SUSTAINED_THRESHOLD,
                ),
                "fixed_0.7_holdout_trigger_timing": _trigger_summary(
                    holdout,
                    "rnnoise_sustained_100ms_trigger_ms",
                ),
            },
        },
        "real_device_metrics": _real_device_metrics(
            clips,
            score_name="rnnoise_sustained_100ms_score",
            threshold=selected_threshold,
        ),
        "threshold_curves": {
            "rnnoise_peak": _threshold_curve(
                clips, "rnnoise_peak", RNNOISE_THRESHOLDS
            ),
            "rnnoise_ema_peak": _threshold_curve(
                clips, "rnnoise_ema_peak", RNNOISE_THRESHOLDS
            ),
            "rnnoise_sustained_100ms": _threshold_curve(
                clips, "rnnoise_sustained_100ms_score", RNNOISE_THRESHOLDS
            ),
            "rnnoise_sustained_200ms": _threshold_curve(
                clips, "rnnoise_sustained_200ms_score", RNNOISE_THRESHOLDS
            ),
            "silero_raw_200ms": _threshold_curve(
                clips, "silero_raw_score", SILERO_THRESHOLDS
            ),
            "silero_after_rnnoise_200ms": _threshold_curve(
                clips, "silero_after_rnnoise_score", SILERO_THRESHOLDS
            ),
        },
        "scenario_metrics": {
            "rnnoise_fixed_peak_0.35": _group_metrics(
                clips, "rnnoise_peak", RNNOISE_CURRENT_THRESHOLD
            ),
            "rnnoise_current_adaptive_policy_fresh_session": (
                _group_trigger_metrics(clips, "rnnoise_current_policy_trigger_ms")
            ),
            "silero_raw_production_gate": _group_trigger_metrics(
                clips, "silero_raw_trigger_ms"
            ),
            "silero_after_rnnoise_production_gate": _group_trigger_metrics(
                clips, "silero_after_rnnoise_trigger_ms"
            ),
            "rnnoise_sustained_100ms_0.7_exploratory": _group_metrics(
                clips,
                "rnnoise_sustained_100ms_score",
                RNNOISE_EXPLORATORY_SUSTAINED_THRESHOLD,
            ),
        },
        "locale_recall": {
            "rnnoise_current_adaptive_policy_fresh_session": (
                _locale_trigger_recall(clips, "rnnoise_current_policy_trigger_ms")
            ),
            "silero_raw_production_gate": _locale_trigger_recall(
                clips, "silero_raw_trigger_ms"
            ),
            "rnnoise_sustained_100ms_0.7_exploratory": _locale_recall(
                clips,
                "rnnoise_sustained_100ms_score",
                RNNOISE_EXPLORATORY_SUSTAINED_THRESHOLD,
            ),
        },
        "score_distributions": {
            "rnnoise_peak": _score_distributions(clips, "rnnoise_peak"),
            "rnnoise_ema_peak": _score_distributions(
                clips, "rnnoise_ema_peak"
            ),
            "rnnoise_sustained_200ms": _score_distributions(
                clips, "rnnoise_sustained_200ms_score"
            ),
            "silero_raw_200ms": _score_distributions(
                clips, "silero_raw_score"
            ),
        },
        "trigger_timing": {
            "rnnoise_fixed_peak_0.35": _trigger_summary(
                clips, "rnnoise_trigger_ms"
            ),
            "rnnoise_current_adaptive_policy_fresh_session": _trigger_summary(
                clips, "rnnoise_current_policy_trigger_ms"
            ),
            "rnnoise_sustained_100ms_0.7_exploratory": _trigger_summary(
                clips, "rnnoise_sustained_100ms_trigger_ms"
            ),
            "silero_raw_0.5_200ms": _trigger_summary(
                clips, "silero_raw_trigger_ms"
            ),
            "silero_after_rnnoise_0.5_200ms": _trigger_summary(
                clips, "silero_after_rnnoise_trigger_ms"
            ),
        },
        "performance": performance,
        "limitations": limitations,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--asset-dir", type=Path, default=REPO_ROOT / "data" / "vad_models"
    )
    parser.add_argument("--speech-per-locale", type=int, default=8)
    parser.add_argument("--negative-per-kind", type=int, default=12)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--real-device-manifest",
        type=Path,
        help="JSON manifest of labeled real-device recordings",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    clips, corpus_manifest = build_corpus(
        args.repo_root.resolve(),
        speech_per_locale=args.speech_per_locale,
        negative_per_kind=args.negative_per_kind,
        seed=args.seed,
    )
    if args.real_device_manifest is not None:
        real_clips, real_summary = load_real_device_manifest(
            args.real_device_manifest
        )
        clips.extend(real_clips)
        corpus_manifest["real_device"] = real_summary
    print(
        f"built {len(clips)} clips; evaluating RNNoise and Silero",
        file=sys.stderr,
    )
    evaluated, performance = evaluate_corpus(clips, args.asset_dir.resolve())
    report = build_report(
        evaluated,
        corpus_manifest=corpus_manifest,
        performance=performance,
        seed=args.seed,
        asset_dir=args.asset_dir.resolve(),
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
