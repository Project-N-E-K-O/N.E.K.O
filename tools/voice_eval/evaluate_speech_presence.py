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
RNNOISE_EXPLORATORY_SUSTAINED_THRESHOLD = 0.8
RNNOISE_EXPLORATORY_SUSTAINED_WINDOWS = 5
SILERO_CURRENT_THRESHOLD = 0.5
SILERO_OFFSET_THRESHOLD = 0.35
SILERO_MINIMUM_SPEECH_MS = 200
SILERO_WINDOW_MS = 1000 * SileroVad.WINDOW_SAMPLES / SileroVad.SAMPLE_RATE
SILERO_MINIMUM_WINDOWS = max(
    1, math.ceil(SILERO_MINIMUM_SPEECH_MS / SILERO_WINDOW_MS)
)
DEFAULT_SEED = 2398
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
    rnnoise_sustained_100ms_trigger_ms: float | None
    silero_raw_trigger_ms: float | None
    silero_after_rnnoise_trigger_ms: float | None
    speech_start_ms: float | None


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


def _pcm16(samples: np.ndarray) -> bytes:
    return (
        np.clip(samples, -1.0, 1.0) * 32767
    ).round().astype("<i2").tobytes()


def _rnnoise_process(
    processor: AudioProcessor, samples_48k: np.ndarray
) -> tuple[list[float], list[float], bytes, float]:
    chunk_samples = SAMPLE_RATE_48K * RNNOISE_CHUNK_MS // 1000
    peak_values: list[float] = []
    ema_values: list[float] = []
    processed_chunks: list[bytes] = []
    started = time.perf_counter()
    for start in range(0, samples_48k.size, chunk_samples):
        chunk = samples_48k[start : start + chunk_samples]
        processed = processor.process_chunk(_pcm16(chunk))
        if processed:
            processed_chunks.append(processed)
        peak = processor.rnnoise_probability_peak
        ema = processor.rnnoise_probability_ema
        if peak is not None:
            peak_values.append(float(peak))
        if ema is not None:
            ema_values.append(float(ema))
    elapsed = time.perf_counter() - started
    return peak_values, ema_values, b"".join(processed_chunks), elapsed


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
    vad = SileroVad(enabled=True, asset_dir=asset_dir, intra_op_threads=1)
    if not vad.load():
        raise RuntimeError(f"Silero failed to load: {vad.unavailable_reason}")
    rss_after_silero = process.memory_info().rss

    evaluated: list[EvaluatedClip] = []
    rnnoise_seconds = 0.0
    silero_raw_seconds = 0.0
    silero_after_rnnoise_seconds = 0.0
    audio_seconds = 0.0
    try:
        for index, clip in enumerate(clips, start=1):
            processor.reset()
            vad.reset_stream()
            peak_values, ema_values, processed_16k, rnnoise_elapsed = (
                _rnnoise_process(processor, clip.samples_48k)
            )
            raw_16k = soxr.resample(
                clip.samples_48k, SAMPLE_RATE_48K, SAMPLE_RATE_16K, quality="HQ"
            )
            raw_started = time.perf_counter()
            raw_probabilities = vad.process_pcm16(_pcm16(raw_16k))
            raw_elapsed = time.perf_counter() - raw_started
            vad.reset_stream()
            denoised_started = time.perf_counter()
            denoised_probabilities = vad.process_pcm16(processed_16k)
            denoised_elapsed = time.perf_counter() - denoised_started
            duration = clip.samples_48k.size / SAMPLE_RATE_48K
            audio_seconds += duration
            rnnoise_seconds += rnnoise_elapsed
            silero_raw_seconds += raw_elapsed
            silero_after_rnnoise_seconds += denoised_elapsed
            evaluated.append(
                EvaluatedClip(
                    clip_id=clip.clip_id,
                    label=clip.label,
                    scenario=clip.scenario,
                    locale=clip.locale,
                    snr_db=clip.snr_db,
                    noise_kind=clip.noise_kind,
                    duration_seconds=duration,
                    rnnoise_peak=max(peak_values, default=0.0),
                    rnnoise_ema_peak=max(ema_values, default=0.0),
                    rnnoise_sustained_100ms_score=silero_presence_score(
                        peak_values, minimum_windows=5
                    ),
                    rnnoise_sustained_200ms_score=silero_presence_score(
                        peak_values, minimum_windows=10
                    ),
                    silero_raw_score=silero_presence_score(raw_probabilities),
                    silero_after_rnnoise_score=silero_presence_score(
                        denoised_probabilities
                    ),
                    rnnoise_trigger_ms=_first_threshold_trigger_ms(
                        peak_values, RNNOISE_CURRENT_THRESHOLD, RNNOISE_CHUNK_MS
                    ),
                    rnnoise_sustained_100ms_trigger_ms=_first_sustained_trigger_ms(
                        peak_values,
                        RNNOISE_EXPLORATORY_SUSTAINED_THRESHOLD,
                        RNNOISE_EXPLORATORY_SUSTAINED_WINDOWS,
                        RNNOISE_CHUNK_MS,
                    ),
                    silero_raw_trigger_ms=first_silero_trigger_ms(
                        raw_probabilities, SILERO_CURRENT_THRESHOLD
                    ),
                    silero_after_rnnoise_trigger_ms=first_silero_trigger_ms(
                        denoised_probabilities, SILERO_CURRENT_THRESHOLD
                    ),
                    speech_start_ms=clip.speech_start_ms,
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
        "rnnoise_pipeline_seconds": rnnoise_seconds,
        "silero_raw_seconds": silero_raw_seconds,
        "silero_after_rnnoise_seconds": silero_after_rnnoise_seconds,
        "rnnoise_pipeline_realtime_factor": rnnoise_seconds / audio_seconds,
        "silero_raw_realtime_factor": silero_raw_seconds / audio_seconds,
        "silero_after_rnnoise_realtime_factor": (
            silero_after_rnnoise_seconds / audio_seconds
        ),
        "rnnoise_rss_delta_mib": (rss_after_rnnoise - rss_before_rnnoise) / mib,
        "silero_rss_delta_mib": (rss_after_silero - rss_after_rnnoise) / mib,
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


def _current_strategy_metrics(
    clips: Sequence[EvaluatedClip],
) -> dict[str, dict[str, float | int]]:
    rnnoise = [
        clip.rnnoise_peak >= RNNOISE_CURRENT_THRESHOLD for clip in clips
    ]
    rnnoise_100ms = [
        clip.rnnoise_sustained_100ms_score >= RNNOISE_CURRENT_THRESHOLD
        for clip in clips
    ]
    rnnoise_200ms = [
        clip.rnnoise_sustained_200ms_score >= RNNOISE_CURRENT_THRESHOLD
        for clip in clips
    ]
    silero_raw = [
        clip.silero_raw_score >= SILERO_CURRENT_THRESHOLD for clip in clips
    ]
    silero_after = [
        clip.silero_after_rnnoise_score >= SILERO_CURRENT_THRESHOLD
        for clip in clips
    ]
    return {
        "rnnoise_peak_0.35": _metric_row(clips, rnnoise),
        "rnnoise_sustained_100ms_0.35": _metric_row(clips, rnnoise_100ms),
        "rnnoise_sustained_200ms_0.35": _metric_row(clips, rnnoise_200ms),
        "silero_raw_0.5_200ms": _metric_row(clips, silero_raw),
        "silero_after_rnnoise_0.5_200ms": _metric_row(clips, silero_after),
        "rnnoise_and_silero_after_rnnoise": _metric_row(
            clips,
            [left and right for left, right in zip(rnnoise, silero_after, strict=True)],
        ),
        "rnnoise_or_silero_raw": _metric_row(
            clips,
            [left or right for left, right in zip(rnnoise, silero_raw, strict=True)],
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
    return {
        "schema_version": 1,
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
            "rnnoise_peak_0.35": _group_metrics(
                clips, "rnnoise_peak", RNNOISE_CURRENT_THRESHOLD
            ),
            "silero_raw_0.5_200ms": _group_metrics(
                clips, "silero_raw_score", SILERO_CURRENT_THRESHOLD
            ),
            "silero_after_rnnoise_0.5_200ms": _group_metrics(
                clips, "silero_after_rnnoise_score", SILERO_CURRENT_THRESHOLD
            ),
            "rnnoise_sustained_100ms_0.8_exploratory": _group_metrics(
                clips,
                "rnnoise_sustained_100ms_score",
                RNNOISE_EXPLORATORY_SUSTAINED_THRESHOLD,
            ),
        },
        "locale_recall": {
            "rnnoise_peak_0.35": _locale_recall(
                clips, "rnnoise_peak", RNNOISE_CURRENT_THRESHOLD
            ),
            "silero_raw_0.5_200ms": _locale_recall(
                clips, "silero_raw_score", SILERO_CURRENT_THRESHOLD
            ),
            "rnnoise_sustained_100ms_0.8_exploratory": _locale_recall(
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
            "rnnoise_peak_0.35": _trigger_summary(clips, "rnnoise_trigger_ms"),
            "rnnoise_sustained_100ms_0.8_exploratory": _trigger_summary(
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
        "limitations": [
            "Repository TTS and synthetic noise are a pre-benchmark, not a room recording study.",
            "No television, acoustic echo, far-field microphone, or overlapping-speaker labels are present.",
            "RNNoise evidence is measured on the desktop 48 kHz pipeline only.",
            "Silero is measured both on raw 16 kHz audio and after the production RNNoise/AGC/limiter pipeline.",
            "Speech presence cannot replace SmartTurn semantic completion.",
        ],
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
