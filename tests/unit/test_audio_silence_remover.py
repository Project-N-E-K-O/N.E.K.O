# -*- coding: utf-8 -*-
"""
Smart silence trimming — unit tests.

Coverage:
- _samples_to_float / _float_to_samples inversion
- _rms_dbfs correctness
- detect_silence across silence distributions
- trim_silence center trimming + seamless joining
- Task cancellation (CancelledError)
- format_duration_mmss formatting
- Edge cases (all/no silence, mono/multichannel, varying bit depths)

Target coverage ≥ 90%
"""

import io
import wave
import hashlib
import pytest
import numpy as np
import os
import sys

# Ensure project root in sys.path.
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
    SILENCE_THRESHOLD_DBFS,
    MIN_SILENCE_DURATION_MS,
    RETAINED_SILENCE_MS,
)

# ==================== Helpers ====================

def _make_wav(
    samples: np.ndarray,
    sample_rate: int = 16000,
    sample_width: int = 2,
    channels: int = 1,
) -> io.BytesIO:
    """Generate WAV BytesIO from float64 samples (-1.0 to 1.0)."""
    # Convert to PCM.
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
    """Generate sine-wave samples."""
    t = np.arange(int(sample_rate * duration_s)) / sample_rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float64)


def _generate_silence(duration_s: float, sample_rate: int = 16000) -> np.ndarray:
    """Generate pure silence."""
    return np.zeros(int(sample_rate * duration_s), dtype=np.float64)


def _generate_low_noise(duration_s: float, sample_rate: int = 16000, amplitude: float = 1e-4) -> np.ndarray:
    """Generate low noise, well below -40 dBFS threshold."""
    n = int(sample_rate * duration_s)
    rng = np.random.default_rng(42)
    return (rng.uniform(-1, 1, n) * amplitude).astype(np.float64)


# ==================== Basic function tests ====================

class TestSamplesToFloat:
    """_samples_to_float / _float_to_samples are inverses."""

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
        """Clamp out-of-range values."""
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
    """RMS dBFS calculation."""

    def test_full_scale_sine(self):
        """Full-scale sine wave ≈ -3 dBFS."""
        t = np.arange(16000) / 16000.0
        sine = np.sin(2 * np.pi * 440 * t)
        dbfs = _rms_dbfs(sine)
        assert -4.0 < dbfs < -2.0  # -3.01 dBFS

    def test_silence(self):
        """Pure silence → very low dBFS."""
        silence = np.zeros(1000)
        dbfs = _rms_dbfs(silence)
        assert dbfs <= -100.0

    def test_half_amplitude(self):
        """Half-amplitude sine ≈ -9 dBFS."""
        t = np.arange(16000) / 16000.0
        sine = 0.5 * np.sin(2 * np.pi * 440 * t)
        dbfs = _rms_dbfs(sine)
        assert -10.0 < dbfs < -8.0

    def test_empty_array(self):
        dbfs = _rms_dbfs(np.array([]))
        assert dbfs == -100.0

    def test_low_noise_below_threshold(self):
        """Low noise must be below -40 dBFS."""
        noise = _generate_low_noise(0.1, amplitude=5e-5)
        dbfs = _rms_dbfs(noise)
        assert dbfs < SILENCE_THRESHOLD_DBFS


# ==================== detect_silence tests ====================

