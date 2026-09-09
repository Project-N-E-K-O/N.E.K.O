"""Probe the pinned CAM++ graph with exact, synthetic short PCM windows."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import sys
import time
import wave

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main_logic.asr_client.speaker_shadow.campplus import (  # noqa: E402
    CAMPPLUS_EXECUTABLE_MINIMUM_FRAMES,
    CAMPPLUS_EXECUTABLE_MINIMUM_SAMPLES,
    CAMPPLUS_FEATURE_MINIMUM_SAMPLES,
    CAMPPLUS_MINIMUM_SAMPLES,
    CampPlusEmbeddingModel,
    compute_campplus_features,
)


SAMPLE_RATE_HZ = 16_000
DEFAULT_SAMPLE_COUNTS = (
    399,
    400,
    560,
    719,
    720,
    880,
    1_600,
    4_000,
    8_000,
    16_000,
    23_999,
    24_000,
)


def _synthetic_pcm16(sample_count: int) -> bytes:
    indices = np.arange(sample_count, dtype=np.float64)
    waveform = (
        0.17 * np.sin(2 * np.pi * 173 * indices / SAMPLE_RATE_HZ)
        + 0.06 * np.sin(2 * np.pi * 641 * indices / SAMPLE_RATE_HZ)
        + 0.02 * np.sin(2 * np.pi * 37 * indices / SAMPLE_RATE_HZ)
    )
    return np.rint(waveform * 32767).astype("<i2").tobytes()


def _read_wav_pcm16(path: Path) -> bytes:
    try:
        with wave.open(str(path), "rb") as source:
            if (
                source.getnchannels() != 1
                or source.getsampwidth() != 2
                or source.getframerate() != SAMPLE_RATE_HZ
                or source.getcomptype() != "NONE"
            ):
                raise ValueError("wav_must_be_mono_pcm16le_16khz")
            expected_bytes = source.getnframes() * 2
            pcm16 = source.readframes(source.getnframes())
    except (EOFError, OSError, wave.Error):
        raise ValueError("wav_unreadable") from None
    if len(pcm16) != expected_bytes:
        raise ValueError("wav_truncated")
    return pcm16


def _probe_one(
    model: CampPlusEmbeddingModel,
    *,
    pcm16: bytes,
    repetitions: int,
) -> dict[str, object]:
    sample_count = len(pcm16) // 2
    report: dict[str, object] = {
        "sample_count": sample_count,
        "duration_ms": sample_count * 1000.0 / SAMPLE_RATE_HZ,
        "pcm_bytes": len(pcm16),
        "input_transforms": [],
    }
    try:
        features = compute_campplus_features(
            pcm16,
            sample_rate_hz=SAMPLE_RATE_HZ,
        )
    except ValueError as exc:
        report.update(
            feature_executable=False,
            embedding_executable=False,
            error=str(exc),
        )
        return report

    try:
        report["feature_executable"] = bool(np.isfinite(features).all())
        report["feature_frames"] = int(features.shape[0])
    finally:
        features.fill(0)

    embeddings: list[np.ndarray] = []
    elapsed_ms: list[float] = []
    try:
        for _ in range(repetitions):
            started = time.perf_counter()
            embedding = model.probe_short_input_embedding_from_pcm16(
                pcm16,
                sample_rate_hz=SAMPLE_RATE_HZ,
            )
            elapsed_ms.append((time.perf_counter() - started) * 1000.0)
            embeddings.append(embedding)
    except (RuntimeError, ValueError) as exc:
        report.update(embedding_executable=False, error=str(exc))
        for embedding in embeddings:
            embedding.fill(0)
        return report
    finally:
        pcm16 = b""

    baseline = embeddings[0]
    maximum_difference = max(
        float(np.max(np.abs(candidate - baseline)))
        for candidate in embeddings
    )
    minimum_cosine = min(
        float(np.dot(candidate, baseline)) for candidate in embeddings
    )
    report.update(
        embedding_executable=True,
        embedding_dimension=int(baseline.shape[0]),
        embedding_finite=bool(
            all(np.isfinite(candidate).all() for candidate in embeddings)
        ),
        embedding_norm=float(np.linalg.norm(baseline)),
        deterministic=(
            math.isfinite(maximum_difference)
            and maximum_difference <= 1e-7
            and minimum_cosine >= 0.999999
        ),
        maximum_absolute_difference=maximum_difference,
        minimum_cosine=minimum_cosine,
        latency_ms={
            "minimum": min(elapsed_ms),
            "median": statistics.median(elapsed_ms),
            "maximum": max(elapsed_ms),
        },
    )
    for embedding in embeddings:
        embedding.fill(0)
    return report


def probe(
    *,
    asset_dir: Path | None,
    sample_counts: list[int],
    repetitions: int,
    source_pcm16: bytes | None = None,
) -> dict[str, object]:
    if not sample_counts or any(value <= 0 for value in sample_counts):
        raise ValueError("sample counts must be positive")
    if repetitions < 2:
        raise ValueError("repetitions must be at least 2")
    if source_pcm16 is not None and (
        not isinstance(source_pcm16, bytes)
        or not source_pcm16
        or len(source_pcm16) % 2
    ):
        raise ValueError("source_pcm_invalid")

    model = CampPlusEmbeddingModel(asset_dir=asset_dir)
    if not model.load():
        model.close()
        raise RuntimeError("verified local CAM++ model could not be loaded")
    try:
        windows: list[dict[str, object]] = []
        available_samples = (
            len(source_pcm16) // 2 if source_pcm16 is not None else None
        )
        for sample_count in sorted(set(sample_counts)):
            if (
                available_samples is not None
                and sample_count > available_samples
            ):
                windows.append(
                    {
                        "sample_count": sample_count,
                        "duration_ms": (
                            sample_count * 1000.0 / SAMPLE_RATE_HZ
                        ),
                        "embedding_executable": False,
                        "error": "wav_window_exceeds_source",
                        "input_transforms": [],
                    }
                )
                continue
            pcm16 = (
                _synthetic_pcm16(sample_count)
                if source_pcm16 is None
                else source_pcm16[: sample_count * 2]
            )
            windows.append(
                _probe_one(
                    model,
                    pcm16=pcm16,
                    repetitions=repetitions,
                )
            )
            pcm16 = b""
    finally:
        model.close()

    successful = [
        int(window["sample_count"])
        for window in windows
        if window.get("embedding_executable") is True
    ]
    return {
        "schema_version": 1,
        "offline": True,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "feature_minimum_samples": CAMPPLUS_FEATURE_MINIMUM_SAMPLES,
        "pinned_finite_embedding_minimum_frames": (
            CAMPPLUS_EXECUTABLE_MINIMUM_FRAMES
        ),
        "pinned_finite_embedding_minimum_samples": (
            CAMPPLUS_EXECUTABLE_MINIMUM_SAMPLES
        ),
        "production_minimum_samples": CAMPPLUS_MINIMUM_SAMPLES,
        "minimum_successful_probed_samples": min(successful, default=None),
        "repetitions": repetitions,
        "source_kind": "wav" if source_pcm16 is not None else "synthetic",
        "windows": windows,
        "scope": "execution_and_numerical_safety_only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-dir", type=Path)
    parser.add_argument(
        "--wav",
        type=Path,
        help="optional mono PCM16LE 16 kHz WAV; its path is not reported",
    )
    parser.add_argument(
        "--samples",
        type=int,
        nargs="+",
        default=None,
    )
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args(argv)
    source_pcm16 = _read_wav_pcm16(args.wav) if args.wav else None
    sample_counts = (
        list(args.samples)
        if args.samples is not None
        else (
            [len(source_pcm16) // 2]
            if source_pcm16 is not None
            else list(DEFAULT_SAMPLE_COUNTS)
        )
    )
    report = probe(
        asset_dir=args.asset_dir.resolve() if args.asset_dir else None,
        sample_counts=sample_counts,
        repetitions=args.repetitions,
        source_pcm16=source_pcm16,
    )
    source_pcm16 = b""
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
