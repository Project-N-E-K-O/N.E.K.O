"""Virtual clock used by the testbench session (P06 full rolling-cursor model).

Concepts
--------
The testbench time is **a rolling cursor**, not a static "wall-clock override":

``cursor``
    The current virtual "now". Everything that reads ``datetime.now()`` in
    the prompt pipeline / memory ops MUST go through :meth:`now` instead so
    tests are deterministic. ``None`` means "fall back to real wall time"
    (before Bootstrap is set, or after :meth:`reset`).

``bootstrap_at`` / ``initial_last_gap_seconds``
    Session-start metadata. ``bootstrap_at`` is the "virtual now at session
    creation" (distinct from ``cursor`` because ``cursor`` advances each
    turn). ``initial_last_gap_seconds`` = "last time the user spoke to NEKO
    was X seconds before bootstrap"; used **only** before the first message
    (after the first message, ``session.messages[-1].timestamp`` takes
    authority).

``per_turn_default_seconds``
    Default "+ Δt" applied each turn when the tester hasn't staged a
    specific delta. Consumed by future Auto-Dialog / Scripted loops; Manual
    composer can opt-in via a checkbox.

``pending_advance`` / ``pending_set``
    "Next-turn staged time" — declared by composer ("next turn will happen
    2h from now") or by the time_router endpoint. Consumed at the very
    start of ``/chat/send`` via :meth:`consume_pending`. Mutually exclusive
    at most one of the two is non-None at a time.

Determinism contract
--------------------
* ``now()`` NEVER calls real ``datetime.now()`` once ``cursor`` is set; real
  time only leaks in the very first moment before Bootstrap.
* All mutation methods return the new cursor / relevant value so routers
  can echo the new state back to the client without a second ``to_dict``
  call.
* ``from_dict`` tolerates partial payloads (e.g. existing P02 saves that
  only contain ``cursor``) — missing keys default to "unset", not raise.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def _parse_iso(raw: Any) -> datetime | None:
    """Accept ISO-8601 string, ``None``, or already-``datetime``."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        return datetime.fromisoformat(raw)
    raise TypeError(f"Cannot parse datetime from {type(raw).__name__}")


class _Unset:
    """Sentinel distinguishing "not provided" from explicit ``None``."""
    __slots__ = ()
    def __repr__(self) -> str:  # pragma: no cover
        return "<UNSET>"


_UNSET = _Unset()


