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

from tests.testbench.chat_messages import (
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_USER,
    SOURCE_EXTERNAL_EVENT_BANNER,
)
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


# ── recent.json shortcut: import current session.messages ────────────
#
# P25 Day 2 polish r6 (2026-04-23): tester 反馈 "把测试区现有对话一键
# 落盘到最近对话记忆" 是高频操作 — 之前需要手动把 session.messages 转
# LangChain 规范 ({type:human|ai|system, data:{content}}) 再走
# PUT /api/memory/recent. 每次都要开 DevTools 拼 JSON 太硬核.
#
# 本端点是 **raw dump** 通道 (不跑 LLM 压缩, 也不抽事实), 和
# `memory.trigger/recent.compress` 平行共存:
#     * /memory/recent/import_from_session  →  直接落 session.messages
#       (user/assistant/system 三类, 按时间顺序, 不走 LLM).
#     * /memory/trigger/recent.compress     →  跑 LLM 压缩旧记录, 再写
#       (preview-then-commit 两阶段).
#
# 过滤规则:
#     * ``source == external_event_banner`` 跳过 — banner 是 UI-only
#       视觉标记, 不是真实对话 (prompt_builder 在发送 LLM 前也会过滤,
#       见 chat_messages.py L52-L54); 塞进 recent.json 会让下一次
#       /chat/send 把 banner 再注回 wire, 造成语义污染.
#     * role ∉ {user, assistant, system} 跳过 — LangChain
#       messages_to_dict 只认这三类; 预留的 simuser/script/auto 是
#       source 标签, role 本身必然是 user/assistant, 不会命中这条.
#     * content 为空字符串跳过 — recent.json 里的空 content 毫无记忆
#       价值, 反而会让 compress 阶段误把它当作 "用户沉默一拍".
#
# 写策略 (body.mode):
#     * "append" (默认) — 读 existing recent.json, 新消息追加到尾部,
#       然后整体原子写回; 不去重 (简单对话重复导入就是 tester 自己
#       的意图).
#     * "replace" — 不读 existing, 直接用本轮消息覆盖整个文件.
#
# 单写 choke-point: 走 ``session_operation`` 锁, 同 write_memory.


class RecentImportFromSessionPayload(BaseModel):
    """Body for ``POST /api/memory/recent/import_from_session``.

    ``mode``:
        * ``"append"`` (default) — 原子读 + 合并 + 写回; 旧条目保留, 新
          条目追加到尾部.
        * ``"replace"`` — 直接用本轮 session.messages 覆盖整个文件.
    """

    mode: str = "append"


_ROLE_TO_LANGCHAIN_TYPE: dict[str, str] = {
    ROLE_USER: "human",
    ROLE_ASSISTANT: "ai",
    ROLE_SYSTEM: "system",
}


def _session_messages_to_recent_dicts(session) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Convert ``session.messages`` → LangChain canonical on-disk dicts.

    Returns ``(dicts, skipped_counts)``. ``dicts`` mirrors exactly what
    ``utils.llm_client.messages_to_dict(lc_messages)`` would produce, so
    ``CompressedRecentHistoryManager.load_all()`` (which calls
    ``messages_from_dict``) round-trips without reshaping.

    We build the dicts by hand (not via LangChain classes →
    ``messages_to_dict``) so the helper stays pure-python — callers
    inside routers don't need a session_operation or LLM client context.
    The on-disk shape is defined at ``utils.llm_client.py`` L71-L95 and
    is stable since LangChain 0.1.
    """
    dicts: list[dict[str, Any]] = []
    skipped = {"banner": 0, "unsupported_role": 0, "empty_content": 0}

    for m in (session.messages or []):
        if (m.get("source") or "") == SOURCE_EXTERNAL_EVENT_BANNER:
            skipped["banner"] += 1
            continue

        role = m.get("role")
        lc_type = _ROLE_TO_LANGCHAIN_TYPE.get(role)
        if not lc_type:
            skipped["unsupported_role"] += 1
            continue

        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            skipped["empty_content"] += 1
            continue

        dicts.append({"type": lc_type, "data": {"content": content}})

    return dicts, skipped


@router.post("/recent/import_from_session")
async def import_recent_from_session(
    body: RecentImportFromSessionPayload | None = None,
) -> dict[str, Any]:
    """One-click dump ``session.messages`` into ``recent.json``.

    Shortcut for the Chat workspace "把当前对话内容添加到最近对话记忆"
    button. See module-level comment "recent.json shortcut" for the full
    policy (filtering rules, append vs replace semantics).

    Returns
    -------
    ``{
        character_name, path,
        mode, added, existing, total,
        skipped: {banner, unsupported_role, empty_content},
    }``

    Error mapping:
        * 400 ``InvalidMode``        — ``mode`` ∉ {append, replace}.
        * 404 ``NoActiveSession``    — no session (standard).
        * 409 ``NoCharacterSelected``— character_name 为空.
        * 409 ``NoMessagesToImport`` — filtered list is empty (tester
          pressed button with empty chat, or only banners).
        * 409 ``SessionBusy``        — another op holds the session lock.
    """
    if body is None:
        body = RecentImportFromSessionPayload()
    mode = (body.mode or "append").strip().lower()
    if mode not in ("append", "replace"):
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "InvalidMode",
                "message": f"mode 必须是 'append' 或 'replace', 收到: {body.mode!r}",
            },
        )

    # Pre-flight: no session → clean 404 instead of letting session_operation
    # raise raw LookupError (which the global handler maps to opaque 500).
    # UI toast for this path is "先创建会话", so keeping the error actionable
    # matters; the server's existing memory.trigger handlers accept a 500
    # here for historical reasons (see PROGRESS.md L406), but new endpoints
    # should be crisp.
    _require_session()

    spec = _KINDS["recent"]
    store = get_session_store()
    try:
        async with store.session_operation("memory.recent.import_from_session") as session:
            character = _require_character(session)
            path = _resolve_path(character, spec["filename"])

            new_dicts, skipped = _session_messages_to_recent_dicts(session)
            if not new_dicts:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_type": "NoMessagesToImport",
                        "message": (
                            "session.messages 中没有可落盘的对话. "
                            "(banner / 空内容 / 未支持 role 已全部过滤.) "
                            "先发送几条消息或用外部事件触发再试."
                        ),
                        "skipped": skipped,
                    },
                )

            if mode == "append":
                existing, _ = _read_json(path, spec)
                if not isinstance(existing, list):
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "error_type": "InvalidRootType",
                            "message": (
                                f"{path.name} 顶层不是 list, "
                                "不能 append. 去 Setup → Memory 手动修复, 或用 mode='replace'."
                            ),
                        },
                    )
                final_data = list(existing) + new_dicts
                existing_count = len(existing)
            else:
                final_data = new_dicts
                existing_count = 0

            _atomic_write_json(path, final_data)
            python_logger().info(
                "memory_router: imported recent from session (mode=%s, "
                "added=%d, existing=%d, total=%d, skipped=%s) → %s",
                mode, len(new_dicts), existing_count, len(final_data),
                skipped, path,
            )
            _snapshot_capture(session, trigger="memory_op")
            return {
                "character_name": character,
                "path": str(path),
                "mode": mode,
                "added": len(new_dicts),
                "existing": existing_count,
                "total": len(final_data),
                "skipped": skipped,
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


@router.post("/prompt_preview/{op}")
async def prompt_preview_memory_op(
    op: str, body: MemoryTriggerPayload,
) -> dict[str, Any]:
    """Show what wire ``op`` would send to the memory LLM, without calling it.

    P25 r7 — the Chat page's Preview Panel is now chat-only; memory LLM
    wires are exposed here so the Memory sub-page [预览 prompt] button
    can fetch the wire without paying the 2-10 s LLM round trip.

    Behavior:
        * Pure function over (session snapshot, params). Does not stamp
          ``session.last_llm_wire`` (that would pollute the Chat panel
          for an unrelated UI click).
        * Takes no session lock — read-only; OK to run concurrently
          with chat.send.
        * Returns :class:`memory_runner.MemoryPromptPreview` as dict:
          ``{op, wire_messages, note, params_echo, warnings}``.
        * Same error vocabulary as ``/trigger/{op}``: 404 UnknownOp, 409
          for "no input" cases (``RecentEmpty`` / ``NoMessages`` /
          ``NotEnoughFacts`` / ``QueueEmpty``), 422 ``NoPromptForOp``
          (specifically for ``persona.add_fact`` which has no LLM call).
    """
    _require_op(op)
    session = _require_session()
    try:
        preview = await memory_runner.build_memory_prompt_preview(
            session, op, body.params,
        )
        return preview.to_dict()
    except memory_runner.MemoryOpError as exc:
        raise _wrap_memory_op_error(exc) from exc
