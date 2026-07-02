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

"""Game session debug log endpoints (/logs*).

Split out of the former monolithic ``main_routers/game_router.py``.
"""

from ._shared import router

import html
import json
from typing import Any
from fastapi import Request
from fastapi.responses import HTMLResponse
from utils.game_log import (
    append_game_session_debug_log as _append_game_session_debug_log,
    enable_game_session_debug_log as _enable_game_session_debug_log,
    find_game_session_debug_log as _find_game_session_debug_log,
    GAME_SESSION_DEBUG_ACTIVE_IDLE_TTL_SECONDS,
    GAME_SESSION_DEBUG_LOG_ENTRY_LIMIT,
    GAME_SESSION_DEBUG_RETAINED_SESSION_LIMIT,
    GAME_SESSION_DEBUG_RETAINED_SESSION_TTL_SECONDS,
    list_game_session_debug_log_summaries,
    public_game_session_debug_log,
)


def _game_log_payload_flag_is_true(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return False


@router.get("/logs")
async def game_logs(session_id: str = "", game_type: str = "", since: int = 0, limit: int = 300):
    if session_id:
        entry = _find_game_session_debug_log(session_id, game_type)
        if not entry:
            return {"ok": True, "missing": True, "entries": [], "session_id": session_id, "game_type": game_type}
        return {"ok": True, "log": public_game_session_debug_log(entry, since=since, limit=limit)}
    return {
        "ok": True,
        "sessions": list_game_session_debug_log_summaries(str(game_type or "").strip()),
        "retention": {
            "entry_limit": GAME_SESSION_DEBUG_LOG_ENTRY_LIMIT,
            "active_idle_ttl_seconds": GAME_SESSION_DEBUG_ACTIVE_IDLE_TTL_SECONDS,
            "retained_completed_session_limit": GAME_SESSION_DEBUG_RETAINED_SESSION_LIMIT,
            "retained_completed_session_ttl_seconds": GAME_SESSION_DEBUG_RETAINED_SESSION_TTL_SECONDS,
        },
    }


@router.get("/logs/view", response_class=HTMLResponse)
async def game_log_view(session_id: str = "", game_type: str = "", limit: int = 300):
    game_type_s = str(game_type or "").strip()
    session_id_s = str(session_id or "").strip()
    entry = _find_game_session_debug_log(session_id_s, game_type_s) if session_id_s else None
    if entry:
        payload = public_game_session_debug_log(entry, limit=limit)
        title = f"{entry.get('game_type') or 'game'} / {session_id_s}"
        body = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        title = "小游戏诊断日志"
        body = json.dumps({
            "ok": True,
            "message": "请带上 session_id，或查看 sessions 列表。",
            "sessions": list_game_session_debug_log_summaries(game_type_s),
        }, ensure_ascii=False, indent=2)
    safe_title = html.escape(title)
    safe_body = html.escape(body)
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<style>
body {{ font-family: ui-monospace, Consolas, monospace; margin: 16px; background: #101418; color: #e8eef4; }}
a {{ color: #8cc8ff; }}
pre {{ white-space: pre-wrap; word-break: break-word; line-height: 1.35; }}
.hint {{ color: #9fb0c0; margin-bottom: 12px; }}
</style>
</head>
<body>
<div class="hint">小游戏场次诊断日志 | JSON: /api/game/logs?session_id=...</div>
<pre>{safe_body}</pre>
</body>
</html>"""
    )


@router.post("/logs/enable")
async def game_log_enable(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    session_id = str(data.get("session_id") or data.get("sessionId") or "").strip()
    game_type = str(data.get("game_type") or data.get("gameType") or "game").strip()
    lanlan_name = str(data.get("lanlan_name") or data.get("lanlanName") or "").strip()
    if not session_id:
        return {"ok": False, "reason": "missing_session_id"}

    from ..system_router import _validate_local_mutation_request

    validation_error = _validate_local_mutation_request(
        request,
        payload=data,
        error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )
    if validation_error is not None:
        return validation_error

    entry = _enable_game_session_debug_log(game_type, session_id, lanlan_name=lanlan_name)
    if entry is None:
        return {"ok": False, "reason": "enable_failed", "session_id": session_id, "game_type": game_type}
    item = _append_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        category="route",
        event="session_log_enabled",
        source=str(data.get("source") or "backend"),
        message="小游戏场次诊断日志已手动启用",
        details={"reason": str(data.get("reason") or "manual")},
    )
    return {
        "ok": True,
        "session_id": session_id,
        "game_type": game_type,
        "seq": item.get("seq") if isinstance(item, dict) else None,
    }


@router.post("/logs")
async def game_log_ingest(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    session_id = str(data.get("session_id") or data.get("sessionId") or "").strip()
    game_type = str(data.get("game_type") or data.get("gameType") or "game").strip()
    lanlan_name = str(data.get("lanlan_name") or data.get("lanlanName") or "").strip()
    if not session_id:
        return {"ok": False, "reason": "missing_session_id"}

    from ..system_router import _validate_local_mutation_request

    validation_error = _validate_local_mutation_request(
        request,
        payload=data,
        error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )
    if validation_error is not None:
        return validation_error

    ignored = {
        "session_id", "sessionId", "game_type", "gameType", "lanlan_name", "lanlanName", "level",
        "category", "event", "type", "source", "message",
        "sensitive_possible", "sensitivePossible",
        "preserve_message", "preserveMessage", "preserve_details", "preserveDetails",
        "no_truncate", "noTruncate", "_csrf_token",
    }
    preserve_message = (
        _game_log_payload_flag_is_true(data.get("preserve_message"))
        or _game_log_payload_flag_is_true(data.get("preserveMessage"))
    )
    preserve_details = (
        _game_log_payload_flag_is_true(data.get("preserve_details"))
        or _game_log_payload_flag_is_true(data.get("preserveDetails"))
    )
    item = _append_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        level=str(data.get("level") or "info"),
        category=str(data.get("category") or "frontend"),
        event=str(data.get("event") or data.get("type") or "client_event"),
        source=str(data.get("source") or "frontend"),
        message=str(data.get("message") or ""),
        details=data.get("details") if isinstance(data.get("details"), dict) else {
            key: value for key, value in data.items()
            if key not in ignored
        },
        sensitive_possible=bool(data.get("sensitive_possible") or data.get("sensitivePossible")),
        preserve_message=preserve_message,
        preserve_details=preserve_details,
    )
    return {"ok": item is not None, "seq": item.get("seq") if isinstance(item, dict) else None}