class VirtualClock:
    """Controllable replacement for :func:`datetime.now`."""

    def __init__(
        self,
        cursor: datetime | None = None,
        *,
        bootstrap_at: datetime | None = None,
        initial_last_gap_seconds: int | None = None,
        per_turn_default_seconds: int | None = None,
    ) -> None:
        self.cursor: datetime | None = cursor
        self.bootstrap_at: datetime | None = bootstrap_at
        self.initial_last_gap_seconds: int | None = initial_last_gap_seconds
        self.per_turn_default_seconds: int | None = per_turn_default_seconds
        # Pending is **not** part of Bootstrap — it's a composer-level staging
        # that lives one turn at most. Never persisted into characters.json
        # or long-term state; saved sessions do include it though, so a
        # mid-staging crash doesn't lose the tester's input.
        self.pending_advance: timedelta | None = None
        self.pending_set: datetime | None = None

    # ── reading ────────────────────────────────────────────────────

    def now(self) -> datetime:
        """Virtual "now"; falls back to real time when cursor is unset."""
        return self.cursor if self.cursor is not None else datetime.now()

    def gap_to(self, earlier: datetime) -> timedelta:
        """``now() - earlier``; negative when ``earlier`` is in the future."""
        return self.now() - earlier

    # ── cursor mutation ────────────────────────────────────────────

    def set_now(self, dt: datetime | None) -> datetime | None:
        """Pin the cursor to ``dt`` (or release it when ``None``)."""
        self.cursor = dt
        return self.cursor

    def advance(self, delta: timedelta) -> datetime:
        """Move the cursor forward by ``delta`` and return the new value.

        If the cursor was unset, the advance is anchored at real now at the
        moment of the call — so subsequent ``now()`` reads stay stable.
        """
        base = self.now()
        self.cursor = base + delta
        return self.cursor

    # ── bootstrap mutation ─────────────────────────────────────────

    def set_bootstrap(
        self,
        *,
        bootstrap_at: datetime | None | _Unset = _UNSET,
        initial_last_gap_seconds: int | None | _Unset = _UNSET,
        sync_cursor: bool = True,
    ) -> None:
        """Partial update of the bootstrap metadata.

        ``sync_cursor=True`` (default) mirrors the usual UX: setting the
        session start time also moves the live cursor to the same instant
        when there are no messages yet (tester expects "session starts at
        this moment"). Callers that only want to change the "last gap" hint
        can pass ``sync_cursor=False``.
        """
        if bootstrap_at is not _UNSET:
            self.bootstrap_at = bootstrap_at
            if sync_cursor and bootstrap_at is not None:
                self.cursor = bootstrap_at
        if initial_last_gap_seconds is not _UNSET:
            self.initial_last_gap_seconds = initial_last_gap_seconds

    # ── per-turn default ───────────────────────────────────────────

    def set_per_turn_default(self, seconds: int | None) -> None:
        """Default "+ Δt" per turn; ``None`` disables auto-advance."""
        self.per_turn_default_seconds = seconds

    # ── next-turn staging ──────────────────────────────────────────

    def stage_next_turn(
        self,
        *,
        delta: timedelta | None = None,
        absolute: datetime | None = None,
    ) -> None:
        """Declare the next turn's time.

        Exactly one of ``delta`` / ``absolute`` must be provided (both
        ``None`` clears any existing pending). If both are set, ``absolute``
        wins — mirroring the tester's most explicit intent.
        """
        if delta is None and absolute is None:
            self.pending_advance = None
            self.pending_set = None
            return
        if absolute is not None:
            self.pending_set = absolute
            self.pending_advance = None
        else:
            self.pending_advance = delta
            self.pending_set = None

    def clear_pending(self) -> None:
        self.pending_advance = None
        self.pending_set = None

    def consume_pending(self) -> datetime | None:
        """Apply staged time to the cursor; return new cursor (or existing).

        Called at the very start of ``/chat/send``. No-op when nothing is
        staged, in which case the caller may still apply
        ``per_turn_default_seconds`` explicitly.
        """
        if self.pending_set is not None:
            self.cursor = self.pending_set
        elif self.pending_advance is not None:
            self.cursor = self.now() + self.pending_advance
        self.pending_advance = None
        self.pending_set = None
        return self.cursor

    # ── full-reset ─────────────────────────────────────────────────

    def reset(self) -> None:
        """Forget everything; behave as a freshly-constructed clock."""
        self.cursor = None
        self.bootstrap_at = None
        self.initial_last_gap_seconds = None
        self.per_turn_default_seconds = None
        self.pending_advance = None
        self.pending_set = None

    # ── serialization ──────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "cursor": self.cursor.isoformat() if self.cursor else None,
            "bootstrap_at": self.bootstrap_at.isoformat() if self.bootstrap_at else None,
            "initial_last_gap_seconds": self.initial_last_gap_seconds,
            "per_turn_default_seconds": self.per_turn_default_seconds,
            "pending": {
                "advance_seconds": (
                    int(self.pending_advance.total_seconds())
                    if self.pending_advance is not None else None
                ),
                "absolute": self.pending_set.isoformat() if self.pending_set else None,
            },
            "is_real_time": self.cursor is None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "VirtualClock":
        """Rehydrate; tolerates partial payloads (e.g. legacy P02 saves)."""
        if not payload:
            return cls()
        inst = cls(
            cursor=_parse_iso(payload.get("cursor")),
            bootstrap_at=_parse_iso(payload.get("bootstrap_at")),
            initial_last_gap_seconds=payload.get("initial_last_gap_seconds"),
            per_turn_default_seconds=payload.get("per_turn_default_seconds"),
        )
        pending = payload.get("pending") or {}
        if isinstance(pending, dict):
            adv = pending.get("advance_seconds")
            if isinstance(adv, (int, float)):
                inst.pending_advance = timedelta(seconds=int(adv))
            inst.pending_set = _parse_iso(pending.get("absolute"))
        return inst

