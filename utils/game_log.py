# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Mini-game session diagnostic log in-memory buffer and helpers.

Fallback/degrade contract:
callers must make the primary log message itself explicit when a fallback,
degrade, skip, timeout, parse failure, or recovery path is used. Do not rely on
a second summary field or details-only metadata to explain that the path was not
normal success. Shared logging helpers should reuse the original logger/console
message and args; if that text is unclear, fix the original message at the call
site. Structured fields such as fallback/degraded/skipped/reason are for
filtering and diagnosis, not for correcting an ambiguous message.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from datetime import datetime
from typing import Any

GAME_SESSION_DEBUG_LOG_ENTRY_LIMIT = 1000
GAME_SESSION_DEBUG_ACTIVE_IDLE_TTL_SECONDS = 10 * 60
GAME_SESSION_DEBUG_RETAINED_SESSION_LIMIT = 1
GAME_SESSION_DEBUG_RETAINED_SESSION_TTL_SECONDS = 5 * 60
# Retention is scoped to the single current mini-game scene for this process.
# game_type and lanlan_name are diagnostic/filter metadata, not retention keys:
# a new active scene intentionally clears completed logs from other game types
# or characters. A future multi-scene log pool should introduce an explicit
# scene/scope id instead of reusing these metadata fields.
_GAME_SESSION_DEBUG_MESSAGE_LIMIT = 1200
_GAME_SESSION_DEBUG_STRING_LIMIT = 2000
_GAME_SESSION_DEBUG_DICT_LIMIT = 48
_GAME_SESSION_DEBUG_LIST_LIMIT = 40
_GAME_SESSION_DEBUG_MAX_DEPTH = 4

_game_session_debug_logs: "OrderedDict[str, dict]" = OrderedDict()


def _game_debug_log_key(game_type: Any, session_id: Any) -> str:
    return f"{str(game_type or '').strip()}:{str(session_id or '').strip()}"


def _find_game_session_debug_log(session_id: Any, game_type: Any = "") -> dict | None:
    session_id_s = str(session_id or "").strip()
    game_type_s = str(game_type or "").strip()
    if not session_id_s:
        return None
    if game_type_s:
        return _game_session_debug_logs.get(_game_debug_log_key(game_type_s, session_id_s))
    matched = [
        entry for entry in _game_session_debug_logs.values()
        if str(entry.get("session_id") or "") == session_id_s
    ]
    if not matched:
        return None
    matched.sort(key=lambda entry: float(entry.get("updated_at") or 0.0), reverse=True)
    return matched[0]


def find_game_session_debug_log(session_id: Any, game_type: Any = "") -> dict | None:
    cleanup_game_session_debug_logs()
    return _find_game_session_debug_log(session_id, game_type)


def _safe_game_debug_text(value: Any, *, limit: int = _GAME_SESSION_DEBUG_STRING_LIMIT) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated +{len(text) - limit} chars>"


def _safe_game_debug_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= _GAME_SESSION_DEBUG_MAX_DEPTH:
        return _safe_game_debug_text(value, limit=240)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_game_debug_text(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _GAME_SESSION_DEBUG_DICT_LIMIT:
                result["_truncated"] = f"+{len(value) - _GAME_SESSION_DEBUG_DICT_LIMIT} keys"
                break
            result[_safe_game_debug_text(key, limit=120)] = _safe_game_debug_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [_safe_game_debug_value(item, depth=depth + 1) for item in items[:_GAME_SESSION_DEBUG_LIST_LIMIT]]
        if len(items) > _GAME_SESSION_DEBUG_LIST_LIMIT:
            result.append({"_truncated": f"+{len(items) - _GAME_SESSION_DEBUG_LIST_LIMIT} items"})
        return result
    return _safe_game_debug_text(value)


def _game_debug_log_time(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="milliseconds")
    except Exception:
        return ""


def _drop_completed_game_session_debug_logs() -> None:
    for key, entry in list(_game_session_debug_logs.items()):
        if entry.get("status") != "active":
            del _game_session_debug_logs[key]


def _drop_other_game_session_debug_logs(retained_key: str) -> None:
    for key in list(_game_session_debug_logs.keys()):
        if key != retained_key:
            del _game_session_debug_logs[key]


def _get_or_create_game_session_debug_log(
    game_type: Any,
    session_id: Any,
    *,
    lanlan_name: str = "",
    activate: bool = False,
    create: bool = True,
) -> dict | None:
    game_type_s = str(game_type or "").strip()
    session_id_s = str(session_id or "").strip()
    if not game_type_s or not session_id_s:
        return None
    key = _game_debug_log_key(game_type_s, session_id_s)
    now = time.time()
    entry = _game_session_debug_logs.get(key)
    if entry is None:
        if not create:
            return None
        entry = {
            "key": key,
            "game_type": game_type_s,
            "session_id": session_id_s,
            "lanlan_name": str(lanlan_name or ""),
            "status": "active" if activate else "open",
            "created_at": now,
            "created_time": _game_debug_log_time(now),
            "updated_at": now,
            "updated_time": _game_debug_log_time(now),
            "ended_at": None,
            "ended_time": None,
            "last_viewed_at": None,
            "seq": 0,
            "entries": [],
        }
        _game_session_debug_logs[key] = entry
        if activate:
            _drop_other_game_session_debug_logs(key)
    else:
        _game_session_debug_logs.move_to_end(key)
        entry["updated_at"] = now
        if lanlan_name and not entry.get("lanlan_name"):
            entry["lanlan_name"] = str(lanlan_name)
        if activate:
            entry["status"] = "active"
            entry["ended_at"] = None
            entry["ended_time"] = None
            _drop_other_game_session_debug_logs(key)
    return entry


def enable_game_session_debug_log(game_type: Any, session_id: Any, *, lanlan_name: str = "") -> dict | None:
    return _get_or_create_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        activate=True,
        create=True,
    )


def touch_game_session_debug_log(game_type: Any, session_id: Any, *, lanlan_name: str = "") -> bool:
    cleanup_game_session_debug_logs()
    entry = _get_or_create_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        activate=False,
        create=False,
    )
    if entry is None or entry.get("status") != "active":
        return False
    now = time.time()
    entry["updated_at"] = now
    entry["updated_time"] = _game_debug_log_time(now)
    return True


def cleanup_game_session_debug_logs(now: float | None = None) -> None:
    current_time = time.time() if now is None else float(now)
    retained_keys: set[str] = set()
    completed_entries: list[dict] = []
    has_active_session = False
    for key, entry in list(_game_session_debug_logs.items()):
        if entry.get("status") == "active":
            reference_time = float(entry.get("updated_at") or entry.get("created_at") or 0.0)
            if reference_time and current_time - reference_time > GAME_SESSION_DEBUG_ACTIVE_IDLE_TTL_SECONDS:
                del _game_session_debug_logs[key]
                continue
            has_active_session = True
            continue
        completed_entries.append(entry)

    if has_active_session:
        _drop_completed_game_session_debug_logs()
        return

    completed_entries.sort(
        key=lambda entry: float(entry.get("ended_at") or entry.get("updated_at") or entry.get("created_at") or 0.0),
        reverse=True,
    )
    for entry in completed_entries[:GAME_SESSION_DEBUG_RETAINED_SESSION_LIMIT]:
        reference_time = float(entry.get("ended_at") or entry.get("updated_at") or entry.get("created_at") or 0.0)
        if reference_time and current_time - reference_time <= GAME_SESSION_DEBUG_RETAINED_SESSION_TTL_SECONDS:
            retained_keys.add(str(entry.get("key") or ""))

    for key, entry in list(_game_session_debug_logs.items()):
        if entry.get("status") == "active":
            continue
        if key not in retained_keys:
            del _game_session_debug_logs[key]


