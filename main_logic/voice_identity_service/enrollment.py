"""Minimal PCM validation for one-shot local Owner voice enrollment."""

from __future__ import annotations

import math

import numpy as np


ENROLLMENT_SAMPLE_RATE_HZ = 16_000
ENROLLMENT_MINIMUM_AUDIO_MS = 1_500
ENROLLMENT_TARGET_AUDIO_MS = 4_000
ENROLLMENT_MINIMUM_PCM_BYTES = (
    ENROLLMENT_SAMPLE_RATE_HZ * ENROLLMENT_MINIMUM_AUDIO_MS // 1_000 * 2
)
ENROLLMENT_MAXIMUM_PCM_BYTES = (
    ENROLLMENT_SAMPLE_RATE_HZ * ENROLLMENT_TARGET_AUDIO_MS // 1_000 * 2
)
_FRAME_SAMPLES = 320
_ACTIVE_FRAME_RMS = 0.008
_MAX_CLIPPED_SAMPLE_RATIO = 0.05


class EnrollmentAudioError(ValueError):
    """A stable, UI-safe rejection reason for enrollment PCM."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def validate_enrollment_pcm16(pcm16: bytes) -> None:
    """Accept usable 16 kHz mono PCM16 without retaining derived samples."""

    if type(pcm16) is not bytes or len(pcm16) % 2:
        raise EnrollmentAudioError("invalid_pcm")
    if len(pcm16) < ENROLLMENT_MINIMUM_PCM_BYTES:
        raise EnrollmentAudioError("speech_too_short")
    if len(pcm16) > ENROLLMENT_MAXIMUM_PCM_BYTES:
        raise EnrollmentAudioError("audio_too_long")

    samples: np.ndarray | None = None
    normalized: np.ndarray | None = None
    frames: np.ndarray | None = None
    try:
        samples = np.frombuffer(pcm16, dtype="<i2")
        if samples.size == 0:
            raise EnrollmentAudioError("silence")
        clipped = np.count_nonzero(np.abs(samples.astype(np.int32)) >= 32_760)
        if clipped / samples.size > _MAX_CLIPPED_SAMPLE_RATIO:
            raise EnrollmentAudioError("severe_clipping")

        complete_samples = samples.size - samples.size % _FRAME_SAMPLES
        if complete_samples < _FRAME_SAMPLES:
            raise EnrollmentAudioError("speech_too_short")
        normalized = samples[:complete_samples].astype(np.float32)
        normalized /= np.float32(32_768.0)
        frames = normalized.reshape(-1, _FRAME_SAMPLES)
        rms = np.sqrt(
            np.mean(frames * frames, axis=1, dtype=np.float32),
            dtype=np.float32,
        )
        active_frames = int(np.count_nonzero(rms >= _ACTIVE_FRAME_RMS))
        required_frames = math.ceil(
            ENROLLMENT_MINIMUM_AUDIO_MS
            / (_FRAME_SAMPLES * 1_000 / ENROLLMENT_SAMPLE_RATE_HZ)
        )
        if active_frames < required_frames:
            raise EnrollmentAudioError("speech_too_short")
    finally:
        if frames is not None:
            frames.fill(0.0)
        if normalized is not None:
            normalized.fill(0.0)