class TestDetectSilence:

    def test_no_silence(self):
        """Pure sine wave without silent segments."""
        sine = _generate_sine(440, 2.0)
        wav = _make_wav(sine)
        result = detect_silence(wav)
        assert result.original_duration_ms == pytest.approx(2000, abs=10)
        assert len(result.silence_segments) == 0
        assert result.total_silence_ms == 0

    def test_all_silence(self):
        """All silence."""
        silence = _generate_silence(2.0)
        wav = _make_wav(silence)
        result = detect_silence(wav)
        assert len(result.silence_segments) >= 1
        assert result.total_silence_ms == pytest.approx(2000, rel=0.1)
        assert result.saving_percentage >= 90.0

    def test_leading_silence(self):
        """Leading 1s silence + 1s sine."""
        silence = _generate_silence(1.0)
        sine = _generate_sine(440, 1.0)
        audio = np.concatenate([silence, sine])
        wav = _make_wav(audio)
        result = detect_silence(wav)
        assert len(result.silence_segments) >= 1
        # First silence should be about 1000ms.
        first_seg = result.silence_segments[0]
        assert first_seg.start_ms == pytest.approx(0, abs=50)
        assert first_seg.duration_ms >= MIN_SILENCE_DURATION_MS

    def test_trailing_silence(self):
        """1s sine + 1s silence."""
        sine = _generate_sine(440, 1.0)
        silence = _generate_silence(1.0)
        audio = np.concatenate([sine, silence])
        wav = _make_wav(audio)
        result = detect_silence(wav)
        assert len(result.silence_segments) >= 1
        last_seg = result.silence_segments[-1]
        assert last_seg.end_ms == pytest.approx(2000, abs=50)

    def test_middle_silence(self):
        """Sine + 1s silence + sine."""
        sine1 = _generate_sine(440, 1.0)
        silence = _generate_silence(1.0)
        sine2 = _generate_sine(880, 1.0)
        audio = np.concatenate([sine1, silence, sine2])
        wav = _make_wav(audio)
        result = detect_silence(wav)
        assert len(result.silence_segments) >= 1
        assert result.total_silence_ms >= 800  # About 1000ms.

    def test_short_silence_not_detected(self):
        """Do not detect silence below threshold (150ms < 200ms)."""
        sine1 = _generate_sine(440, 1.0)
        short_silence = _generate_silence(0.15)
        sine2 = _generate_sine(880, 1.0)
        audio = np.concatenate([sine1, short_silence, sine2])
        wav = _make_wav(audio)
        result = detect_silence(wav)
        # 150ms < 200ms threshold, must not detect.
        assert len(result.silence_segments) == 0

    def test_multiple_silence_segments(self):
        """Multiple silent segments."""
        parts = []
        for i in range(3):
            parts.append(_generate_sine(440 + i * 200, 0.5))
            parts.append(_generate_silence(0.7))  # 700ms > 500ms threshold.
        parts.append(_generate_sine(1200, 0.5))
        audio = np.concatenate(parts)
        wav = _make_wav(audio)
        result = detect_silence(wav)
        assert len(result.silence_segments) == 3

    def test_low_noise_as_silence(self):
        """Low noise (< -40 dBFS) also counts as silence."""
        sine = _generate_sine(440, 1.0)
        noise = _generate_low_noise(1.0)
        audio = np.concatenate([sine, noise])
        wav = _make_wav(audio)
        result = detect_silence(wav)
        assert len(result.silence_segments) >= 1

    def test_cancel_detection(self):
        """Test cancellation detection."""
        sine = _generate_sine(440, 5.0)
        wav = _make_wav(sine)
        call_count = [0]

        def cancel_fn():
            call_count[0] += 1
            return call_count[0] > 10  # Cancel after tenth check.

        with pytest.raises(CancelledError):
            detect_silence(wav, cancel_check=cancel_fn)

    def test_progress_callback(self):
        """Test progress callback invocation."""
        sine = _generate_sine(440, 2.0)
        wav = _make_wav(sine)
        progress_values = []

        def on_progress(pct):
            progress_values.append(pct)

        detect_silence(wav, progress_callback=on_progress)
        assert len(progress_values) > 0
        assert progress_values[-1] == 100

    def test_stereo_audio(self):
        """Stereo audio."""
        sine_l = _generate_sine(440, 1.0)
        sine_r = _generate_sine(880, 1.0)
        silence = _generate_silence(1.0)
        # Stereo: interleaved left/right channels.
        stereo_voice = np.column_stack([sine_l, sine_r]).flatten()
        stereo_silence = np.column_stack([silence, silence]).flatten()
        audio = np.concatenate([stereo_voice, stereo_silence])
        wav = _make_wav(audio, channels=2)
        result = detect_silence(wav)
        assert result.channels == 2
        assert len(result.silence_segments) >= 1

    def test_format_returned(self):
        """Detection results include audio format information."""
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
        # New algorithm retains RETAINED_SILENCE_MS per silence, removing excess only.
        expected_removable = sum(
            max(0, s.duration_ms - RETAINED_SILENCE_MS)
            for s in result.silence_segments
        )
        assert result.removable_silence_ms == pytest.approx(expected_removable, abs=1)
        assert result.estimated_duration_ms == pytest.approx(
            result.original_duration_ms - expected_removable, abs=1
        )


# ==================== trim_silence tests ====================

