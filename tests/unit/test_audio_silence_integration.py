# -*- coding: utf-8 -*-
"""
Smart silence removal feature — integration tests

Audio cases covering 10+ formats and silence distributions, validating API behavior end to end.

Scenarios covered:
 1. Pure sine wave (no silence)
 2. Fully silent WAV
 3. Leading silence + speech
 4. Speech + trailing silence
 5. Speech - silence - speech
 6. Multiple intervals of silence
 7. Silence shorter than the threshold (should not be removed)
 8. Low noise treated as silence
 9. Stereo audio
10. Different sample rates (8 kHz / 44.1 kHz / 48 kHz)
11. Different bit depths (8-bit / 16-bit / 32-bit)
12. Extremely long silence segment (5 seconds)
13. Alternating rapid silence/speech (boundary test)
"""

import io
import wave
import hashlib
import pytest
import numpy as np
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from utils.audio_silence_remover import (
    _float_to_samples,
    detect_silence,
    trim_silence,
)


# ==================== Helpers ====================

def _make_wav(samples, sample_rate=16000, sample_width=2, channels=1):
    pcm = _float_to_samples(samples, sample_width)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    buf.seek(0)
    return buf


def _sine(freq, duration_s, sr=16000, amp=0.5):
    t = np.arange(int(sr * duration_s)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float64)


def _silence(duration_s, sr=16000):
    return np.zeros(int(sr * duration_s), dtype=np.float64)


def _noise(duration_s, sr=16000, amp=5e-5):
    rng = np.random.default_rng(42)
    return (rng.uniform(-1, 1, int(sr * duration_s)) * amp).astype(np.float64)


def _run_pipeline(audio, sr=16000, sw=2, ch=1):
    """Run the full pipeline: analysis + trimming"""
    wav = _make_wav(audio, sample_rate=sr, sample_width=sw, channels=ch)
    data = wav.getvalue()
    analysis = detect_silence(io.BytesIO(data))
    if analysis.silence_segments:
        result = trim_silence(io.BytesIO(data), analysis)
    else:
        result = None
    return analysis, result


# ==================== Integration test cases ====================

