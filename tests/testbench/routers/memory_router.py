"""Setup → Memory four-file CRUD surface (P07).

Scope:
    Expose the 4 canonical per-character memory JSON files for direct view /
    edit in the testbench UI:

    ============  ================================================
    kind          file (under ``cm.memory_dir / <character> /``)
    ============  ================================================
    recent        recent.json       (list — LangChain messages)
    facts         facts.json        (list — fact dicts)
    reflections   reflections.json  (list — reflection dicts)
    persona       persona.json      (dict — entity → {"facts": [...]})
    ============  ================================================

Policy:
    * **Direct JSON only**: we do NOT go through :class:`PersonaManager` /
      :class:`FactStore` / :class:`ReflectionEngine`. Those loaders run lazy
      migrations + side effects (e.g. ``ensure_persona`` syncs character_card
      into ``persona.json``) which would surprise a tester who explicitly
      just saved a file. Raw JSON is what they see, raw JSON is what they
      edit; the real app's loaders will still run their migrations next
      time they touch the file.
    * **Top-level shape check only**: we validate ``list`` vs ``dict`` and
      that each item is a dict, then write. Detailed schema validation is
      out of scope — it's a testbench editor, tester is allowed to craft
      malformed data to probe how the pipeline reacts.
    * **Read-tolerates-missing**: GET returns the canonical empty value
      (``[]`` or ``{}``) with ``exists=False`` so the UI can pre-populate
      a blank editor without a second request.
    * **Writes are atomic**: ``tmp + os.replace`` so an editor Save that
      gets killed mid-flight can't leave a half-written JSON file.

Prerequisites (returned as HTTP 4xx when unmet):
    * No active session → 404 (same convention as Persona / Time).
    * Active session but ``session.persona.character_name`` empty → 409
      ``NoCharacterSelected`` so the UI can prompt: "先在 Persona 或 Import
      子页选一个角色".
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from utils.config_manager import get_config_manager

from tests.testbench.logger import python_logger
from tests.testbench.pipeline import memory_runner
from tests.testbench.pipeline.snapshot_store import capture_safe as _snapshot_capture
from tests.testbench.session_store import (
    SessionConflictError,
    get_session_store,
)

router = APIRouter(prefix="/api/memory", tags=["memory"])


# ── kind registry ────────────────────────────────────────────────────
#
# Each kind maps to a filename under the character's memory dir + the
# *expected* top-level JSON type. Keeping this in one spot avoids copy/paste
# across 8 handlers and makes it trivial to add ``surfaced`` / archive files
# later (they'd just be new entries).

_KINDS: dict[str, dict[str, Any]] = {
    "recent":      {"filename": "recent.json",      "root_type": list, "empty": list},
    "facts":       {"filename": "facts.json",       "root_type": list, "empty": list},
    "reflections": {"filename": "reflections.json", "root_type": list, "empty": list},
    "persona":     {"filename": "persona.json",     "root_type": dict, "empty": dict},
}


# ── helpers ──────────────────────────────────────────────────────────


def _require_session():
    """Return active session or HTTP 404."""
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


def _require_character(session) -> str:
    """Extract ``session.persona.character_name`` or raise 409 NoCharacterSelected."""
    name = (session.persona or {}).get("character_name") or ""
    name = str(name).strip()
    if not name:
        raise HTTPException(
            status_code=409,
            detail={
                "error_type": "NoCharacterSelected",
                "message": (
                    "session.persona.character_name 为空. 请先在 Setup → Persona 填写角色名, "
                    "或在 Setup → Import 从真实角色导入."
                ),
            },
        )
    return name


def _require_kind(kind: str) -> dict[str, Any]:
    spec = _KINDS.get(kind)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "UnknownMemoryKind",
                "message": f"未知 memory kind: {kind!r}; 合法值: {sorted(_KINDS)}",
            },
        )
    return spec


def _resolve_path(character: str, filename: str) -> Path:
    """``cm.memory_dir / <character> / <filename>`` — always a sandbox path.

    Because the router runs *inside* an active session, ``cm.memory_dir`` is
    already patched to ``sandbox_root/N.E.K.O/memory``. We still join by hand
    rather than use ``memory.ensure_character_dir`` to avoid creating the
    directory on a plain GET (writes create it themselves).
    """
    cm = get_config_manager()
    return Path(str(cm.memory_dir)) / character / filename


def _read_json(path: Path, spec: dict[str, Any]) -> tuple[Any, bool]:
    """Return (value, exists). Missing / empty → ``spec['empty']()``.

    Does NOT repair invalid JSON on disk — raises HTTP 500 so tester knows
    to go fix (or delete) the corrupted file via Paths workspace (P20).
    """
    if not path.exists():
        return spec["empty"](), False
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": "InvalidMemoryJson",
                "message": f"{path.name} 不是合法 JSON: {exc}",
                "path": str(path),
            },
        ) from exc
    return data, True


# P24 §4.1.2 (2026-04-21): delegates to the unified atomic_io chokepoint
# (now includes fsync — previously missing here, per P21.1 G1 gap).
from tests.testbench.pipeline.atomic_io import atomic_write_json as _atomic_write_json  # noqa: E402


def _validate_shape(data: Any, spec: dict[str, Any]) -> None:
    """Top-level type check only (list vs dict, items are dicts).

    Leaves field-level validation to the real memory modules — they'll
    complain (or silently skip) at the next real load; letting the tester
    craft malformed payloads is a feature.
    """
    want = spec["root_type"]
    if not isinstance(data, want):
        raise HTTPException(
            status_code=422,
            detail={
                "error_type": "InvalidRootType",
                "message": f"顶层必须是 {want.__name__}, 收到 {type(data).__name__}",
            },
        )
    if want is list:
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error_type": "InvalidListItem",
                        "message": f"list[{i}] 不是 object (dict), 而是 {type(item).__name__}",
                    },
                )
    else:
        for key, value in data.items():
            if not isinstance(value, dict):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error_type": "InvalidDictValue",
                        "message": f"dict[{key!r}] 不是 object, 而是 {type(value).__name__}",
                    },
                )


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


class MemoryWritePayload(BaseModel):
    """Body for ``PUT /api/memory/{kind}``.

    Using a wrapper (``{data: ...}``) rather than a bare list/dict so we can
    extend later with e.g. ``{data, create_backup: true}`` without breaking
    the contract.
    """

    data: Any


# ── endpoints ────────────────────────────────────────────────────────


@router.get("/state")
async def memory_state() -> dict[str, Any]:
    """Compact "what do we have?" read used as the Memory landing probe.

    Lists each kind with ``{exists, size_bytes, mtime}``. Doesn't read the
    content (cheap stat calls), so it's safe to call on every subpage open.
    """
    session = _require_session()
    character = _require_character(session)
    cm = get_config_manager()
    char_dir = Path(str(cm.memory_dir)) / character

    files: dict[str, dict[str, Any]] = {}
    for kind, spec in _KINDS.items():
        p = char_dir / spec["filename"]
        stat: dict[str, Any] = {"exists": p.exists(), "path": str(p)}
        if p.exists():
            try:
                s = p.stat()
                stat["size_bytes"] = s.st_size
                stat["mtime"] = int(s.st_mtime)
            except OSError:
                pass
        files[kind] = stat

    return {
        "session_id": session.id,
        "character_name": character,
        "memory_root": str(char_dir),
        "files": files,
    }


# IMPORTANT: ``/previews`` must be declared BEFORE the ``/{kind}`` wildcard
# so FastAPI's path-matcher doesn't capture it as ``kind="previews"`` and
# return ``UnknownMemoryKind`` (the wildcard has no static-vs-dynamic
# preference — whoever declares first wins).


@router.get("/previews")
async def list_memory_previews() -> dict[str, Any]:
    """Return the session's pending previews for UI badges (P10).

    Does NOT require ``session_operation`` — it's a read of the in-memory
    cache only. Expired entries (older than
    :data:`memory_runner.MEMORY_PREVIEW_TTL_SECONDS`) are pruned in the
    same call so the UI always sees fresh state.
    """
    session = _require_session()
    memory_runner.prune_expired_previews(session)
    return {
        "session_id": session.id,
        "ttl_seconds": memory_runner.MEMORY_PREVIEW_TTL_SECONDS,
        "previews": memory_runner.list_previews(session),
    }


@router.get("/{kind}")
async def read_memory(kind: str) -> dict[str, Any]:
    """Return the JSON content of one memory file + metadata envelope.

    Envelope shape: ``{kind, path, exists, data}``. ``data`` is the
    canonical empty value (``[]`` / ``{}``) when the file is missing, so
    the UI can render an empty editor without a second request.
    """
    spec = _require_kind(kind)
    session = _require_session()
    character = _require_character(session)
    path = _resolve_path(character, spec["filename"])
    data, exists = _read_json(path, spec)
    return {
        "kind": kind,
        "path": str(path),
        "character_name": character,
        "exists": exists,
        "data": data,
    }


@router.put("/{kind}")
async def write_memory(kind: str, body: MemoryWritePayload) -> dict[str, Any]:
    """Replace the file content with ``body.data`` after shape check."""
    spec = _require_kind(kind)
    _validate_shape(body.data, spec)

    store = get_session_store()
    try:
        async with store.session_operation(f"memory.write:{kind}") as session:
            character = _require_character(session)
            path = _resolve_path(character, spec["filename"])
            _atomic_write_json(path, body.data)
            python_logger().info(
                "memory_router: wrote %s (%d bytes)", path, path.stat().st_size,
            )
            _snapshot_capture(session, trigger="memory_op")
            return {
                "kind": kind,
                "path": str(path),
                "character_name": character,
                "exists": True,
                "data": body.data,
            }
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


# ── P10: trigger / commit / discard for memory ops ──────────────────
#
# Routing convention:
#   POST /api/memory/trigger/{op}   body: {params: {...}}
#   POST /api/memory/commit/{op}    body: {edits: {...}}
#   POST /api/memory/discard/{op}   body: (empty)
#   GET  /api/memory/previews
#
# We keep these under the same ``/api/memory`` prefix so the UI only
# needs one base URL for everything memory-related. ``trigger`` and
# ``commit`` both acquire ``session_operation`` (busy=memory.{op}:{phase})
# so the single-session lock is honored, matching chat.send / memory.write.


def _wrap_memory_op_error(exc: memory_runner.MemoryOpError) -> HTTPException:
    """Translate :class:`MemoryOpError` to FastAPI's HTTPException.

    Error shape intentionally mirrors the existing handlers (``error_type``
    + ``message``) so the UI toast renderer stays uniform.
    """
    return HTTPException(
        status_code=exc.status,
        detail={
            "error_type": exc.code,
            "message": exc.message,
        },
    )


class MemoryTriggerPayload(BaseModel):
    """Body for ``POST /api/memory/trigger/{op}``.

    All op-specific parameters live inside ``params`` so the wire shape
    stays stable even as individual ops add/rename knobs. Unknown keys
    are forwarded as-is to the op handler — handlers document their own
    contract (see :mod:`tests.testbench.pipeline.memory_runner`).
    """

    params: dict[str, Any] = {}


class MemoryCommitPayload(BaseModel):
    """Body for ``POST /api/memory/commit/{op}``.

    ``edits`` is an optional dict with a subset of the preview payload
    fields the tester wants to override before write. Each op's commit
    handler documents which fields it honors (e.g. ``edits.extracted``
    for facts.extract, ``edits.reflection.text`` for reflect, ...).
    Omitting ``edits`` commits the original preview unchanged.
    """

    edits: dict[str, Any] = {}


def _require_op(op: str) -> None:
    if not memory_runner.is_valid_op(op):
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "UnknownMemoryOp",
                "message": (
                    f"未知 memory op: {op!r}; 合法值: "
                    f"{', '.join(memory_runner.ALL_OPS)}"
                ),
            },
        )


@router.post("/trigger/{op}")
async def trigger_memory_op(op: str, body: MemoryTriggerPayload) -> dict[str, Any]:
    """Run the dry-run for ``op`` and cache the result on the session.

    Takes the session lock for the duration of the LLM call (typical
    memory ops take 2-10 s). Returns the preview payload directly; the
    UI drawer renders it, lets the tester edit, then POSTs to
    ``/commit/{op}``. Re-triggering the same op overwrites the cache.
    """
    _require_op(op)
    store = get_session_store()
    try:
        async with store.session_operation(f"memory.{op}:preview") as session:
            result = await memory_runner.trigger_op(session, op, body.params)
            return result.to_dict()
    except memory_runner.MemoryOpError as exc:
        raise _wrap_memory_op_error(exc) from exc
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


@router.post("/commit/{op}")
async def commit_memory_op(op: str, body: MemoryCommitPayload) -> dict[str, Any]:
    """Write the (possibly tester-edited) cached preview to disk.

    Clears the cache entry on success (and on non-retryable failure —
    see ``memory_runner.commit_op`` docstring for the rationale).
    """
    _require_op(op)
    store = get_session_store()
    try:
        async with store.session_operation(f"memory.{op}:commit") as session:
            result = await memory_runner.commit_op(session, op, body.edits)
            _snapshot_capture(session, trigger="memory_op")
            return result
    except memory_runner.MemoryOpError as exc:
        raise _wrap_memory_op_error(exc) from exc
    except SessionConflictError as exc:
        raise _wrap_conflict(exc) from exc


@router.post("/discard/{op}")
async def discard_memory_op(op: str) -> dict[str, Any]:
    """Drop the cached preview without writing. Idempotent."""
    _require_op(op)
    session = _require_session()
    dropped = memory_runner.discard_op(session, op)
    return {"op": op, "discarded": dropped}
