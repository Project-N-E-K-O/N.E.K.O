# -*- coding: utf-8 -*-
"""
智能静音裁剪功能 — 单元测试

覆盖范围:
- _samples_to_float / _float_to_samples 互逆性
- _rms_dbfs 计算正确性
- detect_silence 多种静音分布场景
- trim_silence 中心裁剪 + 无缝拼接
- 取消任务 (CancelledError)
- format_duration_mmss 格式化
- 边界情况（全静音、无静音、单声道/多声道、多种位深度）

目标覆盖率 ≥ 90%
"""

import io
import math
import struct
import wave
import hashlib
import pytest
import numpy as np
import os
import sys

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from utils.audio_silence_remover import (
    _samples_to_float,
    _float_to_samples,
    _rms_dbfs,
    detect_silence,
    trim_silence,
    format_duration_mmss,
    convert_to_wav_if_needed,
    CancelledError,
    SilenceAnalysisResult,
    SilenceSegment,
    SILENCE_THRESHOLD_DBFS,
    MIN_SILENCE_DURATION_MS,
    RETAINED_SILENCE_MS,
)

# ==================== 辅助函数 ====================

def _make_wav(
    samples: np.ndarray,
    sample_rate: int = 16000,
    sample_width: int = 2,
    channels: int = 1,
) -> io.BytesIO:
    """从 float64 采样生成 WAV BytesIO (-1.0 ~ 1.0)"""
    # 转 PCM
    pcm = _float_to_samples(samples, sample_width)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    buf.seek(0)
    return buf


def _generate_sine(freq: float, duration_s: float, sample_rate: int = 16000, amplitude: float = 0.5) -> np.ndarray:
    """生成正弦波采样"""
    t = np.arange(int(sample_rate * duration_s)) / sample_rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float64)


def _generate_silence(duration_s: float, sample_rate: int = 16000) -> np.ndarray:
    """生成纯静音"""
    return np.zeros(int(sample_rate * duration_s), dtype=np.float64)


def _generate_low_noise(duration_s: float, sample_rate: int = 16000, amplitude: float = 1e-4) -> np.ndarray:
    """生成低噪声（远低于 -40 dBFS 阈值）"""
    n = int(sample_rate * duration_s)
    rng = np.random.default_rng(42)
    return (rng.uniform(-1, 1, n) * amplitude).astype(np.float64)


# ==================== 基础函数测试 ====================

class TestSamplesToFloat:
    """_samples_to_float / _float_to_samples 互逆性"""

    def test_16bit_roundtrip(self):
        original = np.array([0.0, 0.5, -0.5, 0.99, -0.99], dtype=np.float64)
        pcm = _float_to_samples(original, sample_width=2)
        restored = _samples_to_float(pcm, sample_width=2)
        np.testing.assert_allclose(restored, original, atol=1 / 32768)

    def test_8bit_roundtrip(self):
        original = np.array([0.0, 0.5, -0.5], dtype=np.float64)
        pcm = _float_to_samples(original, sample_width=1)
        restored = _samples_to_float(pcm, sample_width=1)
        np.testing.assert_allclose(restored, original, atol=1 / 128)

    def test_32bit_roundtrip(self):
        original = np.array([0.0, 0.123, -0.456], dtype=np.float64)
        pcm = _float_to_samples(original, sample_width=4)
        restored = _samples_to_float(pcm, sample_width=4)
        np.testing.assert_allclose(restored, original, atol=1 / 2147483648)

    def test_24bit_roundtrip(self):
        original = np.array([0.0, 0.25, -0.75], dtype=np.float64)
        pcm = _float_to_samples(original, sample_width=3)
        restored = _samples_to_float(pcm, sample_width=3)
        np.testing.assert_allclose(restored, original, atol=1 / 8388608)

    def test_clipping(self):
        """超出范围的值应被裁剪"""
        original = np.array([1.5, -1.5], dtype=np.float64)
        pcm = _float_to_samples(original, sample_width=2)
        restored = _samples_to_float(pcm, sample_width=2)
        assert all(abs(v) <= 1.0 for v in restored)

    def test_unsupported_width(self):
        with pytest.raises(ValueError, match="不支持"):
            _samples_to_float(b'\x00\x00', sample_width=5)
        with pytest.raises(ValueError, match="不支持"):
            _float_to_samples(np.array([0.0]), sample_width=5)


class TestRmsDbfs:
    """RMS dBFS 计算"""

    def test_full_scale_sine(self):
        """满刻度正弦波 ≈ -3 dBFS"""
        t = np.arange(16000) / 16000.0
        sine = np.sin(2 * np.pi * 440 * t)
        dbfs = _rms_dbfs(sine)
        assert -4.0 < dbfs < -2.0  # -3.01 dBFS

    def test_silence(self):
        """纯静音 → 非常低的 dBFS"""
        silence = np.zeros(1000)
        dbfs = _rms_dbfs(silence)
        assert dbfs <= -100.0

    def test_half_amplitude(self):
        """半幅度正弦 ≈ -9 dBFS"""
        t = np.arange(16000) / 16000.0
        sine = 0.5 * np.sin(2 * np.pi * 440 * t)
        dbfs = _rms_dbfs(sine)
        assert -10.0 < dbfs < -8.0

    def test_empty_array(self):
        dbfs = _rms_dbfs(np.array([]))
        assert dbfs == -100.0

    def test_low_noise_below_threshold(self):
        """低噪声应该低于 -40 dBFS"""
        noise = _generate_low_noise(0.1, amplitude=5e-5)
        dbfs = _rms_dbfs(noise)
        assert dbfs < SILENCE_THRESHOLD_DBFS


# ==================== detect_silence 测试 ====================

class TestDetectSilence:

    def test_no_silence(self):
        """纯正弦波，无静音段"""
        sine = _generate_sine(440, 2.0)
        wav = _make_wav(sine)
        result = detect_silence(wav)
        assert result.original_duration_ms == pytest.approx(2000, abs=10)
        assert len(result.silence_segments) == 0
        assert result.total_silence_ms == 0

    def test_all_silence(self):
        """全静音"""
        silence = _generate_silence(2.0)
        wav = _make_wav(silence)
        result = detect_silence(wav)
        assert len(result.silence_segments) >= 1
        assert result.total_silence_ms == pytest.approx(2000, rel=0.1)
        assert result.saving_percentage >= 90.0

    def test_leading_silence(self):
        """开头 1s 静音 + 1s 正弦"""
        silence = _generate_silence(1.0)
        sine = _generate_sine(440, 1.0)
        audio = np.concatenate([silence, sine])
        wav = _make_wav(audio)
        result = detect_silence(wav)
        assert len(result.silence_segments) >= 1
        # 第一段静音应该大约 1000ms
        first_seg = result.silence_segments[0]
        assert first_seg.start_ms == pytest.approx(0, abs=50)
        assert first_seg.duration_ms >= MIN_SILENCE_DURATION_MS

    def test_trailing_silence(self):
        """1s 正弦 + 1s 静音"""
        sine = _generate_sine(440, 1.0)
        silence = _generate_silence(1.0)
        audio = np.concatenate([sine, silence])
        wav = _make_wav(audio)
        result = detect_silence(wav)
        assert len(result.silence_segments) >= 1
        last_seg = result.silence_segments[-1]
        assert last_seg.end_ms == pytest.approx(2000, abs=50)

    def test_middle_silence(self):
        """正弦 + 1s 静音 + 正弦"""
        sine1 = _generate_sine(440, 1.0)
        silence = _generate_silence(1.0)
        sine2 = _generate_sine(880, 1.0)
        audio = np.concatenate([sine1, silence, sine2])
        wav = _make_wav(audio)
        result = detect_silence(wav)
        assert len(result.silence_segments) >= 1
        assert result.total_silence_ms >= 800  # 大约 1000ms

    def test_short_silence_not_detected(self):
        """短于阈值 (150ms < 200ms) 的静音不应被检测"""
        sine1 = _generate_sine(440, 1.0)
        short_silence = _generate_silence(0.15)
        sine2 = _generate_sine(880, 1.0)
        audio = np.concatenate([sine1, short_silence, sine2])
        wav = _make_wav(audio)
        result = detect_silence(wav)
        # 150ms < 200ms 阈值，不应被检测
        assert len(result.silence_segments) == 0

    def test_multiple_silence_segments(self):
        """多段静音"""
        parts = []
        for i in range(3):
            parts.append(_generate_sine(440 + i * 200, 0.5))
            parts.append(_generate_silence(0.7))  # 700ms > 500ms 阈值
        parts.append(_generate_sine(1200, 0.5))
        audio = np.concatenate(parts)
        wav = _make_wav(audio)
        result = detect_silence(wav)
        assert len(result.silence_segments) == 3

    def test_low_noise_as_silence(self):
        """低噪声 (< -40 dBFS) 也应被视为静音"""
        sine = _generate_sine(440, 1.0)
        noise = _generate_low_noise(1.0)
        audio = np.concatenate([sine, noise])
        wav = _make_wav(audio)
        result = detect_silence(wav)
        assert len(result.silence_segments) >= 1

    def test_cancel_detection(self):
        """测试取消检测"""
        sine = _generate_sine(440, 5.0)
        wav = _make_wav(sine)
        call_count = [0]

        def cancel_fn():
            call_count[0] += 1
            return call_count[0] > 10  # 在第 10 次检查后取消

        with pytest.raises(CancelledError):
            detect_silence(wav, cancel_check=cancel_fn)

    def test_progress_callback(self):
        """测试进度回调被调用"""
        sine = _generate_sine(440, 2.0)
        wav = _make_wav(sine)
        progress_values = []

        def on_progress(pct):
            progress_values.append(pct)

        detect_silence(wav, progress_callback=on_progress)
        assert len(progress_values) > 0
        assert progress_values[-1] == 100

    def test_stereo_audio(self):
        """立体声音频"""
        sine_l = _generate_sine(440, 1.0)
        sine_r = _generate_sine(880, 1.0)
        silence = _generate_silence(1.0)
        # 立体声：左右声道交错
        stereo_voice = np.column_stack([sine_l, sine_r]).flatten()
        stereo_silence = np.column_stack([silence, silence]).flatten()
        audio = np.concatenate([stereo_voice, stereo_silence])
        wav = _make_wav(audio, channels=2)
        result = detect_silence(wav)
        assert result.channels == 2
        assert len(result.silence_segments) >= 1

    def test_format_returned(self):
        """检测结果包含音频格式信息"""
        sine = _generate_sine(440, 1.0, sample_rate=44100)
        wav = _make_wav(sine, sample_rate=44100)
        result = detect_silence(wav)
        assert result.sample_rate == 44100
        assert result.sample_width == 2
        assert result.channels == 1

    def test_estimated_duration_computed(self):
        """estimated_duration = original - removable_silence"""
        sine = _generate_sine(440, 2.0)
        silence = _generate_silence(1.0)
        audio = np.concatenate([sine, silence])
        wav = _make_wav(audio)
        result = detect_silence(wav)
        # 新算法: 每段静音保留 RETAINED_SILENCE_MS，超出部分才被移除
        expected_removable = sum(
            max(0, s.duration_ms - RETAINED_SILENCE_MS)
            for s in result.silence_segments
        )
        assert result.removable_silence_ms == pytest.approx(expected_removable, abs=1)
        assert result.estimated_duration_ms == pytest.approx(
            result.original_duration_ms - expected_removable, abs=1
        )


# ==================== trim_silence 测试 ====================

class TestTrimSilence:

    def test_no_silence_segments(self):
        """没有静音段时返回原始音频"""
        sine = _generate_sine(440, 1.0)
        wav = _make_wav(sine)
        analysis = SilenceAnalysisResult(
            original_duration_ms=1000,
            silence_segments=[],
            total_silence_ms=0,
            estimated_duration_ms=1000,
            sample_rate=16000,
            sample_width=2,
            channels=1,
        )
        result = trim_silence(wav, analysis)
        assert result.removed_silence_ms == 0
        assert result.md5  # 有 MD5 值

    def test_basic_trim(self):
        """基本裁剪：移除中间静音"""
        sine1 = _generate_sine(440, 1.0)
        silence = _generate_silence(1.0)
        sine2 = _generate_sine(880, 1.0)
        audio = np.concatenate([sine1, silence, sine2])
        wav_orig = _make_wav(audio)

        # 先分析
        analysis = detect_silence(io.BytesIO(wav_orig.getvalue()))

        # 再裁剪
        result = trim_silence(io.BytesIO(wav_orig.getvalue()), analysis)
        assert result.trimmed_duration_ms < result.original_duration_ms
        assert result.removed_silence_ms > 500
        assert result.md5  # 有 MD5

        # 输出应该是有效的 WAV
        out_buf = io.BytesIO(result.audio_data)
        with wave.open(out_buf, 'rb') as wf:
            assert wf.getframerate() == 16000
            assert wf.getsampwidth() == 2
            assert wf.getnchannels() == 1

    def test_md5_consistency(self):
        """相同输入产生相同 MD5"""
        sine = _generate_sine(440, 1.0)
        silence = _generate_silence(1.0)
        audio = np.concatenate([sine, silence])
        wav_data = _make_wav(audio).getvalue()

        analysis = detect_silence(io.BytesIO(wav_data))
        result1 = trim_silence(io.BytesIO(wav_data), analysis)
        result2 = trim_silence(io.BytesIO(wav_data), analysis)
        assert result1.md5 == result2.md5

    def test_md5_matches_content(self):
        """MD5 与实际数据一致"""
        sine = _generate_sine(440, 1.0)
        silence = _generate_silence(1.0)
        audio = np.concatenate([sine, silence])
        wav = _make_wav(audio)

        analysis = detect_silence(io.BytesIO(wav.getvalue()))
        result = trim_silence(io.BytesIO(wav.getvalue()), analysis)
        actual_md5 = hashlib.md5(result.audio_data).hexdigest()
        assert result.md5 == actual_md5

    def test_output_params_match_input(self):
        """输出音频参数与输入一致"""
        sine = _generate_sine(440, 1.0, sample_rate=44100)
        silence = _generate_silence(1.0, sample_rate=44100)
        audio = np.concatenate([sine, silence])
        wav = _make_wav(audio, sample_rate=44100)

        analysis = detect_silence(io.BytesIO(wav.getvalue()))
        result = trim_silence(io.BytesIO(wav.getvalue()), analysis)
        assert result.sample_rate == 44100
        assert result.sample_width == 2
        assert result.channels == 1

    def test_all_silence_trim(self):
        """全静音音频裁剪后保留约 200ms"""
        silence = _generate_silence(2.0)
        wav = _make_wav(silence)
        analysis = detect_silence(io.BytesIO(wav.getvalue()))
        result = trim_silence(io.BytesIO(wav.getvalue()), analysis)
        # 全静音裁剪后保留静音段首尾各 100ms，共约 200ms
        assert 150 < result.trimmed_duration_ms < 300

    def test_cancel_trim(self):
        """取消裁剪任务"""
        parts = []
        for _ in range(20):
            parts.append(_generate_sine(440, 0.2))
            parts.append(_generate_silence(0.6))
        audio = np.concatenate(parts)
        wav = _make_wav(audio)

        analysis = detect_silence(io.BytesIO(wav.getvalue()))
        call_count = [0]

        def cancel_fn():
            call_count[0] += 1
            return call_count[0] > 5

        with pytest.raises(CancelledError):
            trim_silence(io.BytesIO(wav.getvalue()), analysis, cancel_check=cancel_fn)

    def test_progress_callback(self):
        """进度回调正确调用"""
        sine1 = _generate_sine(440, 1.0)
        silence = _generate_silence(1.0)
        sine2 = _generate_sine(880, 1.0)
        audio = np.concatenate([sine1, silence, sine2])
        wav = _make_wav(audio)

        analysis = detect_silence(io.BytesIO(wav.getvalue()))
        progress_values = []
        _ = trim_silence(
            io.BytesIO(wav.getvalue()),
            analysis,
            progress_callback=lambda p: progress_values.append(p),
        )
        assert len(progress_values) > 0
        assert progress_values[-1] == 100

    def test_center_cut_no_clicks(self):
        """中心裁剪不会导致爆音"""
        # 创建两段频率不同的音频，中间有静音
        sine1 = _generate_sine(200, 0.5, amplitude=0.8)
        silence = _generate_silence(1.0)
        sine2 = _generate_sine(1000, 0.5, amplitude=0.8)
        audio = np.concatenate([sine1, silence, sine2])
        wav = _make_wav(audio)

        analysis = detect_silence(io.BytesIO(wav.getvalue()))
        result = trim_silence(io.BytesIO(wav.getvalue()), analysis)

        # 解析输出音频
        out_buf = io.BytesIO(result.audio_data)
        with wave.open(out_buf, 'rb') as wf:
            out_data = wf.readframes(wf.getnframes())

        out_samples = _samples_to_float(out_data, 2)
        # 检查没有超出范围的值
        assert np.all(np.abs(out_samples) <= 1.0)
        # 检查没有急剧变化（爆音检测）
        diff = np.diff(out_samples)
        # 差分不应有极端跳变
        assert np.max(np.abs(diff)) < 0.5  # 合理范围

    def test_multiple_segments_trim(self):
        """多段静音裁剪"""
        parts = []
        for i in range(5):
            parts.append(_generate_sine(300 + i * 100, 0.3))
            parts.append(_generate_silence(0.6))
        parts.append(_generate_sine(800, 0.3))
        audio = np.concatenate(parts)
        wav = _make_wav(audio)

        analysis = detect_silence(io.BytesIO(wav.getvalue()))
        assert len(analysis.silence_segments) == 5

        result = trim_silence(io.BytesIO(wav.getvalue()), analysis)
        # 每段 600ms 静音裁剪至 200ms，移除 5×400ms = 2000ms
        # 原始约 4.8s，裁剪后约 2.8s
        assert result.trimmed_duration_ms < result.original_duration_ms * 0.7


# ==================== format_duration_mmss 测试 ====================

class TestFormatDuration:

    def test_zero(self):
        assert format_duration_mmss(0) == "00:00"

    def test_one_minute(self):
        assert format_duration_mmss(60000) == "01:00"

    def test_mixed(self):
        assert format_duration_mmss(65000) == "01:05"

    def test_large(self):
        assert format_duration_mmss(600000) == "10:00"

    def test_seconds_only(self):
        assert format_duration_mmss(30000) == "00:30"

    def test_sub_second_rounds_down(self):
        assert format_duration_mmss(999) == "00:00"

    def test_exact_boundary(self):
        assert format_duration_mmss(59999) == "00:59"


# ==================== convert_to_wav_if_needed 测试 ====================

class TestConvertToWav:

    def test_wav_passthrough(self):
        """WAV 文件直接通过"""
        sine = _generate_sine(440, 0.5)
        wav = _make_wav(sine)
        result_buf, fmt = convert_to_wav_if_needed(wav, "test.wav")
        assert fmt == 'wav'
        # 验证返回的是有效 WAV
        with wave.open(result_buf, 'rb') as wf:
            assert wf.getframerate() == 16000

    def test_invalid_wav(self):
        """无效 WAV 文件应报错"""
        bad_buf = io.BytesIO(b"not a wav file")
        with pytest.raises(ValueError, match="无效"):
            convert_to_wav_if_needed(bad_buf, "test.wav")

    def test_unsupported_format_no_pydub(self):
        """没有 pydub 时，非 WAV 格式应给出清晰错误"""
        buf = io.BytesIO(b"fake mp3 data")
        # 如果 pydub 不存在，应该抛出 ValueError
        # 如果 pydub 存在但数据无效，也会报错
        with pytest.raises((ValueError, Exception)):
            convert_to_wav_if_needed(buf, "test.mp3")


