"""Virtual clock used by the testbench session.

P02 only exposes the **minimum** API needed to wire a session to a
controllable time source (``now`` / ``set_now`` / ``advance``). The full
rolling-cursor model (``bootstrap`` / ``pending_next_turn`` /
``consume_pending`` / ``per_turn_default`` / ``gap_to``) lands in P06 once
the prompt builder actually consumes it.

Keeping this module deliberately small means future work can extend it
without reshaping the public surface; all current callers only rely on
:meth:`VirtualClock.now`.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


class VirtualClock:
    """Controllable replacement for :func:`datetime.now`.

    When ``cursor`` is ``None`` the clock returns real wall time. Once
    :meth:`set_now` or :meth:`advance` is called the cursor takes over and
    every ``now()`` reads deterministically from it.
    """

    def __init__(self, cursor: datetime | None = None) -> None:
        self.cursor: datetime | None = cursor

    # ── reading ────────────────────────────────────────────────────

    def now(self) -> datetime:
        """Return the current virtual time, or real now when unset."""
        return self.cursor if self.cursor is not None else datetime.now()

    # ── mutation ───────────────────────────────────────────────────

    def set_now(self, dt: datetime | None) -> None:
        """Pin the cursor to ``dt`` (or release it when ``None``)."""
        self.cursor = dt

    def advance(self, delta: timedelta) -> datetime:
        """Move the cursor forward by ``delta`` and return the new value.

        If the cursor was unset, the advance is anchored to real now at
        the moment of the call so subsequent reads are stable.
        """
        base = self.now()
        self.cursor = base + delta
        return self.cursor

    # ── serialization ──────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "cursor": self.cursor.isoformat() if self.cursor else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "VirtualClock":
        if not payload:
            return cls()
        raw = payload.get("cursor")
        cursor = datetime.fromisoformat(raw) if raw else None
        return cls(cursor=cursor)
