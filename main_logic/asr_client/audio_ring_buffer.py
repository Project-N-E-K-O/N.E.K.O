"""Bounded in-memory PCM16 buffers for pre-roll and connection wake-up."""

from __future__ import annotations


class AudioRingBuffer:
    """Retain the newest fixed-duration mono PCM16 audio without disk writes."""

    def __init__(self, *, capacity_ms: int, sample_rate_hz: int = 16_000) -> None:
        if capacity_ms <= 0:
            raise ValueError("capacity_ms must be positive")
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        self._sample_rate_hz = sample_rate_hz
        self._capacity_bytes = sample_rate_hz * 2 * capacity_ms // 1_000
        self._capacity_bytes -= self._capacity_bytes % 2
        if self._capacity_bytes <= 0:
            raise ValueError("capacity_ms is too small for the sample rate")
        self._audio = bytearray()

    @property
    def duration_ms(self) -> int:
        return len(self._audio) * 1_000 // (self._sample_rate_hz * 2)

    @property
    def sample_rate_hz(self) -> int:
        return self._sample_rate_hz

    def append(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int | None = None,
    ) -> bytes:
        if not isinstance(pcm16, bytes):
            raise TypeError("PCM16 audio must be bytes")
        if len(pcm16) % 2:
            raise ValueError("PCM16 audio must contain complete samples")
        effective_rate = sample_rate_hz or self._sample_rate_hz
        if effective_rate != self._sample_rate_hz:
            raise ValueError("audio sample rate does not match the ring buffer")
        if not pcm16:
            return b""

        self._audio.extend(pcm16)
        overflow = len(self._audio) - self._capacity_bytes
        if overflow <= 0:
            return b""
        overflow += overflow % 2
        dropped = bytes(self._audio[:overflow])
        del self._audio[:overflow]
        return dropped

    def peek(self) -> bytes:
        return bytes(self._audio)

    def drain(self) -> bytes:
        payload = bytes(self._audio)
        self._audio.clear()
        return payload

    def clear(self) -> None:
        self._audio.clear()
