"""Numpy-only Whisper log-mel preprocessing used by Smart Turn v3."""

from __future__ import annotations

import numpy as np


def _hertz_to_mel(frequencies: np.ndarray) -> np.ndarray:
    frequencies = np.asarray(frequencies, dtype=np.float64)
    min_log_hertz = 1000.0
    min_log_mel = 15.0
    logstep = np.log(6.4) / 27.0
    mels = frequencies / (200.0 / 3.0)
    return np.where(
        frequencies >= min_log_hertz,
        min_log_mel + np.log(np.maximum(frequencies, min_log_hertz) / min_log_hertz) / logstep,
        mels,
    )


def _mel_to_hertz(mels: np.ndarray) -> np.ndarray:
    mels = np.asarray(mels, dtype=np.float64)
    min_log_hertz = 1000.0
    min_log_mel = 15.0
    logstep = np.log(6.4) / 27.0
    frequencies = (200.0 / 3.0) * mels
    return np.where(
        mels >= min_log_mel,
        min_log_hertz * np.exp(logstep * (mels - min_log_mel)),
        frequencies,
    )


def whisper_mel_filter_bank() -> np.ndarray:
    """Return the Slaney-normalized 201x80 bank used by Whisper."""

    fft_frequencies = np.linspace(0.0, 8000.0, 201)
    mel_min, mel_max = _hertz_to_mel(np.asarray([0.0, 8000.0]))
    filter_frequencies = _mel_to_hertz(np.linspace(mel_min, mel_max, 82))
    filter_diff = np.diff(filter_frequencies)
    slopes = filter_frequencies[None, :] - fft_frequencies[:, None]
    down = -slopes[:, :-2] / filter_diff[:-1]
    up = slopes[:, 2:] / filter_diff[1:]
    filters = np.maximum(0.0, np.minimum(down, up))
    filters *= (2.0 / (filter_frequencies[2:82] - filter_frequencies[:80]))[None, :]
    return filters


class WhisperFeatureExtractor:
    SAMPLE_RATE = 16_000
    MAX_SAMPLES = 8 * SAMPLE_RATE
    N_FFT = 400
    HOP_LENGTH = 160
    N_FRAMES = 800

    def __init__(self) -> None:
        self._mel_filters = whisper_mel_filter_bank()
        indices = np.arange(self.N_FFT, dtype=np.float64)
        self._window = 0.5 - 0.5 * np.cos(2 * np.pi * indices / self.N_FFT)
        padded_samples = self.MAX_SAMPLES + self.N_FFT
        full_frames = 1 + (padded_samples - self.N_FFT) // self.HOP_LENGTH
        self._frame_indices = (
            np.arange(self.N_FFT)[None, :]
            + self.HOP_LENGTH * np.arange(full_frames)[:, None]
        )

    def extract(self, audio: np.ndarray) -> np.ndarray:
        """Return `[80, 800]` float32 features for the trailing 8 seconds."""

        values = np.asarray(audio)
        if values.ndim != 1:
            raise ValueError("Smart Turn audio must be mono")
        if values.size > self.MAX_SAMPLES:
            values = values[-self.MAX_SAMPLES :]
        elif values.size < self.MAX_SAMPLES:
            values = np.pad(values, (self.MAX_SAMPLES - values.size, 0))
        values = values.astype(np.float64)
        values = (values - values.mean()) / np.sqrt(values.var() + 1e-7)
        centered = np.pad(values, (self.N_FFT // 2, self.N_FFT // 2), mode="reflect")
        frames = centered[self._frame_indices] * self._window[None, :]
        spectrum = np.fft.rfft(frames, n=self.N_FFT, axis=1)
        power = (spectrum.real**2 + spectrum.imag**2).T
        mel_spectrum = self._mel_filters.T @ power
        log_spectrum = np.log10(np.clip(mel_spectrum, 1e-10, None))[:, :-1]
        log_spectrum = np.maximum(log_spectrum, log_spectrum.max() - 8.0)
        return ((log_spectrum + 4.0) / 4.0).astype(np.float32)
