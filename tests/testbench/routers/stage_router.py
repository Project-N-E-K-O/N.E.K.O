"""Stage Coach HTTP surface (P14).

五个端点, 全部薄封装 :mod:`pipeline.stage_coordinator`:

* ``GET   /api/stage``            — 当前阶段 + 推荐 op + 上下文快照 + 历史
* ``POST  /api/stage/preview``    — 一律 412 PreviewUnsupported (stage 层
                                     不包 dry-run; memory op 去 Setup →
                                     Memory 点 Trigger, evaluation op
                                     去 Evaluation → Run 子页或直接调
                                     ``/api/judge/run`` {persist:false})
* ``POST  /api/stage/advance``    — 推进到下一阶段 (循环: evaluation →
                                     chat_turn), 只改 stage_state, 不跑副作用
* ``POST  /api/stage/skip``       — 同 advance 但 history 标 ``skipped=true``
* ``POST  /api/stage/rewind``     — 跳到任意合法阶段, 历史保留不截断

PLAN §P14: **Stage Coach 永远只建议 + 预览 + 等确认, 绝不自动跑任何副作用**.
因此 stage 的切换**只能**通过本 router 显式触发 — ``/chat/send`` /
``/chat/auto_dialog/start`` / ``/chat/script/*`` 等业务端点不会自动把
stage 从 chat_turn 推到 post_turn_memory_update; 测试人员必须点 UI 按钮.

锁粒度 (与 P04/P06/P10 对齐):
* GET /stage: 不进 session_operation — 纯读快照, 允许与任何 BUSY op 并发观测
* POST advance/skip/rewind: 进 session_operation(BUSY), 与 /chat/send 等
  互斥 (避免在 stream 中间切 stage 造成 history 错位), 持锁时间极短
* POST preview: 同 GET, 不进 session_operation (P14 里它必然直接 412)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tests.testbench.pipeline.snapshot_store import capture_safe as _snapshot_capture
from tests.testbench.pipeline.stage_coordinator import (
    STAGE_ORDER,
    StageError,
    advance,
    describe_stage,
    preview_suggested_op,
    rewind,
    skip,
)
from tests.testbench.session_store import (
    SessionConflictError,
    SessionState,
    get_session_store,
)

router = APIRouter(prefix="/api/stage", tags=["stage"])


# ── helpers ──────────────────────────────────────────────────────────


def _require_session():
    session = get_session_store().get()
    if session is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "NoActiveSession",
                "message": "No active session; POST /api/session to create one.",
            },
        )
    return session


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


def _wrap_stage_error(exc: StageError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={
            "error_type": exc.code,
            "message": exc.message,
        },
    )


# ── request models ───────────────────────────────────────────────────


class _PreviewBody(BaseModel):
    op_id: str | None = None


class _AdvanceBody(BaseModel):
    op_id: str | None = None
    note: str | None = Field(default=None, max_length=500)


class _SkipBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class _RewindBody(BaseModel):
    target_stage: str
    note: str | None = Field(default=None, max_length=500)


# ── endpoints ────────────────────────────────────────────────────────


@router.get("")
async def get_stage() -> dict[str, Any]:
    """Read-only: current stage + suggested op + context snapshot + history.

    Does **not** go through ``session_operation`` — the returned snapshot
    is "best effort at read time"; it's safe to call while a BUSY op
    (chat/send, auto_dialog) is in flight and the UI refreshes the chip
    after the op ends via the ``session:change`` event bus anyway.
    """
    session = _require_session()
    return describe_stage(session)


@router.post("/preview")
async def stage_preview(body: _PreviewBody) -> dict[str, Any]:
    """Always 412 PreviewUnsupported (see stage_coordinator docstring).

    Kept as an endpoint rather than omitted so the frontend can call it
    uniformly; per-stage dry-run entries live in their own routers
    (``/api/memory/trigger/{op}`` for memory, ``/api/judge/run``
    with ``persist=False`` for evaluation).
    """
    session = _require_session()
    try:
        return preview_suggested_op(session, body.op_id)
    except StageError as exc:
        raise _wrap_stage_error(exc) from exc


@router.post("/advance")
async def stage_advance(body: _AdvanceBody) -> dict[str, Any]:
    """Move to the next stage. No data side-effect (messages/memory stay put)."""
    store = get_session_store()
    try:
        async with store.session_operation(
            "stage.advance",
            state=SessionState.BUSY,
        ) as session:
            try:
                result = advance(session, op_id=body.op_id, note=body.note)
            except StageError as exc:
                raise _wrap_stage_error(exc) from exc
            _snapshot_capture(session, trigger="stage_advance")
            return result
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


@router.post("/skip")
async def stage_skip(body: _SkipBody) -> dict[str, Any]:
    """Move to the next stage; history entry marked ``skipped=True``."""
    store = get_session_store()
    try:
        async with store.session_operation(
            "stage.skip",
            state=SessionState.BUSY,
        ) as session:
            try:
                result = skip(session, note=body.note)
            except StageError as exc:
                raise _wrap_stage_error(exc) from exc
            _snapshot_capture(session, trigger="stage_advance")
            return result
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


@router.post("/rewind")
async def stage_rewind(body: _RewindBody) -> dict[str, Any]:
    """Jump to an arbitrary stage.

    **Does not undo data side-effects** — messages / memory / virtual
    clock stay put; only the UI pointer moves. For true data rewind
    use P18's snapshot/rewind system (not yet implemented).
    """
    if body.target_stage not in STAGE_ORDER:
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "UnknownStage",
                "message": (
                    f"未知阶段: {body.target_stage!r}; "
                    f"合法值: {', '.join(STAGE_ORDER)}"
                ),
            },
        )
    store = get_session_store()
    try:
        async with store.session_operation(
            "stage.rewind",
            state=SessionState.BUSY,
        ) as session:
            try:
                result = rewind(session, body.target_stage, note=body.note)
            except StageError as exc:
                raise _wrap_stage_error(exc) from exc
            _snapshot_capture(session, trigger="stage_rewind")
            return result
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc
