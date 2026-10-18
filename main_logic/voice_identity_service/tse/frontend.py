"""NumPy frontends matching the frozen REAL-TSE ONNX conversion.

All methods are synchronous and belong on the inference worker. Separation uses
centered 512-sample periodic-Hann windows and 128-sample hops. Short streams use
repeated reflection (one sample uses edge padding), an explicit extension of
the upstream frontend which rejects streams shorter than 257 samples.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .contracts import pcm_float32

WINDOW_SIZE = 512
HOP_SIZE = 128
CENTER = WINDOW_SIZE // 2
WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(WINDOW_SIZE) / WINDOW_SIZE)).astype(np.float32)
WINDOW.flags.writeable = False
WINDOW_SQUARED = WINDOW.astype(np.float64) ** 2
WINDOW_SQUARED.flags.writeable = False


def empty_spectrum() -> np.ndarray:
    return np.empty((1, 2, CENTER + 1, 0), dtype=np.float32)


class TseEnrollmentFrontend:
    """Kaldi fbank with frozen mel/Hamming constants and deterministic dither.

The dither has standard deviation 1 on PCM * 32768. A local fixed seed makes
the same enrollment repeatable without sharing a mutable random generator.
"""

    def __init__(self, constants_path: Path) -> None:
        with np.load(constants_path, allow_pickle=False) as constants:
            self._mel = np.array(constants["mel"], dtype=np.float32, copy=True)
            self._hamming = np.array(constants["hamming"], dtype=np.float32, copy=True)
        if self._mel.shape != (80, 257) or self._hamming.shape != (400,):
            raise ValueError("incompatible TSE frontend constants")
        if not np.isfinite(self._mel).all() or not np.isfinite(self._hamming).all():
            raise ValueError("nonfinite TSE frontend constants")

    def fbank(self, pcm: np.ndarray, *, noise: np.ndarray | None = None) -> np.ndarray:
        samples = pcm_float32(pcm)
        # Registration supplies three-second segments. Bound accidental requests
        # before making the overlapping frame matrix; a validation clip is not a reference.
        if not 400 <= samples.size <= 30 * 16000:
            raise ValueError("TSE enrollment requires 25 ms to 30 s of PCM")
        samples = samples * np.float32(32768)
        frames = np.lib.stride_tricks.sliding_window_view(samples, 400)[::160].copy()
        if noise is None:
            noise = np.random.default_rng(703).standard_normal(frames.shape, dtype=np.float32)
        else:
            noise = np.asarray(noise, dtype=np.float32)
            if noise.shape != frames.shape or not np.isfinite(noise).all():
                raise ValueError("invalid enrollment dither")
        frames += noise
        frames -= frames.mean(axis=1, keepdims=True, dtype=np.float32)
        previous = np.concatenate((frames[:, :1], frames[:, :-1]), axis=1)
        frames -= np.float32(0.97) * previous
        spectrum = np.fft.rfft(frames * self._hamming, n=512, axis=1)
        power = (spectrum.real ** 2 + spectrum.imag ** 2).astype(np.float32)
        energies = power @ self._mel.T
        features = np.log(np.maximum(energies, np.finfo(np.float32).eps)).astype(np.float32)
        features -= features.mean(axis=0, keepdims=True, dtype=np.float32)
        if not np.isfinite(features).all():
            raise ValueError("nonfinite TSE enrollment features")
        return np.ascontiguousarray(features[None])


class StreamingSTFT:
    """Bounded overlap buffer; input PCM is supplied in at most 640-sample blocks."""

    def __init__(self) -> None:
        self._buffer = np.empty(0, np.float32)
        self._started = False
        self._closed = False
        self.total_samples = 0

    def _frames(self) -> np.ndarray:
        if self._buffer.size < WINDOW_SIZE:
            return empty_spectrum()
        frames = np.lib.stride_tricks.sliding_window_view(self._buffer, WINDOW_SIZE)[::HOP_SIZE]
        spectrum = np.fft.rfft(frames * WINDOW, axis=-1)
        self._buffer = self._buffer[len(frames) * HOP_SIZE:].copy()
        return np.stack((spectrum.real.T, spectrum.imag.T), axis=0)[None].astype(np.float32)

    def push(self, pcm: np.ndarray) -> np.ndarray:
        if self._closed:
            raise RuntimeError("TSE analysis is closed")
        samples = pcm_float32(pcm)
        if samples.size > 640:
            raise ValueError("TSE frontend block exceeds 40 ms")
        self.total_samples += samples.size
        self._buffer = np.concatenate((self._buffer, samples))
        if not self._started:
            if self._buffer.size <= CENTER:
                return empty_spectrum()
            self._buffer = np.concatenate((self._buffer[1:CENTER + 1][::-1], self._buffer))
            self._started = True
        return self._frames()

    def flush(self) -> np.ndarray:
        if self._closed:
            raise RuntimeError("TSE analysis is closed")
        self._closed = True
        if not self.total_samples:
            return empty_spectrum()
        if not self._started:
            mode = "reflect" if self.total_samples > 1 else "edge"
            self._buffer = np.pad(self._buffer, (CENTER, CENTER), mode=mode)
            self._started = True
        else:
            self._buffer = np.concatenate((self._buffer, self._buffer[-CENTER - 1:-1][::-1]))
        result = self._frames()
        self._buffer = np.empty(0, np.float32)
        return result


class StreamingISTFT:
    """Overlap-add output retains original time, including an explicit tail length."""

    def __init__(self) -> None:
        self._numerator = np.zeros(WINDOW_SIZE, np.float64)
        self._denominator = np.zeros(WINDOW_SIZE, np.float64)
        self._skip = CENTER
        self._closed = False
        self.emitted_samples = 0

    def _emit(self, count: int) -> np.ndarray:
        output = self._numerator[:count] / np.maximum(self._denominator[:count], 1e-20)
        self._numerator = np.concatenate((self._numerator[count:], np.zeros(count)))
        self._denominator = np.concatenate((self._denominator[count:], np.zeros(count)))
        skip = min(self._skip, count)
        self._skip -= skip
        output = output[skip:].astype(np.float32)
        self.emitted_samples += output.size
        return output

    def push(self, spectrum: np.ndarray) -> np.ndarray:
        if self._closed:
            raise RuntimeError("TSE synthesis is closed")
        values = np.asarray(spectrum)
        if values.shape[:3] != (1, 2, CENTER + 1) or values.ndim != 4:
            raise ValueError("invalid TSE output spectrum")
        if not np.isfinite(values).all():
            raise ValueError("nonfinite TSE output spectrum")
        complex_frames = values[0, 0] + 1j * values[0, 1]
        wave_frames = np.fft.irfft(complex_frames.T, n=WINDOW_SIZE, axis=-1)
        pieces = []
        for frame in wave_frames:
            self._numerator += frame * WINDOW
            self._denominator += WINDOW_SQUARED
            pieces.append(self._emit(HOP_SIZE))
        return np.concatenate(pieces) if pieces else np.empty(0, np.float32)

    def flush(self, total_samples: int) -> np.ndarray:
        if self._closed:
            raise RuntimeError("TSE synthesis is closed")
        remaining = total_samples - self.emitted_samples
        if remaining < 0 or remaining + self._skip > WINDOW_SIZE:
            raise ValueError("TSE synthesis length mismatch")
        self._closed = True
        result = self._emit(remaining + self._skip)
        self._numerator.fill(0)
        self._denominator.fill(0)
        return result
