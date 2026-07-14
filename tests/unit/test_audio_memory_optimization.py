# -*- coding: utf-8 -*-
"""Regression tests for the low-allocation audio paths."""

from __future__ import annotations

import io
import math
import time
import wave

import numpy as np
import pytest

from utils.audio_processor import AudioProcessor
from utils.audio_silence_remover import (
    SilenceAnalysisResult,
    SilenceSegment,
    _samples_to_float,
    _rms_dbfs,
    trim_silence,
)


class _IdentityDenoiser:
    def process_frame(self, frame: np.ndarray) -> tuple[np.ndarray, float]:
        return frame.copy(), 0.1


def _processor() -> AudioProcessor:
    processor = AudioProcessor.__new__(AudioProcessor)
    processor._denoiser = _IdentityDenoiser()
    processor._frame_buffer = np.empty(processor.RNNOISE_FRAME_SIZE, dtype=np.int16)
    processor._frame_buffer_size = 0
    processor._last_speech_prob = 0.0
    processor._last_speech_time = time.time()
    return processor


@pytest.mark.parametrize(
    ("sample_width", "dtype", "values"),
    [
        (1, np.uint8, [0, 1, 127, 128, 254, 255]),
        (2, np.int16, [-32768, -1, 0, 1, 32767]),
        (4, np.int32, [-2147483648, -1, 0, 1, 2147483647]),
    ],
)
def test_samples_to_float_uses_float32_with_bounded_error(
    sample_width: int,
    dtype: np.dtype,
    values: list[int],
) -> None:
    pcm = np.asarray(values, dtype=dtype).tobytes()
    result = _samples_to_float(pcm, sample_width)

    assert result.dtype == np.float32
    reference = np.asarray(values, dtype=np.float64)
    if sample_width == 1:
        reference = (reference - 128.0) / 128.0
    else:
        reference /= float(1 << (sample_width * 8 - 1))
    np.testing.assert_allclose(result, reference, rtol=0.0, atol=3e-8)


@pytest.mark.parametrize("level_dbfs", [-40.1, -40.01, -39.99, -39.9])
def test_float32_rms_preserves_near_threshold_decisions(level_dbfs: float) -> None:
    phase = np.arange(480, dtype=np.float64) * (2.0 * np.pi * 7.0 / 480.0)
    peak = math.sqrt(2.0) * (10.0 ** (level_dbfs / 20.0))
    pcm = (np.sin(phase) * peak * 2147483648.0).astype(np.int32).tobytes()
    reference = np.frombuffer(pcm, dtype=np.int32).astype(np.float64) / 2147483648.0
    candidate = _samples_to_float(pcm, 4)

    assert (_rms_dbfs(reference) < -40.0) == (_rms_dbfs(candidate) < -40.0)


def test_trim_silence_copies_original_s32_pcm_frames_exactly() -> None:
    sample_rate = 1000
    channels = 2
    frames = np.arange(2000, dtype=np.int64).reshape(1000, channels)
    frames[0] = np.iinfo(np.int32).min
    frames[-1] = np.iinfo(np.int32).max
    frames = frames.astype(np.int32)
    source_pcm = frames.tobytes()
    source = io.BytesIO()
    with wave.open(source, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(4)
        wf.setframerate(sample_rate)
        wf.writeframes(source_pcm)

    analysis = SilenceAnalysisResult(
        original_duration_ms=1000.0,
        silence_segments=[SilenceSegment(start_ms=200.0, end_ms=800.0)],
        sample_rate=sample_rate,
        sample_width=4,
        channels=channels,
    )
    result = trim_silence(io.BytesIO(source.getvalue()), analysis)

    with wave.open(io.BytesIO(result.audio_data), "rb") as wf:
        output_pcm = wf.readframes(wf.getnframes())
        assert wf.getsampwidth() == 4
        assert wf.getnchannels() == channels
    assert output_pcm == frames[np.r_[0:300, 700:1000]].tobytes()
    assert result.trimmed_duration_ms == pytest.approx(600.0)


def test_rnnoise_fixed_buffer_preserves_chunk_boundaries() -> None:
    processor = _processor()
    first = np.arange(100, dtype=np.int16)
    second = np.arange(100, 600, dtype=np.int16)

    assert processor._process_with_rnnoise(first).size == 0
    output = processor._process_with_rnnoise(second)

    np.testing.assert_array_equal(output, np.arange(480, dtype=np.int16))
    assert processor._frame_buffer_size == 120
    np.testing.assert_array_equal(
        processor._frame_buffer[: processor._frame_buffer_size],
        np.arange(480, 600, dtype=np.int16),
    )


def test_rnnoise_one_second_burst_is_byte_exact() -> None:
    processor = _processor()
    audio = np.arange(processor.RNNOISE_SAMPLE_RATE, dtype=np.int16)

    output = processor._process_with_rnnoise(audio)

    np.testing.assert_array_equal(output, audio)
    assert processor._frame_buffer_size == 0


def test_rnnoise_overflow_keeps_latest_one_second() -> None:
    processor = _processor()
    pending = np.full(100, -123, dtype=np.int16)
    assert processor._process_with_rnnoise(pending).size == 0
    audio = np.arange(processor.RNNOISE_SAMPLE_RATE, dtype=np.int16)

    output = processor._process_with_rnnoise(audio)

    np.testing.assert_array_equal(output, audio)
    assert processor._frame_buffer_size == 0
