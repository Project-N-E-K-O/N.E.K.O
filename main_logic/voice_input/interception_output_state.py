"""Synchronous output ordering and segment ownership for interception."""

from dataclasses import dataclass

from main_logic.voice_turn.interception_events import (
    InterceptionOutputEvent,
    InterceptionOutputIdentity,
    InterceptionOutputKind,
)


@dataclass(frozen=True, slots=True)
class _OutputCursor:
    owner: tuple[object, object]
    identity: InterceptionOutputIdentity
    sequence: int
    end_sample: int
    ended: bool


class InterceptionOutputState:
    """Track only event consumption and an open segment, never delivery debt.

    Owners are opaque (bridge, generation) pairs. Core checks authority before
    advancing the cursor and again after downstream awaits; receipt settlement
    remains bound to the original runtime independently of this state.
    """

    def __init__(self) -> None:
        self._cursor: _OutputCursor | None = None
        self._segment_owner: tuple[object, object] | None = None

    def accept_event(
        self, owner: tuple[object, object], event: InterceptionOutputEvent,
    ) -> bool:
        cursor = self._cursor
        if cursor is not None and cursor.owner == owner:
            valid = (
                not cursor.ended and event.identity == cursor.identity
                and event.sequence == cursor.sequence + 1
                and event.start_sample == cursor.end_sample
            )
        else:
            valid = event.sequence == 0 and event.start_sample == 0
        if not valid:
            return False
        self._cursor = _OutputCursor(
            owner, event.identity, event.sequence, event.end_sample,
            event.kind is InterceptionOutputKind.END,
        )
        return True

    def has_open_segment(self, owner: tuple[object, object]) -> bool:
        return self._segment_owner == owner

    def mark_audio_accepted(self, owner: tuple[object, object]) -> None:
        self._segment_owner = owner

    def mark_boundary_applied(self, owner: tuple[object, object]) -> None:
        if self._segment_owner == owner:
            self._segment_owner = None
