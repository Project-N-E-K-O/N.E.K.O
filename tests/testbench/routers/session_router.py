"""Session lifecycle endpoints.

Exposes the surface the browser needs for the single-active-session
model:

- ``POST   /api/session``         create a fresh session (+ sandbox)
- ``GET    /api/session``         inspect the current session
- ``DELETE /api/session``         tear it down (ConfigManager restored)
- ``GET    /api/session/state``   compact state for UI polling
- ``POST   /api/session/reset``   three-tier reset (P20)

P20 adds the Reset endpoint; save/load/rewind live elsewhere already
(``snapshot_router`` for rewind, P21 will add persistence).
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tests.testbench.pipeline.reset_runner import reset_session
from tests.testbench.session_store import (
    SessionConflictError,
    SessionState,
    get_session_store,
)

router = APIRouter(prefix="/api/session", tags=["session"])


# ── request / response models ───────────────────────────────────────

class CreateSessionRequest(BaseModel):
    """Body for ``POST /api/session``. All fields optional."""

    name: str | None = Field(
        default=None,
        description="Human-friendly label; defaults to session-<id>.",
        max_length=200,
    )


class SessionStateResponse(BaseModel):
    """Compact state dict returned by ``GET /api/session/state``."""

    has_session: bool
    state: str
    busy_op: str | None = None
    session_id: str | None = None


# ── endpoints ───────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_session(body: CreateSessionRequest | None = None) -> dict[str, Any]:
    """Create (or replace) the single active session.

    Any previously active session is destroyed first — the underlying
    ``ConfigManager`` is a singleton so concurrent sessions are not
    possible. See ``PLAN.md §本期主动不做`` for rationale.
    """
    store = get_session_store()
    try:
        session = await store.create(name=(body.name if body else None))
    except SessionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_type": "SessionConflict",
                "message": str(exc),
                "state": exc.state.value,
                "busy_op": exc.busy_op,
            },
        ) from exc
    return session.describe()


@router.get("")
async def get_session() -> dict[str, Any]:
    """Return the current session description, or a ``has_session=False``
    marker when nothing is active (never 404 — the UI polls this on load).
    """
    store = get_session_store()
    session = store.get()
    if session is None:
        return {"has_session": False}
    return {"has_session": True, **session.describe()}


@router.delete("", status_code=200)
async def delete_session(purge_sandbox: bool = True) -> dict[str, Any]:
    """Destroy the active session and (by default) its sandbox directory."""
    store = get_session_store()
    await store.destroy(purge_sandbox=purge_sandbox)
    return {"ok": True}


class ResetSessionRequest(BaseModel):
    """Body for ``POST /api/session/reset``.

    ``confirm`` must be ``True`` — the UI always sends the second-
    confirmation state explicitly, so the API surface rejects any
    accidental POST without it. This is an intentional 400 vs 200
    split rather than a quiet no-op.
    """

    level: Literal["soft", "medium", "hard"] = Field(
        ...,
        description=(
            "Reset tier. soft = clear messages + eval_results only; "
            "medium = soft + wipe memory files; "
            "hard = wipe sandbox + reset all session state (keeps "
            "model_config and backup snapshots)."
        ),
    )
    confirm: bool = Field(
        default=False,
        description=(
            "UI-side second confirmation. Must be true; rejecting "
            "`confirm=false` prevents accidental reset via curl."
        ),
    )


@router.post("/reset")
async def reset_current_session(body: ResetSessionRequest) -> dict[str, Any]:
    """Perform a three-tier reset on the active session.

    Flow:
      1. Acquire ``session_operation("session.reset", state=RESETTING)``
         so any concurrent chat/send/script waits (and the UI disables
         risky buttons via polled ``/state``).
      2. Inside the lock, :func:`reset_runner.reset_session` captures a
         ``pre_reset_backup`` snapshot, then mutates state per level.
      3. Returns ``{ok: true, stats: {level, removed, preserved,
         pre_reset_backup_id}}`` so the UI can toast exactly what was
         wiped.

    Error codes:
      * 400 if ``confirm != true`` or ``level`` invalid (pydantic
        already covers the latter).
      * 404 if no session active.
      * 409 if another operation already holds the session lock.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail=(
                "confirm must be true — reset is destructive; "
                "UI should gate behind an explicit second confirmation."
            ),
        )

    store = get_session_store()
    if store.get() is None:
        raise HTTPException(status_code=404, detail="no active session")

    try:
        async with store.session_operation(
            "session.reset", state=SessionState.RESETTING,
        ) as session:
            stats = reset_session(session, body.level)
            return {"ok": True, "stats": stats, **session.describe()}
    except SessionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_type": "SessionConflict",
                "message": str(exc),
                "state": exc.state.value,
                "busy_op": exc.busy_op,
            },
        ) from exc


@router.get("/state", response_model=SessionStateResponse)
async def get_session_state() -> SessionStateResponse:
    """Tiny state blob intended for frequent UI polling / top-bar updates."""
    state = get_session_store().get_state()
    # Make sure ``state`` looks like a valid ``SessionState`` value even
    # when no session is active.
    state_str = state.get("state", SessionState.IDLE.value)
    return SessionStateResponse(
        has_session=state["has_session"],
        state=state_str,
        busy_op=state.get("busy_op"),
        session_id=state.get("session_id"),
    )
