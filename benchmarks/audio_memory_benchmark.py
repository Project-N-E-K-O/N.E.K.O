# -*- coding: utf-8 -*-
"""Measure peak allocations and numeric drift for audio memory changes.

Run with::

    uv run --no-sync python benchmarks/audio_memory_benchmark.py

The legacy helpers intentionally mirror the pre-optimization implementations so
the benchmark remains useful after the production paths change.
"""

from __future__ import annotations

import gc
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import time
import tracemalloc
import wave
from typing import Callable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.audio_processor import AudioProcessor
from utils.audio_silence_remover import (
    SilenceAnalysisResult,
    SilenceSegment,
    _samples_to_float,
    trim_silence,
)


def _legacy_samples_to_float(data: bytes, sample_width: int) -> np.ndarray:
    if sample_width == 1:
        arr = np.frombuffer(data, dtype=np.uint8).astype(np.float64)
        return (arr - 128.0) / 128.0
    if sample_width == 2:
        arr = np.frombuffer(data, dtype=np.int16).astype(np.float64)
        return arr / 32768.0
    if sample_width == 3:
        raw = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
        i32 = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        i32[i32 >= 0x800000] -= 0x1000000
        return i32.astype(np.float64) / 8388608.0
    if sample_width == 4:
        arr = np.frombuffer(data, dtype=np.int32).astype(np.float64)
        return arr / 2147483648.0
    raise ValueError(sample_width)


def _candidate_samples_to_float32(data: bytes, sample_width: int) -> np.ndarray:
    if sample_width == 1:
        arr = np.frombuffer(data, dtype=np.uint8).astype(np.float32)
        return (arr - np.float32(128.0)) / np.float32(128.0)
    if sample_width == 2:
        arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        return arr / np.float32(32768.0)
    if sample_width == 3:
        raw = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
        i32 = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        i32[i32 >= 0x800000] -= 0x1000000
        return i32.astype(np.float32) / np.float32(8388608.0)
    if sample_width == 4:
        arr = np.frombuffer(data, dtype=np.int32).astype(np.float32)
        return arr / np.float32(2147483648.0)
    raise ValueError(sample_width)


def _legacy_float_to_samples(arr: np.ndarray, sample_width: int) -> bytes:
    arr = np.clip(arr, -1.0, 1.0)
    if sample_width == 1:
        return ((arr * 128.0) + 128.0).astype(np.uint8).tobytes()
    if sample_width == 2:
        return (arr * 32768.0).astype(np.int16).tobytes()
    if sample_width == 3:
        i32 = np.clip(arr * 8388608.0, -8388608, 8388607).astype(np.int32)
        u32 = i32.view(np.uint32)
        raw = np.empty((len(u32), 3), dtype=np.uint8)
        raw[:, 0] = u32 & 0xFF
        raw[:, 1] = (u32 >> 8) & 0xFF
        raw[:, 2] = (u32 >> 16) & 0xFF
        return raw.tobytes()
    if sample_width == 4:
        return (arr * 2147483648.0).astype(np.int32).tobytes()
    raise ValueError(sample_width)


def _rms_dbfs(samples: np.ndarray) -> float:
    if len(samples) == 0:
        return -100.0
    rms = np.sqrt(np.mean(samples**2))
    if rms < 1e-10:
        return -100.0
    return 20.0 * math.log10(rms)


def _pcm_bytes(values: np.ndarray, sample_width: int) -> bytes:
    if sample_width == 1:
        return values.astype(np.uint8).tobytes()
    if sample_width == 2:
        return values.astype(np.int16).tobytes()
    if sample_width == 3:
        i32 = values.astype(np.int32)
        u32 = i32.view(np.uint32)
        raw = np.empty((len(u32), 3), dtype=np.uint8)
        raw[:, 0] = u32 & 0xFF
        raw[:, 1] = (u32 >> 8) & 0xFF
        raw[:, 2] = (u32 >> 16) & 0xFF
        return raw.tobytes()
    if sample_width == 4:
        return values.astype(np.int32).tobytes()
    raise ValueError(sample_width)


def _numeric_error_benchmark() -> dict[str, dict[str, float | int]]:
    rng = np.random.default_rng(20260715)
    ranges = {
        1: (0, 256, np.uint16),
        2: (-32768, 32768, np.int32),
        3: (-8388608, 8388608, np.int32),
        4: (-2147483648, 2147483648, np.int64),
    }
    results: dict[str, dict[str, float | int]] = {}
    for sample_width, (low, high, dtype) in ranges.items():
        values = rng.integers(low, high, size=480 * 100, dtype=dtype)
        data = _pcm_bytes(values, sample_width)
        reference = _legacy_samples_to_float(data, sample_width)
        candidate = _candidate_samples_to_float32(data, sample_width)
        production = _samples_to_float(data, sample_width)
        db_errors = []
        for start in range(0, len(reference), 480):
            ref_db = _rms_dbfs(reference[start : start + 480])
            cand_db = _rms_dbfs(candidate[start : start + 480])
            db_errors.append(abs(ref_db - cand_db))

        threshold_errors = []
        decision_mismatches = 0
        phase = np.arange(480, dtype=np.float64) * (2.0 * np.pi * 7.0 / 480.0)
        for level_dbfs in np.linspace(-40.1, -39.9, 201):
            peak = math.sqrt(2.0) * (10.0 ** (level_dbfs / 20.0))
            threshold_pcm = _legacy_float_to_samples(np.sin(phase) * peak, sample_width)
            threshold_reference = _legacy_samples_to_float(threshold_pcm, sample_width)
            threshold_candidate = _candidate_samples_to_float32(
                threshold_pcm, sample_width
            )
            ref_db = _rms_dbfs(threshold_reference)
            cand_db = _rms_dbfs(threshold_candidate)
            threshold_errors.append(abs(ref_db - cand_db))
            decision_mismatches += (ref_db < -40.0) != (cand_db < -40.0)
        results[str(sample_width)] = {
            "candidate_max_sample_error": float(
                np.max(np.abs(reference - candidate.astype(np.float64)))
            ),
            "candidate_max_dbfs_error": float(max(db_errors)),
            "candidate_near_threshold_max_dbfs_error": float(max(threshold_errors)),
            "candidate_threshold_mismatches": decision_mismatches,
            "production_dtype_bits": int(production.dtype.itemsize * 8),
            "production_max_sample_error": float(
                np.max(np.abs(reference - production.astype(np.float64)))
            ),
        }
    return results


