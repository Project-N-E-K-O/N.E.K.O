"""Duration-bounded microphone ingress queue."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from main_logic.asr_client.lifecycle_contracts import VoiceIngressToken


@dataclass(frozen=True, slots=True)
class QueuedMicFrame:
    message: dict
    duration_us: int
    source_rate_hz: int
    token: VoiceIngressToken
    received_at: float

    @classmethod
    def from_message(
        cls,
        message: dict,
        *,
        token: VoiceIngressToken,
        received_at: float | None = None,
    ) -> "QueuedMicFrame":
        samples = message.get("data")
        if not isinstance(samples, list):
            raise ValueError("MIC_PCM_SAMPLES_REQUIRED")
        declared_rate_hz = message.get("sample_rate_hz")
        if declared_rate_hz is None:
            source_rate_hz = 48_000 if len(samples) == 480 else 16_000
        elif declared_rate_hz in {16_000, 48_000}:
            source_rate_hz = int(declared_rate_hz)
        else:
            raise ValueError("MIC_SAMPLE_RATE_UNSUPPORTED")
        duration_us = (
            len(samples) * 1_000_000 + source_rate_hz - 1
        ) // source_rate_hz
        return cls(
            message=message,
            duration_us=duration_us,
            source_rate_hz=source_rate_hz,
            token=token,
            received_at=time.monotonic() if received_at is None else received_at,
        )


class AudioDurationQueue:
    """An asyncio queue bounded by both PCM duration and frame count."""

    def __init__(self, *, capacity_us: int, max_frames: int) -> None:
        if capacity_us <= 0 or max_frames <= 0:
            raise ValueError("audio queue limits must be positive")
        self.capacity_us = capacity_us
        self.maxsize = max_frames
        self._duration_us = 0
        self._queue: asyncio.Queue[QueuedMicFrame] = asyncio.Queue(
            maxsize=max_frames
        )

    @property
    def duration_us(self) -> int:
        return self._duration_us

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()

    def can_accept(self, frame: QueuedMicFrame) -> bool:
        return bool(
            self._queue.qsize() < self.maxsize
            and self._duration_us + frame.duration_us <= self.capacity_us
        )

    def put_nowait(self, frame: QueuedMicFrame) -> None:
        if not self.can_accept(frame):
            raise asyncio.QueueFull
        self._queue.put_nowait(frame)
        self._duration_us += frame.duration_us

    async def get(self) -> QueuedMicFrame:
        frame = await self._queue.get()
        self._duration_us -= frame.duration_us
        return frame

    def get_nowait(self) -> QueuedMicFrame:
        frame = self._queue.get_nowait()
        self._duration_us -= frame.duration_us
        return frame

    def task_done(self) -> None:
        self._queue.task_done()
