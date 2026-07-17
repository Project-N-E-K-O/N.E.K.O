"""Aggregate physical ASR segments into ordered logical user turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Hashable


SegmentId = Hashable


@dataclass(frozen=True, slots=True)
class AggregatedTranscript:
    """One completed logical turn and the physical segments it consumed."""

    turn_id: int
    segment_ids: tuple[SegmentId, ...]
    text: str


@dataclass(slots=True)
class _LogicalTurn:
    segment_ids: list[SegmentId] = field(default_factory=list)
    transcripts: dict[SegmentId, str] = field(default_factory=dict)
    complete: bool = False


class SegmentAggregator:
    """Single source of truth for physical-to-logical transcript assembly."""

    def __init__(self) -> None:
        self._turn_id = 0
        self._next_turn_to_publish = 1
        self._turns: dict[int, _LogicalTurn] = {}
        self._segment_turns: dict[SegmentId, int] = {}

    @property
    def turn_id(self) -> int:
        return self._turn_id

    def begin_turn(self, turn_id: int | None = None) -> int:
        next_turn_id = self._turn_id + 1 if turn_id is None else turn_id
        if next_turn_id <= 0:
            raise ValueError("turn_id must be positive")
        if next_turn_id <= self._turn_id:
            raise ValueError("turn_id must increase monotonically")
        for stale_turn_id, stale_turn in tuple(self._turns.items()):
            if not stale_turn.complete:
                self.discard_turn(stale_turn_id)
        self._turn_id = next_turn_id
        self._turns[next_turn_id] = _LogicalTurn()
        self._next_turn_to_publish = min(self._turns)
        return next_turn_id

    def register_segment(self, turn_id: int, segment_id: SegmentId) -> bool:
        turn = self._turns.get(turn_id)
        if turn is None or turn.complete or segment_id in self._segment_turns:
            return False
        turn.segment_ids.append(segment_id)
        self._segment_turns[segment_id] = turn_id
        return True

    def has_segments(self, turn_id: int) -> bool:
        turn = self._turns.get(turn_id)
        return bool(turn and turn.segment_ids)

    def turn_for_segment(self, segment_id: SegmentId) -> int | None:
        return self._segment_turns.get(segment_id)

    def record_transcript(self, segment_id: SegmentId, text: str) -> bool:
        turn_id = self._segment_turns.get(segment_id)
        if turn_id is None:
            return False
        turn = self._turns.get(turn_id)
        if turn is None or segment_id in turn.transcripts:
            return False
        turn.transcripts[segment_id] = " ".join(str(text or "").split())
        return True

    def complete_turn(self, turn_id: int) -> bool:
        turn = self._turns.get(turn_id)
        if turn is None or not turn.segment_ids:
            return False
        turn.complete = True
        return True

    def collect_ready(self) -> list[AggregatedTranscript]:
        ready: list[AggregatedTranscript] = []
        while True:
            turn = self._turns.get(self._next_turn_to_publish)
            if (
                turn is None
                or not turn.complete
                or any(
                    segment_id not in turn.transcripts
                    for segment_id in turn.segment_ids
                )
            ):
                break
            turn_id = self._next_turn_to_publish
            segment_ids = tuple(turn.segment_ids)
            ready.append(
                AggregatedTranscript(
                    turn_id=turn_id,
                    segment_ids=segment_ids,
                    text=" ".join(
                        turn.transcripts[segment_id]
                        for segment_id in segment_ids
                        if turn.transcripts[segment_id]
                    ),
                )
            )
            for segment_id in segment_ids:
                self._segment_turns.pop(segment_id, None)
            self._turns.pop(turn_id, None)
            self._next_turn_to_publish += 1
        return ready

    def discard_turn(self, turn_id: int) -> None:
        turn = self._turns.pop(turn_id, None)
        if turn is None:
            return
        for segment_id in turn.segment_ids:
            self._segment_turns.pop(segment_id, None)

    def add_transcript(
        self,
        turn_id: int,
        segment_id: SegmentId,
        text: str,
        *,
        forced_split: bool,
    ) -> str | None:
        """Compatibility helper for callers that submit complete segments."""

        if isinstance(segment_id, int) and segment_id <= 0:
            raise ValueError("segment_id must be positive")
        if turn_id != self._turn_id:
            return None
        self.register_segment(turn_id, segment_id)
        if not self.record_transcript(segment_id, text):
            return None
        if forced_split:
            return None
        self.complete_turn(turn_id)
        ready = self.collect_ready()
        return ready[0].text if ready else None

    def clear(self, *, next_turn_id: int | None = None) -> None:
        self._turns.clear()
        self._segment_turns.clear()
        if next_turn_id is not None:
            if next_turn_id <= 0:
                raise ValueError("next_turn_id must be positive")
            self._turn_id = next_turn_id - 1
            self._next_turn_to_publish = next_turn_id
        else:
            self._next_turn_to_publish = self._turn_id + 1
