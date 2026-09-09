from pathlib import Path
import os

import numpy as np
import pytest

from main_logic.voice_identity.tse.frontend import (
    StreamingISTFT, StreamingSTFT, TseEnrollmentFrontend, WINDOW,
)


@pytest.mark.parametrize("length", [0, 1, 2, 127, 128, 129, 255, 256, 257, 383, 511, 512, 639, 640, 641, 4097])
def test_frontend_preserves_every_sample_with_random_chunks(length):
    rng = np.random.default_rng(length)
    pcm = rng.normal(0, 0.1, length).astype(np.float32)
    analysis, synthesis = StreamingSTFT(), StreamingISTFT()
    parts, spectra = [], []
    offset = 0
    while offset < length:
        count = int(rng.integers(1, 641))
        spectrum = analysis.push(pcm[offset:offset + count])
        spectra.append(spectrum)
        parts.append(synthesis.push(spectrum))
        offset += count
    tail = analysis.flush()
    spectra.append(tail)
    parts.append(synthesis.push(tail))
    parts.append(synthesis.flush(length))
    actual = np.concatenate(parts)
    assert actual.dtype == np.float32
    assert len(actual) == length
    np.testing.assert_allclose(actual, pcm, atol=5e-8, rtol=2e-6)
    if length:
        padded = np.pad(pcm, (256, 256), mode="reflect" if length > 1 else "edge")
        frames = np.lib.stride_tricks.sliding_window_view(padded, 512)[::128]
        spec = np.fft.rfft(frames * WINDOW, axis=1)
        expected = np.stack((spec.real.T, spec.imag.T), axis=0)[None].astype(np.float32)
        np.testing.assert_array_equal(np.concatenate(spectra, axis=-1), expected)


def test_frontend_rejects_late_audio_and_invalid_inputs():
    analysis, synthesis = StreamingSTFT(), StreamingISTFT()
    with pytest.raises(ValueError):
        analysis.push(np.zeros(641, np.float32))
    with pytest.raises(ValueError):
        analysis.push(np.array([np.nan], np.float32))
    with pytest.raises(ValueError):
        synthesis.push(np.zeros((1, 2, 256, 1), np.float32))
    with pytest.raises(ValueError):
        synthesis.push(np.full((1, 2, 257, 1), np.inf, np.float32))
    analysis.flush()
    synthesis.flush(0)
    for operation in (lambda: analysis.push([]), analysis.flush,
                      lambda: synthesis.push(np.zeros((1, 2, 257, 0), np.float32)),
                      lambda: synthesis.flush(0)):
        with pytest.raises(RuntimeError):
            operation()


def test_enrollment_frontend_is_bounded_repeatable_and_mean_centered(tmp_path):
    path = tmp_path / "constants.npz"
    np.savez(path, mel=np.ones((80, 257), np.float32), hamming=np.hamming(400).astype(np.float32))
    frontend = TseEnrollmentFrontend(path)
    pcm = np.random.default_rng(1).normal(0, 0.05, 48000).astype(np.float32)
    first = frontend.fbank(pcm)
    np.testing.assert_array_equal(first, frontend.fbank(pcm))
    assert first.shape == (1, 298, 80)
    np.testing.assert_allclose(first.mean(axis=1), 0, atol=3e-5)
    for bad in (np.zeros(399), np.zeros(480001), np.zeros((1, 48000))):
        with pytest.raises(ValueError):
            frontend.fbank(bad)
    with pytest.raises(ValueError):
        frontend.fbank(pcm, noise=np.zeros((1, 400)))


def test_frontend_constants_contract(tmp_path):
    path = tmp_path / "constants.npz"
    np.savez(path, mel=np.zeros((1, 257)), hamming=np.zeros(400))
    with pytest.raises(ValueError):
        TseEnrollmentFrontend(path)
    np.savez(path, mel=np.full((80, 257), np.nan), hamming=np.zeros(400))
    with pytest.raises(ValueError):
        TseEnrollmentFrontend(path)


def test_real_frontend_matches_frozen_torchaudio_golden():
    directory = os.environ.get("NEKO_TSE_TEST_ASSET_DIR")
    if not directory:
        pytest.skip("set NEKO_TSE_TEST_ASSET_DIR to local verified models; never download in CI")
    golden_dir = Path(os.environ.get("NEKO_TSE_TEST_GOLDEN_DIR", directory))
    if not (golden_dir / "enrollment_golden.npz").is_file():
        pytest.skip("the local prototype golden fixture was not supplied")
    frontend = TseEnrollmentFrontend(Path(directory) / "tse_frontend_constants.npz")
    with np.load(golden_dir / "enrollment_golden.npz", allow_pickle=False) as golden:
        for name in ("normal", "quiet", "silence"):
            actual = frontend.fbank(golden[f"{name}_pcm"], noise=golden[f"{name}_noise"])
            np.testing.assert_allclose(actual, golden[f"{name}_fb"], atol=2e-5, rtol=2e-4)
