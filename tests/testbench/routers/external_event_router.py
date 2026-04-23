"""External-event simulation router (P25 §3 Day 1).

Endpoints
---------
* ``POST /api/session/external-event`` — simulate one event of the three
  kinds (avatar / agent_callback / proactive). Body:
  ``{kind, payload, mirror_to_recent?}``. Long-running (LLM round trip);
  holds the per-session BUSY lock for the duration so a concurrent
  ``/chat/send`` or ``auto_dialog/start`` cannot corrupt interleaved
  messages. Returns :class:`SimulationResult.to_dict`.
* ``GET /api/session/external-event/dedupe-info`` — read-only snapshot
  of the avatar dedupe cache. Cheap; does not acquire the session lock.
* ``POST /api/session/external-event/dedupe-reset`` — clear the avatar
  dedupe cache and rearm the overflow notice. Mutating; takes the BUSY
  lock briefly.

Design notes
------------
* **BUSY lock, never AbortController (L19)**: the simulation appends
  several ``session.messages`` records and may write to ``recent.json``.
  Allowing the browser to abort mid-call would leave some of those
  writes applied and others not — the exact footgun L19 flags. We hold
  the lock for the full LLM round trip (up to a minute) and rely on
  upstream ``timeout`` to bound it rather than a client-driven abort.
* **Single entry point for three kinds**: keeps API surface small and
  makes the UI tab switcher trivial (one fetch helper shared across
  three forms). See P25_BLUEPRINT §2.6 "三类一个抽象".
* **Frontend mutation rule (L19) enforcement is the UI's job** —
  see :mod:`static/ui/chat/external_events_panel.js` Day 2 for the
  no-AbortController convention on the three Simulate buttons.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tests.testbench.pipeline.external_events import (
    SimulationKind,
    peek_dedupe_info,
    reset_dedupe,
    simulate_agent_callback,
    simulate_avatar_interaction,
    simulate_proactive,
)
from tests.testbench.session_store import (
    SessionConflictError,
    SessionState,
    get_session_store,
)

router = APIRouter(prefix="/api/session", tags=["external-event"])


# ── request / response schemas ───────────────────────────────────────


class _ExternalEventRequest(BaseModel):
    """POST /api/session/external-event body."""

    kind: str = Field(
        ...,
        description=(
            "事件类型; 必须是 avatar / agent_callback / proactive 之一."
        ),
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "事件 payload, 结构随 kind 变化 (详见 SimulationResult 文档 + "
            "P25_BLUEPRINT §2.1). avatar 需 {interaction_id, tool_id, "
            "action_id, target='avatar', intensity?, ...}; agent_callback "
            "需 {callbacks: [str | {text:str}, ...]}; proactive 需 "
            "{kind: home|screenshot|window|news|video|personal|music}."
        ),
    )
    mirror_to_recent: bool = Field(
        default=False,
        description=(
            "P25_BLUEPRINT §2.4 opt-in. 勾上则把本次事件产出的 memory "
            "pair 额外写入 memory/recent.json (默认只写 session.messages)."
        ),
    )


class _DedupeResetResponse(BaseModel):
    cleared: int = Field(..., description="清除前 cache 中的条目数.")


# ── helpers ──────────────────────────────────────────────────────────


def _session_conflict_to_http(exc: SessionConflictError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error_type": "SessionConflict",
            "message": str(exc),
            "state": exc.state.value,
            "busy_op": exc.busy_op,
        },
    )


def _lookup_error_to_http(exc: LookupError) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error_type": "NoActiveSession", "message": str(exc)},
    )


def _parse_kind(raw: str) -> SimulationKind:
    try:
        return SimulationKind(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "InvalidKind",
                "message": (
                    f"kind={raw!r} 不受支持; 合法值: "
                    f"{sorted(k.value for k in SimulationKind)}."
                ),
            },
        ) from exc


# ── endpoints ────────────────────────────────────────────────────────


@router.post("/external-event")
async def post_external_event(body: _ExternalEventRequest) -> dict[str, Any]:
    """Run one simulation handler end-to-end and return a ``SimulationResult``.

    Holds the per-session BUSY lock for the full duration. Client callers
    MUST NOT attach an ``AbortController`` (P25 L19): aborting midway
    would leave ``session.messages`` / ``recent.json`` partially written.
    """
    kind = _parse_kind(body.kind)

    store = get_session_store()
    try:
        async with store.session_operation(
            f"external_event.{kind.value}",
            state=SessionState.BUSY,
        ) as session:
            if kind == SimulationKind.AVATAR:
                result = await simulate_avatar_interaction(
                    session,
                    body.payload,
                    mirror_to_recent=body.mirror_to_recent,
                )
            elif kind == SimulationKind.AGENT_CALLBACK:
                result = await simulate_agent_callback(
                    session,
                    body.payload,
                    mirror_to_recent=body.mirror_to_recent,
                )
            elif kind == SimulationKind.PROACTIVE:
                result = await simulate_proactive(
                    session,
                    body.payload,
                    mirror_to_recent=body.mirror_to_recent,
                )
            else:  # pragma: no cover — _parse_kind guards above
                raise HTTPException(status_code=400, detail={
                    "error_type": "InvalidKind",
                    "message": f"unexpected kind={kind!r}",
                })
            return {"kind": kind.value, "result": result.to_dict()}
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise _lookup_error_to_http(exc) from exc


@router.get("/external-event/dedupe-info")
async def get_dedupe_info() -> dict[str, Any]:
    """Read-only cache snapshot; does NOT acquire the session lock.

    Cheap enough to poll from the UI while a simulation is in flight,
    which is the whole point — tester wants to see "the cache has 3
    entries, the one I just posted is at ts=...".
    """
    store = get_session_store()
    session = store.get()
    if session is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "NoActiveSession",
                "message": "No active session; create one via POST /api/session first.",
            },
        )
    return {"kind": "avatar", "info": peek_dedupe_info(session)}


@router.post("/external-event/dedupe-reset", response_model=_DedupeResetResponse)
async def post_dedupe_reset() -> _DedupeResetResponse:
    """Clear the avatar dedupe cache for the active session.

    Takes the BUSY lock briefly; tester usually hits this only while no
    simulation is running, but the lock is cheap and avoids a subtle race
    where a clear interleaves with a should_persist call from a concurrent
    simulation and resurrects a just-evicted key.
    """
    store = get_session_store()
    try:
        async with store.session_operation(
            "external_event.dedupe_reset",
            state=SessionState.BUSY,
        ) as session:
            summary = reset_dedupe(session)
            return _DedupeResetResponse(cleared=int(summary.get("cleared", 0)))
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise _lookup_error_to_http(exc) from exc


__all__ = ["router"]
