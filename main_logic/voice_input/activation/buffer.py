"""Bounded normalized-audio storage for voice-session activation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .contracts import AudioFrame


class FrameRangeUnavailable(RuntimeError):
    """Raised when a requested candidate is incomplete or already evicted."""


@dataclass(frozen=True, slots=True)
class BufferAppendResult:
    """Details of one accepted append and any capacity eviction."""

    evicted_sequences: tuple[int, ...]


class BoundedAudioFrameBuffer:
    """Keep the newest contiguous PCM frames under time and byte ceilings."""

    def __init__(self, *, max_seconds: float, max_bytes: int) -> None:
        if max_seconds <= 0:
            raise ValueError("VOICE_ACTIVATION_BUFFER_SECONDS_INVALID")
        if max_bytes <= 0:
            raise ValueError("VOICE_ACTIVATION_BUFFER_BYTES_INVALID")
        self._max_seconds = float(max_seconds)
        self._max_bytes = int(max_bytes)
        self._frames: deque[AudioFrame] = deque()
        self._total_bytes = 0
        self._total_seconds = 0.0

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def total_seconds(self) -> float:
        return self._total_seconds

    @property
    def oldest_sequence(self) -> int | None:
        return self._frames[0].sequence if self._frames else None

    @property
    def latest_sequence(self) -> int | None:
        return self._frames[-1].sequence if self._frames else None

    def clear(self) -> None:
        self._frames.clear()
        self._total_bytes = 0
        self._total_seconds = 0.0

    def append(self, frame: AudioFrame) -> BufferAppendResult:
        if self._frames:
            previous = self._frames[-1]
            if frame.sequence != previous.sequence + 1:
                raise ValueError("VOICE_ACTIVATION_BUFFER_SEQUENCE_GAP")
            if frame.sample_start != previous.sample_end:
                raise ValueError("VOICE_ACTIVATION_BUFFER_SAMPLE_GAP")

        self._frames.append(frame)
        self._total_bytes += len(frame.pcm)
        self._total_seconds += frame.duration_seconds
        evicted: list[int] = []
        while (
            self._total_bytes > self._max_bytes
            or self._total_seconds > self._max_seconds + 1e-9
        ):
            removed = self._frames.popleft()
            self._total_bytes -= len(removed.pcm)
            self._total_seconds -= removed.duration_seconds
            evicted.append(removed.sequence)
        return BufferAppendResult(tuple(evicted))

    def get_range(
        self,
        start_sequence: int,
        end_sequence: int,
    ) -> tuple[AudioFrame, ...]:
        if start_sequence < 0 or end_sequence < start_sequence:
            raise ValueError("VOICE_ACTIVATION_BUFFER_RANGE_INVALID")
        selected = tuple(
            frame
            for frame in self._frames
            if start_sequence <= frame.sequence <= end_sequence
        )
        expected_count = end_sequence - start_sequence + 1
        if (
            len(selected) != expected_count
            or not selected
            or selected[0].sequence != start_sequence
            or selected[-1].sequence != end_sequence
        ):
            raise FrameRangeUnavailable("VOICE_ACTIVATION_BUFFER_RANGE_UNAVAILABLE")
        return selected

    def replay_start_sequence(
        self,
        candidate_start_sequence: int,
        *,
        pre_roll_seconds: float,
    ) -> int:
        candidate = self.get_range(
            candidate_start_sequence,
            candidate_start_sequence,
        )[0]
        threshold = candidate.captured_at - max(0.0, pre_roll_seconds)
        for frame in self._frames:
            if frame.captured_end_at > threshold:
                return frame.sequence
        raise FrameRangeUnavailable("VOICE_ACTIVATION_BUFFER_CONTEXT_UNAVAILABLE")