# ==================== 端到端流程测试 ====================

class TestEndToEnd:

    def test_full_pipeline(self):
        """完整流程：生成 → 分析 → 裁剪 → 验证"""
        # 1. 生成测试音频：正弦 1s + 静音 1s + 正弦 1s + 静音 0.8s + 正弦 0.5s
        parts = [
            _generate_sine(440, 1.0),
            _generate_silence(1.0),
            _generate_sine(880, 1.0),
            _generate_silence(0.8),
            _generate_sine(660, 0.5),
        ]
        audio = np.concatenate(parts)
        wav = _make_wav(audio)
        orig_data = wav.getvalue()

        # 2. 分析
        analysis = detect_silence(io.BytesIO(orig_data))
        assert analysis.has_silence if hasattr(analysis, 'has_silence') else len(analysis.silence_segments) > 0
        assert analysis.saving_percentage > 0

        # 3. 裁剪
        result = trim_silence(io.BytesIO(orig_data), analysis)
        assert result.trimmed_duration_ms < result.original_duration_ms
        assert result.md5 == hashlib.md5(result.audio_data).hexdigest()

        # 4. 验证输出格式
        out_buf = io.BytesIO(result.audio_data)
        with wave.open(out_buf, 'rb') as wf:
            assert wf.getframerate() == 16000
            assert wf.getsampwidth() == 2
            assert wf.getnchannels() == 1

    def test_pipeline_with_different_sample_rates(self):
        """不同采样率的测试"""
        for sr in [8000, 22050, 44100, 48000]:
            sine = _generate_sine(440, 0.5, sample_rate=sr)
            silence = _generate_silence(0.7, sample_rate=sr)
            audio = np.concatenate([sine, silence, sine])
            wav = _make_wav(audio, sample_rate=sr)
            data = wav.getvalue()

            analysis = detect_silence(io.BytesIO(data))
            if analysis.silence_segments:
                result = trim_silence(io.BytesIO(data), analysis)
                assert result.sample_rate == sr
                assert result.md5 == hashlib.md5(result.audio_data).hexdigest()

    def test_pipeline_preserves_bit_depth(self):
        """不同位深度保持一致"""
        for sw in [1, 2, 3, 4]:
            sine = _generate_sine(440, 0.5)
            silence = _generate_silence(0.7)
            audio = np.concatenate([sine, silence])
            wav = _make_wav(audio, sample_width=sw)
            data = wav.getvalue()

            analysis = detect_silence(io.BytesIO(data))
            if analysis.silence_segments:
                result = trim_silence(io.BytesIO(data), analysis)
                assert result.sample_width == sw