class TestTrimSilence:

    def test_no_silence_segments(self):
        """Return original audio when no silence exists."""
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
        assert result.md5  # MD5 present.

    def test_basic_trim(self):
        """Basic trimming: remove middle silence."""
        sine1 = _generate_sine(440, 1.0)
        silence = _generate_silence(1.0)
        sine2 = _generate_sine(880, 1.0)
        audio = np.concatenate([sine1, silence, sine2])
        wav_orig = _make_wav(audio)

        # Analyze first.
        analysis = detect_silence(io.BytesIO(wav_orig.getvalue()))

        # Then trim.
        result = trim_silence(io.BytesIO(wav_orig.getvalue()), analysis)
        assert result.trimmed_duration_ms < result.original_duration_ms
        assert result.removed_silence_ms > 500
        assert result.md5  # MD5 present.

        # Output must be valid WAV.
        out_buf = io.BytesIO(result.audio_data)
        with wave.open(out_buf, 'rb') as wf:
            assert wf.getframerate() == 16000
            assert wf.getsampwidth() == 2
            assert wf.getnchannels() == 1

    def test_md5_consistency(self):
        """Identical inputs produce identical MD5."""
        sine = _generate_sine(440, 1.0)
        silence = _generate_silence(1.0)
        audio = np.concatenate([sine, silence])
        wav_data = _make_wav(audio).getvalue()

        analysis = detect_silence(io.BytesIO(wav_data))
        result1 = trim_silence(io.BytesIO(wav_data), analysis)
        result2 = trim_silence(io.BytesIO(wav_data), analysis)
        assert result1.md5 == result2.md5

    def test_md5_matches_content(self):
        """MD5 matches actual data."""
        sine = _generate_sine(440, 1.0)
        silence = _generate_silence(1.0)
        audio = np.concatenate([sine, silence])
        wav = _make_wav(audio)

        analysis = detect_silence(io.BytesIO(wav.getvalue()))
        result = trim_silence(io.BytesIO(wav.getvalue()), analysis)
        actual_md5 = hashlib.md5(result.audio_data).hexdigest()
        assert result.md5 == actual_md5

    def test_output_params_match_input(self):
        """Output audio parameters match input."""
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
        """All-silent audio retains about 200ms after trimming."""
        silence = _generate_silence(2.0)
        wav = _make_wav(silence)
        analysis = detect_silence(io.BytesIO(wav.getvalue()))
        result = trim_silence(io.BytesIO(wav.getvalue()), analysis)
        # All-silent input retains 100ms at each end, about 200ms total.
        assert 150 < result.trimmed_duration_ms < 300

    def test_cancel_trim(self):
        """Cancel trimming task."""
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
        """Invoke progress callbacks correctly."""
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
        """Center trimming causes no audio pops."""
        # Create two different-frequency audio segments separated by silence.
        sine1 = _generate_sine(200, 0.5, amplitude=0.8)
        silence = _generate_silence(1.0)
        sine2 = _generate_sine(1000, 0.5, amplitude=0.8)
        audio = np.concatenate([sine1, silence, sine2])
        wav = _make_wav(audio)

        analysis = detect_silence(io.BytesIO(wav.getvalue()))
        result = trim_silence(io.BytesIO(wav.getvalue()), analysis)

        # Parse output audio.
        out_buf = io.BytesIO(result.audio_data)
        with wave.open(out_buf, 'rb') as wf:
            out_data = wf.readframes(wf.getnframes())

        out_samples = _samples_to_float(out_data, 2)
        # Check no out-of-range values.
        assert np.all(np.abs(out_samples) <= 1.0)
        # Check no abrupt changes (pop detection).
        diff = np.diff(out_samples)
        # Differences must not contain extreme jumps.
        assert np.max(np.abs(diff)) < 0.5  # Reasonable range.

    def test_multiple_segments_trim(self):
        """Trim multiple silent segments."""
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
        # Trim each 600ms silence to 200ms, removing 5×400ms = 2000ms.
        # Original ~4.8s, trimmed ~2.8s.
        assert result.trimmed_duration_ms < result.original_duration_ms * 0.7


# ==================== format_duration_mmss tests ====================

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


# ==================== convert_to_wav_if_needed tests ====================

class TestConvertToWav:

    def test_wav_passthrough(self):
        """Pass WAV files through directly."""
        sine = _generate_sine(440, 0.5)
        wav = _make_wav(sine)
        result_buf, fmt = convert_to_wav_if_needed(wav, "test.wav")
        assert fmt == 'wav'
        # Verify returned WAV is valid.
        with wave.open(result_buf, 'rb') as wf:
            assert wf.getframerate() == 16000

    def test_invalid_wav(self):
        """Invalid WAV files must error."""
        bad_buf = io.BytesIO(b"not a wav file")
        with pytest.raises(ValueError, match="无效"):
            convert_to_wav_if_needed(bad_buf, "test.wav")

    def test_unsupported_format_no_pydub(self):
        """Non-WAV without pydub gives clear error."""
        buf = io.BytesIO(b"fake mp3 data")
        # Missing pydub raises ValueError.
        # Available pydub with invalid data also errors.
        with pytest.raises((ValueError, Exception)):
            convert_to_wav_if_needed(buf, "test.mp3")


# ==================== End-to-end flow tests ====================

class TestEndToEnd:

    def test_full_pipeline(self):
        """Complete flow: generate → analyze → trim → validate."""
        # 1. Generate test audio: sine 1s + silence 1s + sine 1s + silence 0.8s + sine 0.5s.
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

        # 2. Analyze.
        analysis = detect_silence(io.BytesIO(orig_data))
        assert analysis.has_silence if hasattr(analysis, 'has_silence') else len(analysis.silence_segments) > 0
        assert analysis.saving_percentage > 0

        # 3. Trim.
        result = trim_silence(io.BytesIO(orig_data), analysis)
        assert result.trimmed_duration_ms < result.original_duration_ms
        assert result.md5 == hashlib.md5(result.audio_data).hexdigest()

        # 4. Validate output format.
        out_buf = io.BytesIO(result.audio_data)
        with wave.open(out_buf, 'rb') as wf:
            assert wf.getframerate() == 16000
            assert wf.getsampwidth() == 2
            assert wf.getnchannels() == 1

    def test_pipeline_with_different_sample_rates(self):
        """Test different sample rates."""
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
        """Preserve consistency across bit depths."""
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