def _make_trim_fixture(duration_s: int = 30) -> tuple[bytes, SilenceAnalysisResult]:
    sample_rate = 48000
    channels = 2
    sample_width = 4
    sample_count = duration_s * sample_rate * channels
    samples = np.arange(sample_count, dtype=np.int32)
    wav_buf = io.BytesIO()
    with wave.open(wav_buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    segments = [
        SilenceSegment(start_ms=5000.0, end_ms=7000.0),
        SilenceSegment(start_ms=14000.0, end_ms=16000.0),
        SilenceSegment(start_ms=23000.0, end_ms=25000.0),
    ]
    analysis = SilenceAnalysisResult(
        original_duration_ms=duration_s * 1000.0,
        silence_segments=segments,
        total_silence_ms=6000.0,
        removable_silence_ms=5400.0,
        estimated_duration_ms=duration_s * 1000.0 - 5400.0,
        sample_rate=sample_rate,
        sample_width=sample_width,
        channels=channels,
    )
    return wav_buf.getvalue(), analysis


def _legacy_trim(audio_data: bytes, analysis: SilenceAnalysisResult) -> bytes:
    with wave.open(io.BytesIO(audio_data), "rb") as wf:
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        channels = wf.getnchannels()
        raw_data = wf.readframes(wf.getnframes())
    float_samples = _legacy_samples_to_float(raw_data, sample_width).reshape(-1, channels)
    retain_half = int(sample_rate * 0.1)
    result_parts: list[np.ndarray] = []
    prev_end = 0
    for segment in analysis.silence_segments:
        segment_start = int(segment.start_ms * sample_rate / 1000.0)
        segment_end = int(segment.end_ms * sample_rate / 1000.0)
        cut_start = segment_start + retain_half
        cut_end = segment_end - retain_half
        if cut_start >= cut_end:
            continue
        if cut_start > prev_end:
            result_parts.append(float_samples[prev_end:cut_start])
        prev_end = cut_end
    if prev_end < len(float_samples):
        result_parts.append(float_samples[prev_end:])
    final_samples = np.concatenate(result_parts, axis=0)
    pcm_data = _legacy_float_to_samples(final_samples.reshape(-1), sample_width)
    output = io.BytesIO()
    with wave.open(output, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return output.getvalue()


def _analysis_scratch(
    audio_data: bytes,
    converter: Callable[[bytes, int], np.ndarray],
) -> bytes:
    with wave.open(io.BytesIO(audio_data), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        raw_data = wf.readframes(wf.getnframes())
    samples = converter(raw_data, sample_width)
    mono = samples.reshape(-1, channels).mean(axis=1)
    return repr(_rms_dbfs(mono[:480])).encode("ascii")


class _IdentityDenoiser:
    def process_frame(self, frame: np.ndarray) -> tuple[np.ndarray, float]:
        return frame.copy(), 0.1


class _LegacyRnnoiseProcessor:
    frame_size = 480
    sample_rate = 48000

    def __init__(self) -> None:
        self.frame_buffer = np.array([], dtype=np.int16)
        self.denoiser = _IdentityDenoiser()

    def process(self, audio: np.ndarray) -> np.ndarray:
        self.frame_buffer = np.concatenate([self.frame_buffer, audio])
        if len(self.frame_buffer) > self.sample_rate:
            self.frame_buffer = self.frame_buffer[-self.sample_rate :]
        output_frames = []
        while len(self.frame_buffer) >= self.frame_size:
            frame = self.frame_buffer[: self.frame_size]
            self.frame_buffer = self.frame_buffer[self.frame_size :]
            denoised, _ = self.denoiser.process_frame(frame)
            output_frames.append(denoised)
        if output_frames:
            return np.concatenate(output_frames)
        return np.array([], dtype=np.int16)


def _current_rnnoise_processor() -> AudioProcessor:
    processor = AudioProcessor.__new__(AudioProcessor)
    processor._denoiser = _IdentityDenoiser()
    processor._frame_buffer = np.empty(processor.RNNOISE_FRAME_SIZE, dtype=np.int16)
    processor._frame_buffer_size = 0
    processor._last_speech_prob = 0.0
    processor._last_speech_time = time.time()
    return processor


def _run_rnnoise(
    processor: object,
    chunks: list[np.ndarray],
    method: Callable[[object, np.ndarray], np.ndarray],
) -> bytes:
    output = io.BytesIO()
    for chunk in chunks:
        output.write(method(processor, chunk).tobytes())
    return output.getvalue()


def _measure_peak(call: Callable[[], bytes]) -> tuple[int, bytes]:
    gc.collect()
    tracemalloc.start()
    try:
        result = call()
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return peak, result


def _retained_array_bytes(array: np.ndarray) -> int:
    root = array
    while isinstance(root.base, np.ndarray):
        root = root.base
    return int(root.nbytes)


def _memory_benchmark() -> dict[str, dict[str, int | bool | float]]:
    audio_data, analysis = _make_trim_fixture()
    legacy_analysis_peak, _ = _measure_peak(
        lambda: _analysis_scratch(audio_data, _legacy_samples_to_float)
    )
    current_analysis_peak, _ = _measure_peak(
        lambda: _analysis_scratch(audio_data, _samples_to_float)
    )
    legacy_trim_peak, legacy_trim_output = _measure_peak(
        lambda: _legacy_trim(audio_data, analysis)
    )
    current_trim_peak, current_trim_output = _measure_peak(
        lambda: trim_silence(io.BytesIO(audio_data), analysis).audio_data
    )

    rng = np.random.default_rng(20260715)
    burst = [rng.integers(-32768, 32768, size=48000, dtype=np.int16)]
    stream_data = rng.integers(-32768, 32768, size=48000, dtype=np.int16)
    stream = [stream_data[start : start + 512] for start in range(0, len(stream_data), 512)]

    results: dict[str, dict[str, int | bool | float]] = {
        "analysis_scratch_30s_stereo_48k_s32": {
            "legacy_peak_bytes": legacy_analysis_peak,
            "current_peak_bytes": current_analysis_peak,
            "peak_reduction_percent": 100.0
            * (legacy_analysis_peak - current_analysis_peak)
            / legacy_analysis_peak,
        },
        "trim_30s_stereo_48k_s32": {
            "legacy_peak_bytes": legacy_trim_peak,
            "current_peak_bytes": current_trim_peak,
            "peak_reduction_percent": 100.0
            * (legacy_trim_peak - current_trim_peak)
            / legacy_trim_peak,
            "output_bytes_equal": legacy_trim_output == current_trim_output,
            "output_md5_equal": hashlib.md5(legacy_trim_output).digest()
            == hashlib.md5(current_trim_output).digest(),
        }
    }
    for name, chunks in (("rnnoise_1s_burst", burst), ("rnnoise_512_sample_stream", stream)):
        legacy_peak, legacy_output = _measure_peak(
            lambda chunks=chunks: _run_rnnoise(
                _LegacyRnnoiseProcessor(), chunks, _LegacyRnnoiseProcessor.process
            )
        )
        current_peak, current_output = _measure_peak(
            lambda chunks=chunks: _run_rnnoise(
                _current_rnnoise_processor(), chunks, AudioProcessor._process_with_rnnoise
            )
        )
        legacy_retained = _LegacyRnnoiseProcessor()
        _run_rnnoise(legacy_retained, chunks, _LegacyRnnoiseProcessor.process)
        current_retained = _current_rnnoise_processor()
        _run_rnnoise(current_retained, chunks, AudioProcessor._process_with_rnnoise)
        results[name] = {
            "legacy_peak_bytes": legacy_peak,
            "current_peak_bytes": current_peak,
            "peak_reduction_percent": 100.0 * (legacy_peak - current_peak) / legacy_peak,
            "output_bytes_equal": legacy_output == current_output,
            "legacy_retained_buffer_bytes": _retained_array_bytes(
                legacy_retained.frame_buffer
            ),
            "current_retained_buffer_bytes": _retained_array_bytes(
                current_retained._frame_buffer
            ),
            "legacy_concatenate_calls": len(chunks) * 2,
            "current_concatenate_calls": 0,
        }
    return results


def main() -> None:
    print(
        json.dumps(
            {
                "numeric_float32_candidate": _numeric_error_benchmark(),
                "memory": _memory_benchmark(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