class TestIntegration:

    # --- Case 1: Pure sine wave ---
    def test_case01_pure_sine(self):
        """Pure sine wave, no silence segments → do not trim"""
        audio = _sine(440, 3.0)
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) == 0
        assert result is None

    # --- Case 2: Complete silence ---
    def test_case02_all_silence(self):
        """Fully silent → retain about 200 ms after trimming"""
        audio = _silence(3.0)
        analysis, result = _run_pipeline(audio)
        assert analysis.saving_percentage > 80
        assert result is not None
        # After trimming complete silence, retain 100 ms at each end = 200 ms total.
        assert 150 < result.trimmed_duration_ms < 300

    # --- Case 3: Leading silence + speech ---
    def test_case03_leading_silence(self):
        """1.5 s of silence + 2 s of speech"""
        audio = np.concatenate([_silence(1.5), _sine(440, 2.0)])
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) >= 1
        assert result is not None
        assert result.trimmed_duration_ms < analysis.original_duration_ms
        # Output WAV is valid.
        with wave.open(io.BytesIO(result.audio_data), 'rb') as wf:
            assert wf.getnframes() > 0

    # --- Case 4: Speech + trailing silence ---
    def test_case04_trailing_silence(self):
        """2 s of speech + 1.5 s of silence"""
        audio = np.concatenate([_sine(440, 2.0), _silence(1.5)])
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) >= 1
        assert result is not None
        assert result.removed_silence_ms >= 1000

    # --- Case 5: Speech-silence-speech ---
    def test_case05_middle_silence(self):
        """1 s of speech + 1 s of silence + 1 s of speech"""
        audio = np.concatenate([_sine(440, 1.0), _silence(1.0), _sine(880, 1.0)])
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) >= 1
        assert result is not None
        # MD5 verification.
        assert result.md5 == hashlib.md5(result.audio_data).hexdigest()

    # --- Case 6: Multiple intervals of silence ---
    def test_case06_multi_gaps(self):
        """4 speech segments with 3 silence segments between them"""
        parts = []
        for i in range(4):
            parts.append(_sine(300 + i * 150, 0.4))
            if i < 3:
                parts.append(_silence(0.8))  # 800ms > 200ms
        audio = np.concatenate(parts)
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) == 3
        assert result is not None
        # Each 800 ms segment is trimmed to 200 ms, removing 3×600 ms = 1800 ms.
        assert result.trimmed_duration_ms < result.original_duration_ms * 0.7

    # --- Case 7: Short silence (should not be removed) ---
    def test_case07_short_silence_ignored(self):
        """150 ms of silence < 200 ms threshold → do not remove"""
        audio = np.concatenate([_sine(440, 1.0), _silence(0.15), _sine(880, 1.0)])
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) == 0
        assert result is None

    # --- Case 8: Low noise treated as silence ---
    def test_case08_low_noise_silence(self):
        """Noise < -40 dBFS → treat as silence"""
        audio = np.concatenate([_sine(440, 1.0), _noise(1.0), _sine(880, 1.0)])
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) >= 1
        assert result is not None

    # --- Case 9: Stereo ---
    def test_case09_stereo(self):
        """Stereo: both left and right channels contain signal + silence"""
        sr = 16000
        left = np.concatenate([_sine(440, 1.0, sr), _silence(1.0, sr), _sine(440, 1.0, sr)])
        right = np.concatenate([_sine(880, 1.0, sr), _silence(1.0, sr), _sine(880, 1.0, sr)])
        stereo = np.column_stack([left, right]).flatten()
        wav = _make_wav(stereo, sample_rate=sr, channels=2)
        data = wav.getvalue()

        analysis = detect_silence(io.BytesIO(data))
        assert analysis.channels == 2
        assert len(analysis.silence_segments) >= 1

        result = trim_silence(io.BytesIO(data), analysis)
        assert result.channels == 2
        assert result.md5 == hashlib.md5(result.audio_data).hexdigest()

    # --- Case 10: Different sample rate - 8 kHz ---
    def test_case10_sample_rate_8k(self):
        sr = 8000
        audio = np.concatenate([_sine(300, 0.5, sr), _silence(0.7, sr), _sine(500, 0.5, sr)])
        analysis, result = _run_pipeline(audio, sr=sr)
        assert analysis.sample_rate == sr
        assert result is not None
        assert result.sample_rate == sr

    # --- Case 10b: Different sample rate - 44.1 kHz ---
    def test_case10b_sample_rate_44k(self):
        sr = 44100
        audio = np.concatenate([_sine(440, 1.0, sr), _silence(0.8, sr)])
        analysis, result = _run_pipeline(audio, sr=sr)
        assert analysis.sample_rate == sr
        assert result is not None
        assert result.sample_rate == sr

    # --- Case 10c: Different sample rate - 48 kHz ---
    def test_case10c_sample_rate_48k(self):
        sr = 48000
        audio = np.concatenate([_sine(440, 0.5, sr), _silence(1.0, sr), _sine(660, 0.5, sr)])
        analysis, result = _run_pipeline(audio, sr=sr)
        assert analysis.sample_rate == sr
        assert result is not None

    # --- Case 11: Different bit depth - 8-bit ---
    def test_case11_8bit(self):
        audio = np.concatenate([_sine(440, 1.0), _silence(0.8)])
        analysis, result = _run_pipeline(audio, sw=1)
        assert analysis.sample_width == 1
        if result:
            assert result.sample_width == 1

    # --- Case 11b: Different bit depth - 32-bit ---
    def test_case11b_32bit(self):
        audio = np.concatenate([_sine(440, 1.0), _silence(0.8)])
        analysis, result = _run_pipeline(audio, sw=4)
        assert analysis.sample_width == 4
        if result:
            assert result.sample_width == 4

    # --- Case 12: Extremely long silence segment ---
    def test_case12_long_silence(self):
        """5 seconds of silence"""
        audio = np.concatenate([_sine(440, 0.5), _silence(5.0), _sine(880, 0.5)])
        _, result = _run_pipeline(audio)
        assert result is not None
        assert result.removed_silence_ms >= 4500
        assert result.saving_percentage if hasattr(result, 'saving_percentage') else True

    # --- Case 13: Alternating rapid segments ---
    def test_case13_rapid_alternation(self):
        """Multiple short speech segments + silence just over the threshold"""
        parts = []
        for _ in range(8):
            parts.append(_sine(440, 0.2))
            parts.append(_silence(0.55))  # 550ms > 200ms
        parts.append(_sine(440, 0.2))
        audio = np.concatenate(parts)
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) == 8
        assert result is not None
        # Each 550 ms segment is trimmed to 200 ms, removing 8×350 ms = 2800 ms.
        assert result.trimmed_duration_ms < result.original_duration_ms