def append_game_session_debug_log(
    game_type: Any,
    session_id: Any,
    *,
    lanlan_name: str = "",
    level: str = "info",
    category: str = "backend",
    event: str = "",
    source: str = "backend",
    message: str = "",
    details: Any = None,
    sensitive_possible: bool = False,
    preserve_message: bool = False,
    preserve_details: bool = False,
) -> dict | None:
    """Append one mini-game session log entry.

    Fallback/error-recovery callers must make ``message`` itself say the source
    path failed, timed out, degraded, skipped, or used fallback. Do not add a
    second summary just to repair an unclear message; fix the original
    logger/console text at the call site. ``details`` fields such as
    reason/error_type/fallback/degraded/skipped_reason are structured filters,
    not replacements for clear primary log text.
    """
    try:
        cleanup_game_session_debug_logs()
        entry = _get_or_create_game_session_debug_log(
            game_type,
            session_id,
            lanlan_name=lanlan_name,
            create=False,
        )
        if entry is None:
            return None
        if entry.get("status") != "active":
            return None
        entry["seq"] = int(entry.get("seq") or 0) + 1
        now = time.time()
        item = {
            "seq": entry["seq"],
            "ts": now,
            "time": _game_debug_log_time(now),
            "level": _safe_game_debug_text(level, limit=24) or "info",
            "category": _safe_game_debug_text(category, limit=64) or "backend",
            "event": _safe_game_debug_text(event, limit=96) or "event",
            "source": _safe_game_debug_text(source, limit=96) or "backend",
            "message": str(message or "") if preserve_message else _safe_game_debug_text(
                message,
                limit=_GAME_SESSION_DEBUG_MESSAGE_LIMIT,
            ),
            "sensitive_possible": bool(sensitive_possible),
            "details": details if preserve_details else _safe_game_debug_value(details if details is not None else {}),
        }
        entry["entries"].append(item)
        if len(entry["entries"]) > GAME_SESSION_DEBUG_LOG_ENTRY_LIMIT:
            del entry["entries"][:len(entry["entries"]) - GAME_SESSION_DEBUG_LOG_ENTRY_LIMIT]
        entry["updated_at"] = now
        entry["updated_time"] = _game_debug_log_time(now)
        _game_session_debug_logs.move_to_end(entry["key"])
        cleanup_game_session_debug_logs(now)
        return item
    except Exception:
        return None


def mark_game_session_debug_log_active(game_type: Any, session_id: Any, *, lanlan_name: str = "") -> None:
    entry = _get_or_create_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        activate=True,
        create=False,
    )
    if entry is None:
        return
    append_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        category="route",
        event="session_active",
        message="小游戏场次日志已激活",
    )


def mark_game_session_debug_log_ended(game_type: Any, session_id: Any, *, lanlan_name: str = "", reason: str = "") -> None:
    entry = _get_or_create_game_session_debug_log(game_type, session_id, lanlan_name=lanlan_name, create=False)
    if entry is None:
        return
    if entry.get("status") == "ended":
        return
    if entry.get("status") == "active":
        append_game_session_debug_log(
            game_type,
            session_id,
            lanlan_name=lanlan_name,
            category="route",
            event="session_ended",
            message="小游戏场次已结束，日志进入保留窗口",
            details={"reason": reason or "unknown"},
        )
    ended_at = time.time()
    entry["status"] = "ended"
    entry["ended_at"] = ended_at
    entry["ended_time"] = _game_debug_log_time(ended_at)
    cleanup_game_session_debug_logs(ended_at)


def public_game_session_debug_log(entry: dict, *, since: int = 0, limit: int = 200) -> dict:
    safe_limit = max(1, min(int(limit or 200), GAME_SESSION_DEBUG_LOG_ENTRY_LIMIT))
    safe_since = max(0, int(since or 0))
    entries = [
        item for item in list(entry.get("entries") or [])
        if int(item.get("seq") or 0) > safe_since
    ][-safe_limit:]
    entry["last_viewed_at"] = time.time()
    return {
        "key": entry.get("key"),
        "game_type": entry.get("game_type"),
        "session_id": entry.get("session_id"),
        "lanlan_name": entry.get("lanlan_name"),
        "status": entry.get("status"),
        "created_at": entry.get("created_at"),
        "created_time": entry.get("created_time"),
        "updated_at": entry.get("updated_at"),
        "updated_time": entry.get("updated_time"),
        "ended_at": entry.get("ended_at"),
        "ended_time": entry.get("ended_time"),
        "last_viewed_at": entry.get("last_viewed_at"),
        "entry_count": len(entry.get("entries") or []),
        "entries": entries,
    }


def list_game_session_debug_log_summaries(game_type: str = "") -> list[dict]:
    cleanup_game_session_debug_logs()
    summaries = []
    for entry in _game_session_debug_logs.values():
        if game_type and entry.get("game_type") != game_type:
            continue
        summaries.append({
            "game_type": entry.get("game_type"),
            "session_id": entry.get("session_id"),
            "lanlan_name": entry.get("lanlan_name"),
            "status": entry.get("status"),
            "created_at": entry.get("created_at"),
            "created_time": entry.get("created_time"),
            "updated_at": entry.get("updated_at"),
            "updated_time": entry.get("updated_time"),
            "ended_at": entry.get("ended_at"),
            "ended_time": entry.get("ended_time"),
            "entry_count": len(entry.get("entries") or []),
        })
    summaries.sort(key=lambda item: float(item.get("updated_at") or 0.0), reverse=True)
    return summaries
