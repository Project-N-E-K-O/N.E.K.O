"""P27.0 — read-only conversation corpus reader (memory-trace foundation).

Single read-only entry point that loads a character's conversation *turns*
from the two places they can live inside the active session sandbox:

* ``time_indexed.db`` — the main program's SQLite archive of full original
  dialogue. ``persona_router.import_from_real`` recursively copies this file
  into the sandbox (``_copytree_safe`` walks ``rglob("*")``), so for an
  *imported real character* the conversation corpus is already on disk.
* ``recent.json`` — the compressed recent-history window (LangChain on-disk
  ``{type, data:{content}}`` shape, round-trippable via ``messages_from_dict``).

Why this module exists (P27 blueprint 2.4 / R11)
------------------------------------------------
Although ``time_indexed.db`` is physically copied on import, **no testbench
feature ever reads its conversation content** — ``prompt_builder`` only calls
``TimeIndexedMemory.get_last_conversation_time`` (a single timestamp). The P27
memory-trace aggregator (P27.1) and Tier C reverse attribution (P27.3) are the
first consumers of the actual turns, so they share this one reader.

Hard rules (non-negotiable)
---------------------------
* **Read only.** Never writes, never creates the db (readonly engine bails out
  when the file is absent — see ``TimeIndexedMemory._ensure_engine_exists``).
* **Always release the SQLite handle.** ``TimeIndexedMemory`` keeps a
  SQLAlchemy engine (and a class-level cache) open on ``time_indexed.db``; on
  Windows that is an OS-level lock that blocks later snapshot/reset ``rmtree``
  (see ``snapshot_store`` / ``reset_runner``). We therefore open in readonly
  mode and ``cleanup()`` inside a ``finally`` no matter what.
* **Soft errors only.** Missing db / empty table / parse failure -> empty
  ``turns`` plus a ``warnings`` string. This function never raises for absent
  or malformed data; callers render a partial graph.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.config_manager import get_config_manager

from tests.testbench.logger import python_logger

# Widest practical window for "read every turn". ``retrieve_original_by_timeframe``
# compares against the stored ISO timestamp string; ``datetime.min``/``max``
# render to ``0001-...``/``9999-...`` which bound any real conversation.
_WIDE_START = datetime.min
_WIDE_END = datetime.max

# Default ceiling so a pathologically large imported db cannot pull the whole
# table into memory at once. Aggregator/attribution can override.
_DEFAULT_LIMIT_ROWS = 5000

# Keep the widest "read every turn" path from holding both the complete raw
# SQLAlchemy row list and the normalized trace list at the same time.
_TIME_INDEX_BATCH_SIZE = 256


def _normalize_role(raw_type: Any) -> str:
    """Map LangChain message ``type`` to the trace graph's role vocabulary."""
    t = str(raw_type or "").lower()
    if t in ("human", "user"):
        return "user"
    if t in ("ai", "assistant"):
        return "assistant"
    if t == "system":
        return "system"
    return t or "other"


def _coerce_content(raw: Any) -> str:
    """Force a message ``content`` (str / list-of-blocks / other) into text.

    Modern chat messages store ``content`` as a list of typed blocks
    (``[{"type": "text", "text": "..."}, {"type": "image_url", ...}]``). The
    trace graph wants human-readable text, not the raw JSON envelope, so we
    flatten text blocks into their ``text`` and replace non-text blocks with a
    short ``[type]`` placeholder. Falls back to ``json.dumps`` only for shapes
    we don't recognize.
    """
    if isinstance(raw, str):
        return raw
    if raw is None:
        return ""
    if isinstance(raw, list):
        parts: list[str] = []
        for block in raw:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
                else:
                    btype = block.get("type") or "block"
                    parts.append(f"[{btype}]")
            else:
                parts.append(str(block))
        joined = " ".join(p for p in parts if p)
        if joined.strip():
            return joined
    if isinstance(raw, dict):
        text = raw.get("text") or raw.get("content")
        if isinstance(text, str) and text.strip():
            return text
    try:
        return json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(raw)


def _recent_msg_id(role: str, content: str, ordinal: int) -> str:
    """Stable id for a recent.json turn (no native id on disk; hash content).

    ``ordinal`` is the turn's position in recent.json. It is part of the hash so
    that repeated identical turns (e.g. several short "ok" replies) get distinct
    ids — otherwise the lineage graph's ``_add_node`` would de-duplicate them,
    dropping real turns and mis-pointing reverse attribution at a single copy.
    """
    raw = f"{ordinal}\x00{role}\x00{content}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"msg:{digest[:12]}"


def _db_turn_id(session_id: Any, row_index: int) -> str:
    """Stable id for a time_indexed.db row (session_id + row ordinal)."""
    sid = str(session_id if session_id is not None else "")
    return f"tdb:{sid}:{row_index}"


def _normalize_ts(raw: Any) -> str | None:
    """Render a db timestamp (str / datetime / None) to an ISO string or None."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.isoformat()
    return str(raw)


def _character_memory_dir(character: str) -> Path:
    """Resolve ``<sandbox memory_dir>/<character>`` without creating it.

    Mirrors ``memory_runner._memory_dir`` but **does not** ``mkdir`` — this is a
    pure reader and must not materialize directories as a side effect.
    """
    cm = get_config_manager()
    return Path(str(cm.memory_dir)) / character


def load_recent_turns(character: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Load turns from ``recent.json``; returns ``(turns, warnings)``.

    Banner pseudo-messages have already been stripped before recent.json is
    written (see ``memory_runner`` import path), so recent.json holds only real
    turns. We still skip empty content defensively.
    """
    warnings: list[str] = []
    turns: list[dict[str, Any]] = []
    recent_path = _character_memory_dir(character) / "recent.json"
    if not recent_path.exists():
        return turns, warnings

    try:
        with recent_path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"recent.json 读取失败 ({type(exc).__name__}): {exc}")
        return turns, warnings
    if not isinstance(data, list):
        warnings.append(
            f"recent.json 顶层应为 list, 实际 {type(data).__name__}; 跳过"
        )
        return turns, warnings

    for ordinal, entry in enumerate(data):
        if not isinstance(entry, dict):
            continue
        raw_type = entry.get("type")
        payload = entry.get("data")
        content_raw = payload.get("content") if isinstance(payload, dict) else None
        role = _normalize_role(raw_type)
        content = _coerce_content(content_raw)
        if not content.strip():
            continue
        turns.append({
            "id": _recent_msg_id(role, content, ordinal),
            "ts": None,
            "session_id": None,
            "role": role,
            "content": content,
            "origin": "recent_json",
        })
    return turns, warnings


def load_time_indexed_turns(
    character: str, *, limit_rows: int | None = _DEFAULT_LIMIT_ROWS,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Load turns from ``time_indexed.db``; returns ``(turns, warnings, present)``.

    ``present`` is True only when the db file exists on disk (so the caller can
    distinguish "imported real character, no chat yet" from "never had a db" in
    the UI). The SQLite engine is opened readonly and unconditionally disposed
    in a ``finally`` block.
    """
    warnings: list[str] = []
    turns: list[dict[str, Any]] = []

    db_path = _character_memory_dir(character) / "time_indexed.db"
    present = db_path.is_file()
    if not present:
        return turns, warnings, present

    # Imported lazily so this module stays importable even if the main-program
    # memory package fails to load in some stripped environment.
    try:
        from memory.timeindex import TimeIndexedMemory
    except Exception as exc:  # noqa: BLE001 — defensive: never break the graph
        warnings.append(f"无法导入 TimeIndexedMemory: {type(exc).__name__}: {exc}")
        return turns, warnings, present

    tm = None
    try:
        # recent_history_manager is unused on the readonly retrieve path; None
        # keeps construction cheap and side-effect free.
        tm = TimeIndexedMemory(None)  # type: ignore[arg-type]
        row_index = 0
        for batch in tm.iter_original_by_timeframe_batches(
            character,
            _WIDE_START,
            _WIDE_END,
            batch_size=_TIME_INDEX_BATCH_SIZE,
            limit_rows=limit_rows,
        ):
            for row in batch:
                idx = row_index
                row_index += 1
                try:
                    ts_raw, session_id, message_raw = row[0], row[1], row[2]
                except (IndexError, TypeError):
                    continue
                role, content = _parse_db_message(message_raw)
                if not content.strip():
                    continue
                turns.append({
                    "id": _db_turn_id(session_id, idx),
                    "ts": _normalize_ts(ts_raw),
                    "session_id": str(session_id) if session_id is not None else None,
                    "role": role,
                    "content": content,
                    "origin": "time_indexed_db",
                })
    except Exception as exc:  # noqa: BLE001 — soft error, never crash the graph
        # A later batch may fail after earlier rows were normalized. Preserve
        # the previous one-shot reader's all-or-empty contract rather than
        # exposing an incomplete archive as a valid corpus prefix.
        turns.clear()
        warnings.append(
            f"time_indexed.db 读取失败 ({type(exc).__name__}): {exc}"
        )
        python_logger().warning(
            "conversation_corpus: reading time_indexed.db for %s failed: %s",
            character, exc,
        )
    finally:
        # Release the SQLite handle so a subsequent snapshot/reset rmtree is not
        # blocked by a lingering engine lock (Windows). MUST run on every path.
        if tm is not None:
            try:
                tm.cleanup()
            except Exception as exc:  # noqa: BLE001
                python_logger().warning(
                    "conversation_corpus: TimeIndexedMemory.cleanup failed: %s",
                    exc,
                )
    return turns, warnings, present


def _parse_db_message(message_raw: Any) -> tuple[str, str]:
    """Parse a ``time_indexed_original.message`` cell into ``(role, content)``.

    On-disk shape (``utils.llm_client.SQLChatMessageHistory._serialize``)::

        {"type": "human|ai|system", "data": {"content": "..."}}
    """
    if isinstance(message_raw, (bytes, bytearray)):
        try:
            message_raw = message_raw.decode("utf-8")
        except Exception:  # noqa: BLE001
            message_raw = str(message_raw)
    if isinstance(message_raw, str):
        try:
            message_raw = json.loads(message_raw)
        except json.JSONDecodeError:
            return "other", message_raw
    if isinstance(message_raw, dict):
        role = _normalize_role(message_raw.get("type"))
        payload = message_raw.get("data")
        content = payload.get("content") if isinstance(payload, dict) else None
        return role, _coerce_content(content)
    return "other", _coerce_content(message_raw)


def load_conversation_corpus(
    character: str,
    *,
    include_recent: bool = True,
    include_db: bool = True,
    limit_rows: int | None = _DEFAULT_LIMIT_ROWS,
) -> dict[str, Any]:
    """Aggregate a character's conversation turns from all available sources.

    Returns a stable, JSON-serializable dict::

        {
          "character": str,
          "turns": [ {id, ts, session_id, role, content, origin}, ... ],
          "sources": {
             "time_indexed_db": bool,   # db file present on disk
             "recent_json": bool,       # recent.json yielded >=1 turn
          },
          "counts": {"time_indexed_db": int, "recent_json": int, "total": int},
          "warnings": [str, ...],
        }

    Never raises for absent / malformed data — only soft warnings. ``turns``
    keeps db turns first (chronological, the authoritative archive) followed by
    recent.json turns; callers can re-sort by ``ts`` when present.
    """
    character = str(character or "").strip()
    warnings: list[str] = []
    db_turns: list[dict[str, Any]] = []
    recent_turns: list[dict[str, Any]] = []
    db_present = False

    if not character:
        return {
            "character": "",
            "turns": [],
            "sources": {"time_indexed_db": False, "recent_json": False},
            "counts": {"time_indexed_db": 0, "recent_json": 0, "total": 0},
            "warnings": ["character 为空, 无法定位对话语料"],
        }

    if include_db:
        db_turns, db_warnings, db_present = load_time_indexed_turns(
            character, limit_rows=limit_rows,
        )
        warnings.extend(db_warnings)
    if include_recent:
        recent_turns, recent_warnings = load_recent_turns(character)
        warnings.extend(recent_warnings)

    turns = db_turns + recent_turns
    return {
        "character": character,
        "turns": turns,
        "sources": {
            "time_indexed_db": db_present,
            "recent_json": len(recent_turns) > 0,
        },
        "counts": {
            "time_indexed_db": len(db_turns),
            "recent_json": len(recent_turns),
            "total": len(turns),
        },
        "warnings": warnings,
    }
