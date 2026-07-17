"""Aggregate physical segmented-ASR responses into one logical user turn."""

from __future__ import annotations


class SegmentAggregator:
    def __init__(self) -> None:
        self._turn_id = 0
        self._segments: dict[int, str] = {}
        self._published = False

    @property
    def turn_id(self) -> int:
        return self._turn_id

    def begin_turn(self) -> int:
        self._turn_id += 1
        self._segments.clear()
        self._published = False
        return self._turn_id

    def add_transcript(
        self,
        turn_id: int,
        segment_id: int,
        text: str,
        *,
        forced_split: bool,
    ) -> str | None:
        if segment_id <= 0:
            raise ValueError("segment_id must be positive")
        if turn_id != self._turn_id or self._published:
            return None
        normalized = " ".join(str(text or "").split())
        if normalized and segment_id not in self._segments:
            self._segments[segment_id] = normalized
        if forced_split:
            return None
        self._published = True
        return " ".join(self._segments[index] for index in sorted(self._segments))

    def clear(self) -> None:
        self._segments.clear()
        self._published = False
