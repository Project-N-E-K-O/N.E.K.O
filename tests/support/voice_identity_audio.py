from __future__ import annotations

import numpy as np
from main_logic.voice_identity_service.enrollment_audio import EnrollmentAudioNormalizationError


def _pcm() -> bytes:
    return np.full(48_000, 4_000, dtype='<i2').tobytes()


def _verification_pcm(milliseconds: int = 5_000) -> bytes:
    return np.full(48_000 * milliseconds // 1_000, 4_000, dtype='<i2').tobytes()


def _embedding(seed: float = 0.25):
    return np.full(192, seed, dtype=np.float32)


class _AudioNormalizer:
    def __init__(self, nr_enabled: bool, *, failure_code: str | None = None) -> None:
        self.nr_enabled = nr_enabled
        self.failure_code = failure_code
        self.calls: list[tuple[int, int, int]] = []

    async def normalize(self, pcm16: bytes, *, sample_rate_hz: int, target_samples: int) -> bytes:
        self.calls.append((len(pcm16), sample_rate_hz, target_samples))
        if self.failure_code is not None:
            raise EnrollmentAudioNormalizationError(self.failure_code)
        assert sample_rate_hz == 48_000
        assert target_samples in (48_000, 80_000)
        required_bytes = target_samples * 2
        if len(pcm16) < required_bytes:
            raise EnrollmentAudioNormalizationError('speech_too_short')
        return pcm16[:required_bytes]


__all__ = ['_AudioNormalizer', '_pcm', '_verification_pcm', '_embedding']