"""Bounded buffering for already-normalized 16 kHz mono PCM16 audio."""

from __future__ import annotations

from collections import deque
from threading import Lock

import numpy as np


class Pcm16RingBuffer:
    """A chunked, bounded ring that copies only when a snapshot is requested."""

    SAMPLE_RATE = 16_000
    SAMPLE_WIDTH_BYTES = 2

    def __init__(self, max_seconds: float = 8.0) -> None:
        if max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        self._capacity_bytes = int(max_seconds * self.SAMPLE_RATE) * self.SAMPLE_WIDTH_BYTES
        self._chunks: deque[bytes] = deque()
        self._size_bytes = 0
        self._lock = Lock()

    @property
    def capacity_samples(self) -> int:
        return self._capacity_bytes // self.SAMPLE_WIDTH_BYTES

    @property
    def sample_count(self) -> int:
        with self._lock:
            return self._size_bytes // self.SAMPLE_WIDTH_BYTES

    def append(self, pcm16_le: bytes | bytearray | memoryview) -> None:
        chunk = bytes(pcm16_le)
        if len(chunk) % self.SAMPLE_WIDTH_BYTES:
            raise ValueError("PCM16 input must contain complete little-endian samples")
        if not chunk:
            return
        with self._lock:
            if len(chunk) >= self._capacity_bytes:
                self._chunks.clear()
                self._chunks.append(chunk[-self._capacity_bytes :])
                self._size_bytes = self._capacity_bytes
                return
            self._chunks.append(chunk)
            self._size_bytes += len(chunk)
            overflow = self._size_bytes - self._capacity_bytes
            while overflow > 0:
                oldest = self._chunks[0]
                if len(oldest) <= overflow:
                    self._chunks.popleft()
                    self._size_bytes -= len(oldest)
                    overflow -= len(oldest)
                    continue
                trim = overflow + (overflow % self.SAMPLE_WIDTH_BYTES)
                self._chunks[0] = oldest[trim:]
                self._size_bytes -= trim
                overflow = 0

    def snapshot_bytes(self) -> bytes:
        with self._lock:
            return b"".join(self._chunks)

    def snapshot_float32(self) -> np.ndarray:
        pcm = self.snapshot_bytes()
        if not pcm:
            return np.empty(0, dtype=np.float32)
        return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0

    def reset(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._size_bytes = 0
