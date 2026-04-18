"""Virtual Clock HTTP surface (P06).

Endpoints are intentionally thin wrappers over :class:`VirtualClock` so the
state machine stays exclusively in :mod:`virtual_clock`. Each mutating
endpoint goes through :meth:`SessionStore.session_operation` — clock writes
are cheap but the lock also doubles as a "no concurrent send + time edit"
guarantee, which matters for :meth:`VirtualClock.consume_pending` semantics
in later phases.

Shape rationale:
    * Most inputs use **seconds** (``int``) rather than ISO duration strings
      because JS ``Number`` is exact for reasonable turn lengths and the UI
      already owns its own parser (``time_utils.js``). Absolute timestamps
      stay ISO-8601 via ``pydantic.AwareDatetime``-free ``datetime`` (we
      keep the test bench naive, matching ``datetime.now()`` upstream).
    * Every response embeds the full ``clock`` dict (``to_dict``) so the
      UI never needs to round-trip a separate GET after a mutation.
    * 404 when no session (same convention as Persona).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from tests.testbench.session_store import (
    SessionConflictError,
    get_session_store,
)

router = APIRouter(prefix="/api/time", tags=["time"])


# ── helpers ──────────────────────────────────────────────────────────


def _require_session():
    """Return active session or raise HTTP 404 (no active session)."""
    session = get_session_store().get()
    if session is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "NoActiveSession",
                "message": "No active session; create one via POST /api/session first.",
            },
        )
    return session


def _snapshot(session) -> dict[str, Any]:
    """Full clock state + session id so the UI can sanity-check who owns it."""
    return {
        "session_id": session.id,
        "clock": session.clock.to_dict(),
    }


def _wrap_conflict(exc: SessionConflictError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error_type": "SessionBusy",
            "message": str(exc),
            "state": exc.state.value,
            "busy_op": exc.busy_op,
        },
    )


# ── request models ───────────────────────────────────────────────────


class BootstrapPayload(BaseModel):
    """Body for ``PUT /api/time/bootstrap``.

    Either / both fields may be provided; omit a field to leave it
    unchanged. Explicit ``null`` clears the stored value (distinct from
    "not provided").
    """

    # Pydantic v2 preserves the difference between "missing" and "null"
    # via :attr:`model_fields_set`, which we use downstream to decide
    # whether a field was intentionally cleared.
    bootstrap_at: datetime | None = None
    initial_last_gap_seconds: int | None = Field(default=None, ge=0)
    sync_cursor: bool = True  # mirror bootstrap_at onto cursor (default UX)


class CursorPayload(BaseModel):
    """Body for ``PUT /api/time/cursor``."""

    absolute: datetime | None  # ``null`` releases cursor → real-time fallback.


class AdvancePayload(BaseModel):
    """Body for ``POST /api/time/advance``."""

    delta_seconds: int = Field(..., description="Positive to move forward, negative to roll back.")


class PerTurnDefaultPayload(BaseModel):
    """Body for ``PUT /api/time/per_turn_default``."""

    seconds: int | None = Field(default=None, ge=0)


class StageNextTurnPayload(BaseModel):
    """Body for ``POST /api/time/stage_next_turn``.

    Provide exactly one of ``delta_seconds`` / ``absolute``; both empty
    clears any existing pending stage (equivalent to DELETE).
    """

    delta_seconds: int | None = None
    absolute: datetime | None = None

    @model_validator(mode="after")
    def _exactly_one_or_none(self) -> "StageNextTurnPayload":
        if self.delta_seconds is not None and self.absolute is not None:
            raise ValueError("Provide either delta_seconds or absolute, not both.")
        return self


# ── read endpoints ───────────────────────────────────────────────────


@router.get("")
async def get_time() -> dict[str, Any]:
    """Full clock snapshot (cursor + bootstrap + per_turn_default + pending)."""
    session = _require_session()
    return _snapshot(session)


@router.get("/cursor")
async def get_cursor() -> dict[str, Any]:
    """Compact "live now" read for frequent polling (1 Hz UI tick).

    Keeping this endpoint small + cheap means the Virtual Clock page can
    refresh without thrashing the whole ``/api/time`` payload.
    """
    session = _require_session()
    now = session.clock.now()
    return {
        "session_id": session.id,
        "now": now.isoformat(),
        "is_real_time": session.clock.cursor is None,
    }


# ── mutation endpoints ───────────────────────────────────────────────


@router.put("/cursor")
async def set_cursor(body: CursorPayload) -> dict[str, Any]:
    """Pin the live cursor, or pass ``null`` to release back to real time."""
    store = get_session_store()
    try:
        async with store.session_operation("time.set_cursor") as session:
            session.clock.set_now(body.absolute)
            return _snapshot(session)
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


@router.post("/advance")
async def advance_cursor(body: AdvancePayload) -> dict[str, Any]:
    """Shift the live cursor by ``delta_seconds`` (can be negative)."""
    store = get_session_store()
    try:
        async with store.session_operation("time.advance") as session:
            session.clock.advance(timedelta(seconds=body.delta_seconds))
            return _snapshot(session)
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


@router.put("/bootstrap")
async def set_bootstrap(body: BootstrapPayload) -> dict[str, Any]:
    """Partial update of session-start bootstrap metadata.

    Uses :attr:`pydantic.BaseModel.model_fields_set` to distinguish "field
    not provided" from explicit ``null``, so the UI can clear just one of
    the two knobs independently.
    """
    store = get_session_store()
    # Capture the client's intent BEFORE entering the lock — cheap, and
    # keeps the critical section obvious.
    set_fields = body.model_fields_set
    try:
        async with store.session_operation("time.set_bootstrap") as session:
            kwargs: dict[str, Any] = {"sync_cursor": body.sync_cursor}
            if "bootstrap_at" in set_fields:
                kwargs["bootstrap_at"] = body.bootstrap_at
            if "initial_last_gap_seconds" in set_fields:
                kwargs["initial_last_gap_seconds"] = body.initial_last_gap_seconds
            session.clock.set_bootstrap(**kwargs)
            return _snapshot(session)
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


@router.put("/per_turn_default")
async def set_per_turn_default(body: PerTurnDefaultPayload) -> dict[str, Any]:
    """Set (or clear with ``null``) the default "+ Δt" applied each turn."""
    store = get_session_store()
    try:
        async with store.session_operation("time.per_turn_default") as session:
            session.clock.set_per_turn_default(body.seconds)
            return _snapshot(session)
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


@router.post("/stage_next_turn")
async def stage_next_turn(body: StageNextTurnPayload) -> dict[str, Any]:
    """Stage the next turn's time (delta or absolute); both empty clears."""
    store = get_session_store()
    try:
        async with store.session_operation("time.stage_next_turn") as session:
            session.clock.stage_next_turn(
                delta=(
                    timedelta(seconds=body.delta_seconds)
                    if body.delta_seconds is not None else None
                ),
                absolute=body.absolute,
            )
            return _snapshot(session)
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


@router.delete("/stage_next_turn")
async def clear_stage_next_turn() -> dict[str, Any]:
    """Explicit "clear pending" — nicer REST semantics than POST with empty body."""
    store = get_session_store()
    try:
        async with store.session_operation("time.clear_stage") as session:
            session.clock.clear_pending()
            return _snapshot(session)
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


@router.post("/reset")
async def reset_clock() -> dict[str, Any]:
    """Forget bootstrap + cursor + per-turn default + pending (back to real time).

    The session itself stays alive; only the clock goes back to fresh-ctor
    state. Use the session-level Reset for "also nuke messages/memory".
    """
    store = get_session_store()
    try:
        async with store.session_operation("time.reset") as session:
            session.clock.reset()
            return _snapshot(session)
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc
