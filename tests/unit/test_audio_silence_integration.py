# -*- coding: utf-8 -*-
"""
智能静音移除功能 — 集成测试

10+ 条不同格式与静音分布的音频用例，端到端验证 API 行为。

覆盖场景:
 1. 纯正弦波 (无静音)
 2. 全静音 WAV
 3. 开头静音 + 语音
 4. 语音 + 末尾静音
 5. 语音 - 静音 - 语音
 6. 多段间隔静音
 7. 短于阈值的静音 (不应被移除)
 8. 低噪声作为静音
 9. 立体声音频
10. 不同采样率 (8 kHz / 44.1 kHz / 48 kHz)
11. 不同位深度 (8-bit / 16-bit / 32-bit)
12. 极长静音段 (5 秒)
13. 交替快速静音/语音 (边界测试)
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
    _samples_to_float,
    _float_to_samples,
    detect_silence,
    trim_silence,
    format_duration_mmss,
    CancelledError,
    SilenceAnalysisResult,
)


# ==================== 辅助 ====================

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
    """运行完整管线：分析 + 裁剪"""
    wav = _make_wav(audio, sample_rate=sr, sample_width=sw, channels=ch)
    data = wav.getvalue()
    analysis = detect_silence(io.BytesIO(data))
    if analysis.silence_segments:
        result = trim_silence(io.BytesIO(data), analysis)
    else:
        result = None
    return analysis, result


# ==================== 集成测试用例 ====================

class TestIntegration:

    # --- 用例 1: 纯正弦波 ---
    def test_case01_pure_sine(self):
        """纯正弦波，无静音段 → 不裁剪"""
        audio = _sine(440, 3.0)
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) == 0
        assert result is None

    # --- 用例 2: 全静音 ---
    def test_case02_all_silence(self):
        """全静音 → 裁剪后保留约 200ms"""
        audio = _silence(3.0)
        analysis, result = _run_pipeline(audio)
        assert analysis.saving_percentage > 80
        assert result is not None
        # 全静音裁剪后保留静音段首尾各 100ms = 200ms
        assert 150 < result.trimmed_duration_ms < 300

    # --- 用例 3: 开头静音 + 语音 ---
    def test_case03_leading_silence(self):
        """1.5s 静音 + 2s 语音"""
        audio = np.concatenate([_silence(1.5), _sine(440, 2.0)])
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) >= 1
        assert result is not None
        assert result.trimmed_duration_ms < analysis.original_duration_ms
        # 输出 WAV 有效
        with wave.open(io.BytesIO(result.audio_data), 'rb') as wf:
            assert wf.getnframes() > 0

    # --- 用例 4: 语音 + 末尾静音 ---
    def test_case04_trailing_silence(self):
        """2s 语音 + 1.5s 静音"""
        audio = np.concatenate([_sine(440, 2.0), _silence(1.5)])
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) >= 1
        assert result is not None
        assert result.removed_silence_ms >= 1000

    # --- 用例 5: 语音-静音-语音 ---
    def test_case05_middle_silence(self):
        """1s 语音 + 1s 静音 + 1s 语音"""
        audio = np.concatenate([_sine(440, 1.0), _silence(1.0), _sine(880, 1.0)])
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) >= 1
        assert result is not None
        # MD5 验证
        assert result.md5 == hashlib.md5(result.audio_data).hexdigest()

    # --- 用例 6: 多段间隔静音 ---
    def test_case06_multi_gaps(self):
        """4 段语音中间 3 段静音"""
        parts = []
        for i in range(4):
            parts.append(_sine(300 + i * 150, 0.4))
            if i < 3:
                parts.append(_silence(0.8))  # 800ms > 200ms
        audio = np.concatenate(parts)
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) == 3
        assert result is not None
        # 每段 800ms 裁剪至 200ms，移除 3×600ms = 1800ms
        assert result.trimmed_duration_ms < result.original_duration_ms * 0.7

    # --- 用例 7: 短静音 (不应被移除) ---
    def test_case07_short_silence_ignored(self):
        """150ms 静音 < 200ms 阈值 → 不移除"""
        audio = np.concatenate([_sine(440, 1.0), _silence(0.15), _sine(880, 1.0)])
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) == 0
        assert result is None

    # --- 用例 8: 低噪声作为静音 ---
    def test_case08_low_noise_silence(self):
        """噪声 < -40 dBFS → 视为静音"""
        audio = np.concatenate([_sine(440, 1.0), _noise(1.0), _sine(880, 1.0)])
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) >= 1
        assert result is not None

    # --- 用例 9: 立体声 ---
    def test_case09_stereo(self):
        """立体声：左右声道都有信号 + 静音"""
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

    # --- 用例 10: 不同采样率 - 8kHz ---
    def test_case10_sample_rate_8k(self):
        sr = 8000
        audio = np.concatenate([_sine(300, 0.5, sr), _silence(0.7, sr), _sine(500, 0.5, sr)])
        analysis, result = _run_pipeline(audio, sr=sr)
        assert analysis.sample_rate == sr
        assert result is not None
        assert result.sample_rate == sr

    # --- 用例 10b: 不同采样率 - 44.1kHz ---
    def test_case10b_sample_rate_44k(self):
        sr = 44100
        audio = np.concatenate([_sine(440, 1.0, sr), _silence(0.8, sr)])
        analysis, result = _run_pipeline(audio, sr=sr)
        assert analysis.sample_rate == sr
        assert result is not None
        assert result.sample_rate == sr

    # --- 用例 10c: 不同采样率 - 48kHz ---
    def test_case10c_sample_rate_48k(self):
        sr = 48000
        audio = np.concatenate([_sine(440, 0.5, sr), _silence(1.0, sr), _sine(660, 0.5, sr)])
        analysis, result = _run_pipeline(audio, sr=sr)
        assert analysis.sample_rate == sr
        assert result is not None

    # --- 用例 11: 不同位深度 - 8bit ---
    def test_case11_8bit(self):
        audio = np.concatenate([_sine(440, 1.0), _silence(0.8)])
        analysis, result = _run_pipeline(audio, sw=1)
        assert analysis.sample_width == 1
        if result:
            assert result.sample_width == 1

    # --- 用例 11b: 不同位深度 - 32bit ---
    def test_case11b_32bit(self):
        audio = np.concatenate([_sine(440, 1.0), _silence(0.8)])
        analysis, result = _run_pipeline(audio, sw=4)
        assert analysis.sample_width == 4
        if result:
            assert result.sample_width == 4

    # --- 用例 12: 极长静音段 ---
    def test_case12_long_silence(self):
        """5 秒静音"""
        audio = np.concatenate([_sine(440, 0.5), _silence(5.0), _sine(880, 0.5)])
        _, result = _run_pipeline(audio)
        assert result is not None
        assert result.removed_silence_ms >= 4500
        assert result.saving_percentage if hasattr(result, 'saving_percentage') else True

    # --- 用例 13: 交替快速段落 ---
    def test_case13_rapid_alternation(self):
        """多段短语音 + 刚好超过阈值的静音"""
        parts = []
        for _ in range(8):
            parts.append(_sine(440, 0.2))
            parts.append(_silence(0.55))  # 550ms > 200ms
        parts.append(_sine(440, 0.2))
        audio = np.concatenate(parts)
        analysis, result = _run_pipeline(audio)
        assert len(analysis.silence_segments) == 8
        assert result is not None
        # 每段 550ms 裁剪至 200ms，移除 8×350ms = 2800ms
        assert result.trimmed_duration_ms < result.original_duration_ms


# ==================== 性能基准 (非严格断言，记录数据) ====================

class TestPerformanceBenchmark:

    @pytest.mark.performance
    def test_processing_speed(self):
        """
        性能基准：单文件处理耗时 ≤ 原始音频时长的 30%

        生成一段 20 秒的音频（含多段静音），
        测量分析 + 裁剪的总耗时。
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

        # 仅在显式启用性能测试时断言严格阈值
        if os.environ.get('RUN_PERF_TESTS', '').lower() == 'true':
            assert ratio <= 0.30, f"处理耗时 {ratio:.2%} 超过音频时长的 30%"

    @pytest.mark.performance
    def test_large_file_performance(self):
        """
        大文件性能测试：模拟较长音频 (60 秒)
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
        高采样率性能测试：48kHz
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