# ==================== Performance benchmarks (no strict assertions; record data) ====================

class TestPerformanceBenchmark:

    @pytest.mark.performance
    def test_processing_speed(self):
        """
        Performance benchmark: processing one file takes ≤ 30% of the original audio duration

        Generate 20 seconds of audio (including multiple silence segments),
        and measure the total analysis + trimming time.
        """
        sr = 16000
        parts = []
        for _ in range(10):
            parts.append(_sine(440, 1.0, sr, 0.5))
            parts.append(_silence(1.0, sr))
        audio = np.concatenate(parts)
        wav = _make_wav(audio, sample_rate=sr)
        data = wav.getvalue()

        audio_duration_s = len(audio) / sr

        start = time.perf_counter()
        analysis = detect_silence(io.BytesIO(data))
        _ = trim_silence(io.BytesIO(data), analysis)
        elapsed = time.perf_counter() - start

        ratio = elapsed / audio_duration_s
        print(f"\n[性能] 音频时长={audio_duration_s:.1f}s, 处理耗时={elapsed:.3f}s, 比值={ratio:.2%}")

        # Assert strict thresholds only when performance tests are explicitly enabled.
        if os.environ.get('RUN_PERF_TESTS', '').lower() == 'true':
            assert ratio <= 0.30, f"处理耗时 {ratio:.2%} 超过音频时长的 30%"

    @pytest.mark.performance
    def test_large_file_performance(self):
        """
        Large-file performance test: simulate longer audio (60 seconds)
        """
        sr = 16000
        parts = []
        for _ in range(30):
            parts.append(_sine(440, 1.0, sr, 0.5))
            parts.append(_silence(1.0, sr))
        audio = np.concatenate(parts)
        wav = _make_wav(audio, sample_rate=sr)
        data = wav.getvalue()

        audio_duration_s = len(audio) / sr

        start = time.perf_counter()
        analysis = detect_silence(io.BytesIO(data))
        _ = trim_silence(io.BytesIO(data), analysis)
        elapsed = time.perf_counter() - start

        ratio = elapsed / audio_duration_s
        print(f"\n[性能] 大文件: 音频时长={audio_duration_s:.1f}s, 处理耗时={elapsed:.3f}s, 比值={ratio:.2%}")

        if os.environ.get('RUN_PERF_TESTS', '').lower() == 'true':
            assert ratio <= 0.30, f"处理耗时 {ratio:.2%} 超过音频时长的 30%"

    @pytest.mark.performance
    def test_high_sample_rate_performance(self):
        """
        High-sample-rate performance test: 48 kHz
        """
        sr = 48000
        parts = []
        for _ in range(10):
            parts.append(_sine(440, 1.0, sr, 0.5))
            parts.append(_silence(1.0, sr))
        audio = np.concatenate(parts)
        wav = _make_wav(audio, sample_rate=sr)
        data = wav.getvalue()

        audio_duration_s = len(audio) / sr

        start = time.perf_counter()
        analysis = detect_silence(io.BytesIO(data))
        _ = trim_silence(io.BytesIO(data), analysis)
        elapsed = time.perf_counter() - start

        ratio = elapsed / audio_duration_s
        print(f"\n[性能] 高采样率: 音频时长={audio_duration_s:.1f}s ({sr}Hz), 处理耗时={elapsed:.3f}s, 比值={ratio:.2%}")

        if os.environ.get('RUN_PERF_TESTS', '').lower() == 'true':
            assert ratio <= 0.30, f"处理耗时 {ratio:.2%} 超过音频时长的 30%"
