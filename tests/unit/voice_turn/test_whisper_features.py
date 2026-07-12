import numpy as np

from main_logic.voice_turn.whisper_features import (
    WhisperFeatureExtractor,
    whisper_mel_filter_bank,
)


def test_mel_filter_bank_matches_reviewed_whisper_reference_values():
    filters = whisper_mel_filter_bank()
    assert filters.shape == (201, 80)
    np.testing.assert_allclose(filters.sum(), 1.9990242, atol=1e-7)


def test_features_have_golden_statistics_for_synthetic_tone():
    samples = np.arange(16_000)
    tone = (0.1 * np.sin(2 * np.pi * 440 * samples / 16_000)).astype(np.float32)
    features = WhisperFeatureExtractor().extract(tone)
    assert features.shape == (80, 800)
    assert features.dtype == np.float32
    np.testing.assert_allclose(
        [features.sum(), features.mean(), features.std(), features.min(), features.max()],
        [-6148.0396, -0.096063115, 0.15449585, -0.11026861, 1.8897314],
        rtol=1e-5,
        atol=1e-5,
    )


def test_long_audio_keeps_trailing_eight_seconds():
    extractor = WhisperFeatureExtractor()
    tail = np.linspace(-0.2, 0.2, extractor.MAX_SAMPLES, dtype=np.float32)
    prefix = np.ones(1234, dtype=np.float32)
    np.testing.assert_array_equal(extractor.extract(np.concatenate((prefix, tail))), extractor.extract(tail))
