"""Session lifecycle endpoints (P02 minimum).

Exposes just enough surface for the browser to prove the single-active-
session model works end to end:

- ``POST   /api/session``        create a fresh session (+ sandbox)
- ``GET    /api/session``        inspect the current session
- ``DELETE /api/session``        tear it down (ConfigManager restored)
- ``GET    /api/session/state``  compact state for UI polling

Later phases extend this router with save/load/reset/rewind/snapshot
endpoints per ``PLAN.md``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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
