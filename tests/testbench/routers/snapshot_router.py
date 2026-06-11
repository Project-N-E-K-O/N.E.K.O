"""Snapshot timeline + rewind HTTP surface (P18).

六个端点, 全部薄封装 :class:`pipeline.snapshot_store.SnapshotStore`:

* ``GET    /api/snapshots``                  — 时间线 metadata 列表
* ``POST   /api/snapshots``                  — 手动建一条 (trigger=manual)
* ``GET    /api/snapshots/{id}``             — 单条完整 payload (含 messages/memory_files)
* ``DELETE /api/snapshots/{id}``             — 删除单条
* ``POST   /api/snapshots/{id}/rewind``      — 回退到该快照 (持 REWINDING 锁)
* ``PUT    /api/snapshots/{id}/label``       — 重命名 label

锁粒度 (对齐 stage_router 风格):
* GET 路径不进 session_operation — 只读 metadata/payload, 允许与 BUSY op 并发观测.
* POST /snapshots (manual 建) 进 BUSY 短持锁, 与 chat/send 等互斥, 确保建快照
  时的 messages/memory_files 是一致的瞬时状态.
* DELETE / PUT label 也进 BUSY 短持锁 (写路径应该串行).
* POST /rewind 进 REWINDING 独占锁 — 会 rmtree 沙盒 memory 目录, 绝不能并发.

后端任何业务路由要**自动**建快照的话, 不走本 router, 而是直接调用
``session.snapshot_store.capture(session, trigger=..., label=...)`` —
参考 :mod:`tests.testbench.routers.chat_router` 里各端点末尾的 capture 调用.
本 router 只负责 UI 发起的显式 CRUD + rewind.
"""
from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, HTTPException

from tests.testbench.pipeline.snapshot_store import RewindFileLockedError
from pydantic import BaseModel, Field

from tests.testbench.pipeline.snapshot_store import Snapshot
from tests.testbench.session_store import (
    SessionConflictError,
    SessionState,
    get_session_store,
)

router = APIRouter(prefix="/api/snapshots", tags=["snapshots"])


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


def _snapshot_full_payload(snap: Snapshot, *, include_memory_files: bool) -> dict[str, Any]:
    """Full snapshot dict for GET /snapshots/{id}.

    ``memory_files`` are **opt-out** via ``include_memory_files=False``
    because on a fully-loaded session they can be a few MiB of base64'd
    blob which the UI rarely needs (the detail drawer only wants counts
    + top-level message list). P21 export will opt back in.
    """
    payload = snap.to_json_dict()
    if not include_memory_files:
        file_sizes = {
            relpath: len(base64.b64decode(b64))
            for relpath, b64 in payload.get("memory_files", {}).items()
        }
        payload["memory_files"] = None
        payload["memory_file_sizes"] = file_sizes
    payload["metadata"] = snap.metadata()
    return payload


# ── request models ───────────────────────────────────────────────────


class _ManualCreateBody(BaseModel):
    label: str | None = Field(default=None, max_length=120)


class _UpdateLabelBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)


class _UpdateConfigBody(BaseModel):
    """Body for ``POST /api/snapshots/config``.

    Either or both fields may be provided; unspecified fields keep their
    current value. Input ranges are validated server-side in
    ``SnapshotStore.update_config`` (and mirrored here for early reject).
    """

    max_hot: int | None = Field(default=None, ge=1, le=500)
    debounce_seconds: float | None = Field(default=None, ge=0.0, le=3600.0)


# ── endpoints ────────────────────────────────────────────────────────


@router.get("")
async def list_snapshots() -> dict[str, Any]:
    """Return timeline metadata (oldest → newest).

    Read-only; no session lock. Safe to poll while a BUSY op is in flight
    — the returned list is a snapshot (small irony) of whatever happened
    to be committed when the read landed.
    """
    session = _require_session()
    store = session.snapshot_store
    return {
        "items": store.list_metadata(),
        "max_hot": store.max_hot,
        "debounce_seconds": store.debounce_seconds,
    }


@router.post("/config")
async def update_snapshot_config(body: _UpdateConfigBody) -> dict[str, Any]:
    """Update the hot cap / debounce seconds on the active session.

    Session-scoped: the change applies only to the currently active
    session's ``SnapshotStore``; a future (new) session is created with
    defaults (``DEFAULT_MAX_HOT=30`` / ``DEFAULT_DEBOUNCE_SECONDS=5``).
    This matches the testbench's single-session model where "设置后
    跨 session 继承" semantics would need a global config layer
    (deliberately out of P24 scope per §12.3.A — if needed later, a
    small ``tb_config`` pref or ``POST /api/config/ui_prefs`` endpoint
    can preserve the value).

    Shrinking ``max_hot`` below the current hot snapshot count triggers
    an immediate spill of the excess to cold storage (see
    ``SnapshotStore.update_config``).

    Returns the effective ``{max_hot, debounce_seconds}`` after the
    update — the client can echo these back into its form state without
    an extra GET round trip.

    P24 Day 7 §12.3.A #3 — 2026-04-22 delivery.
    """
    session = _require_session()
    try:
        result = session.snapshot_store.update_config(
            max_hot=body.max_hot,
            debounce_seconds=body.debounce_seconds,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "InvalidSnapshotConfig",
                "message": str(exc),
            },
        ) from exc
    return result


@router.post("")
async def create_manual_snapshot(body: _ManualCreateBody) -> dict[str, Any]:
    """User-initiated snapshot. Trigger is always ``manual``; label is
    user-supplied (falls back to auto-derived ``tN:manual``).

    Short BUSY lock so the captured messages/memory_files are coherent
    with whatever the user just saw on screen.
    """
    store = get_session_store()
    try:
        async with store.session_operation(
            "snapshot.manual_create", state=SessionState.BUSY,
        ) as session:
            snap = session.snapshot_store.capture(
                session, trigger="manual", label=body.label,
            )
            return {"item": snap.metadata()}
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


@router.get("/{snapshot_id}")
async def get_snapshot(
    snapshot_id: str,
    include_memory_files: bool = False,
) -> dict[str, Any]:
    """Full payload of a single snapshot.

    Set ``include_memory_files=1`` to get the base64'd memory-file blob
    alongside messages. Default behaviour replaces the blob with a small
    ``memory_file_sizes`` map (relpath → bytes) which is enough for UI
    drawers and cheap over the wire.
    """
    session = _require_session()
    snap = session.snapshot_store.get(snapshot_id)
    if snap is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "SnapshotNotFound",
                "message": f"snapshot {snapshot_id} not found",
            },
        )
    return {"item": _snapshot_full_payload(snap, include_memory_files=include_memory_files)}


@router.delete("/{snapshot_id}")
async def delete_snapshot(snapshot_id: str) -> dict[str, Any]:
    """Delete a single snapshot (hot or cold).

    BUSY lock to stay consistent with other write paths; also guards
    against racing a capture that's mid-flight.
    """
    store = get_session_store()
    try:
        async with store.session_operation(
            "snapshot.delete", state=SessionState.BUSY,
        ) as session:
            removed = session.snapshot_store.delete(snapshot_id)
            if not removed:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error_type": "SnapshotNotFound",
                        "message": f"snapshot {snapshot_id} not found",
                    },
                )
            return {"deleted_id": snapshot_id}
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


@router.put("/{snapshot_id}/label")
async def rename_snapshot(
    snapshot_id: str, body: _UpdateLabelBody,
) -> dict[str, Any]:
    """Change the user-facing label of a snapshot."""
    store = get_session_store()
    try:
        async with store.session_operation(
            "snapshot.rename", state=SessionState.BUSY,
        ) as session:
            ok = session.snapshot_store.update_label(snapshot_id, body.label)
            if not ok:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error_type": "SnapshotNotFound",
                        "message": f"snapshot {snapshot_id} not found",
                    },
                )
            # Return the updated metadata so the UI can re-render.
            for meta in session.snapshot_store.list_metadata():
                if meta["id"] == snapshot_id:
                    return {"item": meta}
            return {"item": {"id": snapshot_id, "label": body.label}}  # pragma: no cover
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


@router.post("/{snapshot_id}/rewind")
async def rewind_snapshot(snapshot_id: str) -> dict[str, Any]:
    """Restore session state to ``snapshot_id``.

    Uses :class:`SessionState.REWINDING` so the UI shows "回退中…" and
    disables dangerous controls (save, load, other rewinds). The lock is
    held for the duration of the rmtree + file-rewrite + field swap,
    which in practice is fast (<100ms per MB of memory files) but can
    spike on Windows where file deletion is slower. No streaming — the
    endpoint returns once everything is consistent.
    """
    session = _require_session()
    if session.snapshot_store.get(snapshot_id) is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "SnapshotNotFound",
                "message": f"snapshot {snapshot_id} not found",
            },
        )
    store = get_session_store()
    try:
        async with store.session_operation(
            "snapshot.rewind", state=SessionState.REWINDING,
        ) as locked_session:
            try:
                result = locked_session.snapshot_store.rewind_to(
                    locked_session, snapshot_id,
                )
            except LookupError as exc:
                # Deleted between the pre-check and the lock acquisition.
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error_type": "SnapshotNotFound",
                        "message": str(exc),
                    },
                ) from exc
            except RewindFileLockedError as exc:
                # WinError 32 on time_indexed.db (SQLite) and friends.
                # This is recoverable: the user just needs to close the
                # holder (rare external case like DB Browser) or wait a
                # beat for Python GC. Returning 409 puts the "busy"
                # semantics in the UI's hands — the reset page / chip
                # already knows how to render a friendly toast for 409.
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_type": "SandboxFileLocked",
                        "message": str(exc),
                        "memory_dir": str(exc.memory_dir),
                        "locked_files": [str(p) for p in exc.locked_files[:20]],
                        "hint": (
                            "Close any external tool that may be viewing "
                            "time_indexed.db / persona.json etc. and retry. "
                            "If nothing external is holding them, this is "
                            "usually a transient GC race — retry once."
                        ),
                    },
                ) from exc
            locked_session.logger.log_sync(
                "snapshot.rewind",
                payload={
                    "snapshot_id": snapshot_id,
                    "dropped_count": result.get("dropped_count"),
                },
            )
            return result
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc
