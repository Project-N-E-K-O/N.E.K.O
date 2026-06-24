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

"""
Game Router

Generic game LLM interaction endpoints. Uses an A+B "double act" pattern:
  A (behind-the-scenes decision): OmniOfflineClient text-only LLM that receives game events and generates lines + structured control instructions
  B (front-of-stage output): sends A's result to the current session mode's output channel (voice/TTS/text bubble)

Currently implemented: soccer. The generic route /{game_type}/chat supports extending to other games later.
"""

import asyncio
import html
import json
import math
import random
import re
import shutil
import sqlite3
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse


_EXTERNAL_VOICE_DEDUP_TTL_SECONDS = 30.0
_EXTERNAL_VOICE_DEDUP_MAX_ENTRIES = 64
_SSML_TAG_PATTERN = re.compile(
    r"</?(?:[a-z][\w-]*:)?(?:"
    r"speak|p|s|break|say-as|phoneme|sub|prosody|emphasis|voice|audio|mark|lang|w|token|express-as|effect"
    r")(?:\s+[^<>\n]{0,120})?\s*/?>",
    re.IGNORECASE,
)

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from config.prompts.prompts_badminton import get_badminton_quick_lines_fallback
from config.prompts.prompts_game import (
    PREGAME_CONTEXT_INPUT_WATERMARK,
    get_badminton_pregame_context_formatter_labels,
    get_badminton_pregame_context_prompt,
    get_badminton_quick_lines_prompt,
    get_badminton_quick_lines_user_prompt,
    get_badminton_system_prompt,
    SOCCER_SYSTEM_PROMPT as _SOCCER_SYSTEM_PROMPT,
    get_soccer_anger_pressure_cap_message,
    get_soccer_anger_pressure_cap_reason,
    get_soccer_pregame_context_formatter_labels,
    get_soccer_pregame_context_prompt,
    get_soccer_quick_lines_prompt,
    get_soccer_quick_lines_user_prompt,
    get_soccer_system_prompt,
)
from config.prompts.prompts_game_route import (
    GAME_CONTEXT_SIGNAL_GROUP_KEYS,
    get_compact_realtime_context_texts,
    get_game_chat_event_user_prompt,
    get_game_archive_fallback_highlight_labels,
    get_game_archive_highlight_source_labels,
    get_game_archive_memory_highlighter_system_prompt,
    get_game_archive_memory_highlighter_user_prompt,
    get_game_archive_memory_summary_labels,
    get_game_archive_memory_text_labels,
    get_game_context_formatter_labels,
    get_game_context_organizer_system_prompt,
    get_game_context_organizer_user_prompt,
    get_game_dialog_memory_line_labels,
    get_game_postgame_context_labels,
    get_game_postgame_event_texts,
    get_game_postgame_realtime_nudge_labels,
    get_game_recent_history_message_labels,
)
from .shared_state import get_config_manager, get_session_manager
from main_logic.mirror_meta import (
    MIRROR_USER_TEXT_INPUT_TYPE,
    MIRROR_USER_VOICE_TRANSCRIPT_INPUT_TYPE,
    build_mirror_meta,
)
from utils.game_route_state import (
    _game_route_states,
    _get_active_game_route_state,
    _get_route_lock,
    _get_supersede_lock,
    _route_state_key,
    is_game_route_active,
    register_voice_transcript_handler,
)
from utils.game_log import (
    append_game_session_debug_log as _append_game_session_debug_log,
    enable_game_session_debug_log as _enable_game_session_debug_log,
    find_game_session_debug_log as _find_game_session_debug_log,
    GAME_SESSION_DEBUG_ACTIVE_IDLE_TTL_SECONDS,
    GAME_SESSION_DEBUG_LOG_ENTRY_LIMIT,
    GAME_SESSION_DEBUG_RETAINED_SESSION_LIMIT,
    GAME_SESSION_DEBUG_RETAINED_SESSION_TTL_SECONDS,
    list_game_session_debug_log_summaries,
    mark_game_session_debug_log_active as _mark_game_session_debug_log_active,
    mark_game_session_debug_log_ended as _mark_game_session_debug_log_ended,
    public_game_session_debug_log,
    touch_game_session_debug_log as _touch_game_session_debug_log,
)
from utils.language_utils import get_global_language, normalize_language_code, is_supported_language_code
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Game")

router = APIRouter(tags=["game"], prefix="/api/game")


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

    from .system_router import _validate_local_mutation_request

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

    from .system_router import _validate_local_mutation_request

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


# ── Session 池 ─────────────────────────────────────────────────────
# key = f"{lanlan_name}:{game_type}:{session_id}"
# value = { session: OmniOfflineClient, reply_chunks: list, last_activity: float, lock: asyncio.Lock }
_game_sessions: Dict[str, dict] = {}

# 超时清理：30 分钟无活动自动销毁
_SESSION_TIMEOUT_SECONDS = 30 * 60
_GAME_ROUTE_ACTIVATION_LOG_LIMIT = 32
_BADMINTON_SCORE_SESSION_TTL_SECONDS = 10 * 60
_BADMINTON_SCORING_MODES = {"duel"}
_BADMINTON_GAME_TYPES = {"badminton"}
_BADMINTON_SHOT_TYPE_ALIASES = {
    "line_in": "line_in",
    "net_touch": "net_touch",
    "zone_in": "zone_in",
    "out": "out",
    "net": "net",
}
_badminton_recent_score_sessions: Dict[tuple[str, str], dict] = {}

_SOCCER_QUICK_LINE_KEYS = {
    "goal-scored", "goal-conceded", "own-goal-by-ai", "own-goal-by-player",
    "steal", "stolen", "player-idle", "player-charging-long",
    "free-ball", "startle-direct", "startle-graze", "zoneout",
}
_BADMINTON_QUICK_LINE_KEYS = {
    "line_in", "net_touch", "zone_in", "out", "net",
    "shot_missed", "game_over", "long_aim", "close_to_record",
    "new_record", "streak_5", "streak_10", "streak_15", "streak_20",
}
_badminton_quick_lines_cache: OrderedDict[str, Dict[str, list[str]]] = OrderedDict()
_BADMINTON_QUICK_LINES_CACHE_MAX = 32
_badminton_chat_rate_windows: OrderedDict[str, list[float]] = OrderedDict()
_BADMINTON_CHAT_RATE_WINDOW_SECONDS = 8.0
_BADMINTON_CHAT_RATE_MAX = 10
_BADMINTON_SCORES_DB_PATH: Path | None = None
_BADMINTON_LEGACY_SCORES_DB_PATH = Path(__file__).resolve().with_name("badminton_scores.db")
_DEFAULT_GAME_MEMORY_TAIL_COUNT = 6
_MAX_GAME_MEMORY_TAIL_COUNT = 50


def _is_badminton_game_type(game_type: Any) -> bool:
    return str(game_type or "").strip().lower() in _BADMINTON_GAME_TYPES


async def _push_game_window_state_change(
    mgr,
    *,
    action: str,
    lanlan_name: str,
    game_type: str,
    session_id: str = "",
) -> None:
    """Broadcast the 'game window opened/closed' WS event so the chat.html / pet
    multi-windows can collapse / restore in sync (user-level UX linkage; not
    involved in any game-route state decisions, purely drives frontend layout).

    Single source of truth: ``game_route_start`` pushes ``opened`` after
    activation, and ``_finalize_game_route_state_inner`` pushes ``closed`` after
    flipping the state to inactive. All finalize paths (/route/end / heartbeat
    sweep / supersede) go through the inner helper, keeping coverage in sync
    with ``is_game_route_active`` — the frontend never ends up in an orphaned
    "game already over but the UI is still locked collapsed" state.

    Multi-window forwarding relies on the existing ``WS_PROXY_CHANNELS.RAW_MESSAGE``
    IPC (the pet main window receives WS → the forwarder relays it to chat.html),
    the same bus as mini_game_invite_resolved; no new IPC channel needed.
    """
    if not mgr or not lanlan_name:
        return
    payload: dict[str, Any] = {
        "type": "game_window_state_change",
        "action": action,
        "lanlan_name": lanlan_name,
        "game_type": game_type,
    }
    if session_id:
        payload["session_id"] = session_id
    try:
        ws = getattr(mgr, "websocket", None)
        if ws is None or not hasattr(ws, "send_json"):
            return
        client_state = getattr(ws, "client_state", None)
        if client_state is not None and client_state != client_state.CONNECTED:
            return
        await ws.send_json(payload)
    except Exception as exc:
        logger.warning(
            "game_window_state_change WS push failed (action=%s, game=%s, lanlan=%s): %s",
            action, game_type, lanlan_name, exc,
        )


_DEFAULT_SOCCER_GAME_MEMORY_ENABLED = False
_SOCCER_GAME_MEMORY_POLICY_FIELDS = (
    "soccer_game_memory_enabled",
    "soccer_game_memory_player_interaction_enabled",
    "soccer_game_memory_event_reply_enabled",
    "soccer_game_memory_archive_enabled",
    "soccer_game_memory_postgame_context_enabled",
)
_DEFAULT_BADMINTON_GAME_MEMORY_ENABLED = False
_BADMINTON_GAME_MEMORY_POLICY_FIELDS = (
    "badminton_game_memory_enabled",
    "badminton_game_memory_player_interaction_enabled",
    "badminton_game_memory_event_reply_enabled",
    "badminton_game_memory_archive_enabled",
    "badminton_game_memory_postgame_context_enabled",
)
_GAME_CONTEXT_ORGANIZE_TRIGGER_COUNT = 15
_GAME_CONTEXT_RECENT_KEEP_COUNT = 6
_GAME_CONTEXT_RECENT_WINDOW_MAX_COUNT = _GAME_CONTEXT_ORGANIZE_TRIGGER_COUNT
_GAME_CONTEXT_FAILURE_VISIBLE_WINDOW_MAX_COUNT = 64
_GAME_CONTEXT_FAILURE_FALLBACK_KEEP_COUNT = 8
_GAME_CONTEXT_FINALIZE_WAIT_SECONDS = 5.0
_GAME_CONTEXT_SIGNAL_GROUPS = GAME_CONTEXT_SIGNAL_GROUP_KEYS
_LEGACY_SIGNAL_GROUP_ALIASES = {
    "玩家信号": "player_signals",
    "关系互动信号": "relationship_signals",
    "猫娘信号": "character_signals",
    "本局事实": "session_facts",
    "口头声明": "verbal_claims",
}
_GAME_CONTEXT_MAX_SIGNALS_PER_GROUP = 8
_GAME_CONTEXT_MAX_EVIDENCE_PER_SIGNAL = 2
_SOCCER_MOODS = {"calm", "happy", "angry", "relaxed", "sad", "surprised"}
_SOCCER_DIFFICULTIES = {"max", "lv2", "lv3", "lv4"}
_SOCCER_DEFAULT_DIFFICULTIES = ("lv2", "lv3")
_SOCCER_ANGER_PRESSURE_CAP_WEAK = 8
_SOCCER_ANGER_PRESSURE_CAP_DEFAULT = 25
_SOCCER_ANGER_PRESSURE_CAP_STRONG = 50
_SOCCER_EMOTION_INERTIA = {"low", "medium", "high", "very_high"}
_SOCCER_GAME_STANCES = {
    "neutral_play",
    "teaching",
    "soft_teasing",
    "competitive",
    "punishing",
    "withdrawn",
}

# 游戏期间外部主入口路由状态。这里记录的是“主语音入口/主聊天窗是否被游戏接管”，
# 不是游戏页面内部另起一套聊天入口。
# 实际容器在 utils/game_route_state.py，这里只 re-import 以保持现有调用点 / 测试
# 使用 game_router._game_route_states / _route_state_key 的写法不变。
_DEFAULT_LAST_FULL_DIALOGUE_COUNT = 8
_GAME_ROUTE_OUTPUT_LIMIT = 50
_GAME_ROUTE_HEARTBEAT_INTERVAL_SECONDS = 2.5
_GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS = 10.0
_GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS = 60.0
_GAME_ROUTE_HEARTBEAT_SWEEP_SECONDS = 2.0
_ACCIDENTAL_GAME_ENTRY_GRACE_MS = 10_000
_SESSION_CLEANUP_SWEEP_SECONDS = 60.0
_GAME_DEBUG_MATERIAL_LOG_LIMIT = 24000

# Per-(lanlan, game_type, session_id) creation lock for ``_get_or_create_session``.
#
# B6: without this, two concurrent ``_run_game_chat`` calls for the same
# key both miss the cache, both build an ``OmniOfflineClient`` and both
# ``await session.connect(...)``. The second insertion overwrites
# ``_game_sessions[key]`` so the first ``entry`` is now an orphan: its
# ``lock`` is held by the first ``_run_game_chat``, but the cache no
# longer points at it, so nothing will ever ``close()`` that session.
#
# Lifecycle (codex P2 follow-up): the create lock for a key is only
# meaningful while a session for that key may be created or alive. After
# ``_close_and_remove_session`` evicts the session from
# ``_game_sessions``, any further ``_get_or_create_session`` call for
# the same key would build a fresh session anyway — the lock entry just
# accumulates without protecting anything. So evict the create lock at
# the same time as the session, otherwise the dict grows unbounded over
# uptime as session_ids churn.
_game_session_create_locks: Dict[str, asyncio.Lock] = {}


def _get_session_create_lock(key: str) -> asyncio.Lock:
    """Lazy-init the per-key creation lock; sync helper, never awaits."""
    lock = _game_session_create_locks.get(key)
    if lock is None:
        lock = _game_session_create_locks.setdefault(key, asyncio.Lock())
    return lock


def _log_game_debug_material(
    label: str,
    material: Any,
    *,
    game_type: str = "",
    session_id: str = "",
    lanlan_name: str = "",
    source: str = "",
) -> None:
    """Log test-visible game context/memory material with a bounded body."""
    if isinstance(material, str):
        text = material
    else:
        try:
            text = json.dumps(material, ensure_ascii=False, indent=2)
        except Exception:
            text = str(material)
    if not text.strip():
        return
    truncated = ""
    body = text
    if len(body) > _GAME_DEBUG_MATERIAL_LOG_LIMIT:
        omitted = len(body) - _GAME_DEBUG_MATERIAL_LOG_LIMIT
        body = body[:_GAME_DEBUG_MATERIAL_LOG_LIMIT]
        truncated = f" truncated=+{omitted}"
    logger.info(
        "🎮 调试材料[%s]: game=%s session=%s lanlan=%s source=%s chars=%d%s\n%s",
        label,
        game_type or "-",
        session_id or "-",
        lanlan_name or "-",
        source or "-",
        len(text),
        truncated,
        body,
    )


def _infer_service_source(base_url: str, model: str = "", api_type: str = "") -> Dict[str, str]:
    """Infer a compact provider label for logs/debug responses."""
    raw_url = str(base_url or "").strip()
    raw_model = str(model or "").strip()
    raw_api_type = str(api_type or "").strip()
    model_lower = raw_model.lower()
    api_lower = raw_api_type.lower()

    host = ""
    try:
        host = (urlparse(raw_url).hostname or "").lower()
    except Exception:
        host = ""

    provider = "unknown"
    if api_lower == "local" or host in {"localhost", "127.0.0.1"}:
        provider = "local"
    elif api_lower == "gemini" or "gemini" in model_lower or "googleapis.com" in host or "generativelanguage" in host:
        provider = "gemini"
    elif "qwen" in model_lower or "dashscope" in host or "aliyuncs.com" in host:
        provider = "qwen"
    elif "glm" in model_lower or "bigmodel.cn" in host:
        provider = "glm"
    elif "gpt" in model_lower or "openai" in host:
        provider = "openai"
    elif "openrouter" in host:
        provider = "openrouter"
    elif "lanlan.app" in host and "free" in model_lower:
        provider = "lanlan-free"
    elif api_lower:
        provider = api_lower
    elif host:
        provider = host

    label_parts = [provider]
    if raw_model:
        label_parts.append(raw_model)

    return {
        "provider": provider,
        "model": raw_model,
        "api_type": raw_api_type,
        "base_url": raw_url,
        "host": host,
        "label": " / ".join(label_parts),
    }


def _badminton_duel_difficulty_control_prompt(language: str | None = None) -> str:
    lang = normalize_language_code(language)
    if lang == "zh":
        return (
            "\n对战难度控制补充：duel 模式下你可以在台词后另起一行输出 "
            "{\"difficulty\":\"max|lv2|lv3|lv4\"}。"
            "max=最强/认真压制，lv2=强但略慢，lv3=明显放水，lv4=最弱/主要防守。"
            "只在局势、情绪或 balanceHint 需要时调整；spectator 不使用 difficulty。\n"
        )
    return (
        "\nDuel difficulty control addendum: in duel mode, you may output "
        "{\"difficulty\":\"max|lv2|lv3|lv4\"} on a separate line after the spoken line. "
        "max=strongest/serious pressure, lv2=strong but slightly slower, "
        "lv3=clear soft play, lv4=weakest/mostly defensive. Adjust only when the "
        "score, emotion, or balanceHint calls for it; spectator does not use difficulty.\n"
    )


def _build_game_prompt(
    game_type: str,
    lanlan_name: str,
    lanlan_prompt: str,
    pre_game_context: dict | None = None,
    game_context: dict | None = None,
    language: str | None = None,
    mode: str = "spectator",
) -> str:
    """Build the game system prompt."""
    if game_type == "soccer":
        prompt = get_soccer_system_prompt(language).format(name=lanlan_name, personality=lanlan_prompt)
        context_prompt = _format_soccer_pregame_context_for_prompt(pre_game_context, language)
        in_game_context_prompt = _format_game_context_for_prompt(game_context, language)
        return f"{prompt}{context_prompt}{in_game_context_prompt}"
    if _is_badminton_game_type(game_type):
        prompt = get_badminton_system_prompt(language, mode=mode).format(name=lanlan_name, personality=lanlan_prompt)
        if _normalize_badminton_mode(mode) == "duel":
            prompt = f"{prompt}{_badminton_duel_difficulty_control_prompt(language)}"
        context_prompt = _format_badminton_pregame_context_for_prompt(pre_game_context, language, mode=mode)
        in_game_context_prompt = _format_game_context_for_prompt(game_context, language)
        return f"{prompt}{context_prompt}{in_game_context_prompt}"
    # 未来其他游戏在这里扩展
    output_language = str(language or get_global_language() or "en")
    return (
        f"You are {lanlan_name}. {lanlan_prompt}\n"
        f"You are playing a game. Generate short in-character lines in {output_language} for each game event."
    )


def _strip_json_fence(text: str) -> str:
    """Extract the JSON body from an LLM reply, tolerating ```json code fences."""
    raw = text.strip()
    code_block = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, flags=re.S)
    if code_block:
        return code_block.group(1).strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    json_start = raw.find("{")
    json_end = raw.rfind("}")
    if 0 <= json_start < json_end:
        return raw[json_start:json_end + 1].strip()
    return raw


def _soccer_random_default_difficulty() -> str:
    # 默认锁 lv2 与前端 DEFAULT_DIFFICULTY_INDEX / prompts_game initialDifficulty 对齐；
    # lv3 仍是 _SOCCER_DEFAULT_DIFFICULTIES 合法值，允许 upstream 显式 soften 一档。
    return "lv2"


def _normalize_short_text(value: Any, *, max_chars: int = 120) -> str:
    text = str(value or "").strip().replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    if max_chars > 0:
        text = text[:max_chars]
    return text


def _normalize_badminton_shot_type(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _BADMINTON_SHOT_TYPE_ALIASES.get(raw, "")


def _normalize_text_items(value: Any, *, max_items: int = 5, max_chars: int = 80) -> list[str]:
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []

    items: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = _normalize_short_text(item, max_chars=max_chars)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= max_items:
            break
    return items


def _empty_game_context_signals() -> dict:
    return {group: [] for group in _GAME_CONTEXT_SIGNAL_GROUPS}


def _normalize_signal_label(value: Any) -> str:
    text = _normalize_short_text(value, max_chars=60)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_game_context_evidence(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    evidence: list[dict] = []
    seen_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        evidence_id = _normalize_short_text(item.get("id"), max_chars=40)
        quote = _normalize_short_text(item.get("quote"), max_chars=80)
        if not evidence_id or evidence_id in seen_ids:
            continue
        seen_ids.add(evidence_id)
        evidence.append({"id": evidence_id, "quote": quote})
        if len(evidence) >= _GAME_CONTEXT_MAX_EVIDENCE_PER_SIGNAL:
            break
    return evidence


def _normalize_game_context_signal_entry(value: Any) -> dict | None:
    if isinstance(value, str):
        label = _normalize_signal_label(value)
        if not label:
            return None
        return {
            "signalLabel": label,
            "summary": label,
            "evidence": [],
            "lastRound": None,
            "count": 1,
        }
    if not isinstance(value, dict):
        return None

    label = _normalize_signal_label(value.get("signalLabel") or value.get("label"))
    summary = _normalize_short_text(value.get("summary") or label, max_chars=160)
    if not label and summary:
        label = _normalize_signal_label(summary)
    if not label:
        return None

    try:
        count = int(value.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(count, 99))

    last_round = value.get("lastRound", value.get("last_round"))
    try:
        last_round = int(last_round) if last_round is not None else None
    except (TypeError, ValueError):
        last_round = None

    return {
        "signalLabel": label,
        "summary": summary or label,
        "evidence": _normalize_game_context_evidence(value.get("evidence")),
        "lastRound": last_round,
        "count": count,
    }


def _normalize_game_context_signals(value: Any) -> dict:
    signals = _empty_game_context_signals()
    if not isinstance(value, dict):
        return signals
    normalized_value = dict(value)
    for legacy_key, canonical_key in _LEGACY_SIGNAL_GROUP_ALIASES.items():
        if legacy_key not in normalized_value or canonical_key in normalized_value:
            continue
        normalized_value[canonical_key] = normalized_value.get(legacy_key)
    for group in _GAME_CONTEXT_SIGNAL_GROUPS:
        raw_items = normalized_value.get(group)
        if isinstance(raw_items, str):
            raw_items = [raw_items]
        if not isinstance(raw_items, list):
            continue
        normalized: list[dict] = []
        seen_labels: set[str] = set()
        for item in raw_items:
            entry = _normalize_game_context_signal_entry(item)
            if not entry:
                continue
            label_key = entry["signalLabel"]
            if label_key in seen_labels:
                continue
            seen_labels.add(label_key)
            normalized.append(entry)
            if len(normalized) >= _GAME_CONTEXT_MAX_SIGNALS_PER_GROUP:
                break
        signals[group] = normalized
    return signals


def _merge_game_context_evidence(existing: list[dict], incoming: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen_ids: set[str] = set()
    for item in [*(existing or []), *(incoming or [])]:
        if not isinstance(item, dict):
            continue
        evidence_id = _normalize_short_text(item.get("id"), max_chars=40)
        quote = _normalize_short_text(item.get("quote"), max_chars=80)
        if not evidence_id or evidence_id in seen_ids:
            continue
        seen_ids.add(evidence_id)
        merged.append({"id": evidence_id, "quote": quote})
        if len(merged) >= _GAME_CONTEXT_MAX_EVIDENCE_PER_SIGNAL:
            break
    return merged


def _merge_game_context_signals(existing: Any, incoming: Any) -> dict:
    merged = _normalize_game_context_signals(existing)
    incoming_signals = _normalize_game_context_signals(incoming)
    for group in _GAME_CONTEXT_SIGNAL_GROUPS:
        bucket = list(merged.get(group) or [])
        for candidate in incoming_signals.get(group) or []:
            candidate_label = candidate.get("signalLabel")
            candidate_evidence_ids = {
                str(ev.get("id") or "")
                for ev in candidate.get("evidence") or []
                if isinstance(ev, dict) and ev.get("id")
            }
            target = None
            for existing_entry in bucket:
                existing_evidence_ids = {
                    str(ev.get("id") or "")
                    for ev in existing_entry.get("evidence") or []
                    if isinstance(ev, dict) and ev.get("id")
                }
                if existing_entry.get("signalLabel") == candidate_label or (
                    candidate_evidence_ids and existing_evidence_ids & candidate_evidence_ids
                ):
                    target = existing_entry
                    break
            if target is None:
                bucket.append(candidate)
                continue
            target["summary"] = candidate.get("summary") or target.get("summary") or target.get("signalLabel")
            target["evidence"] = _merge_game_context_evidence(
                target.get("evidence") or [],
                candidate.get("evidence") or [],
            )
            try:
                target["count"] = max(1, int(target.get("count") or 1)) + max(1, int(candidate.get("count") or 1))
            except (TypeError, ValueError):
                target["count"] = max(1, int(target.get("count") or 1))
            candidate_round = candidate.get("lastRound")
            target_round = target.get("lastRound")
            if isinstance(candidate_round, int) and (
                not isinstance(target_round, int) or candidate_round > target_round
            ):
                target["lastRound"] = candidate_round
        merged[group] = bucket[-_GAME_CONTEXT_MAX_SIGNALS_PER_GROUP:]
    return merged


def _normalize_game_context_organizer_state(value: Any) -> dict:
    raw = value if isinstance(value, dict) else {}
    try:
        failure_count = int(raw.get("failure_count") or 0)
    except (TypeError, ValueError):
        failure_count = 0
    return {
        "running": raw.get("running") is True,
        "degraded": raw.get("degraded") is True,
        "failure_count": max(0, failure_count),
        "last_organized_id": str(raw.get("last_organized_id") or ""),
        "source": raw.get("source") if isinstance(raw.get("source"), dict) else raw.get("source"),
        "error": str(raw.get("error") or ""),
    }


def _normalize_game_memory_tail_count(value: Any, default: int = _DEFAULT_GAME_MEMORY_TAIL_COUNT) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = int(default)
    return max(1, min(count, _MAX_GAME_MEMORY_TAIL_COUNT))


def _normalize_soccer_game_memory_enabled(value: Any, default: bool = _DEFAULT_SOCCER_GAME_MEMORY_ENABLED) -> bool:
    coerced = _coerce_payload_bool(value)
    return bool(default) if coerced is None else bool(coerced)


def _payload_bool_from_keys(data: dict, keys: tuple[str, ...]) -> bool | None:
    if not isinstance(data, dict):
        return None
    for key in keys:
        if key in data:
            return _normalize_soccer_game_memory_enabled(data.get(key))
    return None


_GAME_MEMORY_POLICY_PAYLOAD_KEYS = {
    "game_memory_enabled": (
        "soccer_game_memory_enabled", "soccerGameMemoryEnabled",
        "game_memory_enabled", "gameMemoryEnabled", "memoryEnabled", "enableGameMemory",
    ),
    "game_memory_player_interaction_enabled": (
        "soccer_game_memory_player_interaction_enabled", "soccerGameMemoryPlayerInteractionEnabled",
        "game_player_interaction_memory_enabled", "gamePlayerInteractionMemoryEnabled",
        "game_memory_player_interaction_enabled", "gameMemoryPlayerInteractionEnabled",
    ),
    "game_memory_event_reply_enabled": (
        "soccer_game_memory_event_reply_enabled", "soccerGameMemoryEventReplyEnabled",
        "game_event_reply_memory_enabled", "gameEventReplyMemoryEnabled",
        "game_memory_event_reply_enabled", "gameMemoryEventReplyEnabled",
    ),
    "game_memory_archive_enabled": (
        "soccer_game_memory_archive_enabled", "soccerGameMemoryArchiveEnabled",
        "game_archive_memory_enabled", "gameArchiveMemoryEnabled",
        "game_memory_archive_enabled", "gameMemoryArchiveEnabled",
    ),
    "game_memory_postgame_context_enabled": (
        "soccer_game_memory_postgame_context_enabled", "soccerGameMemoryPostgameContextEnabled",
        "game_postgame_context_memory_enabled", "gamePostgameContextMemoryEnabled",
        "game_memory_postgame_context_enabled", "gameMemoryPostgameContextEnabled",
    ),
}

_SOCCER_GAME_MEMORY_PAYLOAD_KEYS = {
    "soccer_game_memory_enabled": _GAME_MEMORY_POLICY_PAYLOAD_KEYS["game_memory_enabled"],
    "soccer_game_memory_player_interaction_enabled": _GAME_MEMORY_POLICY_PAYLOAD_KEYS["game_memory_player_interaction_enabled"],
    "soccer_game_memory_event_reply_enabled": _GAME_MEMORY_POLICY_PAYLOAD_KEYS["game_memory_event_reply_enabled"],
    "soccer_game_memory_archive_enabled": _GAME_MEMORY_POLICY_PAYLOAD_KEYS["game_memory_archive_enabled"],
    "soccer_game_memory_postgame_context_enabled": _GAME_MEMORY_POLICY_PAYLOAD_KEYS["game_memory_postgame_context_enabled"],
}


_BADMINTON_GAME_MEMORY_PAYLOAD_KEYS = {
    "badminton_game_memory_enabled": (
        "badminton_game_memory_enabled", "badmintonGameMemoryEnabled",
        "game_memory_enabled", "gameMemoryEnabled", "memoryEnabled", "enableGameMemory",
    ),
    "badminton_game_memory_player_interaction_enabled": (
        "badminton_game_memory_player_interaction_enabled", "badmintonGameMemoryPlayerInteractionEnabled",
        "game_player_interaction_memory_enabled", "gamePlayerInteractionMemoryEnabled",
        "game_memory_player_interaction_enabled", "gameMemoryPlayerInteractionEnabled",
    ),
    "badminton_game_memory_event_reply_enabled": (
        "badminton_game_memory_event_reply_enabled", "badmintonGameMemoryEventReplyEnabled",
        "game_event_reply_memory_enabled", "gameEventReplyMemoryEnabled",
        "game_memory_event_reply_enabled", "gameMemoryEventReplyEnabled",
    ),
    "badminton_game_memory_archive_enabled": (
        "badminton_game_memory_archive_enabled", "badmintonGameMemoryArchiveEnabled",
        "game_archive_memory_enabled", "gameArchiveMemoryEnabled",
        "game_memory_archive_enabled", "gameMemoryArchiveEnabled",
    ),
    "badminton_game_memory_postgame_context_enabled": (
        "badminton_game_memory_postgame_context_enabled", "badmintonGameMemoryPostgameContextEnabled",
        "game_postgame_context_memory_enabled", "gamePostgameContextMemoryEnabled",
        "game_memory_postgame_context_enabled", "gameMemoryPostgameContextEnabled",
    ),
}


def _normalize_game_memory_type(game_type: str | None) -> str:
    game = str(game_type or "soccer").strip().lower()
    if game in _BADMINTON_GAME_TYPES:
        return "badminton"
    return "soccer"


def _game_memory_policy_fields(game_type: str | None) -> tuple[str, ...]:
    gt = _normalize_game_memory_type(game_type)
    if gt == "badminton":
        return _BADMINTON_GAME_MEMORY_POLICY_FIELDS
    return _SOCCER_GAME_MEMORY_POLICY_FIELDS


def _game_memory_payload_keys(game_type: str | None) -> dict:
    gt = _normalize_game_memory_type(game_type)
    if gt == "badminton":
        return _BADMINTON_GAME_MEMORY_PAYLOAD_KEYS
    return _SOCCER_GAME_MEMORY_PAYLOAD_KEYS


def _game_memory_default_enabled(game_type: str | None) -> bool:
    gt = _normalize_game_memory_type(game_type)
    if gt == "badminton":
        return _DEFAULT_BADMINTON_GAME_MEMORY_ENABLED
    return _DEFAULT_SOCCER_GAME_MEMORY_ENABLED


def _sync_generic_game_memory_aliases(policy: dict, fields: tuple[str, ...]) -> dict:
    policy["game_memory_enabled"] = policy[fields[0]]
    policy["gameMemoryEnabled"] = policy[fields[0]]
    policy["game_memory_player_interaction_enabled"] = policy[fields[1]]
    policy["game_memory_event_reply_enabled"] = policy[fields[2]]
    policy["game_memory_archive_enabled"] = policy[fields[3]]
    policy["game_memory_postgame_context_enabled"] = policy[fields[4]]
    return policy


def _game_memory_policy(game_type: str | None, value: Any) -> dict:
    if not isinstance(value, dict):
        value = {}
    gt = _normalize_game_memory_type(game_type)
    fields = _game_memory_policy_fields(gt)
    payload_keys = _game_memory_payload_keys(gt)
    master = _payload_bool_from_keys(value, payload_keys[fields[0]])
    if master is None:
        master = _game_memory_default_enabled(gt)
    policy = {fields[0]: master}
    for field in fields[1:]:
        enabled = _payload_bool_from_keys(value, payload_keys[field])
        policy[field] = master if enabled is None else enabled
    return _sync_generic_game_memory_aliases(policy, fields)


def _soccer_game_memory_policy(value: Any) -> dict:
    return _game_memory_policy("soccer", value)


def _badminton_game_memory_policy(value: Any) -> dict:
    return _game_memory_policy("badminton", value)


def _game_memory_policy_from_payload(
    game_type: str | None,
    data: dict,
    current: dict | None = None,
) -> dict | None:
    if not isinstance(data, dict):
        return None
    gt = _normalize_game_memory_type(game_type)
    fields = _game_memory_policy_fields(gt)
    payload_keys = _game_memory_payload_keys(gt)
    contains_policy_key = any(
        key in data
        for keys in payload_keys.values()
        for key in keys
    )
    if not contains_policy_key:
        return None

    policy = _game_memory_policy(gt, current or {})
    master = _payload_bool_from_keys(data, payload_keys[fields[0]])
    if master is not None:
        for field in fields:
            policy[field] = master

    for field in fields[1:]:
        enabled = _payload_bool_from_keys(data, payload_keys[field])
        if enabled is not None:
            policy[field] = enabled

    return _sync_generic_game_memory_aliases(policy, fields)


def _soccer_game_memory_policy_from_payload(data: dict, current: dict | None = None) -> dict | None:
    return _game_memory_policy_from_payload("soccer", data, current=current)


def _badminton_game_memory_policy_from_payload(data: dict, current: dict | None = None) -> dict | None:
    return _game_memory_policy_from_payload("badminton", data, current=current)


def _game_memory_player_interaction_enabled(value: Any, game_type: str | None = None) -> bool:
    if not isinstance(value, dict):
        return _game_memory_default_enabled(game_type or "soccer")
    gt = _normalize_game_memory_type(game_type or value.get("game_type") or "soccer")
    return _game_memory_policy(gt, value)[_game_memory_policy_fields(gt)[1]]


def _game_memory_event_reply_enabled(value: Any, game_type: str | None = None) -> bool:
    if not isinstance(value, dict):
        return _game_memory_default_enabled(game_type or "soccer")
    gt = _normalize_game_memory_type(game_type or value.get("game_type") or "soccer")
    return _game_memory_policy(gt, value)[_game_memory_policy_fields(gt)[2]]


def _game_memory_archive_enabled(value: Any, game_type: str | None = None) -> bool:
    if not isinstance(value, dict):
        return _game_memory_default_enabled(game_type or "soccer")
    gt = _normalize_game_memory_type(game_type or value.get("game_type") or "soccer")
    return _game_memory_policy(gt, value)[_game_memory_policy_fields(gt)[3]]


def _game_memory_postgame_context_enabled(value: Any, game_type: str | None = None) -> bool:
    if not isinstance(value, dict):
        return _game_memory_default_enabled(game_type or "soccer")
    gt = _normalize_game_memory_type(game_type or value.get("game_type") or "soccer")
    return _game_memory_policy(gt, value)[_game_memory_policy_fields(gt)[4]]


def _soccer_game_memory_player_interaction_enabled(value: Any) -> bool:
    return _game_memory_player_interaction_enabled(value, "soccer")


def _soccer_game_memory_event_reply_enabled(value: Any) -> bool:
    return _game_memory_event_reply_enabled(value, "soccer")


def _soccer_game_memory_archive_enabled(value: Any) -> bool:
    return _game_memory_archive_enabled(value, "soccer")


def _soccer_game_memory_postgame_context_enabled(value: Any) -> bool:
    return _game_memory_postgame_context_enabled(value, "soccer")


def _game_memory_enabled(value: Any, game_type: str = "soccer") -> bool:
    """Legacy aggregate accessor retained for old callers and payloads."""
    if isinstance(value, dict):
        gt = _normalize_game_memory_type(game_type or value.get("game_type") or "soccer")
        return _game_memory_policy(gt, value)[_game_memory_policy_fields(gt)[0]]
    return _game_memory_default_enabled(game_type)


def _game_memory_camel_key(game_type: str, field: str) -> str:
    suffix = field.replace(f"{game_type}_game_memory_", "")
    parts = suffix.split("_")
    return f"{game_type}GameMemory" + "".join(part.capitalize() for part in parts)


def _attach_game_memory_flag_to_event(
    event: dict,
    state: dict | None,
    game_type: str | None = None,
) -> dict:
    event_payload = dict(event) if isinstance(event, dict) else {}
    gt = _normalize_game_memory_type(game_type or (state or {}).get("game_type") or "soccer")
    fields = _game_memory_policy_fields(gt)
    payload_keys = _game_memory_payload_keys(gt)
    has_policy_key = any(
        key in event_payload
        for keys in payload_keys.values()
        for key in keys
    )
    if state is None and not has_policy_key:
        return event_payload
    policy = _game_memory_policy(gt, state or {})
    for field in fields:
        event_payload.setdefault(field, policy[field])
        event_payload.setdefault(_game_memory_camel_key(gt, field), policy[field])
    event_payload.setdefault("gameMemoryEnabled", policy[fields[0]])
    event_payload.setdefault("game_memory_enabled", policy[fields[0]])
    event_payload.setdefault("gameMemoryPlayerInteractionEnabled", policy[fields[1]])
    event_payload.setdefault("game_memory_player_interaction_enabled", policy[fields[1]])
    event_payload.setdefault("gameMemoryEventReplyEnabled", policy[fields[2]])
    event_payload.setdefault("game_memory_event_reply_enabled", policy[fields[2]])
    event_payload.setdefault("gameMemoryArchiveEnabled", policy[fields[3]])
    event_payload.setdefault("game_memory_archive_enabled", policy[fields[3]])
    event_payload.setdefault("gameMemoryPostgameContextEnabled", policy[fields[4]])
    event_payload.setdefault("game_memory_postgame_context_enabled", policy[fields[4]])
    return event_payload


def _is_repeated_neko_invite(opening_line: str, neko_invite_text: str) -> bool:
    line = re.sub(r"[\s，。！？、,.!?~～\"'“”‘’]+", "", str(opening_line or ""))
    invite = re.sub(r"[\s，。！？、,.!?~～\"'“”‘’]+", "", str(neko_invite_text or ""))
    if not line or not invite:
        return False
    return line == invite or line in invite or invite in line


def _default_soccer_pregame_context(*, initial_difficulty: str | None = None) -> dict:
    difficulty = initial_difficulty if initial_difficulty in _SOCCER_DEFAULT_DIFFICULTIES else _soccer_random_default_difficulty()
    return {
        "launchIntent": "unknown",
        "confidence": 0.0,
        "evidence": [],
        "nekoEmotion": "calm",
        "emotionIntensity": 0.0,
        "emotionInertia": "low",
        "gameStance": "neutral_play",
        "stanceNote": "证据不足，按普通陪玩开局。",
        "initialMood": "calm",
        "initialDifficulty": difficulty,
        "openingLine": "",
        "tonePolicy": "普通陪玩，轻松自然，不强行解释成哄开心或关系修复。",
        "difficultyPolicy": "普通陪玩默认中等难度；后续由局内互动和游戏 AI 自然调整。",
        "moodPolicy": "沿用普通陪玩表现；不引入强情绪惯性。",
        "softeningSignals": [],
        "hardeningSignals": [],
        "neutralEventPolicy": "普通比赛事件只产生即时反应，不强行改变关系状态。",
        "specialPolicies": [],
        "postgameCarryback": "赛后只按真实比赛过程和互动自然归档。",
    }


def _normalize_soccer_pregame_context(value: Any, *, neko_invite_text: str = "") -> tuple[dict, bool]:
    """Normalize model output. Returns (context, had_invalid_fields)."""
    base = _default_soccer_pregame_context()
    if not isinstance(value, dict):
        return base, True

    context = dict(base)
    invalid = False

    string_fields = (
        "launchIntent",
        "nekoEmotion",
        "stanceNote",
        "tonePolicy",
        "difficultyPolicy",
        "moodPolicy",
        "neutralEventPolicy",
        "postgameCarryback",
    )
    for field in string_fields:
        if field in value:
            text = _normalize_short_text(value.get(field), max_chars=220)
            if text:
                context[field] = text
            elif value.get(field) not in (None, ""):
                invalid = True

    if "confidence" in value:
        try:
            confidence = float(value.get("confidence"))
            if 0.0 <= confidence <= 1.0:
                context["confidence"] = confidence
            else:
                invalid = True
        except (TypeError, ValueError):
            invalid = True

    if "emotionIntensity" in value:
        try:
            intensity = float(value.get("emotionIntensity"))
            if 0.0 <= intensity <= 1.0:
                context["emotionIntensity"] = intensity
            else:
                invalid = True
        except (TypeError, ValueError):
            invalid = True

    if "emotionInertia" in value:
        inertia = str(value.get("emotionInertia") or "").strip()
        if inertia in _SOCCER_EMOTION_INERTIA:
            context["emotionInertia"] = inertia
        else:
            invalid = True

    if "gameStance" in value:
        stance = str(value.get("gameStance") or "").strip()
        if stance in _SOCCER_GAME_STANCES:
            context["gameStance"] = stance
        else:
            invalid = True

    if "initialMood" in value:
        mood = str(value.get("initialMood") or "").strip()
        if mood in _SOCCER_MOODS:
            context["initialMood"] = mood
        else:
            invalid = True

    if "initialDifficulty" in value:
        difficulty = str(value.get("initialDifficulty") or "").strip()
        if difficulty in _SOCCER_DIFFICULTIES:
            context["initialDifficulty"] = difficulty
        else:
            invalid = True

    if "openingLine" in value:
        opening_line = _normalize_short_text(value.get("openingLine"), max_chars=0)
        if len(opening_line) > 15:
            opening_line = ""
            invalid = True
        if opening_line and _is_repeated_neko_invite(opening_line, neko_invite_text):
            opening_line = ""
        context["openingLine"] = opening_line

    for field in ("evidence", "softeningSignals", "hardeningSignals", "specialPolicies"):
        if field in value:
            items = _normalize_text_items(value.get(field), max_items=5, max_chars=100)
            if items or value.get(field) in (None, "", []):
                context[field] = items
            else:
                invalid = True

    # 普通陪玩和任何被兜底出来的开局都不能默认 max。
    if context["gameStance"] == "neutral_play":
        if context["initialDifficulty"] not in _SOCCER_DEFAULT_DIFFICULTIES:
            invalid = True
        context["initialDifficulty"] = _soccer_random_default_difficulty()
        if not context.get("difficultyPolicy"):
            context["difficultyPolicy"] = base["difficultyPolicy"]

    return context, invalid


def _format_soccer_pregame_context_for_prompt(pre_game_context: Any, language: str | None = None) -> str:
    if not isinstance(pre_game_context, dict):
        return ""
    compact = json.dumps(pre_game_context, ensure_ascii=False, separators=(",", ":"))
    labels = get_soccer_pregame_context_formatter_labels(language)
    return (
        f"{labels['header']}\n"
        f"{compact}\n"
        f"{labels['usage']}\n"
    )


_BADMINTON_EXPRESSIONS = {"cheer", "shock", "hype", "anticipate", "bored", "tease"}
_BADMINTON_INTENSITIES = {"low", "medium", "high"}


def _default_badminton_pregame_context(*, initial_difficulty: str | None = None, mode: str = "spectator") -> dict:
    normalized_mode = _normalize_badminton_mode(mode)
    difficulty = initial_difficulty if initial_difficulty in _SOCCER_DIFFICULTIES else "lv2"
    mode_label = {
        "spectator": "自由练习",
        "duel": "羽毛球对拉",
    }.get(normalized_mode, "羽毛球挑战")
    return {
        "launchIntent": "unknown",
        "confidence": 0.0,
        "evidence": [],
        "nekoEmotion": "calm",
        "emotionIntensity": 0.0,
        "emotionInertia": "low",
        "gameStance": "neutral_play",
        "stanceNote": f"证据不足，按普通{mode_label}陪玩开局。",
        "initialMood": "calm",
        "initialExpression": "anticipate",
        "initialIntensity": "low",
        "initialDifficulty": difficulty,
        "openingLine": "",
        "tonePolicy": "普通陪玩，轻松自然，不强行解释成哄开心或关系修复。",
        "difficultyPolicy": "duel 默认 lv2；spectator 忽略初始 difficulty，由局内事件自然调整。",
        "moodPolicy": "沿用普通羽毛球陪玩表现；不引入强情绪惯性。",
        "expressionPolicy": "默认期待/轻吐槽；根据成功、失误、连中和对拉比分自然变化。",
        "softeningSignals": [],
        "hardeningSignals": [],
        "specialPolicies": [],
        "postgameCarryback": "赛后只按真实羽毛球过程和互动自然归档。",
    }


def _normalize_badminton_pregame_context(
    value: Any,
    *,
    neko_invite_text: str = "",
    mode: str = "spectator",
) -> tuple[dict, bool]:
    """Normalize badminton pregame model output. Returns (context, had_invalid_fields)."""
    base = _default_badminton_pregame_context(mode=mode)
    if not isinstance(value, dict):
        return base, True

    context = dict(base)
    invalid = False

    string_fields = (
        "launchIntent",
        "nekoEmotion",
        "stanceNote",
        "tonePolicy",
        "difficultyPolicy",
        "moodPolicy",
        "expressionPolicy",
        "postgameCarryback",
    )
    for field in string_fields:
        if field in value:
            text = _normalize_short_text(value.get(field), max_chars=220)
            if text:
                context[field] = text
            elif value.get(field) not in (None, ""):
                invalid = True

    if "confidence" in value:
        try:
            confidence = float(value.get("confidence"))
            if 0.0 <= confidence <= 1.0:
                context["confidence"] = confidence
            else:
                invalid = True
        except (TypeError, ValueError):
            invalid = True

    if "emotionIntensity" in value:
        try:
            intensity = float(value.get("emotionIntensity"))
            if 0.0 <= intensity <= 1.0:
                context["emotionIntensity"] = intensity
            else:
                invalid = True
        except (TypeError, ValueError):
            invalid = True

    if "emotionInertia" in value:
        inertia = str(value.get("emotionInertia") or "").strip()
        if inertia in _SOCCER_EMOTION_INERTIA:
            context["emotionInertia"] = inertia
        else:
            invalid = True

    if "gameStance" in value:
        stance = str(value.get("gameStance") or "").strip()
        if stance in _SOCCER_GAME_STANCES:
            context["gameStance"] = stance
        else:
            invalid = True

    if "initialMood" in value:
        mood = str(value.get("initialMood") or "").strip()
        if mood in _SOCCER_MOODS:
            context["initialMood"] = mood
        else:
            invalid = True

    if "initialExpression" in value:
        expression = str(value.get("initialExpression") or "").strip()
        if expression in _BADMINTON_EXPRESSIONS:
            context["initialExpression"] = expression
        else:
            invalid = True

    if "initialIntensity" in value:
        intensity = str(value.get("initialIntensity") or "").strip()
        if intensity in _BADMINTON_INTENSITIES:
            context["initialIntensity"] = intensity
        else:
            invalid = True

    if "initialDifficulty" in value:
        difficulty = str(value.get("initialDifficulty") or "").strip()
        if difficulty in _SOCCER_DIFFICULTIES:
            context["initialDifficulty"] = difficulty
        else:
            invalid = True

    if "openingLine" in value:
        opening_line = _normalize_short_text(value.get("openingLine"), max_chars=0)
        if len(opening_line) > 15:
            opening_line = ""
            invalid = True
        if opening_line and _is_repeated_neko_invite(opening_line, neko_invite_text):
            opening_line = ""
        context["openingLine"] = opening_line

    for field in ("evidence", "softeningSignals", "hardeningSignals", "specialPolicies"):
        if field in value:
            items = _normalize_text_items(value.get(field), max_items=5, max_chars=100)
            if items or value.get(field) in (None, "", []):
                context[field] = items
            else:
                invalid = True

    if context["gameStance"] == "neutral_play":
        if context["initialDifficulty"] not in _SOCCER_DEFAULT_DIFFICULTIES:
            invalid = True
        context["initialDifficulty"] = "lv2"
        if context["initialMood"] == "angry":
            context["initialMood"] = "calm"
            invalid = True
        if context["initialIntensity"] == "high":
            context["initialIntensity"] = "low"
            invalid = True

    return context, invalid


def _format_badminton_pregame_context_for_prompt(
    pre_game_context: Any,
    language: str | None = None,
    *,
    mode: str = "spectator",
) -> str:
    if not isinstance(pre_game_context, dict):
        return ""
    compact = json.dumps(pre_game_context, ensure_ascii=False, separators=(",", ":"))
    labels = get_badminton_pregame_context_formatter_labels(language)
    mode_label = {
        "spectator": "羽毛球自由练习",
        "duel": "羽毛球对拉",
    }.get(_normalize_badminton_mode(mode), "羽毛球挑战")
    return (
        f"{labels['header']}\n"
        f"mode={mode_label}\n"
        f"{compact}\n"
        f"{labels['usage']}\n"
    )


def _dialog_id_index(dialog: list[dict], dialog_id: str) -> int:
    if not dialog_id:
        return -1
    for idx, item in enumerate(dialog):
        if isinstance(item, dict) and str(item.get("id") or "") == dialog_id:
            return idx
    return -1


def _game_context_recent_dialogues(state: dict, keep_count: int = _GAME_CONTEXT_FAILURE_VISIBLE_WINDOW_MAX_COUNT) -> list[dict]:
    dialog = [item for item in state.get("game_dialog_log") or [] if isinstance(item, dict)]
    if not dialog:
        return []
    recent_ids = [
        str(item_id)
        for item_id in state.get("game_context_recent_ids") or []
        if str(item_id or "").strip()
    ]
    if recent_ids:
        by_id = {str(item.get("id") or ""): item for item in dialog}
        recent = [by_id[item_id] for item_id in recent_ids if item_id in by_id]
        if recent:
            return recent[-keep_count:]
    return dialog[-keep_count:]


def _game_context_dialog_lines(
    dialogues: list[dict],
    *,
    max_items: int = 12,
    language: str | None = None,
) -> list[str]:
    lines: list[str] = []
    for item in dialogues[-max_items:]:
        if not isinstance(item, dict):
            continue
        dialog_id = str(item.get("id") or "").strip()
        line = _dialog_memory_line(item, language)
        if dialog_id and line:
            lines.append(f"{dialog_id}: {line}")
        elif line:
            lines.append(line)
    return lines


def _signals_compact_for_prompt(signals: Any) -> dict:
    normalized = _normalize_game_context_signals(signals)
    compact: dict[str, list[dict]] = {}
    for group, items in normalized.items():
        compact[group] = [
            {
                "signalLabel": item.get("signalLabel"),
                "summary": item.get("summary"),
                "evidence": item.get("evidence") or [],
                "count": item.get("count", 1),
                "lastRound": item.get("lastRound"),
            }
            for item in items
        ]
    return compact


def _compact_nonempty_game_context_signals(signals: Any) -> dict:
    compact = _signals_compact_for_prompt(signals)
    return {group: items for group, items in compact.items() if items}


def _game_context_signals_text(signals: Any) -> str:
    compact = _compact_nonempty_game_context_signals(signals)
    if not compact:
        return ""
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def _format_game_context_for_prompt(context: Any, language: str | None = None) -> str:
    if not isinstance(context, dict):
        return ""
    labels = get_game_context_formatter_labels(language)
    degraded = context.get("degraded") is True
    recent_lines = _game_context_dialog_lines(
        context.get("recent_dialogues") or [],
        max_items=_GAME_CONTEXT_FAILURE_VISIBLE_WINDOW_MAX_COUNT,
        language=language,
    )
    if degraded:
        parts = [
            labels["degraded_status"],
            labels["degraded_usage"],
        ]
        if recent_lines:
            parts.append(labels["recent_window"])
            parts.extend(f"- {line}" for line in recent_lines)
        return "\n".join(parts) + "\n"

    summary = _normalize_short_text(context.get("summary"), max_chars=900)
    signals_text = _game_context_signals_text(context.get("signals"))
    parts = [labels["header"]]
    if summary:
        parts.append(labels["summary"].format(summary=summary))
    if signals_text:
        parts.append(labels["signals"])
        parts.append(signals_text)
    if recent_lines:
        parts.append(labels["recent_window"])
        parts.extend(f"- {line}" for line in recent_lines)
    if len(parts) == 1:
        return ""
    parts.append(labels["current_state"])
    parts.append(labels["usage"])
    return "\n".join(parts) + "\n"


def _build_game_context_prompt_payload(state: dict | None, *, include_recent: bool = True) -> dict | None:
    if not isinstance(state, dict):
        return None
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    return {
        "summary": str(state.get("game_context_summary") or ""),
        "signals": _normalize_game_context_signals(state.get("game_context_signals")),
        "recent_dialogues": (
            _game_context_recent_dialogues(state, _GAME_CONTEXT_FAILURE_VISIBLE_WINDOW_MAX_COUNT)
            if include_recent else []
        ),
        "degraded": organizer.get("degraded") is True,
        "organizer": organizer,
    }


_GAME_INTERNAL_LOG_PREFIX_RE = re.compile(r"^\s*glog_\d+\s*[:：]\s*", re.IGNORECASE)
_GAME_CONTROL_PAREN_TRAIL_RE = re.compile(
    r"\s*[\(（][^()（）]*(?:mood|difficulty|reason|expression|intensity|balanceHint|balance_hint)\s*[:=][^()（）]*[\)）]\s*$",
    re.IGNORECASE,
)
_GAME_CONTROL_JSON_TRAIL_RE = re.compile(
    r"\s*\{[^{}]*(?:\"?(?:mood|difficulty|reason|expression|intensity|balanceHint|balance_hint)\"?\s*[:=])[^{}]*\}\s*$",
    re.IGNORECASE,
)
_GAME_INTERNAL_CONTROL_LINE_RE = re.compile(
    r"^\s*(?:reason|mood|difficulty|expression|intensity|balanceHint|balance_hint)\s*[:=]",
    re.IGNORECASE,
)
_GAME_INTERNAL_ADVICE_RE = re.compile(
    r"(?:balanceHint|balance_hint|system advice|system suggestion|系统建议|平衡建议|控制建议)",
    re.IGNORECASE,
)
_GAME_LLM_VISIBLE_EVENT_TOP_LEVEL_DROP_KEYS = frozenset({
    "badmintonGameMemoryEnabled",
    "badminton_game_memory_enabled",
    "badmintonGameMemoryPlayerInteractionEnabled",
    "badminton_game_memory_player_interaction_enabled",
    "badmintonGameMemoryEventReplyEnabled",
    "badminton_game_memory_event_reply_enabled",
    "badmintonGameMemoryArchiveEnabled",
    "badminton_game_memory_archive_enabled",
    "badmintonGameMemoryPostgameContextEnabled",
    "badminton_game_memory_postgame_context_enabled",
    "soccerGameMemoryEnabled",
    "soccer_game_memory_enabled",
    "soccerGameMemoryPlayerInteractionEnabled",
    "soccer_game_memory_player_interaction_enabled",
    "soccerGameMemoryEventReplyEnabled",
    "soccer_game_memory_event_reply_enabled",
    "soccerGameMemoryArchiveEnabled",
    "soccer_game_memory_archive_enabled",
    "soccerGameMemoryPostgameContextEnabled",
    "soccer_game_memory_postgame_context_enabled",
    "gameMemoryEnabled",
    "game_memory_enabled",
    "gameMemoryPlayerInteractionEnabled",
    "game_memory_player_interaction_enabled",
    "gameMemoryEventReplyEnabled",
    "game_memory_event_reply_enabled",
    "gameMemoryArchiveEnabled",
    "game_memory_archive_enabled",
    "gameMemoryPostgameContextEnabled",
    "game_memory_postgame_context_enabled",
    "lanlan_name",
})
_SOCCER_LLM_VISIBLE_SNAPSHOT_DROP_KEYS = frozenset({
    "aiFreezeSec",
    "playerKickStartleWindowSec",
    "playerKickWallBounceForStartle",
    "startle",
    "startleDirectCdSec",
    "startleGrazeCdSec",
    "startleMutualLockSec",
    "zoneoutCooldownSec",
    "ballGhost",
})


def _sanitize_game_visible_line(text: Any) -> str:
    """Keep only natural player-visible speech; strip game route metadata leaks."""
    lines: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        line = _GAME_INTERNAL_LOG_PREFIX_RE.sub("", line).strip()
        if not line:
            continue
        if _GAME_INTERNAL_CONTROL_LINE_RE.search(line) or _GAME_INTERNAL_ADVICE_RE.search(line):
            continue
        previous = None
        while previous != line:
            previous = line
            line = _GAME_CONTROL_JSON_TRAIL_RE.sub("", line).strip()
            line = _GAME_CONTROL_PAREN_TRAIL_RE.sub("", line).strip()
        if not line:
            continue
        if _GAME_INTERNAL_CONTROL_LINE_RE.search(line) or _GAME_INTERNAL_ADVICE_RE.search(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _sanitize_soccer_llm_visible_snapshot(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_soccer_llm_visible_snapshot(item)
            for key, item in value.items()
            if key not in _SOCCER_LLM_VISIBLE_SNAPSHOT_DROP_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_soccer_llm_visible_snapshot(item) for item in value]
    return value


def _build_game_llm_visible_event(game_type: str, event: Any) -> Any:
    """Build the event copy sent to the main game LLM without internal route fields."""
    if not isinstance(event, dict):
        return event

    visible_event = {
        key: value
        for key, value in event.items()
        if key not in _GAME_LLM_VISIBLE_EVENT_TOP_LEVEL_DROP_KEYS
    }
    if _normalize_game_memory_type(game_type) != "soccer":
        return visible_event

    if "currentState" in visible_event:
        visible_event["currentState"] = _sanitize_soccer_llm_visible_snapshot(
            visible_event.get("currentState")
        )

    pending_items = visible_event.get("pendingItems")
    if isinstance(pending_items, list):
        sanitized_items: list[Any] = []
        for item in pending_items:
            if not isinstance(item, dict):
                sanitized_items.append(item)
                continue
            sanitized_item = dict(item)
            if "snapshot" in sanitized_item:
                sanitized_item["snapshot"] = _sanitize_soccer_llm_visible_snapshot(
                    sanitized_item.get("snapshot")
                )
            sanitized_items.append(sanitized_item)
        visible_event["pendingItems"] = sanitized_items

    return visible_event


def _game_dialog_history_user_text(item: dict, labels: dict[str, str]) -> str:
    item_type = item.get("type")
    if item_type == "user":
        text = str(item.get("text") or "").strip()
        return labels["player_line"].format(text=text) if text else ""
    if item_type == "game_event":
        kind = str(item.get("kind") or "event")
        text = str(item.get("text") or "").strip()
        if text:
            return labels["game_event_text"].format(kind=kind, text=text)
        return labels["game_event"].format(kind=kind)
    return ""


def _game_dialog_history_assistant_text(item: dict) -> str:
    item_type = item.get("type")
    if item_type == "assistant":
        line = str(item.get("line") or "").strip()
    elif item_type == "game_event":
        line = str(item.get("result_line") or "").strip()
    else:
        return ""
    return _sanitize_game_visible_line(line)


def _build_game_recent_history_messages(state: dict | None, language: str | None = None) -> list:
    if not isinstance(state, dict):
        return []
    from utils.llm_client import AIMessage, HumanMessage

    labels = get_game_recent_history_message_labels(language)
    messages = []
    last_role = "system"
    dialogues = _game_context_recent_dialogues(state, _GAME_CONTEXT_FAILURE_VISIBLE_WINDOW_MAX_COUNT)
    if dialogues and isinstance(dialogues[-1], dict) and dialogues[-1].get("type") == "user":
        dialogues = dialogues[:-1]
    for item in dialogues:
        if not isinstance(item, dict):
            continue
        user_text = _game_dialog_history_user_text(item, labels)
        assistant_text = _game_dialog_history_assistant_text(item)
        if user_text:
            if last_role == "human" and messages:
                previous_content = str(getattr(messages[-1], "content", "")).rstrip()
                messages[-1].content = f"{previous_content}\n{user_text}" if previous_content else user_text
            else:
                messages.append(HumanMessage(content=user_text))
            last_role = "human"
        if assistant_text:
            if last_role == "human":
                messages.append(AIMessage(content=assistant_text))
                last_role = "ai"
            else:
                messages.append(HumanMessage(content=labels["previous_character_output"].format(text=assistant_text)))
                last_role = "human"
    return messages


def _reset_game_session_text_history_for_turn(entry: dict, route_state: dict | None) -> None:
    session = entry.get("session") if isinstance(entry, dict) else None
    if session is None:
        return
    from utils.llm_client import SystemMessage

    instructions = str(entry.get("instructions") or getattr(session, "_instructions", "") or "")
    language = entry.get("user_language") if isinstance(entry, dict) else None
    history = [SystemMessage(content=instructions)] if instructions else []
    history.extend(_build_game_recent_history_messages(route_state, language))
    session._instructions = instructions
    session._conversation_history = history


def _normalize_quick_lines(value: Any, allowed_keys: set[str] | None = None) -> Dict[str, list[str]]:
    """Validate and trim quick-path lines; failed keys fall back to built-in copy."""
    if not isinstance(value, dict):
        return {}

    normalized: Dict[str, list[str]] = {}
    keys = allowed_keys or _SOCCER_QUICK_LINE_KEYS
    for key in keys:
        lines = value.get(key)
        if not isinstance(lines, list):
            continue
        clean_lines: list[str] = []
        for item in lines:
            if not isinstance(item, str):
                continue
            line = item.strip().replace("\n", " ")
            if not line:
                continue
            clean_lines.append(line[:24])
            if len(clean_lines) >= 4:
                break
        if clean_lines:
            normalized[key] = clean_lines
    return normalized


def _get_badminton_quick_lines_fallback(language: str | None = None) -> Dict[str, list[str]]:
    return get_badminton_quick_lines_fallback(language)


def _extract_request_language_full(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    raw = data.get('i18n_language') or data.get('language') or data.get('lang')
    if not raw or not is_supported_language_code(raw):
        return None
    try:
        return normalize_language_code(str(raw), format='full')
    except Exception:
        return None


def _absorb_request_language(data: Any, lanlan_name: str | None) -> str | None:
    """Extract the frontend i18n ground truth from the request body, writing it back to ``mgr.user_language`` along the way.

    Motivation: ``mgr.user_language`` gets overwritten by the global cache on the
    ``start_session`` path (see ``main_logic/core.py``), and the global cache is the
    product of a Steam SDK startup race failure. The frontend i18n has already
    fetched the correct value asynchronously from ``/api/config/steam_language``;
    piggybacking it on the request body lets us correct the session state
    immediately. Returns the normalized short code (``zh`` / ``en`` ...); returns
    ``None`` when unavailable, letting the caller follow the old fallback chain.
    """
    if not isinstance(data, dict):
        return None
    raw = data.get('i18n_language') or data.get('language') or data.get('lang')
    if not raw:
        return None
    # 前端 / 第三方客户端可能传 ``'undefined'`` / corrupted localStorage / 任意 garbage，
    # ``normalize_language_code`` 对未识别值默认回退 ``'en'``，会静默把 mgr.user_language
    # 翻成英文。回写 session 状态前先用公共白名单 helper 挡掉。
    if not is_supported_language_code(raw):
        return None
    try:
        normalized_short = normalize_language_code(str(raw), format='short')
    except Exception:
        return None
    if not normalized_short:
        return None
    try:
        name = str(lanlan_name or "").strip()
        if name:
            session_manager = get_session_manager()
            manager = session_manager.get(name) if hasattr(session_manager, "get") else None
            if manager is not None:
                normalized_full = normalize_language_code(str(raw), format='full')
                current = getattr(manager, "user_language", None)
                if normalized_full and current != normalized_full:
                    setter = getattr(manager, "set_user_language", None)
                    if callable(setter):
                        setter(str(raw))
    except Exception:
        logger.debug("🎮 absorb request language 失败 lanlan=%s", lanlan_name, exc_info=True)
    return normalized_short


def _resolve_game_prompt_language(
    lanlan_name: str | None = None,
    data: Any = None,
) -> str:
    """Resolve the user's current language for game-route LLM prompts.

    Priority (same shape as ``main_routers/system_router._resolve_proactive_locale``):
      1. ``i18n_language`` / ``language`` / ``lang`` in ``data`` (request body)
         — explicitly sent by the frontend, highest priority; the value is also
         written back to ``mgr.user_language`` so every downstream
         ``_resolve_game_prompt_language`` in this request without access to
         ``data`` hits it too.
      2. ``mgr.user_language`` — the session ground truth synced by the websocket
         greeting_check.
      3. ``get_global_language()`` — process-level cache, final fallback.

    Layer 1 covers a hole beyond PR #1150: in a Steam=zh / system=en environment,
    ``mgr.user_language`` gets overwritten by the (wrong, 'en') global cache at
    ``start_session``, and the soccer short-circuit fires before the frontend ws
    greeting_check pushes the right value up, so it could only see the wrong 'en'.
    Making the request body's i18n truth the top-priority source, combined with
    the self-healing write-back, closes this race.
    """
    request_lang = _absorb_request_language(data, lanlan_name)
    if request_lang:
        return request_lang
    try:
        name = str(lanlan_name or "").strip()
        session_manager = get_session_manager()
        manager = session_manager.get(name) if name and hasattr(session_manager, "get") else None
        language = getattr(manager, "user_language", None)
        if language:
            return normalize_language_code(str(language), format="short") or "en"
    except Exception:
        logger.debug(
            "🎮 赛后归档语言解析失败，使用默认 prompt 语言: lanlan=%s",
            lanlan_name,
            exc_info=True,
        )

    try:
        return normalize_language_code(get_global_language(), format="short") or "en"
    except Exception:
        return "en"


def _get_character_info(lanlan_name: str | None = None) -> Dict[str, Any]:
    """Get the specified character's info from shared_state; uses the current character when unspecified."""
    try:
        config_manager = get_config_manager()
    except RuntimeError:
        # Unit tests historically monkeypatch _get_current_character_info()
        # without bootstrapping shared_state. Keep that seam usable while the
        # production path below supports explicit lanlan_name lookup.
        current_getter = globals().get("_get_current_character_info")
        if getattr(current_getter, "__name__", "") != "_get_current_character_info":
            info = dict(current_getter())
            if lanlan_name:
                info.setdefault("lanlan_name", str(lanlan_name or "").strip())
            info.setdefault("user_language", _resolve_game_prompt_language(info.get("lanlan_name")))
            return info
        raise
    characters = config_manager.load_characters()
    current_name = str(lanlan_name or characters.get('当前猫娘', '') or '').strip()

    master_data = characters.get('主人', {})
    # 显式 str 归一化：'档案名' 来自用户编辑的角色配置 JSON，可能是 None / 数字
    # / 其他非字符串。下面 .replace 的第二个参数必须是 str，且 master_name 还会
    # 直接进入返回 dict 给下游消费，统一在源头收口。
    master_name = str(master_data.get('档案名', '玩家') or '玩家')

    # 获取角色人格 prompt
    # Why: lanlan_prompt_map 存的是带 {LANLAN_NAME} / {MASTER_NAME} 占位符的原始
    # 模板（普通会话路径在 main_server 写入 SessionManager 时才替换）。Game 流程
    # 直接从 config_manager 拿，必须在源头补这一步替换，否则下游 _build_game_prompt
    # / quick_lines / pregame context AI 拼出来的 prompt 会含字面占位符，触发
    # llm_prompt_leak_check 警告并污染人设。
    # 模板本身也用 str() 兜底：极端情况下 lanlan_prompt_map 里的值可能是 None。
    _, _, _, _, _, lanlan_prompt_map, _, _, _ = config_manager.get_character_data()
    lanlan_prompt = str(lanlan_prompt_map.get(current_name, '') or '') \
        .replace('{LANLAN_NAME}', current_name) \
        .replace('{MASTER_NAME}', master_name)

    # 获取小游戏主模型配置；默认跟随文本对话模型，用户可在 API 设置中独立覆盖。
    conversation_config = config_manager.get_model_api_config('game_main')

    return {
        'lanlan_name': current_name,
        'master_name': master_name,
        'lanlan_prompt': lanlan_prompt,
        'model': conversation_config.get('model', ''),
        'base_url': conversation_config.get('base_url', ''),
        'api_type': conversation_config.get('api_type', ''),
        'provider_type': conversation_config.get('provider_type', ''),
        'api_key': conversation_config.get('api_key', ''),
        'user_language': _resolve_game_prompt_language(current_name),
    }


def _get_current_character_info() -> Dict[str, Any]:
    """Get the current character's info from shared_state."""
    return _get_character_info()


def _get_game_route_summary_llm_info(lanlan_name: str | None = None) -> Dict[str, Any]:
    """Resolve character metadata but use the summary model tier for helper calls."""
    info = dict(_get_character_info(lanlan_name))
    try:
        summary_config = get_config_manager().get_model_api_config("game_summary") or {}
    except RuntimeError:
        return info
    model = str(summary_config.get("model") or "").strip()
    base_url = str(summary_config.get("base_url") or "").strip()
    api_key = str(summary_config.get("api_key") or "").strip()
    if not (model and base_url):
        return info
    info.update({
        "model": model,
        "base_url": base_url,
        "api_type": summary_config.get("api_type") or info.get("api_type", ""),
        "provider_type": summary_config.get("provider_type") or info.get("provider_type", ""),
        "api_key": api_key,
    })
    return info


async def _fetch_recent_history_for_pregame(lanlan_name: str) -> tuple[str, str]:
    try:
        from config import MEMORY_SERVER_PORT
        from utils.internal_http_client import get_internal_http_client

        client = get_internal_http_client()
        response = await client.get(
            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/get_recent_history/{lanlan_name}",
            timeout=5.0,
        )
        if not response.is_success:
            return "", "recent_history_failed"
        return str(response.text or ""), ""
    except Exception as exc:
        logger.warning("🎮 开局近期记录读取失败，使用空历史: lanlan=%s err=%s", lanlan_name, exc)
        return "", "recent_history_failed"


async def _run_pregame_context_ai(
    *,
    lanlan_name: str,
    master_name: str,
    lanlan_prompt: str,
    recent_history: str,
    neko_initiated: bool,
    neko_invite_text: str,
    prompt_template: str,
    extra_payload: dict | None = None,
) -> dict:
    char_info = _get_character_info(lanlan_name)
    user_payload = {
        "lanlanName": lanlan_name,
        "masterName": master_name,
        "recentHistory": recent_history or "开始聊天前，没有历史记录。",
        "nekoInitiated": bool(neko_initiated),
        "nekoInviteText": neko_invite_text,
        "characterPromptExcerpt": str(lanlan_prompt or "")[:1200],
    }
    if isinstance(extra_payload, dict):
        user_payload.update(extra_payload)

    try:
        from utils.file_utils import robust_json_loads
        from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm_async
        from utils.token_tracker import set_call_type

        set_call_type("game_pregame_context")
        llm = await create_chat_llm_async(
            char_info["model"],
            char_info["base_url"],
            char_info["api_key"],
            provider_type=char_info.get("provider_type"),
            max_completion_tokens=900,
            timeout=20,
        )
        async with llm:
            result = await llm.ainvoke([  # noqa: LLM_INPUT_BUDGET  # game-session-scoped input (snapshot / history / archive / config), bounded by a single finite game; not external free-text. Deeper per-field truncation tracked as a game-domain follow-up.
                SystemMessage(content=prompt_template),
                HumanMessage(
                    content=f"{json.dumps(user_payload, ensure_ascii=False)}\n{PREGAME_CONTEXT_INPUT_WATERMARK}"
                ),
            ])
        raw = _strip_json_fence(str(result.content or ""))
        parsed = robust_json_loads(raw)
    except Exception as exc:
        logger.warning("🎮 开局上下文分析失败: lanlan=%s err=%s", lanlan_name, exc)
        raise

    if not isinstance(parsed, dict):
        raise ValueError("pregame_context_json_not_object")
    return parsed


async def _run_soccer_pregame_context_ai(
    *,
    lanlan_name: str,
    master_name: str,
    lanlan_prompt: str,
    recent_history: str,
    neko_initiated: bool,
    neko_invite_text: str,
) -> dict:
    char_info = _get_character_info(lanlan_name)
    return await _run_pregame_context_ai(
        lanlan_name=lanlan_name,
        master_name=master_name,
        lanlan_prompt=lanlan_prompt,
        recent_history=recent_history,
        neko_initiated=neko_initiated,
        neko_invite_text=neko_invite_text,
        prompt_template=get_soccer_pregame_context_prompt(char_info.get("user_language")),
        extra_payload={"gameType": "soccer"},
    )


async def _build_soccer_pregame_context(
    *,
    game_type: str,
    session_id: str,
    lanlan_name: str,
    neko_initiated: bool,
    neko_invite_text: str,
) -> tuple[dict, str, str]:
    char_info = _get_character_info(lanlan_name)
    recent_history, history_error = await _fetch_recent_history_for_pregame(lanlan_name)
    _log_game_debug_material(
        "pregame_recent_history",
        recent_history or "开始聊天前，没有历史记录。",
        game_type=game_type,
        session_id=session_id,
        lanlan_name=lanlan_name,
        source="memory_server_recent_history" if not history_error else "fallback_empty_history",
    )

    try:
        raw_context = await _run_soccer_pregame_context_ai(
            lanlan_name=lanlan_name,
            master_name=str(char_info.get("master_name") or "玩家"),
            lanlan_prompt=str(char_info.get("lanlan_prompt") or ""),
            recent_history=recent_history,
            neko_initiated=neko_initiated,
            neko_invite_text=neko_invite_text,
        )
    except ValueError as exc:
        logger.warning("🎮 开局上下文 JSON 非法，使用普通陪玩兜底: lanlan=%s err=%s", lanlan_name, exc)
        context = _default_soccer_pregame_context()
        return context, "fallback", "invalid_json"
    except Exception:
        context = _default_soccer_pregame_context()
        return context, "fallback", "ai_failed"

    context, invalid_fields = _normalize_soccer_pregame_context(
        raw_context,
        neko_invite_text=neko_invite_text,
    )
    source = "ai"
    error = "invalid_fields" if invalid_fields else history_error
    _log_game_debug_material(
        "pregame_context",
        {
            "source": source,
            "error": error,
            "context": context,
        },
        game_type=game_type,
        session_id=session_id,
        lanlan_name=lanlan_name,
        source="game_pregame_context",
    )
    return context, source, error


async def _build_badminton_pregame_context(
    *,
    game_type: str,
    session_id: str,
    lanlan_name: str,
    neko_initiated: bool,
    neko_invite_text: str,
    mode: str = "spectator",
) -> tuple[dict, str, str]:
    normalized_mode = _normalize_badminton_mode(mode)
    char_info = _get_character_info(lanlan_name)
    recent_history, history_error = await _fetch_recent_history_for_pregame(lanlan_name)
    _log_game_debug_material(
        "pregame_recent_history",
        recent_history or "开始聊天前，没有历史记录。",
        game_type=game_type,
        session_id=session_id,
        lanlan_name=lanlan_name,
        source="memory_server_recent_history" if not history_error else "fallback_empty_history",
    )

    try:
        raw_context = await _run_pregame_context_ai(
            lanlan_name=lanlan_name,
            master_name=str(char_info.get("master_name") or "玩家"),
            lanlan_prompt=str(char_info.get("lanlan_prompt") or ""),
            recent_history=recent_history,
            neko_initiated=neko_initiated,
            neko_invite_text=neko_invite_text,
            prompt_template=get_badminton_pregame_context_prompt(char_info.get("user_language")),
            extra_payload={"gameType": game_type, "mode": normalized_mode},
        )
    except ValueError as exc:
        logger.warning("🎮 羽毛球开局上下文 JSON 非法，使用普通陪玩兜底: lanlan=%s err=%s", lanlan_name, exc)
        context = _default_badminton_pregame_context(mode=normalized_mode)
        return context, "fallback", "invalid_json"
    except Exception:
        context = _default_badminton_pregame_context(mode=normalized_mode)
        return context, "fallback", "ai_failed"

    context, invalid_fields = _normalize_badminton_pregame_context(
        raw_context,
        neko_invite_text=neko_invite_text,
        mode=normalized_mode,
    )
    source = "ai"
    error = "invalid_fields" if invalid_fields else history_error
    _log_game_debug_material(
        "pregame_context",
        {
            "source": source,
            "error": error,
            "mode": normalized_mode,
            "context": context,
        },
        game_type=game_type,
        session_id=session_id,
        lanlan_name=lanlan_name,
        source="game_pregame_context",
    )
    return context, source, error


def _public_route_state(state: dict | None) -> dict:
    if not state:
        return {"game_route_active": False}
    public = {k: v for k, v in state.items() if not str(k).startswith("_")}
    public["dialog_count"] = len(public.get("game_dialog_log") or [])
    public["pending_output_count"] = len(public.get("pending_outputs") or [])
    return public


def _game_route_stale_session_response(
    state: dict | None,
    session_id: str,
    *,
    lanlan_name: str,
    method: str,
) -> dict | None:
    if not (state and session_id and session_id != str(state.get("session_id") or "")):
        return None

    result: dict[str, Any] = {
        "ok": True,
        "skipped": "stale_session",
        "reason": "session_id_mismatch",
        "handled": False,
        "lanlan_name": lanlan_name,
        "method": method,
        "state": _public_route_state(state),
    }
    if method == "project_text_mirror":
        result["mirrored"] = False
    elif method == "project_tts":
        result.update({
            "audio_sent": False,
            "audio_committed": False,
            "voice_source": {
                "provider": "project_tts",
                "method": "project_tts",
                "skipped": "stale_session",
            },
        })
    return result


def _game_route_closed_session_response(
    data: dict[str, Any],
    *,
    session_id: str,
    lanlan_name: str,
    method: str,
) -> dict | None:
    source = str(data.get("source") or "")
    if (
        not session_id
        or not data.get("lanlan_name")
        or source not in {"game-llm-result", "game_llm", "game_route"}
    ):
        return None

    result: dict[str, Any] = {
        "ok": True,
        "skipped": "stale_session",
        "reason": "route_closed",
        "handled": False,
        "lanlan_name": lanlan_name,
        "method": method,
        "state": _public_route_state(None),
    }
    if method == "project_text_mirror":
        result["mirrored"] = False
    elif method == "project_tts":
        result.update({
            "audio_sent": False,
            "audio_committed": False,
            "voice_source": {
                "provider": "project_tts",
                "method": "project_tts",
                "skipped": "stale_session",
            },
        })
    return result


def _detect_before_game_external_state(mgr: Any) -> tuple[str, bool]:
    """Return (mode, active) for the current ordinary external session."""
    if not mgr or not getattr(mgr, "is_active", False):
        return "none", False
    session = getattr(mgr, "session", None)
    try:
        from main_logic.omni_realtime_client import OmniRealtimeClient
        from main_logic.omni_offline_client import OmniOfflineClient
    except Exception:
        return str(getattr(mgr, "input_mode", "") or "none"), True
    if isinstance(session, OmniRealtimeClient):
        return "audio", True
    if isinstance(session, OmniOfflineClient):
        return "text", True
    return str(getattr(mgr, "input_mode", "") or "none"), True


def _resolve_lanlan_name(raw: Any = None) -> str:
    lanlan_name = str(raw or "").strip()
    if lanlan_name:
        return lanlan_name
    try:
        return str(_get_current_character_info().get("lanlan_name") or "").strip()
    except Exception:
        return ""


def _find_game_route_state_for_session(
    game_type: str,
    session_id: str,
    lanlan_name: str | None = None,
) -> dict | None:
    for state in _game_route_states.values():
        if (
            str(state.get("game_type") or "") == str(game_type or "")
            and str(state.get("session_id") or "") == str(session_id or "")
            and (
                not lanlan_name
                or str(state.get("lanlan_name") or "") == str(lanlan_name or "")
            )
        ):
            return state
    return None


def _build_route_state(
    game_type: str,
    session_id: str,
    lanlan_name: str,
    last_full_dialogue_count: int | None = None,
) -> dict:
    session_manager = get_session_manager()
    mgr = session_manager.get(lanlan_name)
    before_mode, before_active = _detect_before_game_external_state(mgr)
    try:
        keep_last = int(last_full_dialogue_count or _DEFAULT_LAST_FULL_DIALOGUE_COUNT)
    except (TypeError, ValueError):
        keep_last = _DEFAULT_LAST_FULL_DIALOGUE_COUNT
    keep_last = max(1, min(keep_last, 50))

    now = time.time()
    return {
        "game_type": game_type,
        "session_id": session_id,
        "lanlan_name": lanlan_name,
        "before_game_external_mode": before_mode,
        "before_game_external_active": before_active,
        "game_route_active": True,
        "game_external_voice_route_active": False,
        "game_external_text_route_active": False,
        "game_input_mode": "none",
        "activation_source": "game_event",
        "external_suspended_by_game": False,
        "should_resume_external_on_exit": before_mode == "audio" and before_active,
        "game_input_activation_log": [],
        "game_dialog_log": [],
        "game_dialog_seq": 0,
        "pending_outputs": [],
        "game_context_summary": "",
        "game_context_signals": _empty_game_context_signals(),
        "game_context_recent_ids": [],
        "game_context_organizer": {
            "running": False,
            "degraded": False,
            "failure_count": 0,
            "last_organized_id": "",
            "source": None,
            "error": "",
        },
        "game_last_full_dialogue_count": keep_last,
        "game_memory_tail_count": _DEFAULT_GAME_MEMORY_TAIL_COUNT,
        "soccer_game_memory_enabled": _DEFAULT_SOCCER_GAME_MEMORY_ENABLED,
        "soccer_game_memory_player_interaction_enabled": _DEFAULT_SOCCER_GAME_MEMORY_ENABLED,
        "soccer_game_memory_event_reply_enabled": _DEFAULT_SOCCER_GAME_MEMORY_ENABLED,
        "soccer_game_memory_archive_enabled": _DEFAULT_SOCCER_GAME_MEMORY_ENABLED,
        "soccer_game_memory_postgame_context_enabled": _DEFAULT_SOCCER_GAME_MEMORY_ENABLED,
        "badminton_game_memory_enabled": _DEFAULT_BADMINTON_GAME_MEMORY_ENABLED,
        "badminton_game_memory_player_interaction_enabled": _DEFAULT_BADMINTON_GAME_MEMORY_ENABLED,
        "badminton_game_memory_event_reply_enabled": _DEFAULT_BADMINTON_GAME_MEMORY_ENABLED,
        "badminton_game_memory_archive_enabled": _DEFAULT_BADMINTON_GAME_MEMORY_ENABLED,
        "badminton_game_memory_postgame_context_enabled": _DEFAULT_BADMINTON_GAME_MEMORY_ENABLED,
        "game_memory_enabled": _DEFAULT_SOCCER_GAME_MEMORY_ENABLED,
        "game_memory_player_interaction_enabled": _DEFAULT_SOCCER_GAME_MEMORY_ENABLED,
        "game_memory_event_reply_enabled": _DEFAULT_SOCCER_GAME_MEMORY_ENABLED,
        "game_memory_archive_enabled": _DEFAULT_SOCCER_GAME_MEMORY_ENABLED,
        "game_memory_postgame_context_enabled": _DEFAULT_SOCCER_GAME_MEMORY_ENABLED,
        "last_state": {},
        "finalScore": {},
        "preGameContext": {},
        "pre_game_context_source": "",
        "pre_game_context_error": "",
        "nekoInitiated": False,
        "nekoInviteText": "",
        "game_started": False,
        "game_started_at": None,
        "game_started_elapsed_ms": None,
        "game_exit_started_elapsed_ms": None,
        "accidental_game_entry_exit": False,
        "created_at": now,
        "last_activity": now,
        "heartbeat_enabled": True,
        "last_heartbeat_at": now,
        "heartbeat_interval_seconds": _GAME_ROUTE_HEARTBEAT_INTERVAL_SECONDS,
        "heartbeat_timeout_seconds": _GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS,
        "hidden_heartbeat_timeout_seconds": _GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS,
        "page_visible": True,
        "visibility_state": "visible",
    }


def _activate_game_route(
    game_type: str,
    session_id: str,
    lanlan_name: str,
    last_full_dialogue_count: int | None = None,
) -> dict:
    state = _build_route_state(game_type, session_id, lanlan_name, last_full_dialogue_count)
    _game_route_states[_route_state_key(lanlan_name, game_type)] = state
    logger.info(
        "🎮 游戏路由已激活: game=%s session=%s lanlan=%s before=%s active=%s",
        game_type,
        session_id,
        lanlan_name,
        state["before_game_external_mode"],
        state["before_game_external_active"],
    )
    return state


def _append_route_activation(state: dict, source: str, mode: str, detail: dict | None = None) -> None:
    state["game_input_mode"] = mode
    state["activation_source"] = source
    state["last_activity"] = time.time()
    if mode == "voice":
        state["game_external_voice_route_active"] = True
    elif mode == "text":
        state["game_external_text_route_active"] = True

    clean_detail = detail or {}
    log = state.setdefault("game_input_activation_log", [])
    if not isinstance(log, list):
        log = []
        state["game_input_activation_log"] = log

    # Raw realtime audio arrives as a high-frequency chunk stream.  The
    # activation log records route mode changes, not every chunk.
    if not clean_detail:
        for item in reversed(log):
            if (
                isinstance(item, dict)
                and item.get("source") == source
                and item.get("mode") == mode
                and not item.get("detail")
            ):
                item["ts"] = state["last_activity"]
                return

    log.append({
        "source": source,
        "mode": mode,
        "detail": clean_detail,
        "ts": state["last_activity"],
    })
    if len(log) > _GAME_ROUTE_ACTIVATION_LOG_LIMIT:
        del log[:-_GAME_ROUTE_ACTIVATION_LOG_LIMIT]


def _next_game_dialog_id(state: dict) -> str:
    try:
        seq = int(state.get("game_dialog_seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    seq += 1
    state["game_dialog_seq"] = seq
    return f"glog_{seq:04d}"


def _sync_game_dialog_seq_from_id(state: dict, dialog_id: str) -> None:
    match = re.search(r"(\d+)$", str(dialog_id or ""))
    if not match:
        return
    try:
        seq = int(match.group(1))
        current = int(state.get("game_dialog_seq") or 0)
    except (TypeError, ValueError):
        current = 0
        seq = 0
    if seq > current:
        state["game_dialog_seq"] = seq


def _game_context_pending_dialogues(state: dict) -> list[dict]:
    dialog = [item for item in state.get("game_dialog_log") or [] if isinstance(item, dict)]
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    last_idx = _dialog_id_index(dialog, str(organizer.get("last_organized_id") or ""))
    return dialog[last_idx + 1:]


def _game_context_recent_id_limit(state: dict, pending_count: int) -> int:
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    if (
        pending_count > _GAME_CONTEXT_RECENT_WINDOW_MAX_COUNT
        or organizer.get("running")
        or int(organizer.get("failure_count") or 0) > 0
    ):
        return _GAME_CONTEXT_FAILURE_VISIBLE_WINDOW_MAX_COUNT
    return _GAME_CONTEXT_RECENT_WINDOW_MAX_COUNT


def _apply_game_context_failure_fallback(
    state: dict,
    pending: list[dict],
    *,
    reason: str,
) -> bool:
    if len(pending) < _GAME_CONTEXT_FAILURE_VISIBLE_WINDOW_MAX_COUNT:
        return False
    keep_count = _GAME_CONTEXT_FAILURE_FALLBACK_KEEP_COUNT
    discarded = pending[:-keep_count]
    kept = pending[-keep_count:]
    if not discarded:
        return False
    last_discarded_id = str(discarded[-1].get("id") or "")
    if not last_discarded_id:
        return False

    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    organizer["last_organized_id"] = last_discarded_id
    organizer["degraded"] = False
    organizer["error"] = f"fallback_{reason}_after_{len(pending)}_pending_items"
    state["game_context_organizer"] = organizer
    state["game_context_recent_ids"] = [
        str(item.get("id") or "")
        for item in kept
        if isinstance(item, dict) and item.get("id")
    ]
    logger.warning(
        "🎮 局内上下文整理失败兜底丢弃: game=%s session=%s reason=%s discarded=%s kept=%s last=%s",
        state.get("game_type"),
        state.get("session_id"),
        reason,
        len(discarded),
        len(kept),
        last_discarded_id,
    )
    return True


def _set_game_context_recent_ids(state: dict, dialogues: list[dict] | None = None) -> None:
    source = dialogues if dialogues is not None else _game_context_pending_dialogues(state)
    if dialogues is None and len(source) >= _GAME_CONTEXT_FAILURE_VISIBLE_WINDOW_MAX_COUNT:
        if _apply_game_context_failure_fallback(state, source, reason="overflow"):
            return
        source = _game_context_pending_dialogues(state)
    ids = [str(item.get("id") or "") for item in source if isinstance(item, dict) and item.get("id")]
    limit = _game_context_recent_id_limit(state, len(ids))
    state["game_context_recent_ids"] = ids[-limit:]


def _should_schedule_game_context_organizer(state: dict) -> bool:
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    state["game_context_organizer"] = organizer
    if state.get("_exit_flow_started") or state.get("game_route_active") is False:
        return False
    if organizer.get("running") or organizer.get("degraded"):
        return False
    return len(_game_context_pending_dialogues(state)) >= _GAME_CONTEXT_ORGANIZE_TRIGGER_COUNT


def _maybe_schedule_game_context_organizer(state: dict) -> None:
    """Spawn the per-state organizer task at most once at any given time.

    B4: ``running`` is a dict flag the audit flagged as racy. In practice,
    on CPython this scheduler is invoked from sync code paths only — the
    enclosing ``_append_game_dialog`` body has no ``await`` so two
    coroutines on the same event loop cannot interleave inside it. Still,
    we add a defensive previous-task done-check so an in-flight organizer
    is never silently overwritten if a future change introduces an
    ``await`` boundary in the call chain.
    """
    if not _should_schedule_game_context_organizer(state):
        return
    prev = state.get("_game_context_organizer_task")
    if prev is not None and hasattr(prev, "done") and not prev.done():
        return
    snapshot = [dict(item) for item in _game_context_pending_dialogues(state)]
    if len(snapshot) < _GAME_CONTEXT_ORGANIZE_TRIGGER_COUNT:
        return
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    organizer["running"] = True
    organizer["error"] = ""
    state["game_context_organizer"] = organizer
    try:
        task = asyncio.create_task(_run_game_context_organizer_task(state, snapshot))
        state["_game_context_organizer_task"] = task
    except RuntimeError:
        organizer["running"] = False
        state["game_context_organizer"] = organizer


def _append_game_dialog(state: dict, item: dict) -> None:
    # B2: once finalize has started archiving, the snapshot of
    # ``game_dialog_log`` has already been captured. Mutating it after
    # that point produces entries that never reach the archive — they
    # silently disappear when ``_game_route_states`` is eventually
    # popped by the cleanup sweep. Drop late writes instead.
    if state.get("_exit_flow_started"):
        return
    item = dict(item)
    item.setdefault("ts", time.time())
    if item.get("id"):
        _sync_game_dialog_seq_from_id(state, str(item.get("id") or ""))
    else:
        item["id"] = _next_game_dialog_id(state)
    state.setdefault("game_dialog_log", []).append(item)
    state["last_activity"] = item["ts"]
    _set_game_context_recent_ids(state)
    _maybe_schedule_game_context_organizer(state)


def _append_game_output(state: dict, output: dict) -> None:
    # B2: once finalize has started, ``pending_outputs`` will never be
    # drained again (the route is exiting, the game page won't ``/drain``
    # any further). Late writes accumulate into oblivion.
    if state.get("_exit_flow_started"):
        return
    pending = state.setdefault("pending_outputs", [])
    pending.append(output)
    del pending[:-_GAME_ROUTE_OUTPUT_LIMIT]
    state["last_activity"] = time.time()


def _build_game_context_organizer_payload(
    state: dict,
    snapshot: list[dict],
    language: str | None = None,
) -> dict:
    organize_dialogues = snapshot[:-_GAME_CONTEXT_RECENT_KEEP_COUNT]
    keep_dialogues = snapshot[-_GAME_CONTEXT_RECENT_KEEP_COUNT:]
    return {
        "game": state.get("game_type") or "game",
        "sessionId": state.get("session_id") or "default",
        "lanlanName": state.get("lanlan_name") or "",
        "officialScore": _extract_score_text(state),
        "currentState": state.get("last_state") if isinstance(state.get("last_state"), dict) else {},
        "existingRollingSummary": str(state.get("game_context_summary") or ""),
        "existingSignals": _normalize_game_context_signals(state.get("game_context_signals")),
        "organizeDialogues": [
            {"id": item.get("id"), "line": _dialog_memory_line(item, language)}
            for item in organize_dialogues
            if isinstance(item, dict)
        ],
        "keptRecentDialogues": [
            {"id": item.get("id"), "line": _dialog_memory_line(item, language)}
            for item in keep_dialogues
            if isinstance(item, dict)
        ],
    }


async def _run_game_context_organizer_ai(state: dict, snapshot: list[dict]) -> dict:
    """Summarize older in-game context and extract observable signals."""
    char_info = _get_game_route_summary_llm_info(str(state.get("lanlan_name") or ""))
    language = char_info.get("user_language")
    payload = _build_game_context_organizer_payload(state, snapshot, language)
    system_prompt = get_game_context_organizer_system_prompt(language)
    user_prompt = get_game_context_organizer_user_prompt(language).format(
        payload=json.dumps(payload, ensure_ascii=False)
    )

    try:
        from utils.file_utils import robust_json_loads
        from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm_async
        from utils.token_tracker import set_call_type

        set_call_type("game_context_organizer")
        llm = await create_chat_llm_async(
            char_info["model"],
            char_info["base_url"],
            char_info["api_key"],
            provider_type=char_info.get("provider_type"),
            max_completion_tokens=900,
            timeout=20,
        )
        async with llm:
            result = await llm.ainvoke([  # noqa: LLM_INPUT_BUDGET  # game-session-scoped input (snapshot / history / archive / config), bounded by a single finite game; not external free-text. Deeper per-field truncation tracked as a game-domain follow-up.
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
        raw = _strip_json_fence(str(result.content or ""))
        parsed = robust_json_loads(raw)
    except Exception as exc:
        logger.warning(
            "🎮 局内上下文整理失败: game=%s session=%s err=%s",
            state.get("game_type"),
            state.get("session_id"),
            exc,
        )
        raise

    if not isinstance(parsed, dict):
        raise ValueError("game_context_organizer_json_not_object")
    parsed["source"] = _infer_service_source(
        char_info.get("base_url", ""),
        char_info.get("model", ""),
        char_info.get("api_type", ""),
    )
    return parsed


def _apply_game_context_organizer_success(state: dict, snapshot: list[dict], result: dict) -> None:
    organize_dialogues = snapshot[:-_GAME_CONTEXT_RECENT_KEEP_COUNT]
    if not organize_dialogues:
        return
    target_last_id = str(organize_dialogues[-1].get("id") or "")
    dialog = [item for item in state.get("game_dialog_log") or [] if isinstance(item, dict)]
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    current_last_id = str(organizer.get("last_organized_id") or "")
    current_idx = _dialog_id_index(dialog, current_last_id)
    target_idx = _dialog_id_index(dialog, target_last_id)
    if current_idx > target_idx >= 0:
        organizer["running"] = False
        organizer["error"] = "stale_organizer_result_ignored"
        state["game_context_organizer"] = organizer
        _set_game_context_recent_ids(state)
        return

    summary = _normalize_short_text(
        result.get("rollingSummary") or result.get("rolling_summary") or result.get("summary"),
        max_chars=900,
    )
    if summary:
        state["game_context_summary"] = summary
    state["game_context_signals"] = _merge_game_context_signals(
        state.get("game_context_signals"),
        result.get("signals") if isinstance(result.get("signals"), dict) else {},
    )
    organizer.update({
        "running": False,
        "degraded": False,
        "failure_count": 0,
        "last_organized_id": target_last_id,
        "source": result.get("source") if isinstance(result.get("source"), dict) else result.get("source"),
        "error": "",
    })
    state["game_context_organizer"] = organizer
    _set_game_context_recent_ids(state)


def _apply_game_context_organizer_failure(state: dict, snapshot: list[dict], error: Exception) -> None:
    organize_dialogues = snapshot[:-_GAME_CONTEXT_RECENT_KEEP_COUNT]
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    if organize_dialogues:
        target_last_id = str(organize_dialogues[-1].get("id") or "")
        dialog = [item for item in state.get("game_dialog_log") or [] if isinstance(item, dict)]
        current_last_id = str(organizer.get("last_organized_id") or "")
        current_idx = _dialog_id_index(dialog, current_last_id)
        target_idx = _dialog_id_index(dialog, target_last_id)
        if current_idx > target_idx >= 0:
            organizer["running"] = False
            organizer["error"] = organizer.get("error") or "stale_organizer_failure_ignored"
            state["game_context_organizer"] = organizer
            _set_game_context_recent_ids(state)
            return
    organizer["running"] = False
    organizer["failure_count"] = int(organizer.get("failure_count") or 0) + 1
    organizer["error"] = type(error).__name__
    state["game_context_organizer"] = organizer
    pending = _game_context_pending_dialogues(state)
    fallback_reason = f"organizer_failure_{type(error).__name__}"
    if _apply_game_context_failure_fallback(state, pending, reason=fallback_reason):
        return
    _set_game_context_recent_ids(state)


async def _run_game_context_organizer_task(state: dict, snapshot: list[dict]) -> None:
    succeeded = False
    try:
        result = await _run_game_context_organizer_ai(state, snapshot)
        _apply_game_context_organizer_success(state, snapshot, result)
        succeeded = True
    except Exception as exc:
        _apply_game_context_organizer_failure(state, snapshot, exc)
    finally:
        organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
        if organizer.get("running"):
            organizer["running"] = False
            state["game_context_organizer"] = organizer
        if succeeded and not organizer.get("degraded"):
            _maybe_schedule_game_context_organizer(state)


async def _settle_game_context_organizer_before_archive(state: dict) -> None:
    task = state.get("_game_context_organizer_task")
    if task is None or not hasattr(task, "done"):
        return

    if task.done():
        if task.cancelled():
            organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
            organizer["running"] = False
            organizer["error"] = organizer.get("error") or "cancelled"
            state["game_context_organizer"] = organizer
            return
        try:
            await task
        except Exception as exc:
            organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
            organizer["running"] = False
            organizer["error"] = type(exc).__name__
            state["game_context_organizer"] = organizer
            logger.warning(
                "🎮 退出前收敛局内上下文整理失败: game=%s session=%s err=%s",
                state.get("game_type"),
                state.get("session_id"),
                exc,
            )
        return

    try:
        await asyncio.wait_for(task, timeout=_GAME_CONTEXT_FINALIZE_WAIT_SECONDS)
    except asyncio.TimeoutError:
        organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
        organizer["running"] = False
        organizer["error"] = "finalize_timeout"
        state["game_context_organizer"] = organizer
        logger.warning(
            "🎮 退出前等待局内上下文整理超时，使用已有信息归档: game=%s session=%s timeout=%.1fs",
            state.get("game_type"),
            state.get("session_id"),
            _GAME_CONTEXT_FINALIZE_WAIT_SECONDS,
        )
    except Exception as exc:
        organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
        organizer["running"] = False
        organizer["error"] = type(exc).__name__
        state["game_context_organizer"] = organizer
        logger.warning(
            "🎮 退出前等待局内上下文整理失败，使用已有信息归档: game=%s session=%s err=%s",
            state.get("game_type"),
            state.get("session_id"),
            exc,
        )


async def _cancel_game_context_organizer_before_disabled_archive(state: dict) -> None:
    task = state.get("_game_context_organizer_task")
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    organizer["running"] = False
    organizer["error"] = "archive_disabled"
    state["game_context_organizer"] = organizer

    if task is None or not hasattr(task, "done") or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.debug(
            "🎮 关闭游戏记忆后取消局内上下文整理失败: game=%s session=%s err=%s",
            state.get("game_type"),
            state.get("session_id"),
            exc,
            exc_info=True,
        )


def _route_liveness_at(state: dict) -> float:
    """Return the timestamp proving the game page heartbeat is still alive."""
    for key in ("last_heartbeat_at", "created_at"):
        try:
            value = float(state.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _route_heartbeat_expired(state: dict, now: float) -> bool:
    return now - _route_liveness_at(state) > _route_heartbeat_timeout_seconds(state)


def _route_heartbeat_timeout_seconds(state: dict) -> float:
    """Use a longer grace window while the browser reports the game tab hidden."""
    visibility = str(state.get("visibility_state") or "").strip().lower()
    page_visible = state.get("page_visible")
    hidden = page_visible is False or visibility in {"hidden", "prerender", "unloaded"}
    key = "hidden_heartbeat_timeout_seconds" if hidden else "heartbeat_timeout_seconds"
    fallback = _GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS if hidden else _GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS
    try:
        return max(1.0, float(state.get(key, fallback) or fallback))
    except (TypeError, ValueError):
        return fallback


def _update_route_visibility_from_payload(state: dict, data: dict) -> None:
    visibility = str(data.get("visibilityState") or data.get("visibility_state") or "").strip().lower()
    if visibility:
        state["visibility_state"] = visibility[:32]

    page_visible = data.get("pageVisible")
    if isinstance(page_visible, bool):
        state["page_visible"] = page_visible
    elif visibility:
        state["page_visible"] = visibility == "visible"


def _update_game_memory_enabled_from_payload(
    state: dict,
    data: dict,
    game_type: str | None = None,
) -> None:
    gt = _normalize_game_memory_type(game_type or state.get("game_type") or "soccer")
    policy = _game_memory_policy_from_payload(gt, data, current=state)
    if policy is not None:
        fields = _game_memory_policy_fields(gt)
        for field in fields:
            state[field] = policy[field]
        state["game_memory_enabled"] = policy[fields[0]]
        state["gameMemoryEnabled"] = policy[fields[0]]
        state["game_memory_player_interaction_enabled"] = policy[fields[1]]
        state["game_memory_event_reply_enabled"] = policy[fields[2]]
        state["game_memory_archive_enabled"] = policy[fields[3]]
        state["game_memory_postgame_context_enabled"] = policy[fields[4]]


def _coerce_payload_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _coerce_payload_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _update_route_start_state_from_payload(state: dict, data: dict, *, exiting: bool = False) -> bool:
    """Track whether the user actually clicked the game Start button."""
    was_started = state.get("game_started") is True
    started_value = None
    if "gameStarted" in data:
        started_value = _coerce_payload_bool(data.get("gameStarted"))
    elif "game_started" in data:
        started_value = _coerce_payload_bool(data.get("game_started"))

    elapsed_ms = None
    for key in ("gameStartedElapsedMs", "game_started_elapsed_ms"):
        if key in data:
            elapsed_ms = _coerce_payload_float(data.get(key))
            break
    if elapsed_ms is not None:
        elapsed_ms = max(0.0, elapsed_ms)
        state["game_started_elapsed_ms"] = elapsed_ms
        if exiting:
            state["game_exit_started_elapsed_ms"] = elapsed_ms

    if started_value is True:
        state["game_started"] = True
        if not was_started:
            state["game_started_at"] = time.time() - ((elapsed_ms or 0.0) / 1000.0)
    elif started_value is False and not was_started:
        state["game_started"] = False

    accidental = _coerce_payload_bool(data.get("accidentalGameEntry"))
    if accidental is None:
        accidental = _coerce_payload_bool(data.get("accidental_game_entry"))
    if accidental is True:
        state["accidental_game_entry_exit"] = True

    started_now = not was_started and state.get("game_started") is True
    if started_now:
        # 在 game_started 首次 false→true 的边沿统计游玩次数——不在 /route/start
        # 计数：前端 _prepareGameForStartScreen 会先打开开始屏并调 route/start，
        # 此时 game_started 仍为 false，用户若从开始屏关闭会被记 accidental_page_entry，
        # 那种"开了没玩"不应计入。本函数是所有上报 gameStarted 路径的唯一汇聚点，
        # was_started 守卫保证每局只记一次。game_type 从 state 取（route/start 已写入）。
        #
        # 不带 neko_initiated 维度：state["nekoInitiated"] 只来自 route/start payload，
        # 而邀请被接受后 window.open(game_url) 只透传 lanlan_name/session_id、不回填
        # nekoInitiated，故邀请局会被误标 false。要修准要么动 nekoInitiated（同时驱动
        # pregame 语气分析，越界）要么跨三端加 from_invite 管线（无法充分验证 Electron）。
        # 该维度本非需求项，宁缺毋滥；邀请→游玩转化由 mini_game_invited 与本计数的总量得出。
        try:
            from utils.instrument import counter as _instr_counter
            _instr_counter(
                "mini_game_played",
                game_type=str(state.get("game_type") or "")[:24],
            )
        except Exception:
            # 埋点 best-effort，失败不影响游戏状态机
            pass
    return started_now


def _route_game_started_elapsed_ms(state: dict, *, prefer_exit_elapsed: bool = False) -> float | None:
    if prefer_exit_elapsed:
        exit_elapsed = _coerce_payload_float(state.get("game_exit_started_elapsed_ms"))
        if exit_elapsed is not None:
            return max(0.0, exit_elapsed)
    started_at = _coerce_payload_float(state.get("game_started_at"))
    if started_at is not None:
        return max(0.0, (time.time() - started_at) * 1000.0)
    elapsed = _coerce_payload_float(state.get("game_started_elapsed_ms"))
    if elapsed is not None:
        return max(0.0, elapsed)
    return None


def _game_archive_memory_skip_reason(state: dict, reason: str = "") -> str:
    """Return why game-produced content should not be written to memory."""
    reason_text = str(reason or "").strip()
    if state.get("accidental_game_entry_exit") or reason_text == "accidental_page_entry":
        return "accidental_page_entry"
    if state.get("game_started") is not True:
        return "game_not_started"
    elapsed_ms = _coerce_payload_float(state.get("game_exit_started_elapsed_ms"))
    if elapsed_ms is None and reason_text == "heartbeat_timeout":
        elapsed_ms = _coerce_payload_float(state.get("game_started_elapsed_ms"))
    if elapsed_ms is None:
        elapsed_ms = _route_game_started_elapsed_ms(state, prefer_exit_elapsed=True)
    if elapsed_ms is not None and elapsed_ms < _ACCIDENTAL_GAME_ENTRY_GRACE_MS:
        return "started_under_10s"
    if _game_memory_archive_enabled(state) is False:
        return "game_memory_archive_disabled"
    return ""


def _build_game_archive_memory_skipped_result(reason: str) -> dict:
    message = "game archive memory skipped"
    if reason in {"game_memory_disabled", "soccer_game_memory_archive_disabled", "game_memory_archive_disabled"}:
        message = (
            "game archive memory disabled; game user input mirrors, assistant replies, "
            "tail snippets, archive summary, and postgame context are controlled by game memory policy"
        )
    return {
        "ok": True,
        "status": "skipped",
        "reason": reason or "skipped",
        "message": message,
    }


def _format_ts(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return ""


def _extract_score_text(state: dict) -> str:
    score = state.get("finalScore") if isinstance(state.get("finalScore"), dict) else {}
    last_state = state.get("last_state") if isinstance(state.get("last_state"), dict) else {}
    if not score:
        score = last_state.get("score") if isinstance(last_state.get("score"), dict) else {}
    if not score:
        return "结果未知"
    score_text = _normalize_short_text(score.get("score_text"), max_chars=120)
    if score_text:
        return score_text
    player = score.get("player", "?")
    ai = score.get("ai", "?")
    return f"玩家 {player} : {ai} {state.get('lanlan_name') or 'AI'}"


_GAME_EVENT_MEMORY_LABEL_KEYS = {
    "goal-scored": "event_goal_scored",
    "goal-conceded": "event_goal_conceded",
    "own-goal-by-ai": "event_own_goal_by_ai",
    "own-goal-by-player": "event_own_goal_by_player",
    "steal": "event_steal",
    "stolen": "event_stolen",
    "mailbox-batch": "event_mailbox_batch",
}


def _dialog_memory_line(item: dict, language: str | None = None) -> str:
    labels = get_game_dialog_memory_line_labels(language)
    item_type = item.get("type")
    ts_text = _format_ts(item.get("ts"))
    prefix = f"[{ts_text}] " if ts_text else ""
    if item_type == "user":
        text = str(item.get("text") or "").strip()
        return (
            f"{prefix}{labels['player_line'].format(text=text)}"
            if text else f"{prefix}{labels['player_fallback']}"
        )
    if item_type == "assistant":
        line = str(item.get("line") or "").strip()
        control = item.get("control") if isinstance(item.get("control"), dict) else {}
        control_bits = []
        if control.get("mood"):
            control_bits.append(f"mood={control['mood']}")
        if control.get("difficulty"):
            control_bits.append(f"difficulty={control['difficulty']}")
        suffix = f" ({', '.join(control_bits)})" if control_bits else ""
        return (
            f"{prefix}{labels['assistant_line'].format(source=item.get('source') or 'game_llm', line=line, suffix=suffix)}"
            if line else f"{prefix}{labels['assistant_empty']}"
        )
    if item_type == "game_event":
        kind = str(item.get("kind") or "event")
        label_key = _GAME_EVENT_MEMORY_LABEL_KEYS.get(kind, "event_default")
        label = labels.get(label_key, labels["event_default"])
        text = str(item.get("text") or "").strip()
        line = str(item.get("result_line") or "").strip()
        if text and line:
            return f"{prefix}{labels['game_event_text_and_reply'].format(kind=kind, label=label, text=text, line=line)}"
        if line:
            return f"{prefix}{labels['game_event_reply'].format(kind=kind, label=label, line=line)}"
        if text:
            return f"{prefix}{labels['game_event_text'].format(kind=kind, label=label, text=text)}"
        return f"{prefix}{labels['game_event'].format(kind=kind, label=label)}"
    return f"{prefix}{json.dumps(item, ensure_ascii=False)}"


def _game_dialog_item_allowed_for_memory(item: dict, archive: dict) -> bool:
    """Apply game memory sub-controls to archive source material."""
    item_type = str(item.get("type") or "")
    if item_type == "user":
        return _game_memory_player_interaction_enabled(archive)
    if item_type == "assistant":
        source = str(item.get("source") or "")
        kind = str(item.get("kind") or "")
        if source == "opening_line" or kind == "opening-line":
            return _game_memory_event_reply_enabled(archive)
        return _game_memory_player_interaction_enabled(archive)
    if item_type in {"opening_line", "game_event"}:
        return _game_memory_event_reply_enabled(archive)
    return True


def _summarize_game_archive(state: dict, dialog: list[dict]) -> str:
    game_type = state.get("game_type") or "game"
    score_text = _extract_score_text(state)
    return f"{game_type} 游戏结束。最终/最近结果：{score_text}。"


def _build_game_archive(state: dict) -> dict:
    dialog = list(state.get("game_dialog_log") or [])
    keep_last = int(state.get("game_last_full_dialogue_count") or _DEFAULT_LAST_FULL_DIALOGUE_COUNT)
    key_events = [item for item in dialog if item.get("type") == "game_event"][-20:]
    last_state = state.get("last_state") if isinstance(state.get("last_state"), dict) else {}
    final_score = state.get("finalScore") if isinstance(state.get("finalScore"), dict) else {}
    if not final_score and isinstance(last_state.get("score"), dict):
        final_score = dict(last_state.get("score") or {})
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    return {
        "game_type": state.get("game_type"),
        "session_id": state.get("session_id"),
        "lanlan_name": state.get("lanlan_name"),
        "user_language": _resolve_game_prompt_language(str(state.get("lanlan_name") or "")),
        "dialog_count": len(dialog),
        "full_dialogues": dialog,
        "last_full_dialogues": dialog[-keep_last:],
        "summary": _summarize_game_archive(state, dialog),
        "key_events": key_events,
        "route_activations": list(state.get("game_input_activation_log") or []),
        "last_state": last_state,
        "finalScore": final_score,
        "game_memory_tail_count": _normalize_game_memory_tail_count(state.get("game_memory_tail_count")),
        **_game_memory_policy(str(state.get("game_type") or "soccer"), state),
        "game_context_summary": str(state.get("game_context_summary") or ""),
        "game_context_signals": _normalize_game_context_signals(state.get("game_context_signals")),
        "game_context_recent_ids": [
            str(item_id)
            for item_id in state.get("game_context_recent_ids") or []
            if str(item_id or "").strip()
        ],
        "game_context_organizer": organizer,
        "game_context_degraded": organizer.get("degraded") is True,
        "preGameContext": state.get("preGameContext") if isinstance(state.get("preGameContext"), dict) else {},
        "pre_game_context_source": str(state.get("pre_game_context_source") or ""),
        "pre_game_context_error": str(state.get("pre_game_context_error") or ""),
        "nekoInitiated": bool(state.get("nekoInitiated")),
        "nekoInviteText": str(state.get("nekoInviteText") or ""),
        "game_started": state.get("game_started") is True,
        "game_started_elapsed_ms": _route_game_started_elapsed_ms(state, prefer_exit_elapsed=True),
        "created_at": state.get("created_at"),
        "ended_at": time.time(),
    }


def _archive_game_context_degraded(archive: dict) -> bool:
    organizer = _normalize_game_context_organizer_state(archive.get("game_context_organizer"))
    return archive.get("game_context_degraded") is True or organizer.get("degraded") is True


def _archive_prompt_language(archive: dict) -> str:
    language = str(archive.get("user_language") or "").strip()
    if language:
        return language
    lanlan_name = str(archive.get("lanlan_name") or "").strip()
    if not lanlan_name:
        return ""
    try:
        session_manager = get_session_manager()
        manager = session_manager.get(lanlan_name) if hasattr(session_manager, "get") else None
        language = str(getattr(manager, "user_language", "") or "").strip()
        if language:
            return normalize_language_code(language, format="short") or language
    except Exception:
        logger.debug("赛后归档语言解析失败，使用默认 prompt 语言", exc_info=True)
    return ""


def _build_game_archive_memory_text(archive: dict) -> str:
    language = _archive_prompt_language(archive)
    labels = get_game_archive_memory_text_labels(language)
    degraded = _archive_game_context_degraded(archive)
    lines = [
        labels["record_header"],
        labels["description"],
        labels["game"].format(game_type=archive.get("game_type") or "game"),
        labels["session"].format(session_id=archive.get("session_id") or "default"),
        labels["time"].format(start=_format_ts(archive.get("created_at")), end=_format_ts(archive.get("ended_at"))),
        labels["summary"].format(summary=archive.get("summary") or ""),
        labels["official_result"].format(score_text=_archive_score_text(archive)),
        labels["result_rule"],
    ]
    if degraded:
        lines.append(labels["degraded"])
    else:
        context_summary = _normalize_short_text(archive.get("game_context_summary"), max_chars=900)
        signals_text = _game_context_signals_text(archive.get("game_context_signals"))
        if context_summary:
            lines.append(labels["rolling_summary"].format(summary=context_summary))
        if signals_text:
            lines.append(labels["grouped_signals"].format(signals=signals_text))

    key_events = archive.get("key_events") if isinstance(archive.get("key_events"), list) else []
    key_events = [
        item for item in key_events
        if isinstance(item, dict) and _game_dialog_item_allowed_for_memory(item, archive)
    ]
    if key_events:
        lines.append(labels["key_events"])
        lines.extend(f"- {_dialog_memory_line(item, language)}" for item in key_events[-8:] if isinstance(item, dict))

    pre_game_context = archive.get("preGameContext") if isinstance(archive.get("preGameContext"), dict) else {}
    if pre_game_context:
        lines.append(labels["pregame_context"])
        lines.append(
            json.dumps({
                "gameStance": pre_game_context.get("gameStance"),
                "nekoEmotion": pre_game_context.get("nekoEmotion"),
                "emotionIntensity": pre_game_context.get("emotionIntensity"),
                "emotionInertia": pre_game_context.get("emotionInertia"),
                "postgameCarryback": pre_game_context.get("postgameCarryback"),
            }, ensure_ascii=False)
        )

    last_dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    last_dialogues = [
        item for item in last_dialogues
        if isinstance(item, dict) and _game_dialog_item_allowed_for_memory(item, archive)
    ]
    if last_dialogues:
        lines.append(labels["recent_dialogues"])
        lines.extend(f"- {_dialog_memory_line(item, language)}" for item in last_dialogues if isinstance(item, dict))

    return "\n".join(line for line in lines if line is not None)


def _archive_last_assistant_line(archive: dict) -> str:
    dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    for item in reversed(dialogues):
        if not isinstance(item, dict):
            continue
        if not _game_dialog_item_allowed_for_memory(item, archive):
            continue
        line = str(item.get("line") or item.get("result_line") or "").strip()
        if line:
            return line
    return ""


def _archive_last_user_text(archive: dict) -> str:
    dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    for item in reversed(dialogues):
        if not isinstance(item, dict):
            continue
        if not _game_dialog_item_allowed_for_memory(item, archive):
            continue
        if item.get("type") == "user":
            text = str(item.get("text") or "").strip()
            if text:
                return text
    return ""


def _normalize_memory_highlight_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = text.lstrip("-*•0123456789.、)） ").strip()
    return text


def _normalize_game_archive_memory_highlights(value: Any) -> dict:
    if not isinstance(value, dict):
        return {
            "important_records": [],
            "important_game_events": [],
            "state_carryback": "",
            "postgame_tone": "",
            "memory_summary": "",
        }

    def collect(*keys: str) -> list[str]:
        raw = None
        for key in keys:
            if key in value:
                raw = value.get(key)
                break
        if isinstance(raw, str):
            raw_items = [raw]
        elif isinstance(raw, list):
            raw_items = raw
        else:
            raw_items = []

        items: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = _normalize_memory_highlight_text(item)
            if not text or text in seen:
                continue
            seen.add(text)
            items.append(text)
            if len(items) >= 3:
                break
        return items

    def pick_text(*keys: str, max_chars: int = 180) -> str:
        for key in keys:
            if key not in value:
                continue
            text = _normalize_memory_highlight_text(value.get(key))
            if text:
                return text[:max_chars]
        return ""

    return {
        "important_records": collect(
            "important_records",
            "important_interactions",
            "important_dialogues",
            "relationship_records",
        ),
        "important_game_events": collect(
            "important_game_events",
            "game_events",
            "character_game_events",
            "neko_game_events",
        ),
        "state_carryback": pick_text("state_carryback", "carryback", "postgame_carryback"),
        "postgame_tone": pick_text("postgame_tone", "tone", max_chars=80),
        "memory_summary": pick_text("memory_summary", "summary", max_chars=220),
    }


def _fallback_game_archive_memory_highlights(archive: dict) -> dict:
    language = _archive_prompt_language(archive)
    labels = get_game_archive_fallback_highlight_labels(language)
    records: list[str] = []
    last_user = _archive_last_user_text(archive)
    last_assistant = _archive_last_assistant_line(archive)
    if last_user and last_assistant:
        records.append(labels["user_and_assistant"].format(
            last_user=last_user,
            last_assistant=last_assistant,
        ))
    elif last_user:
        records.append(labels["user_only"].format(last_user=last_user))
    elif last_assistant:
        records.append(labels["assistant_only"].format(last_assistant=last_assistant))

    event_records: list[str] = []
    key_events = archive.get("key_events") if isinstance(archive.get("key_events"), list) else []
    for item in reversed(key_events):
        if not isinstance(item, dict):
            continue
        if not _game_dialog_item_allowed_for_memory(item, archive):
            continue
        line = _dialog_memory_line(item, language)
        if line:
            event_records.append(line)
        if len(event_records) >= 3:
            break
    event_records.reverse()

    return {
        "important_records": records[:3],
        "important_game_events": event_records[:3],
        "state_carryback": "",
        "postgame_tone": "",
        "memory_summary": "",
    }


def _build_game_archive_memory_highlight_source(archive: dict) -> str:
    language = _archive_prompt_language(archive)
    labels = get_game_archive_highlight_source_labels(language)
    dialogues = archive.get("full_dialogues") if isinstance(archive.get("full_dialogues"), list) else []
    if not dialogues:
        dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    degraded = _archive_game_context_degraded(archive)
    lines = [
        labels["game"].format(game_type=archive.get("game_type") or "game"),
        labels["session"].format(session_id=archive.get("session_id") or "default"),
        labels["score"].format(score_text=_archive_score_text(archive)),
        labels["score_explanation"],
        labels["verbal_concession_explanation"],
        labels["role_explanation"],
    ]
    pre_game_context = archive.get("preGameContext") if isinstance(archive.get("preGameContext"), dict) else {}
    if pre_game_context:
        lines.append(
            labels["pregame_context"].format(context=json.dumps({
                "gameStance": pre_game_context.get("gameStance"),
                "nekoEmotion": pre_game_context.get("nekoEmotion"),
                "emotionIntensity": pre_game_context.get("emotionIntensity"),
                "emotionInertia": pre_game_context.get("emotionInertia"),
                "postgameCarryback": pre_game_context.get("postgameCarryback"),
            }, ensure_ascii=False)),
        )
    if degraded:
        lines.append(labels["degraded"])
    else:
        context_summary = _normalize_short_text(archive.get("game_context_summary"), max_chars=900)
        signals_text = _game_context_signals_text(archive.get("game_context_signals"))
        if context_summary:
            lines.append(labels["rolling_summary"].format(summary=context_summary))
        if signals_text:
            lines.append(labels["grouped_signals"].format(signals=signals_text))
        if context_summary or signals_text:
            lines.append(labels["selection_priority"])
    lines.append(labels["full_dialogues"])
    lines.extend(
        f"- {_dialog_memory_line(item, language)}"
        for item in dialogues
        if isinstance(item, dict) and _game_dialog_item_allowed_for_memory(item, archive)
    )
    return "\n".join(lines)


async def _select_game_archive_memory_highlights(archive: dict) -> dict:
    """Ask a small independent LLM call to select meaningful memory items."""
    char_info = _get_game_route_summary_llm_info(str(archive.get("lanlan_name") or ""))
    source = _build_game_archive_memory_highlight_source(archive)
    language = _archive_prompt_language(archive)
    system_prompt = get_game_archive_memory_highlighter_system_prompt(language)

    try:
        # Bound a long game_dialog_log: _build_game_archive stores the whole dialog
        # in full_dialogues and the source builder appends every line. Head+tail
        # keeps the early framing + late outcome within a real token budget. Inside
        # the try so any failure falls back to _fallback_game_archive_memory_highlights.
        from utils.tokenize import truncate_head_tail_tokens
        source = truncate_head_tail_tokens(source, 2000, 2000)
        user_prompt = get_game_archive_memory_highlighter_user_prompt(language).format(source=source)
        from utils.file_utils import robust_json_loads
        from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm_async
        from utils.token_tracker import set_call_type

        set_call_type("game_memory_archive")
        llm = await create_chat_llm_async(
            char_info["model"],
            char_info["base_url"],
            char_info["api_key"],
            provider_type=char_info.get("provider_type"),
            max_completion_tokens=700,
            timeout=20,
        )
        async with llm:
            result = await llm.ainvoke([  # source bounded above via truncate_head_tail_tokens
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
        raw = _strip_json_fence(str(result.content or ""))
        parsed = robust_json_loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("memory_highlight_json_not_object")
        highlights = _normalize_game_archive_memory_highlights(parsed)
        highlights["source"] = _infer_service_source(
            char_info.get("base_url", ""),
            char_info.get("model", ""),
            char_info.get("api_type", ""),
        )
        return highlights
    except Exception as exc:
        logger.warning(
            "🎮 游戏赛后记忆重点筛选失败，使用兜底: game=%s session=%s err=%s",
            archive.get("game_type"),
            archive.get("session_id"),
            exc,
        )
        highlights = _fallback_game_archive_memory_highlights(archive)
        highlights["source"] = {"provider": "fallback", "method": type(exc).__name__}
        return highlights


async def _ensure_game_archive_memory_highlights(archive: dict) -> dict:
    if _archive_game_context_degraded(archive):
        highlights = _normalize_game_archive_memory_highlights({})
        highlights["source"] = {"provider": "game_context_organizer", "method": "degraded_minimal_facts"}
        archive["memory_highlights"] = highlights
        return highlights

    raw_existing = archive.get("memory_highlights")
    existing = _normalize_game_archive_memory_highlights(archive.get("memory_highlights"))
    if (
        existing["important_records"]
        or existing["important_game_events"]
        or existing["state_carryback"]
        or existing["postgame_tone"]
        or existing["memory_summary"]
        or (isinstance(raw_existing, dict) and "source" in raw_existing)
    ):
        source = raw_existing.get("source") if isinstance(raw_existing, dict) else None
        existing["source"] = source
        archive["memory_highlights"] = existing
        return existing
    highlights = await _select_game_archive_memory_highlights(archive)
    highlights = _normalize_game_archive_memory_highlights(highlights) | {
        "source": highlights.get("source") if isinstance(highlights, dict) else None,
    }
    archive["memory_highlights"] = highlights
    return highlights


def _game_dialog_tail_for_memory(archive: dict, tail_count: int) -> list[dict]:
    dialogues = archive.get("full_dialogues") if isinstance(archive.get("full_dialogues"), list) else []
    if not dialogues:
        dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    return [
        item for item in dialogues[-tail_count:]
        if isinstance(item, dict) and _game_dialog_item_allowed_for_memory(item, archive)
    ]


def _game_dialog_item_to_memory_message(item: dict) -> dict | None:
    item_type = str(item.get("type") or "")
    text = ""
    role = ""
    if item_type == "user":
        text = str(item.get("text") or "").strip()
        role = "user"
    elif item_type in {"assistant", "opening_line"}:
        text = str(item.get("line") or item.get("result_line") or "").strip()
        role = "assistant"
    elif item_type == "game_event":
        text = str(item.get("result_line") or item.get("line") or "").strip()
        role = "assistant" if text else ""
    if not role or not text:
        return None
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _build_game_archive_tail_memory_messages(archive: dict, tail_count: int) -> list[dict]:
    messages: list[dict] = []
    for item in _game_dialog_tail_for_memory(archive, tail_count):
        message = _game_dialog_item_to_memory_message(item)
        if message:
            messages.append(message)
    return messages


def _build_game_archive_memory_summary_text(archive: dict, *, tail_count: int | None = None) -> str:
    """Build a compact system note for memory; this is not a user dialogue turn."""
    labels = get_game_archive_memory_summary_labels(_archive_prompt_language(archive))
    score_text = _archive_score_text(archive)
    highlights = _normalize_game_archive_memory_highlights(archive.get("memory_highlights"))
    degraded = _archive_game_context_degraded(archive)
    normalized_tail_count = _normalize_game_memory_tail_count(
        tail_count if tail_count is not None else archive.get("game_memory_tail_count")
    )
    lines = [
        "Game Module Postgame Record: this is a game-module archive, not a verbatim player utterance.",
    ]
    if score_text:
        lines.append(labels["score"].format(score_text=score_text))
    else:
        lines.append(labels["no_score"])
    if degraded:
        lines.append(labels["degraded"])
        lines.append(labels["degraded_no_tail"])
        lines.append(labels["degraded_followup"])
        return "\n".join(lines)

    if highlights["important_records"]:
        lines.append(labels["important_records"])
        lines.extend(f"- {item}" for item in highlights["important_records"])
    if highlights["important_game_events"]:
        lines.append(labels["important_game_events"])
        lines.extend(f"- {item}" for item in highlights["important_game_events"])
    if highlights["state_carryback"]:
        lines.append(labels["state_carryback"].format(value=highlights["state_carryback"]))
    if highlights["postgame_tone"]:
        lines.append(labels["postgame_tone"].format(value=highlights["postgame_tone"]))
    if highlights["memory_summary"]:
        lines.append(labels["memory_summary"].format(value=highlights["memory_summary"]))
    lines.append(labels["tail_rule"].format(tail_count=normalized_tail_count))
    return "\n".join(lines)


def _build_game_archive_memory_messages(archive: dict, tail_count: int | None = None) -> list[dict]:
    """Build the actual /cache payload.

    Normally replay the game's last tail window as ordinary role messages, then
    add one module-generated system archive as the official explanation. In
    degraded mode, only write the system archive with minimal facts.
    """
    normalized_tail_count = _normalize_game_memory_tail_count(
        tail_count if tail_count is not None else archive.get("game_memory_tail_count")
    )
    messages = []
    if not _archive_game_context_degraded(archive):
        messages = _build_game_archive_tail_memory_messages(archive, normalized_tail_count)
    memory_text = _build_game_archive_memory_summary_text(archive, tail_count=normalized_tail_count)
    messages.append({"role": "system", "content": [{"type": "text", "text": memory_text}]})
    return messages


_POSTGAME_SKIP_REASONS = {"heartbeat_timeout", "session_cleanup", "cleanup", "manual_return_to_start"}
_POSTGAME_REALTIME_NUDGE_DELAYS = (1.5, 5.0, 9.0)
_POSTGAME_REALTIME_UNORGANIZED_LIMIT = 12
_POSTGAME_REALTIME_UNORGANIZED_MAX_TOKENS = 1500


def _normalize_postgame_options(raw: Any, *, reason: str) -> dict:
    """Normalize one-shot postgame delivery options from the game-end request."""
    reason_text = str(reason or "").strip().lower()
    options = {
        "enabled": reason_text not in _POSTGAME_SKIP_REASONS,
        "mode": "auto",
        "trigger_voice": True,
        "include_last_dialogues": _DEFAULT_LAST_FULL_DIALOGUE_COUNT,
        "max_chars": 60,
        "min_idle_secs": 0.0,
        "force_on_skip_reason": False,
    }
    if raw is False:
        options["enabled"] = False
    elif isinstance(raw, dict):
        if "enabled" in raw:
            options["enabled"] = bool(raw.get("enabled"))
        mode = str(raw.get("mode") or "").strip().lower()
        if mode in {"auto", "realtime", "text", "off"}:
            options["mode"] = mode
        if options["mode"] == "off":
            options["enabled"] = False
        if "triggerVoice" in raw:
            options["trigger_voice"] = bool(raw.get("triggerVoice"))
        elif "trigger_voice" in raw:
            options["trigger_voice"] = bool(raw.get("trigger_voice"))
        if "forceOnSkipReason" in raw:
            options["force_on_skip_reason"] = bool(raw.get("forceOnSkipReason"))
        for source_key, target_key, low, high in (
            ("includeLastDialogues", "include_last_dialogues", 1, 50),
            ("maxChars", "max_chars", 20, 160),
        ):
            if source_key in raw:
                try:
                    options[target_key] = max(low, min(int(raw.get(source_key)), high))
                except (TypeError, ValueError):
                    pass
        if "minIdleSecs" in raw:
            try:
                options["min_idle_secs"] = max(0.0, min(float(raw.get("minIdleSecs")), 30.0))
            except (TypeError, ValueError):
                pass

    if reason_text in _POSTGAME_SKIP_REASONS and not options["force_on_skip_reason"]:
        options["enabled"] = False
    return options


def _archive_score_text(archive: dict) -> str:
    return _extract_score_text({
        "finalScore": archive.get("finalScore") if isinstance(archive.get("finalScore"), dict) else {},
        "last_state": archive.get("last_state") if isinstance(archive.get("last_state"), dict) else {},
        "lanlan_name": archive.get("lanlan_name"),
    })


def _postgame_last_signals(archive: dict) -> dict:
    dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    signals = {
        "last_user_text": "",
        "last_assistant_line": "",
        "final_mood": "",
        "final_difficulty": "",
    }
    for item in reversed(dialogues):
        if not isinstance(item, dict):
            continue
        if not signals["last_user_text"] and item.get("type") == "user":
            signals["last_user_text"] = str(item.get("text") or "").strip()
        if not signals["last_assistant_line"]:
            signals["last_assistant_line"] = str(item.get("line") or item.get("result_line") or "").strip()
        control = item.get("control") if isinstance(item.get("control"), dict) else {}
        if not signals["final_mood"] and control.get("mood"):
            signals["final_mood"] = str(control.get("mood") or "").strip()
        if not signals["final_difficulty"] and control.get("difficulty"):
            signals["final_difficulty"] = str(control.get("difficulty") or "").strip()
        if all(signals.values()):
            break
    return signals


def _archive_unorganized_dialogues(archive: dict, *, limit: int = _POSTGAME_REALTIME_UNORGANIZED_LIMIT) -> list[dict]:
    dialogues = archive.get("full_dialogues") if isinstance(archive.get("full_dialogues"), list) else []
    dialogues = [item for item in dialogues if isinstance(item, dict)]
    if not dialogues:
        last_dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
        dialogues = [
            item
            for item in last_dialogues
            if isinstance(item, dict)
        ]
    organizer = _normalize_game_context_organizer_state(archive.get("game_context_organizer"))
    last_idx = _dialog_id_index(dialogues, str(organizer.get("last_organized_id") or ""))
    pending = dialogues[last_idx + 1:] if last_idx >= 0 else dialogues
    return pending[-max(1, limit):]


def _append_token_limited_lines(lines: list[str], header: str, raw_lines: list[str], *, max_tokens: int) -> None:
    from utils.tokenize import count_tokens, truncate_to_tokens

    if max_tokens <= 0:
        return

    kept: list[str] = []
    total_tokens = 0
    for raw in reversed(raw_lines):
        line = str(raw or "").strip()
        if not line:
            continue
        line_tokens = count_tokens(line)
        next_total = total_tokens + line_tokens
        if next_total > max_tokens:
            # Even the first (newest) line must respect the budget — a single
            # pasted/long dialogue entry would otherwise bypass the cap.
            if not kept:
                clipped = truncate_to_tokens(line, max_tokens)
                if clipped:
                    kept.insert(0, clipped)
            break
        kept.insert(0, line)
        total_tokens = next_total
    if kept:
        lines.append(header)
        lines.extend(kept)


def _build_game_postgame_context_text(archive: dict) -> str:
    """Context for an already-active Realtime session; it should not speak by itself.

    Reuse already-built game archive material only. Do not trigger another LLM
    pass here; the Realtime session only needs compact postgame continuity.
    """
    language = _archive_prompt_language(archive)
    labels = get_game_postgame_context_labels(language)
    degraded = _archive_game_context_degraded(archive)
    score_text = _archive_score_text(archive)
    highlights = _normalize_game_archive_memory_highlights(archive.get("memory_highlights"))
    if not any(
        (
            highlights["important_records"],
            highlights["important_game_events"],
            highlights["state_carryback"],
            highlights["postgame_tone"],
            highlights["memory_summary"],
        )
    ):
        highlights = _normalize_game_archive_memory_highlights(_fallback_game_archive_memory_highlights(archive))

    lines = [
        labels["header"],
        labels["description"],
        labels["usage"],
        labels["game"].format(game_type=archive.get("game_type") or "game"),
        labels["session"].format(session_id=archive.get("session_id") or "default"),
        labels["time"].format(start=_format_ts(archive.get("created_at")), end=_format_ts(archive.get("ended_at"))),
    ]
    if score_text:
        lines.append(labels["official_result"].format(score_text=score_text))
    summary = str(archive.get("summary") or "").strip()
    if summary:
        lines.append(labels["summary"].format(summary=summary))
    lines.append(labels["result_rule"])

    if degraded:
        lines.append(labels["degraded"])
    else:
        if highlights["memory_summary"]:
            lines.append(labels["memory_summary"].format(value=highlights["memory_summary"]))
        if highlights["important_records"]:
            lines.append(labels["important_records"])
            lines.extend(f"- {item}" for item in highlights["important_records"])
        if highlights["important_game_events"]:
            lines.append(labels["important_game_events"])
            lines.extend(f"- {item}" for item in highlights["important_game_events"])
        if highlights["state_carryback"]:
            lines.append(labels["state_carryback"].format(value=highlights["state_carryback"]))
        if highlights["postgame_tone"]:
            lines.append(labels["postgame_tone"].format(value=highlights["postgame_tone"]))

        context_summary = _normalize_short_text(archive.get("game_context_summary"), max_chars=900)
        signals_text = _game_context_signals_text(archive.get("game_context_signals"))
        if context_summary:
            lines.append(labels["rolling_summary"].format(summary=context_summary))
        if signals_text:
            lines.append(labels["signals"])
            lines.append(signals_text)

    unorganized_lines = [
        f"- {_dialog_memory_line(item, language)}"
        for item in _archive_unorganized_dialogues(archive)
        if isinstance(item, dict)
    ]
    _append_token_limited_lines(
        lines,
        labels["unorganized_window"],
        unorganized_lines,
        max_tokens=_POSTGAME_REALTIME_UNORGANIZED_MAX_TOKENS,
    )

    last_user = _archive_last_user_text(archive)
    last_assistant = _archive_last_assistant_line(archive)
    if last_user:
        lines.append(labels["last_user"].format(text=last_user))
    if last_assistant:
        lines.append(labels["last_assistant"].format(text=last_assistant))

    lines.append(labels["reply_rule"])
    return "\n".join(line for line in lines if line is not None)


def _build_game_postgame_realtime_nudge_instruction(archive: dict, options: dict) -> str:
    labels = get_game_postgame_realtime_nudge_labels(_archive_prompt_language(archive))
    signals = _postgame_last_signals(archive)
    max_chars = int(options.get("max_chars") or 60)
    degraded = _archive_game_context_degraded(archive)
    lines = [
        labels["header"],
        labels["ended"],
        labels["no_ingame"],
    ]
    summary = str(archive.get("summary") or "").strip()
    if summary:
        lines.append(labels["summary"].format(summary=summary))
    score_text = _archive_score_text(archive)
    if score_text:
        lines.append(labels["score"].format(score_text=score_text))
        lines.append(labels["score_rule"])
    if degraded:
        lines.append(labels["degraded"])
    if signals["last_user_text"]:
        lines.append(labels["last_user"].format(text=signals["last_user_text"]))
    if signals["last_assistant_line"]:
        lines.append(labels["last_assistant"].format(text=signals["last_assistant_line"]))
    highlights = _normalize_game_archive_memory_highlights(archive.get("memory_highlights"))
    if highlights["state_carryback"] and not degraded:
        lines.append(labels["state_carryback"].format(value=highlights["state_carryback"]))
    if highlights["postgame_tone"] and not degraded:
        lines.append(labels["postgame_tone"].format(value=highlights["postgame_tone"]))
    lines.append(labels["request"].format(max_chars=max_chars))
    return "\n".join(lines)


def _build_game_postgame_event(game_type: str, archive: dict, options: dict) -> dict:
    language = _archive_prompt_language(archive)
    texts = get_game_postgame_event_texts(language)
    dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    include_count = int(options.get("include_last_dialogues") or _DEFAULT_LAST_FULL_DIALOGUE_COUNT)
    formatted_dialogues = [
        _dialog_memory_line(item, language)
        for item in dialogues[-include_count:]
        if isinstance(item, dict)
    ]
    signals = _postgame_last_signals(archive)
    current_state = dict(archive.get("last_state") or {}) if isinstance(archive.get("last_state"), dict) else {}
    final_score = archive.get("finalScore") if isinstance(archive.get("finalScore"), dict) else {}
    if final_score:
        current_state["score"] = dict(final_score)
    return {
        "kind": "postgame",
        "lanlan_name": archive.get("lanlan_name") or "",
        "label": texts["label"],
        "gameType": game_type,
        "summary": archive.get("summary") or "",
        "scoreText": _archive_score_text(archive),
        "finalScore": final_score,
        "lastDialogues": formatted_dialogues,
        "lastUserText": signals["last_user_text"],
        "lastAssistantLine": signals["last_assistant_line"],
        "finalMood": signals["final_mood"],
        "finalDifficulty": signals["final_difficulty"],
        "currentState": current_state,
        "preGameContext": archive.get("preGameContext") if isinstance(archive.get("preGameContext"), dict) else {},
        "memoryHighlights": _normalize_game_archive_memory_highlights(archive.get("memory_highlights")),
        "request": texts["request"].format(max_chars=int(options.get("max_chars") or 60)),
    }


def _active_realtime_session(mgr: Any) -> Any | None:
    if not (mgr and getattr(mgr, "is_active", False)):
        return None
    session = getattr(mgr, "session", None)
    try:
        from main_logic.omni_realtime_client import OmniRealtimeClient
    except Exception:
        return None
    return session if isinstance(session, OmniRealtimeClient) else None


def _is_gemini_realtime_session(session: Any) -> bool:
    return bool(getattr(session, "_is_gemini", False))


async def _run_postgame_realtime_nudge_task(
    mgr: Any,
    archive: dict,
    options: dict,
    delays: tuple[float, ...],
    *,
    expected_session: Any | None = None,
) -> None:
    lanlan_name = str(archive.get("lanlan_name") or "")
    instruction = _build_game_postgame_realtime_nudge_instruction(archive, options)
    _log_game_debug_material(
        "postgame_realtime_nudge_instruction",
        instruction,
        game_type=str(archive.get("game_type") or ""),
        session_id=str(archive.get("session_id") or ""),
        lanlan_name=lanlan_name,
        source="game_end",
    )
    for attempt, delay in enumerate(delays, start=1):
        try:
            await asyncio.sleep(delay)
            active_session = _active_realtime_session(mgr)
            if not active_session:
                logger.info(
                    "🎮 赛后 Realtime 主动搭话跳过: game=%s session=%s lanlan=%s attempt=%d reason=no_active_realtime_session",
                    archive.get("game_type"),
                    archive.get("session_id"),
                    lanlan_name,
                    attempt,
                )
                return
            if expected_session is not None and active_session is not expected_session:
                logger.info(
                    "🎮 赛后 Realtime 主动搭话跳过: game=%s session=%s lanlan=%s attempt=%d reason=realtime_session_changed",
                    archive.get("game_type"),
                    archive.get("session_id"),
                    lanlan_name,
                    attempt,
                )
                return

            trigger = getattr(mgr, "trigger_voice_proactive_nudge", None)
            if not callable(trigger):
                logger.info(
                    "🎮 赛后 Realtime 主动搭话跳过: game=%s session=%s lanlan=%s attempt=%d reason=trigger_unavailable",
                    archive.get("game_type"),
                    archive.get("session_id"),
                    lanlan_name,
                    attempt,
                )
                return

            delivered = bool(await trigger())
            logger.info(
                "🎮 赛后 Realtime 主动搭话尝试: game=%s session=%s lanlan=%s attempt=%d delay=%.1fs delivered=%s",
                archive.get("game_type"),
                archive.get("session_id"),
                lanlan_name,
                attempt,
                delay,
                delivered,
            )
            if delivered:
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "🎮 赛后 Realtime 主动搭话异常: game=%s session=%s lanlan=%s attempt=%d err=%s",
                archive.get("game_type"),
                archive.get("session_id"),
                lanlan_name,
                attempt,
                exc,
            )
    logger.info(
        "🎮 赛后 Realtime 主动搭话放弃: game=%s session=%s lanlan=%s attempts=%d",
        archive.get("game_type"),
        archive.get("session_id"),
        lanlan_name,
        len(delays),
    )


def _postgame_context_request_id(archive: dict) -> Optional[str]:
    game_type = str(archive.get("game_type") or "game").strip() or "game"
    session_id = str(archive.get("session_id") or "default").strip() or "default"
    ended_at = str(archive.get("ended_at") or "").strip()
    if not ended_at:
        return None
    return f"{game_type}:{session_id}:{ended_at}"


async def _deliver_postgame_to_realtime(mgr: Any, archive: dict, options: dict) -> dict:
    session = _active_realtime_session(mgr)
    if not session:
        return {"ok": False, "mode": "realtime", "action": "skip", "reason": "no_active_realtime_session"}

    text = _build_game_postgame_context_text(archive)
    _log_game_debug_material(
        "postgame_realtime_context",
        text,
        game_type=str(archive.get("game_type") or ""),
        session_id=str(archive.get("session_id") or ""),
        lanlan_name=str(archive.get("lanlan_name") or ""),
        source="game_end",
    )
    if _active_realtime_session(mgr) is not session:
        return {
            "ok": False,
            "mode": "realtime",
            "action": "skip",
            "reason": "realtime_session_changed",
        }

    if _is_gemini_realtime_session(session):
        instruction = _build_game_postgame_realtime_nudge_instruction(archive, options)
        _log_game_debug_material(
            "postgame_realtime_nudge_instruction",
            instruction,
            game_type=str(archive.get("game_type") or ""),
            session_id=str(archive.get("session_id") or ""),
            lanlan_name=str(archive.get("lanlan_name") or ""),
            source="game_end",
        )
        if not options.get("trigger_voice", True):
            return {
                "ok": True,
                "mode": "realtime",
                "action": "skip",
                "reason": "gemini_direct_response_disabled",
                "context_injected": False,
                "nudge_scheduled": False,
            }
        create_response = getattr(session, "create_response", None)
        if not callable(create_response):
            return {
                "ok": False,
                "mode": "realtime",
                "action": "skip",
                "reason": "gemini_create_response_unavailable",
            }
        try:
            await create_response(text + "\n\n" + instruction)
        except Exception as exc:
            logger.warning(
                "🎮 赛后 Gemini Realtime 直接触发失败: game=%s session=%s lanlan=%s err=%s",
                archive.get("game_type"),
                archive.get("session_id"),
                archive.get("lanlan_name"),
                exc,
            )
            return {"ok": False, "mode": "realtime", "action": "skip", "reason": "gemini_direct_response_failed"}
        logger.info(
            "🎮 赛后 Gemini Realtime 已直接触发: game=%s session=%s lanlan=%s bytes=%d",
            archive.get("game_type"),
            archive.get("session_id"),
            archive.get("lanlan_name"),
            len(text) + len(instruction),
        )
        return {
            "ok": True,
            "mode": "realtime",
            "action": "direct_response",
            "context_injected": True,
            "nudge_scheduled": False,
            "reason": "gemini_direct_response",
        }

    append_context = getattr(mgr, "append_context", None)
    if not callable(append_context):
        return {"ok": False, "mode": "realtime", "action": "skip", "reason": "context_method_unavailable"}
    postgame_request_id = _postgame_context_request_id(archive)
    try:
        append_result = await append_context(
            source="game.postgame",
            role="system",
            text=text,
            audience="model",
            timing="now",
            lifetime="current_session",
            request_id=postgame_request_id,
            ordering_key=postgame_request_id,
            metadata={
                "game_type": archive.get("game_type"),
                "lanlan_name": archive.get("lanlan_name"),
                "kind": "postgame",
            },
        )
    except Exception as exc:
        logger.warning(
            "🎮 赛后 Realtime 上下文注入失败: game=%s session=%s lanlan=%s err=%s",
            archive.get("game_type"),
            archive.get("session_id"),
            archive.get("lanlan_name"),
            exc,
        )
        return {"ok": False, "mode": "realtime", "action": "skip", "reason": "context_inject_failed"}
    if not getattr(append_result, "appended", False) and not getattr(append_result, "deduped", False):
        return {
            "ok": False,
            "mode": "realtime",
            "action": "skip",
            "reason": getattr(append_result, "reason", None) or "context_inject_failed",
        }

    logger.info(
        "🎮 赛后 Realtime 上下文已注入: game=%s session=%s lanlan=%s bytes=%d",
        archive.get("game_type"),
        archive.get("session_id"),
        archive.get("lanlan_name"),
        len(text),
    )

    if _active_realtime_session(mgr) is not session:
        return {
            "ok": True,
            "mode": "realtime",
            "action": "context",
            "context_injected": True,
            "nudge_scheduled": False,
            "nudge_reason": "realtime_session_changed",
            "reason": "realtime_session_changed",
            "bytes": len(text),
        }

    if getattr(append_result, "deduped", False):
        return {
            "ok": True,
            "mode": "realtime",
            "action": "context",
            "context_injected": True,
            "nudge_scheduled": False,
            "reason": "context_deduped",
        }

    nudge_scheduled = False
    nudge_reason = "disabled"
    if options.get("trigger_voice", True):
        trigger = getattr(mgr, "trigger_voice_proactive_nudge", None)
        if callable(trigger):
            asyncio.create_task(_run_postgame_realtime_nudge_task(
                mgr,
                dict(archive),
                dict(options),
                _POSTGAME_REALTIME_NUDGE_DELAYS,
                expected_session=session,
            ))
            nudge_scheduled = True
            nudge_reason = "scheduled"
            logger.info(
                "🎮 赛后 Realtime 主动搭话已安排: game=%s session=%s lanlan=%s delays=%s",
                archive.get("game_type"),
                archive.get("session_id"),
                archive.get("lanlan_name"),
                ",".join(f"{d:.1f}s" for d in _POSTGAME_REALTIME_NUDGE_DELAYS),
            )
        else:
            nudge_reason = "trigger_unavailable"

    return {
        "ok": True,
        "mode": "realtime",
        "action": "nudge_scheduled" if nudge_scheduled else "context_only",
        "context_injected": True,
        "nudge_scheduled": nudge_scheduled,
        "nudge_reason": nudge_reason,
        "bytes": len(text),
    }


async def _deliver_postgame_text_bubble(
    game_type: str,
    session_id: str,
    mgr: Any,
    archive: dict,
    options: dict,
    *,
    postgame_snapshot: Optional[dict] = None,
) -> dict:
    if not mgr:
        return {"ok": False, "mode": "text", "action": "skip", "reason": "no_session_manager"}
    if _active_realtime_session(mgr):
        return {"ok": False, "mode": "text", "action": "skip", "reason": "active_realtime_session"}

    prepare = getattr(mgr, "prepare_proactive_delivery", None)
    finish = getattr(mgr, "finish_proactive_delivery", None)
    if not callable(prepare) or not callable(finish):
        return {"ok": False, "mode": "text", "action": "skip", "reason": "text_delivery_unavailable"}

    try:
        prepared = await prepare(min_idle_secs=float(options.get("min_idle_secs") or 0.0))
    except Exception as exc:
        logger.warning(
            "🎮 赛后文本气泡准备失败: game=%s session=%s lanlan=%s err=%s",
            game_type,
            session_id,
            archive.get("lanlan_name"),
            exc,
        )
        return {"ok": False, "mode": "text", "action": "skip", "reason": "prepare_failed"}
    if not prepared:
        return {"ok": True, "mode": "text", "action": "pass", "reason": "condition_not_met"}

    proactive_sid = getattr(mgr, "current_speech_id", None)
    state_machine = getattr(mgr, "state", None)
    # Why: pre-allocated out-dict captures the postgame entry/cache key
    # AS SOON AS ``_run_game_chat`` builds the entry, so the ``finally``
    # below can close it on EVERY termination path — including
    # ``asyncio.CancelledError`` (which is ``BaseException`` and bypasses
    # the structured error-result paths inside ``_run_game_chat``).
    postgame_meta: Dict[str, Any] = {}
    postgame_entry: Optional[dict] = None
    postgame_cache_session_id: Optional[str] = None
    try:
        from main_logic.session_state import SessionEvent
        if state_machine and hasattr(state_machine, "fire"):
            await state_machine.fire(SessionEvent.PROACTIVE_PHASE2)

        event = _build_game_postgame_event(game_type, archive, options)
        _log_game_debug_material(
            "postgame_text_event",
            event,
            game_type=game_type,
            session_id=session_id,
            lanlan_name=str(archive.get("lanlan_name") or ""),
            source="game_end",
        )
        # Why: postgame runs AFTER ``_finalize_game_route_state`` flips the
        # route to inactive, so the standard B1/B2 short-circuits would
        # silently drop this designed teardown step. ``allow_postgame``
        # opts out of those route-active gates; we own the lifecycle of
        # any session built here and close it in the ``finally`` below.
        llm_result = await _run_game_chat(
            game_type, session_id, event, allow_postgame=True,
            postgame_snapshot=postgame_snapshot,
            postgame_meta_out=postgame_meta,
        )
        if isinstance(llm_result, dict):
            postgame_entry = llm_result.get("_postgame_entry")
            postgame_cache_session_id = llm_result.get("_postgame_cache_session_id")
        line = str(llm_result.get("line") or "").strip()
        if not line:
            return {
                "ok": True,
                "mode": "text",
                "action": "pass",
                "reason": llm_result.get("error") or "empty_line",
                "llm_source": llm_result.get("llm_source") or {},
            }

        tts_fed = False
        feed_tts = getattr(mgr, "feed_tts_chunk", None)
        if callable(feed_tts):
            try:
                await feed_tts(line, expected_speech_id=proactive_sid)
                tts_fed = True
            except Exception as exc:
                logger.warning(
                    "🎮 赛后文本气泡 TTS 投喂失败: game=%s session=%s lanlan=%s err=%s",
                    game_type,
                    session_id,
                    archive.get("lanlan_name"),
                    exc,
                )

        committed = bool(await finish(line, expected_speech_id=proactive_sid))
        return {
            "ok": committed,
            "mode": "text",
            "action": "chat" if committed else "pass",
            "reason": "delivered" if committed else "user_took_over",
            "line": line,
            "turn_id": proactive_sid,
            "tts_fed": tts_fed,
            "llm_source": llm_result.get("llm_source") or {},
        }
    except Exception as exc:
        logger.warning(
            "🎮 赛后文本气泡投递失败: game=%s session=%s lanlan=%s err=%s",
            game_type,
            session_id,
            archive.get("lanlan_name"),
            exc,
        )
        return {"ok": False, "mode": "text", "action": "skip", "reason": "deliver_failed"}
    finally:
        try:
            from main_logic.session_state import SessionEvent
            if state_machine and hasattr(state_machine, "fire"):
                await state_machine.fire(SessionEvent.PROACTIVE_DONE)
        except Exception as exc:
            logger.debug("🎮 赛后文本气泡状态机收尾失败: %s", exc, exc_info=True)
        # Why: ``_run_game_chat(..., allow_postgame=True)`` builds the
        # postgame's ``OmniOfflineClient`` at a private cache key
        # (``::postgame::<uuid>`` suffix) so a fresh ``/route/start``
        # reusing the user-facing ``session_id`` cannot land on the same
        # ``_game_sessions`` slot. Identity-gating the eviction stays as
        # defense in depth (a heartbeat sweep could theoretically pop
        # then rebuild the postgame slot). We always close OUR captured
        # entry's session object so the postgame client can never leak.
        # ``OmniOfflineClient.close`` is idempotent, so a peer's prior
        # close is safe to re-run.
        if postgame_entry is None:
            # Why: on ``asyncio.CancelledError`` (or any other
            # ``BaseException``) the await above never returned, so the
            # local ``postgame_entry``/``postgame_cache_session_id`` are
            # still ``None``. The out-dict ``_run_game_chat`` populated
            # mid-call still has the entry, so we fall back to it here.
            postgame_entry = postgame_meta.get("_postgame_entry")
            postgame_cache_session_id = postgame_meta.get("_postgame_cache_session_id")
        if postgame_entry is not None:
            postgame_lanlan = str(
                postgame_entry.get("lanlan_name") or archive.get("lanlan_name") or ""
            )
            cache_session_id = postgame_cache_session_id or session_id
            try:
                key = _game_session_key(postgame_lanlan, game_type, cache_session_id)
                cached = _game_sessions.get(key)
                if cached is postgame_entry:
                    _game_sessions.pop(key, None)
                    _game_session_create_locks.pop(key, None)
            except Exception as exc:
                logger.debug(
                    "🎮 赛后文本气泡 cache 清理失败: game=%s session=%s err=%s",
                    game_type, session_id, exc, exc_info=True,
                )
            postgame_session = postgame_entry.get("session")
            if postgame_session is not None:
                try:
                    await postgame_session.close()
                except Exception as exc:
                    logger.debug(
                        "🎮 赛后文本气泡 session 清理失败: game=%s session=%s err=%s",
                        game_type, session_id, exc, exc_info=True,
                    )


async def _deliver_game_postgame(
    game_type: str,
    session_id: str,
    lanlan_name: str,
    archive: dict,
    options: dict,
    *,
    postgame_snapshot: Optional[dict] = None,
) -> dict:
    if not options.get("enabled", True):
        return {"ok": True, "action": "skip", "reason": "disabled"}
    mgr = get_session_manager().get(lanlan_name) if lanlan_name else None
    mode = str(options.get("mode") or "auto").lower()
    if mode in {"auto", "realtime"} and _active_realtime_session(mgr):
        return await _deliver_postgame_to_realtime(mgr, archive, options)
    if mode == "realtime":
        return {"ok": False, "mode": "realtime", "action": "skip", "reason": "no_active_realtime_session"}
    return await _deliver_postgame_text_bubble(
        game_type, session_id, mgr, archive, options,
        postgame_snapshot=postgame_snapshot,
    )


async def _submit_game_archive_to_memory(archive: dict) -> dict:
    """Persist a compact game archive into recent memory without blocking exit semantics."""
    if _game_memory_archive_enabled(archive) is False:
        return _build_game_archive_memory_skipped_result("game_memory_archive_disabled")
    lanlan_name = str(archive.get("lanlan_name") or "").strip()
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}
    if archive.get("memory_cached"):
        return dict(archive.get("memory_result") or {"ok": True, "status": "already_cached"})

    try:
        from config import MEMORY_SERVER_PORT
        from utils.internal_http_client import get_internal_http_client

        highlights = await _ensure_game_archive_memory_highlights(archive)
        _log_game_debug_material(
            "memory_archive_highlights",
            highlights,
            game_type=str(archive.get("game_type") or ""),
            session_id=str(archive.get("session_id") or ""),
            lanlan_name=lanlan_name,
            source="game_memory_archive",
        )
        messages = _build_game_archive_memory_messages(archive)
        _log_game_debug_material(
            "memory_archive",
            messages,
            game_type=str(archive.get("game_type") or ""),
            session_id=str(archive.get("session_id") or ""),
            lanlan_name=lanlan_name,
            source="memory_server_cache",
        )
        client = get_internal_http_client()
        response = await client.post(
            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/cache/{lanlan_name}",
            json={"input_history": json.dumps(messages, ensure_ascii=False)},
            timeout=8.0,
        )
        data = response.json() if response.content else {}
        if not response.is_success or data.get("status") == "error":
            result = {
                "ok": False,
                "reason": data.get("message") or f"memory_http_{response.status_code}",
                "status_code": response.status_code,
            }
        else:
            result = {
                "ok": True,
                "status": data.get("status", "cached"),
                "count": data.get("count"),
            }
    except Exception as e:
        logger.warning(
            "🎮 游戏归档写入 memory_server 失败: game=%s session=%s lanlan=%s err=%s",
            archive.get("game_type"),
            archive.get("session_id"),
            lanlan_name,
            e,
        )
        result = {"ok": False, "reason": type(e).__name__, "message": str(e)}

    archive["memory_cached"] = bool(result.get("ok"))
    archive["memory_result"] = result
    return result


async def _finalize_game_route_state(
    state: dict,
    *,
    reason: str,
    close_game_session: bool = False,
    close_debug_log: bool = True,
) -> dict:
    """Run the game route exit flow once, including archive submission.

    Concurrent-call semantics:

    - The first caller spawns ``_finalize_game_route_state_inner`` and
      shields its task. Subsequent callers ``await asyncio.shield`` the
      same task.
    - ``close_game_session`` uses **OR-merge** semantics across concurrent
      callers (B5): we stash the requested value on the state under
      ``_exit_close_session_request``, and the inner runner reads that
      flag (not its constructor arg) when deciding whether to close.
      Previously the second caller's ``True`` was silently dropped while
      the first caller's ``False`` won; the second caller then redundantly
      invoked ``_close_and_remove_session`` outside the shield, racing
      with the inner finalize and producing double-pop / double-close.
    - codex P2 follow-up: a late caller arriving with
      ``close_game_session=True`` AFTER the inner runner already passed
      its close-site check (or finished entirely) used to lose its
      request — the dispatcher just awaited the cached task result. We
      now re-check ``_exit_close_session_request`` against the inner's
      result on the existing-task path and perform the close ourselves
      if the inner missed it. ``_close_and_remove_session`` is
      idempotent so concurrent late callers cannot double-close.
    """
    if close_game_session:
        state["_exit_close_session_request"] = True
    elif "_exit_close_session_request" not in state:
        state["_exit_close_session_request"] = False
    if close_debug_log:
        state["_exit_close_debug_log_request"] = True
    else:
        state["_exit_defer_debug_log_close"] = True
        if "_exit_close_debug_log_request" not in state:
            state["_exit_close_debug_log_request"] = False

    existing_task = state.get("_exit_task")
    if existing_task:
        result = await asyncio.shield(existing_task)
        if state.get("_exit_close_session_request") and not result.get("game_session_closed"):
            closed_now = await _close_and_remove_session(
                str(state.get("game_type") or ""),
                str(state.get("session_id") or "default"),
                str(state.get("lanlan_name") or ""),
            )
            if closed_now:
                # Why: mutate the shared result dict so any other awaiter (or
                # subsequent late caller) observes the close. The inner's
                # return dict is the single source of truth handed back to
                # every shielded await.
                result["game_session_closed"] = True
        if (
            state.get("_exit_close_debug_log_request")
            and not state.get("_exit_defer_debug_log_close")
            and not result.get("debug_log_ended")
        ):
            _mark_game_session_debug_log_ended(
                str(state.get("game_type") or ""),
                str(state.get("session_id") or "default"),
                lanlan_name=str(state.get("lanlan_name") or ""),
                reason=reason,
            )
            result["debug_log_ended"] = True
        return result

    task = asyncio.create_task(_finalize_game_route_state_inner(state, reason=reason))
    state["_exit_task"] = task
    return await asyncio.shield(task)


def _build_postgame_context_snapshot(state: dict) -> dict:
    # Why: postgame's prompt context must be FROZEN at finalize time.
    # Without this, ``_build_and_register_game_session`` /
    # ``_refresh_game_session_instructions`` reverse-resolve live
    # route_state via ``_find_game_route_state_for_session`` AFTER finalize
    # has flipped this state inactive — and a fresh ``/route/start`` for
    # the same ``(lanlan, game_type)`` key REPLACES the entry in
    # ``_game_route_states``, so the lookup returns the NEW route's
    # preGameContext / game_context. Snapshotting the two already-resolved
    # dicts the prompt builder needs (``pre_game_context`` and
    # ``game_context``) is the minimum-viable freeze.
    pre_game_context = state.get("preGameContext") if isinstance(state.get("preGameContext"), dict) else None
    return {
        "pre_game_context": pre_game_context,
        "game_context": _build_game_context_prompt_payload(state, include_recent=False),
        "mode": _normalize_badminton_mode(state.get("mode")),
    }


async def _finalize_game_route_state_inner(
    state: dict,
    *,
    reason: str,
) -> dict:
    state["_exit_flow_started"] = True
    state["exit_reason"] = reason
    state["exit_started_at"] = time.time()
    # Capture postgame's prompt context BEFORE flipping the route inactive
    # / before the archive resolution / before any peer ``/route/start``
    # can replace this state in ``_game_route_states``.
    postgame_context_snapshot = _build_postgame_context_snapshot(state)
    state["game_route_active"] = False
    state["game_external_voice_route_active"] = False
    state["game_external_text_route_active"] = False
    state["heartbeat_enabled"] = False
    lanlan_name = str(state.get("lanlan_name") or "")
    mgr = get_session_manager().get(lanlan_name) if lanlan_name else None
    # 推 closed 事件让前端还原 chat.html 折叠态 + 显回 pet 容器。所有 finalize
    # 路径（/route/end / heartbeat sweep / supersede）都走本 inner，与 active
    # flag 翻 false 同源，不会出现"已结束但 UI 仍锁着收缩态"的孤岛。
    await _push_game_window_state_change(
        mgr,
        action="closed",
        lanlan_name=lanlan_name,
        game_type=str(state.get("game_type") or ""),
        session_id=str(state.get("session_id") or ""),
    )
    # Release the SessionManager-level takeover so ordinary chat handlers come
    # back online; chat LLM may produce auto-replies again, but the player has
    # exited the game so that's the desired behavior.
    if mgr is not None:
        mgr._takeover_active = False
        mgr._takeover_input_dispatcher = None
    realtime_restore = {"attempted": False, "ok": True, "reason": "takeover_released"}
    state["realtime_restore"] = realtime_restore
    if mgr and hasattr(mgr, "send_status"):
        try:
            await mgr.send_status(json.dumps({
                "code": "GAME_ROUTE_ENDED",
                "details": {
                    "game_type": str(state.get("game_type") or ""),
                    "session_id": str(state.get("session_id") or ""),
                    "lanlan_name": lanlan_name,
                    "reason": reason,
                    "before_game_external_mode": state.get("before_game_external_mode"),
                    "before_game_external_active": bool(state.get("before_game_external_active")),
                    "should_resume_external_on_exit": bool(state.get("should_resume_external_on_exit")),
                    "realtime_restore": realtime_restore,
                },
            }))
        except Exception as exc:
            logger.warning("⚠️ 游戏路由退出状态通知失败: %s", exc)

    skip_memory_reason = _game_archive_memory_skip_reason(state, reason)
    if skip_memory_reason == "game_memory_archive_disabled":
        await _cancel_game_context_organizer_before_disabled_archive(state)
    else:
        await _settle_game_context_organizer_before_archive(state)

    archive = state.get("archive") if isinstance(state.get("archive"), dict) else None
    if archive is None:
        archive = _build_game_archive(state)
    archive["exit_reason"] = reason
    state["archive"] = archive

    memory_result = state.get("archive_memory_result")
    if not isinstance(memory_result, dict):
        if skip_memory_reason:
            archive["memory_skipped"] = True
            archive["memory_skip_reason"] = skip_memory_reason
            memory_result = _build_game_archive_memory_skipped_result(skip_memory_reason)
        else:
            memory_result = await _submit_game_archive_to_memory(archive)
        state["archive_memory_result"] = memory_result

    # B5: OR-merge close decision across concurrent callers (see note in
    # ``_finalize_game_route_state``). Re-read the flag *after* awaiting
    # the archive work so a second caller arriving mid-finalize with
    # ``close_game_session=True`` still wins.
    session_closed = False
    if state.get("_exit_close_session_request"):
        session_closed = await _close_and_remove_session(
            str(state.get("game_type") or ""),
            str(state.get("session_id") or "default"),
            str(state.get("lanlan_name") or ""),
        )
    debug_log_ended = False
    if state.get("_exit_close_debug_log_request") and not state.get("_exit_defer_debug_log_close"):
        _mark_game_session_debug_log_ended(
            str(state.get("game_type") or ""),
            str(state.get("session_id") or "default"),
            lanlan_name=str(state.get("lanlan_name") or ""),
            reason=reason,
        )
        debug_log_ended = True

    return {
        "archive": archive,
        "archive_memory": memory_result,
        "game_session_closed": session_closed,
        "debug_log_ended": debug_log_ended,
        "exit_reason": reason,
        "realtime_restore": realtime_restore,
        "postgame_context_snapshot": postgame_context_snapshot,
    }


async def _get_or_create_session(
    game_type: str,
    session_id: str,
    lanlan_name: str = "",
    *,
    postgame_snapshot: Optional[dict] = None,
) -> dict:
    """Get or create a game session.

    B6: serialize the cache-miss → ctor → connect → cache-insert sequence
    under a per-key ``asyncio.Lock`` so two concurrent ``_run_game_chat``
    calls for the same ``(lanlan, game_type, session_id)`` cannot both
    build a fresh ``OmniOfflineClient`` and overwrite each other in
    ``_game_sessions``, leaking the loser's connection.

    CodeRabbit follow-up: ``lanlan_name`` may be empty on entry
    (caller-supplied) but canonicalizes to ``char_info["lanlan_name"]``.
    Resolve the canonical key BEFORE acquiring the create lock so we
    only ever take one lock — under the canonical key. The previous
    "lock under raw key, then re-lock under canonical key" shape left
    an orphan ``_game_session_create_locks[raw_key]`` entry whenever
    the canonical resolution changed the key, because
    ``_close_and_remove_session`` only evicts the lock keyed by the
    session's actual storage key (the canonical one).

    The fast-path cache check still uses the raw key so a hit on the
    pre-canonicalization shape (rare; only happens if a session was
    cached under an empty lanlan_name) short-circuits without paying
    the ``_get_character_info`` lookup.
    """
    key = _game_session_key(lanlan_name, game_type, session_id)

    if key in _game_sessions:
        entry = _game_sessions[key]
        entry['last_activity'] = time.time()
        return entry

    char_info = _get_character_info(lanlan_name)
    canonical_lanlan = str(char_info.get("lanlan_name") or lanlan_name or "").strip()
    canonical_key = _game_session_key(canonical_lanlan, game_type, session_id)

    # Fast path: canonical_key may already be cached (e.g. another caller
    # passed the canonical lanlan_name and built it).
    if canonical_key in _game_sessions:
        entry = _game_sessions[canonical_key]
        entry['last_activity'] = time.time()
        return entry

    create_lock = _get_session_create_lock(canonical_key)
    async with create_lock:
        if canonical_key in _game_sessions:
            entry = _game_sessions[canonical_key]
            entry['last_activity'] = time.time()
            return entry
        try:
            return await _build_and_register_game_session(
                canonical_key, game_type, session_id, char_info,
                postgame_snapshot=postgame_snapshot,
            )
        except BaseException:
            # codex P2 (PR #1127 r3182157092): if the build raises,
            # nothing inserts into ``_game_sessions`` for this key, so
            # ``_close_and_remove_session`` will never run for it and
            # the per-key create lock would leak forever. Evict it
            # here.
            #
            # Why conditional pop on ``_waiters``: if a peer task is
            # already awaiting THIS lock (concurrent miss for the same
            # canonical_key), unconditionally popping would let a new
            # arrival call ``_get_session_create_lock`` and receive a
            # FRESH lock object — distinct from the one the peer is
            # awaiting — defeating the build serialization. Leaving
            # the lock in place when there are waiters keeps them on
            # the same Lock instance; the next successful build path
            # registers an entry and ``_close_and_remove_session``
            # will pop normally; another failed build will hit this
            # branch again and eventually find ``_waiters`` empty.
            # ``_waiters`` is CPython-private but stable across all
            # supported Python versions.
            waiters = getattr(create_lock, "_waiters", None)
            if not waiters:
                _game_session_create_locks.pop(canonical_key, None)
            raise


async def _build_and_register_game_session(
    key: str,
    game_type: str,
    session_id: str,
    char_info: dict,
    *,
    postgame_snapshot: Optional[dict] = None,
) -> dict:
    """Build a fresh game session entry; caller must already hold the
    per-key creation lock (see ``_get_or_create_session``).

    ``postgame_snapshot`` (when set) is the authoritative prompt-context
    source for postgame builds — see ``_build_postgame_context_snapshot``.
    Without it, a postgame build that races a fresh ``/route/start`` for
    the same ``(lanlan, game_type, session_id)`` would reverse-resolve
    live route_state and pick up the NEW route's preGameContext /
    game_context.
    """
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.token_tracker import set_call_type

    lanlan_name = str(char_info.get("lanlan_name") or "").strip()
    reply_chunks: list[str] = []

    async def on_text_delta(text: str, is_first: bool, **_kwargs):
        # **_kwargs 吞掉 ui_enabled / tts_enabled（OmniOfflineClient summary 路径会传，
        # 但 game 短台词跑的是非 summary 模式，理论上不会发 UI/TTS 分流。保留 kwargs
        # forward-compat 防签名漂移触发 TypeError。）
        reply_chunks.append(text)

    set_call_type("game_chat")

    session = OmniOfflineClient(
        base_url=char_info['base_url'],
        api_key=char_info['api_key'],
        model=char_info['model'],
        on_text_delta=on_text_delta,
        max_response_length=100,  # 游戏台词要短
        lanlan_name=char_info['lanlan_name'],
        master_name=char_info['master_name'],
    )

    if postgame_snapshot is not None:
        pre_game_context = postgame_snapshot.get("pre_game_context")
        game_context = postgame_snapshot.get("game_context")
        route_mode = _normalize_badminton_mode(postgame_snapshot.get("mode"))
    else:
        route_state = _find_game_route_state_for_session(game_type, _route_session_id(session_id), lanlan_name)
        pre_game_context = route_state.get("preGameContext") if isinstance(route_state, dict) else None
        game_context = _build_game_context_prompt_payload(route_state, include_recent=False)
        route_mode = _normalize_badminton_mode(route_state.get("mode") if isinstance(route_state, dict) else "")
    prompt_args = (
        game_type,
        char_info['lanlan_name'],
        char_info['lanlan_prompt'],
        pre_game_context if isinstance(pre_game_context, dict) else None,
        game_context if isinstance(game_context, dict) else None,
        char_info.get("user_language"),
    )
    if _is_badminton_game_type(game_type):
        system_prompt = _build_game_prompt(*prompt_args, mode=route_mode)
    else:
        system_prompt = _build_game_prompt(*prompt_args)
    try:
        await session.connect(instructions=system_prompt)
    except asyncio.CancelledError:
        # Why: CancelledError doesn't inherit from Exception in Python
        # 3.8+; without this branch a cancelled connect leaks the half-
        # open client.
        try:
            await session.close()
        except Exception:
            # Why: cleanup must remain idempotent on the cancellation
            # path — a close() failure here would mask the original
            # CancelledError that we re-raise below.
            pass
        raise
    except Exception:
        # Connect failed — ensure we don't leak a half-open client. close
        # is idempotent / tolerant of "never connected".
        try:
            await session.close()
        except Exception:
            # Why: cleanup must not raise from the exception path —
            # a close() failure would mask the original connect error
            # that we re-raise below.
            pass
        raise

    entry = {
        'session': session,
        'reply_chunks': reply_chunks,
        'lanlan_name': char_info['lanlan_name'],
        'lanlan_prompt': char_info.get('lanlan_prompt') or '',
        'user_language': char_info.get('user_language'),
        'source': _infer_service_source(
            char_info.get('base_url', ''),
            char_info.get('model', ''),
            char_info.get('api_type', ''),
        ),
        'last_activity': time.time(),
        'lock': asyncio.Lock(),
        'instructions': system_prompt,
        'mode': route_mode,
    }
    _game_sessions[key] = entry

    logger.info(
        "🎮 创建游戏LLM会话: 游戏=%s 会话=%s 角色=%s 模型=%s 人格提示长度=%d字",
        game_type,
        session_id,
        char_info['lanlan_name'],
        char_info['model'],
        len(char_info.get('lanlan_prompt') or ''),
    )
    return entry


async def _refresh_game_session_instructions(
    entry: dict,
    game_type: str,
    session_id: str,
    lanlan_name: str = "",
    *,
    postgame_snapshot: Optional[dict] = None,
) -> None:
    session = entry.get("session") if isinstance(entry, dict) else None
    update = getattr(session, "update_session", None)
    if not callable(update):
        return

    lanlan_name = str(lanlan_name or entry.get("lanlan_name") or "").strip()
    char_info = _get_character_info(lanlan_name)
    entry["user_language"] = char_info.get("user_language")
    if postgame_snapshot is not None:
        pre_game_context = postgame_snapshot.get("pre_game_context")
        game_context = postgame_snapshot.get("game_context")
        route_mode = _normalize_badminton_mode(postgame_snapshot.get("mode"))
    else:
        route_state = _find_game_route_state_for_session(game_type, _route_session_id(session_id), char_info["lanlan_name"])
        pre_game_context = route_state.get("preGameContext") if isinstance(route_state, dict) else None
        game_context = _build_game_context_prompt_payload(route_state, include_recent=False)
        route_mode = _normalize_badminton_mode(route_state.get("mode") if isinstance(route_state, dict) else "")
    prompt_args = (
        game_type,
        char_info["lanlan_name"],
        char_info["lanlan_prompt"],
        pre_game_context if isinstance(pre_game_context, dict) else None,
        game_context if isinstance(game_context, dict) else None,
        char_info.get("user_language"),
    )
    if _is_badminton_game_type(game_type):
        instructions = _build_game_prompt(*prompt_args, mode=route_mode)
    else:
        instructions = _build_game_prompt(*prompt_args)
    if entry.get("instructions") == instructions:
        entry["mode"] = route_mode
        return
    await update({"instructions": instructions})
    entry["instructions"] = instructions
    entry["mode"] = route_mode


def _parse_control_instructions(reply: str, game_type: str = "soccer") -> Dict[str, Any]:
    """Parse structured control instructions from the reply."""
    import json as _json

    text = reply.strip()
    lines = text.split('\n')
    line_text = text
    control = {}
    json_control_seen = False

    def apply_control(parsed: Any) -> None:
        nonlocal json_control_seen
        if not isinstance(parsed, dict):
            return
        json_control_seen = True
        mood = str(parsed.get("mood") or "").strip()
        if mood in _SOCCER_MOODS:
            control["mood"] = mood
        if _is_badminton_game_type(game_type):
            expression = str(parsed.get("expression") or "").strip()
            intensity = str(parsed.get("intensity") or "").strip()
            difficulty = str(parsed.get("difficulty") or "").strip()
            if expression in {"cheer", "shock", "hype", "anticipate", "bored", "tease"}:
                control["expression"] = expression
            if intensity in {"low", "medium", "high"}:
                control["intensity"] = intensity
            if difficulty in _SOCCER_DIFFICULTIES:
                control["difficulty"] = difficulty
            if "reason" in parsed:
                reason = str(parsed.get("reason") or "").strip()
                if reason:
                    control["reason"] = reason[:120]
        else:
            difficulty = str(parsed.get("difficulty") or "").strip()
            if difficulty in _SOCCER_DIFFICULTIES:
                control["difficulty"] = difficulty
            if "reason" in parsed:
                reason = str(parsed.get("reason") or "").strip()
                if reason:
                    control["reason"] = reason[:120]

    # 优先支持规范格式：最后一行单独输出 JSON 控制指令。
    if len(lines) > 1 and lines[-1].strip().startswith('{') and lines[-1].strip().endswith('}'):
        try:
            parsed = _json.loads(lines[-1].strip())
            apply_control(parsed)
            if control or json_control_seen:
                line_text = '\n'.join(lines[:-1]).strip()
        except _json.JSONDecodeError:
            pass

    # 容错：有些模型会把 JSON 粘在台词同一行末尾，也要剥离，避免显示到气泡里。
    if not json_control_seen:
        json_start = text.rfind('{')
        json_end = text.rfind('}')
        if 0 <= json_start < json_end == len(text) - 1:
            try:
                parsed = _json.loads(text[json_start:json_end + 1])
                apply_control(parsed)
                if control or json_control_seen:
                    line_text = text[:json_start].strip()
            except _json.JSONDecodeError:
                pass

    return {
        'line': _sanitize_game_visible_line(line_text),
        'control': control,
    }


def _build_soccer_balance_hint(event: Any) -> Dict[str, Any]:
    """Generate a soft hint from the score: reminds the LLM of the game state without making the control decision for it."""
    if not isinstance(event, dict):
        return {}

    score = event.get('score') or {}
    if not isinstance(score, dict):
        score = {}

    try:
        score_diff = int(event.get('scoreDiff', int(score.get('ai', 0)) - int(score.get('player', 0))))
    except (TypeError, ValueError):
        return {}

    abs_diff = abs(score_diff)
    if abs_diff < 3:
        return {
            'state': 'close_game',
            'scoreDiff': score_diff,
            'intensity': 'low',
            'message': '比分接近，通常可以自由发挥，不需要为了平衡而控制难度。',
        }

    ai_leading = score_diff > 0
    if abs_diff >= 10:
        intensity = 'extreme'
    elif abs_diff >= 6:
        intensity = 'high'
    else:
        intensity = 'medium'

    if ai_leading:
        return {
            'state': 'ai_leading',
            'scoreDiff': score_diff,
            'intensity': intensity,
            'suggestion': 'consider_easing',
            'recommendedDifficulty': 'lv4' if abs_diff >= 10 else 'lv3',
            'message': (
                '你已经明显领先玩家。可以考虑放水、逗玩家、撒娇、故意失误、降低难度，'
                '但如果你有明确情绪或关系理由，也可以继续压制；请在台词里表达原因。'
            ),
        }

    return {
        'state': 'player_leading',
        'scoreDiff': score_diff,
        'intensity': intensity,
        'suggestion': 'consider_trying_harder',
        'recommendedDifficulty': 'max' if abs_diff >= 6 else 'lv2',
        'message': '玩家明显领先你。可以考虑认真起来、提高难度、表现胜负欲或不甘心。',
    }


def _soccer_context_text_blob(value: Any) -> str:
    parts: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            text = _soccer_context_text_blob(item)
            if text:
                parts.append(text)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            text = _soccer_context_text_blob(item)
            if text:
                parts.append(text)
    elif value is not None:
        text = str(value).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _soccer_anger_pressure_cap_applicable(pre_game_context: Any) -> bool:
    if not isinstance(pre_game_context, dict):
        return False
    stance = str(pre_game_context.get("gameStance") or "").strip()
    if stance == "punishing":
        return True

    emotion_values = {
        str(pre_game_context.get("nekoEmotion") or "").strip(),
        str(pre_game_context.get("initialMood") or "").strip(),
    }
    if "angry" not in emotion_values:
        return False

    if stance == "withdrawn":
        return True

    text_blob = _soccer_context_text_blob(pre_game_context).lower()
    anger_context_keywords = (
        "生气", "发火", "愤怒", "爆发", "惩罚", "教训", "报复", "泄愤",
        "冷战", "冲突", "关系修复", "哄", "道歉", "补偿", "赔偿",
        "angry", "punish", "punishment", "repair", "apology", "compensation",
        "cold_war",
    )
    return any(keyword in text_blob for keyword in anger_context_keywords)


def _soccer_anger_pressure_cap_goals(pre_game_context: Any, lanlan_prompt: str = "") -> int:
    text_blob = f"{_soccer_context_text_blob(pre_game_context)} {lanlan_prompt}".lower()
    weak_keywords = (
        "不擅长运动", "运动差", "体力弱", "体弱", "虚弱", "病弱", "容易累",
        "缺乏运动", "宅", "懒得动", "weak", "frail", "sickly",
    )
    strong_keywords = (
        "擅长运动", "运动神经", "体育", "足球", "体力强", "耐力好", "精力充沛",
        "敏捷", "athletic", "sporty", "stamina", "energetic",
    )
    if any(keyword in text_blob for keyword in weak_keywords):
        return _SOCCER_ANGER_PRESSURE_CAP_WEAK
    if any(keyword in text_blob for keyword in strong_keywords):
        return _SOCCER_ANGER_PRESSURE_CAP_STRONG
    return _SOCCER_ANGER_PRESSURE_CAP_DEFAULT


def _build_soccer_anger_pressure_cap(
    event: Any,
    route_state: Any,
    *,
    lanlan_prompt: str = "",
    language: str | None = None,
) -> Dict[str, Any]:
    if not isinstance(event, dict) or not isinstance(route_state, dict):
        return {}
    pre_game_context = route_state.get("preGameContext")
    if not _soccer_anger_pressure_cap_applicable(pre_game_context):
        return {}

    score = event.get("score") if isinstance(event.get("score"), dict) else {}
    try:
        ai_goals = int(score.get("ai", 0))
        player_goals = int(score.get("player", 0))
    except (TypeError, ValueError):
        return {}
    try:
        score_diff = int(event.get("scoreDiff", ai_goals - player_goals))
    except (TypeError, ValueError):
        score_diff = ai_goals - player_goals

    cap_goals = _soccer_anger_pressure_cap_goals(pre_game_context, lanlan_prompt)
    recommended_difficulty = "lv4" if score_diff >= 10 or ai_goals >= cap_goals + 5 else "lv3"
    reached = ai_goals >= cap_goals and score_diff > 0
    return {
        "applicable": True,
        "reached": reached,
        "capGoals": cap_goals,
        "aiGoals": ai_goals,
        "playerGoals": player_goals,
        "scoreDiff": score_diff,
        "recommendedDifficulty": recommended_difficulty,
        "message": get_soccer_anger_pressure_cap_message(language),
        "reason": get_soccer_anger_pressure_cap_reason(language),
    }


def _event_current_difficulty(event: Any) -> str:
    if not isinstance(event, dict):
        return ""
    difficulty = str(event.get("difficulty") or "").strip()
    if difficulty:
        return difficulty
    current_state = event.get("currentState")
    if isinstance(current_state, dict):
        return str(current_state.get("difficulty") or "").strip()
    return ""


def _apply_soccer_anger_pressure_cap(result: Dict[str, Any], event: Any) -> Dict[str, Any]:
    if not isinstance(result, dict) or not isinstance(event, dict):
        return result
    cap = event.get("angerPressureCap") if isinstance(event.get("angerPressureCap"), dict) else {}
    if not cap or cap.get("reached") is not True:
        if cap:
            result["anger_pressure_cap"] = dict(cap, adjusted=False)
        return result

    control = dict(result.get("control") or {})
    requested_difficulty = str(control.get("difficulty") or "").strip()
    current_difficulty = _event_current_difficulty(event)
    should_clamp = requested_difficulty == "max" or (not requested_difficulty and current_difficulty == "max")
    adjusted = False
    if should_clamp:
        control["difficulty"] = str(cap.get("recommendedDifficulty") or "lv3")
        existing_reason = str(control.get("reason") or "").strip()
        cap_reason = str(cap.get("reason") or "").strip() or get_soccer_anger_pressure_cap_reason()
        if existing_reason:
            control["reason"] = f"{existing_reason}；{cap_reason}"
        elif event.get("requestControlReason") is True:
            control["reason"] = cap_reason
        result["control"] = control
        adjusted = True

    result["anger_pressure_cap"] = dict(cap, adjusted=adjusted)
    return result


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_badminton_duel_balance_hint(event: Any) -> Dict[str, Any]:
    """Build a soft balance hint from the Badminton duel score."""
    if not isinstance(event, dict):
        return {}
    current_state = event.get("currentState") if isinstance(event.get("currentState"), dict) else {}
    state_duel = _sanitize_badminton_duel_state(current_state.get("duel")) or {}
    event_duel = _sanitize_badminton_duel_state(event.get("duel")) or {}
    duel = {**state_duel, **event_duel}
    player_score = _safe_int(duel.get("player_score"), 0)
    neko_score = _safe_int(duel.get("neko_score"), 0)
    max_rounds = _safe_int(duel.get("max_rounds"), 0)
    round_num = _safe_int(duel.get("round"), 0)
    remaining_rounds = max(0, max_rounds - round_num) if max_rounds else 999
    max_misses = _safe_int(duel.get("max_misses"), 0)
    player_misses = _safe_int(duel.get("player_misses"), 0)
    neko_misses = _safe_int(duel.get("neko_misses"), 0)
    player_misses_left = max(0, max_misses - player_misses) if max_misses else 999
    neko_misses_left = max(0, max_misses - neko_misses) if max_misses else 999
    miss_context = (
        {
            "playerMisses": player_misses,
            "nekoMisses": neko_misses,
            "maxMisses": max_misses,
            "playerMissesLeft": player_misses_left,
            "nekoMissesLeft": neko_misses_left,
        }
        if max_misses
        else {}
    )

    diff = neko_score - player_score
    active_shooter = str(duel.get("active_shooter") or "").strip().lower()
    trailing_shooter = "player" if diff > 0 else ("neko" if diff < 0 else "")
    pending_shot_points = 2 if active_shooter == trailing_shooter else 0
    remaining_points = remaining_rounds * 2 + pending_shot_points if max_rounds else 999
    abs_diff = abs(diff)
    if max_misses and player_misses_left <= 0:
        return {
            "state": "player_eliminated",
            "diff": diff,
            "remainingRounds": remaining_rounds,
            "remainingPoints": remaining_points,
            **miss_context,
            "intensity": "low",
            "message": "玩家已经三次投丢，按淘汰规则收尾。",
        }
    if max_misses and neko_misses_left <= 0:
        return {
            "state": "neko_eliminated",
            "diff": diff,
            "remainingRounds": remaining_rounds,
            "remainingPoints": remaining_points,
            **miss_context,
            "intensity": "low",
            "message": "Neko 已经三次投丢，按淘汰规则收尾。",
        }
    if abs_diff <= 1:
        return {
            "state": "close",
            "diff": diff,
            "remainingRounds": remaining_rounds,
            "remainingPoints": remaining_points,
            **miss_context,
            "intensity": "low",
            "message": "比分接近，自由发挥。",
        }

    neko_leading = diff > 0
    if not max_misses and neko_leading and diff > remaining_points:
        return {
            "state": "neko_leading_decided",
            "diff": diff,
            "remainingRounds": remaining_rounds,
            "remainingPoints": remaining_points,
            **miss_context,
            "intensity": "low",
            "message": "已经锁定胜局，可以嘴硬庆祝或温和收尾。",
        }
    if not max_misses and not neko_leading and abs_diff > remaining_points:
        return {
            "state": "player_leading_decided",
            "diff": diff,
            "remainingRounds": remaining_rounds,
            "remainingPoints": remaining_points,
            **miss_context,
            "intensity": "low",
            "message": "确定输定了，可以不服气、认输或要求再来一局。",
        }
    if neko_leading:
        return {
            "state": "neko_leading",
            "diff": diff,
            "remainingRounds": remaining_rounds,
            "remainingPoints": remaining_points,
            **miss_context,
            "intensity": "high" if abs_diff >= 5 else "medium",
            "message": "你领先中。可以考虑放水搞笑，也可以继续认真；台词里表达原因。",
        }
    return {
        "state": "player_leading",
        "diff": diff,
        "remainingRounds": remaining_rounds,
        "remainingPoints": remaining_points,
        **miss_context,
        "intensity": "high" if abs_diff >= 5 else "medium",
        "message": "玩家领先中。可以认真起来、不服气、要求重赛。",
    }


def _badminton_duel_event_shooter(event: dict) -> str:
    label = str(event.get("label") or "").lower()
    if "neko" in label:
        return "neko"
    if "player" in label:
        return "player"
    duel = event.get("duel") if isinstance(event.get("duel"), dict) else {}
    shooter = str(duel.get("active_shooter") or event.get("active_shooter") or "").strip().lower()
    return shooter if shooter in {"neko", "player"} else ""


def _build_badminton_duel_anger_pressure_cap(
    event: Any,
    route_state: Any,
    *,
    lanlan_prompt: str = "",
    language: str | None = None,
) -> Dict[str, Any]:
    """Build the anger pressure cap for angry and punishing duel mode."""
    if not isinstance(event, dict) or not isinstance(route_state, dict):
        return {}
    pre_game = route_state.get("preGameContext")
    if not isinstance(pre_game, dict):
        return {}

    stance = str(pre_game.get("gameStance") or "").strip()
    initial_mood = str(pre_game.get("initialMood") or "calm").strip()
    if not (stance == "punishing" and initial_mood == "angry"):
        return {}

    accumulated = _safe_int(route_state.get("anger_pressure_accumulated"), 0)
    kind = str(event.get("kind") or "").strip()
    shot_type = str(event.get("shot_type") or "").strip()
    shooter = _badminton_duel_event_shooter(event)
    duel = event.get("duel") if isinstance(event.get("duel"), dict) else {}
    diff = _safe_int(duel.get("neko_score"), 0) - _safe_int(duel.get("player_score"), 0)

    if kind in {"shot_result", "shot_missed"} and shooter == "neko":
        if kind == "shot_result":
            hit_streak = _safe_int(route_state.get("badminton_neko_hit_streak"), 0) + 1
            route_state["badminton_neko_hit_streak"] = hit_streak
            if hit_streak == 5:
                accumulated += 1
            if hit_streak == 10:
                accumulated += 2
            if _normalize_badminton_shot_type(shot_type) == "line_in":
                accumulated += 2
        else:
            route_state["badminton_neko_hit_streak"] = 0
    elif kind == "shot_missed" and shooter == "player":
        accumulated += 1

    prev_diff = _safe_int(route_state.get("anger_pressure_last_diff"), 0)
    if diff >= 5 and prev_diff < 5:
        accumulated += 3
    route_state["anger_pressure_last_diff"] = diff
    route_state["anger_pressure_accumulated"] = accumulated

    if accumulated < _SOCCER_ANGER_PRESSURE_CAP_WEAK:
        return {}

    cap = _soccer_anger_pressure_cap_goals(pre_game, lanlan_prompt)
    reached = accumulated >= cap
    return {
        "applicable": True,
        "accumulated": accumulated,
        "cap": cap,
        "reached": reached,
        "recommendedDifficulty": "lv3",
        "message": get_soccer_anger_pressure_cap_message(language),
        "reason": get_soccer_anger_pressure_cap_reason(language),
    }


def _apply_badminton_anger_pressure_cap(result: Dict[str, Any], event: Any) -> Dict[str, Any]:
    """Apply duel pressure cap post-processing to avoid continued max pressure."""
    if not isinstance(result, dict) or not isinstance(event, dict):
        return result
    cap = event.get("angerPressureCap") if isinstance(event.get("angerPressureCap"), dict) else {}
    if not cap or cap.get("reached") is not True:
        if cap:
            result["anger_pressure_cap"] = dict(cap, adjusted=False)
        return result

    control = dict(result.get("control") or {})
    requested_difficulty = str(control.get("difficulty") or "").strip()
    current_difficulty = _event_current_difficulty(event)
    should_clamp = requested_difficulty == "max" or (not requested_difficulty and current_difficulty == "max")
    adjusted = False
    if should_clamp:
        control["difficulty"] = str(cap.get("recommendedDifficulty") or "lv3")
        existing_reason = str(control.get("reason") or "").strip()
        cap_reason = str(cap.get("reason") or "").strip() or get_soccer_anger_pressure_cap_reason()
        control["reason"] = f"{existing_reason}；{cap_reason}" if existing_reason else cap_reason
        result["control"] = control
        adjusted = True
    result["anger_pressure_cap"] = dict(cap, adjusted=adjusted)
    return result


def _game_session_key(lanlan_name: str, game_type: str, session_id: str) -> str:
    lanlan = str(lanlan_name or "").strip()
    if lanlan:
        return f"{lanlan}:{game_type}:{session_id}"
    return f"{game_type}:{session_id}"


def _strip_ssml_like_tags(text: str) -> str:
    """Remove known SSML tags before handing text to TTS."""
    line = str(text or "")
    line = _SSML_TAG_PATTERN.sub("", line)
    line = re.sub(r"\s+", " ", line).strip()
    return line[:240]


def _check_badminton_chat_rate(lanlan_name: str, session_id: str) -> bool:
    key = f"{str(lanlan_name or '').strip()}:{str(session_id or '').strip()}"
    now = time.monotonic()
    cutoff = now - _BADMINTON_CHAT_RATE_WINDOW_SECONDS
    window = [ts for ts in _badminton_chat_rate_windows.get(key, []) if ts >= cutoff]
    if len(window) >= _BADMINTON_CHAT_RATE_MAX:
        _badminton_chat_rate_windows[key] = window
        return False
    window.append(now)
    _badminton_chat_rate_windows[key] = window
    _badminton_chat_rate_windows.move_to_end(key)
    while len(_badminton_chat_rate_windows) > 128:
        _badminton_chat_rate_windows.popitem(last=False)
    return True


def _normalize_badminton_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    # shooter/timed/time_attack/HORSE modes were removed; unknown modes fall back to spectator.
    return mode if mode in {"spectator", "duel"} else "spectator"


def _is_badminton_scoring_mode(value: Any) -> bool:
    return _normalize_badminton_mode(value) in _BADMINTON_SCORING_MODES


def _normalize_badminton_non_negative_int(value: Any, *, default: int = 0, max_value: int = 999999) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return max(0, min(int(number), int(max_value)))


def _normalize_badminton_distance(value: Any, *, default: float = 0.0, max_value: float = 10000.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return max(0.0, min(number, float(max_value)))


_BADMINTON_PX_PER_METER = 12.0 * 3.28084


def _format_badminton_distance_meters(distance_px: float) -> str:
    return f"{distance_px / _BADMINTON_PX_PER_METER:.1f}"


def _score_db_game_slug(game_type: Any = "badminton") -> str:
    # This backend layer owns only badminton scores; other game types must not
    # share this leaderboard implicitly.
    return "badminton"


def _get_badminton_scores_db_path(game_type: Any = "badminton") -> Path:
    override = _BADMINTON_SCORES_DB_PATH
    if override is not None:
        return Path(override)
    try:
        base_dir = Path(get_config_manager().app_docs_dir)
    except Exception:
        base_dir = Path.cwd()
    return base_dir / "state" / "game_scores" / f"{_score_db_game_slug(game_type)}_scores.db"


def _prepare_badminton_scores_db_path(game_type: Any = "badminton") -> Path:
    slug = _score_db_game_slug(game_type)
    db_path = _get_badminton_scores_db_path(slug)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    migration_attempted = bool(getattr(_prepare_badminton_scores_db_path, "_migration_attempted", False))
    if (
        slug == "badminton"
        and
        _BADMINTON_SCORES_DB_PATH is None
        and not migration_attempted
        and not db_path.exists()
        and _BADMINTON_LEGACY_SCORES_DB_PATH.exists()
    ):
        try:
            shutil.copy2(_BADMINTON_LEGACY_SCORES_DB_PATH, db_path)
            logger.info(
                "🏸 已迁移羽毛球排行榜 DB: %s -> %s",
                _BADMINTON_LEGACY_SCORES_DB_PATH,
                db_path,
            )
        except Exception as exc:
            logger.warning(
                "🏸 羽毛球排行榜 DB 迁移失败，将使用新 runtime DB: %s",
                exc,
            )
    if slug == "badminton":
        setattr(_prepare_badminton_scores_db_path, "_migration_attempted", True)
    return db_path


def _open_badminton_scores_db(game_type: Any = "badminton") -> sqlite3.Connection:
    conn = sqlite3.connect(str(_prepare_badminton_scores_db_path(game_type)), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_badminton_scores_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS badminton_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            lanlan_name TEXT NOT NULL DEFAULT '',
            score INTEGER NOT NULL,
            streak INTEGER NOT NULL,
            max_distance_px REAL NOT NULL,
            line_in_count INTEGER NOT NULL DEFAULT 0,
            net_touch_count INTEGER NOT NULL DEFAULT 0,
            zone_in_count INTEGER NOT NULL DEFAULT 0,
            mode TEXT NOT NULL DEFAULT 'spectator',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    badminton_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(badminton_scores)").fetchall()
    }
    if "mode" not in badminton_columns:
        conn.execute("ALTER TABLE badminton_scores ADD COLUMN mode TEXT NOT NULL DEFAULT 'spectator'")
    badminton_score_count_columns = {
        "line_in_count": "INTEGER NOT NULL DEFAULT 0",
        "net_touch_count": "INTEGER NOT NULL DEFAULT 0",
        "zone_in_count": "INTEGER NOT NULL DEFAULT 0",
    }
    for column_name, column_definition in badminton_score_count_columns.items():
        if column_name not in badminton_columns:
            conn.execute(
                f"ALTER TABLE badminton_scores ADD COLUMN {column_name} {column_definition}"
            )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bd_scores_score ON badminton_scores(score DESC)"
    )


def _badminton_score_rows(conn: sqlite3.Connection, limit: int | None = None, offset: int = 0) -> list[sqlite3.Row]:
    if limit is not None:
        clean_limit = _normalize_badminton_non_negative_int(limit, default=10, max_value=100)
        clean_offset = _normalize_badminton_non_negative_int(offset, default=0, max_value=100000)
        return conn.execute(
            """
            SELECT
                id, session_id, lanlan_name, score, streak, max_distance_px,
                line_in_count, net_touch_count, zone_in_count, mode, created_at
            FROM badminton_scores
            ORDER BY score DESC, streak DESC, max_distance_px DESC, created_at ASC, id ASC
            LIMIT ? OFFSET ?
            """,
            (clean_limit, clean_offset),
        ).fetchall()
    return conn.execute(
        """
        SELECT
            id, session_id, lanlan_name, score, streak, max_distance_px,
            line_in_count, net_touch_count, zone_in_count, mode, created_at
        FROM badminton_scores
        ORDER BY score DESC, streak DESC, max_distance_px DESC, created_at ASC, id ASC
        """,
    ).fetchall()


def _badminton_rank_for_row(conn: sqlite3.Connection, row: sqlite3.Row) -> int:
    rank_row = conn.execute(
        """
        SELECT COUNT(*) + 1 AS rank
        FROM badminton_scores
        WHERE
            score > ?
            OR (score = ? AND streak > ?)
            OR (score = ? AND streak = ? AND max_distance_px > ?)
            OR (score = ? AND streak = ? AND max_distance_px = ? AND created_at < ?)
            OR (score = ? AND streak = ? AND max_distance_px = ? AND created_at = ? AND id < ?)
        """,
        (
            row["score"],
            row["score"], row["streak"],
            row["score"], row["streak"], row["max_distance_px"],
            row["score"], row["streak"], row["max_distance_px"], row["created_at"],
            row["score"], row["streak"], row["max_distance_px"], row["created_at"], row["id"],
        ),
    ).fetchone()
    return _normalize_badminton_non_negative_int(rank_row["rank"] if rank_row else 1, default=1)


def _badminton_row_to_public_dict(row: sqlite3.Row, rank: int) -> dict:
    created_at = str(row["created_at"] or "")
    distance_px = _normalize_badminton_distance(row["max_distance_px"], default=0.0)
    return {
        "rank": rank,
        "name": _normalize_short_text(row["lanlan_name"], max_chars=80),
        "score": _normalize_badminton_non_negative_int(row["score"]),
        "streak": _normalize_badminton_non_negative_int(row["streak"]),
        "max_distance_m": _format_badminton_distance_meters(distance_px),
        "mode": _normalize_badminton_mode(row["mode"]),
        "date": created_at[:10],
    }


def _badminton_player_identity(lanlan_name: str, session_id: str) -> tuple[str, str]:
    clean_name = _normalize_short_text(lanlan_name, max_chars=80)
    clean_session = _normalize_short_text(session_id, max_chars=120)
    return clean_name, clean_session


def _badminton_score_totals_from_data(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None

    score_value = None
    for key in ("score", "totalScore", "total_score", "player"):
        if key in data:
            score_value = data.get(key)
            break
    if score_value is None:
        return None

    streak_value = 0
    for key in ("streak", "best_streak", "bestStreak", "final_streak", "finalStreak"):
        if key in data:
            streak_value = data.get(key)
            break

    distance_value = 0.0
    for key in ("max_distance_px", "maxDistancePx", "max_distance", "maxDistance", "final_distance", "finalDistance"):
        if key in data:
            distance_value = data.get(key)
            break

    return {
        "score": _normalize_badminton_non_negative_int(score_value),
        "streak": _normalize_badminton_non_negative_int(streak_value),
        "max_distance_px": _normalize_badminton_distance(distance_value),
    }


def _badminton_score_totals_match_submission(data: dict, expected: Any) -> bool:
    if not isinstance(expected, dict):
        return True
    actual = _badminton_score_totals_from_data(data)
    if not actual:
        return False
    return (
        actual["score"] == _normalize_badminton_non_negative_int(expected.get("score"))
        and actual["streak"] == _normalize_badminton_non_negative_int(expected.get("streak"))
        and math.isclose(
            actual["max_distance_px"],
            _normalize_badminton_distance(expected.get("max_distance_px")),
            rel_tol=0.0,
            abs_tol=0.001,
        )
    )


def _prune_badminton_score_sessions(now: float | None = None) -> None:
    current = time.time() if now is None else now
    for key, meta in list(_badminton_recent_score_sessions.items()):
        if float(meta.get("expires_at") or 0.0) <= current:
            _badminton_recent_score_sessions.pop(key, None)


def _remember_badminton_score_session(
    lanlan_name: str,
    session_id: str,
    mode: Any,
    score_totals: dict | None = None,
) -> None:
    clean_mode = _normalize_badminton_mode(mode)
    if not _is_badminton_scoring_mode(clean_mode):
        return
    clean_name, clean_session = _badminton_player_identity(lanlan_name, session_id)
    if not clean_name or not clean_session:
        return
    _prune_badminton_score_sessions()
    meta = {
        "mode": clean_mode,
        "expires_at": time.time() + _BADMINTON_SCORE_SESSION_TTL_SECONDS,
    }
    clean_totals = _badminton_score_totals_from_data(score_totals)
    if clean_totals:
        meta["score_totals"] = clean_totals
    _badminton_recent_score_sessions[(clean_name, clean_session)] = meta


def _badminton_end_payload_completed_round(data: dict) -> bool:
    if _coerce_payload_bool(data.get("round_completed")) is True:
        return True
    if _coerce_payload_bool(data.get("roundCompleted")) is True:
        return True
    current_state = data.get("currentState")
    if isinstance(current_state, dict):
        if _normalize_short_text(current_state.get("state"), max_chars=40) == "game_over":
            return True
        if _coerce_payload_bool(current_state.get("round_completed")) is True:
            return True
        if _coerce_payload_bool(current_state.get("roundCompleted")) is True:
            return True
    return False


def _badminton_score_session_key_from_payload(data: dict) -> tuple[str, str, str] | None:
    session_id = _normalize_short_text(data.get("session_id"), max_chars=120)
    lanlan_name = _normalize_short_text(data.get("lanlan_name"), max_chars=80)
    if not session_id or not lanlan_name:
        return None
    mode = _normalize_badminton_mode(data.get("mode"))
    if not _is_badminton_scoring_mode(mode):
        return None
    return lanlan_name, session_id, mode


def _badminton_score_submission_allowed(data: dict, *, reserve: bool = False) -> bool:
    session_key = _badminton_score_session_key_from_payload(data)
    if not session_key:
        return False
    lanlan_name, session_id, mode = session_key
    _prune_badminton_score_sessions()
    key = (lanlan_name, session_id)
    meta = _badminton_recent_score_sessions.get(key)
    if not (meta and meta.get("mode") == mode):
        return False
    if not _badminton_score_totals_match_submission(data, meta.get("score_totals")):
        return False
    if meta.get("reserved") is True:
        return False
    if reserve:
        meta["reserved"] = True
    return True


def _badminton_score_data_for_insert(data: dict) -> dict:
    session_key = _badminton_score_session_key_from_payload(data)
    if not session_key:
        return data
    lanlan_name, session_id, mode = session_key
    meta = _badminton_recent_score_sessions.get((lanlan_name, session_id))
    totals = meta.get("score_totals") if isinstance(meta, dict) else None
    if not isinstance(totals, dict):
        return data
    insert_data = dict(data)
    insert_data.update({
        "score": _normalize_badminton_non_negative_int(totals.get("score")),
        "streak": _normalize_badminton_non_negative_int(totals.get("streak")),
        "max_distance_px": _normalize_badminton_distance(totals.get("max_distance_px")),
        "mode": mode,
    })
    return insert_data


def _release_badminton_score_session_reservation(data: dict) -> None:
    session_key = _badminton_score_session_key_from_payload(data)
    if not session_key:
        return
    lanlan_name, session_id, _mode = session_key
    meta = _badminton_recent_score_sessions.get((lanlan_name, session_id))
    if meta:
        meta.pop("reserved", None)


def _consume_badminton_score_session(data: dict) -> None:
    session_key = _badminton_score_session_key_from_payload(data)
    if not session_key:
        return
    lanlan_name, session_id, _mode = session_key
    _badminton_recent_score_sessions.pop((lanlan_name, session_id), None)


def _badminton_leaderboard_total_players(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT CASE
            WHEN TRIM(lanlan_name) <> '' THEN lanlan_name
            ELSE session_id
        END) AS total_players
        FROM badminton_scores
        """
    ).fetchone()
    if not row:
        return 0
    return _normalize_badminton_non_negative_int(row["total_players"], default=0)


def _badminton_leaderboard_total_scores(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS total_scores FROM badminton_scores").fetchone()
    if not row:
        return 0
    return _normalize_badminton_non_negative_int(row["total_scores"], default=0)


def _badminton_personal_best(conn: sqlite3.Connection, lanlan_name: str, session_id: str) -> dict | None:
    clean_name, clean_session = _badminton_player_identity(lanlan_name, session_id)
    if not clean_name and not clean_session:
        return None
    if clean_name:
        row = conn.execute(
            """
            SELECT
                id, session_id, lanlan_name, score, streak, max_distance_px,
                line_in_count, net_touch_count, zone_in_count, mode, created_at
            FROM badminton_scores
            WHERE lanlan_name = ?
            ORDER BY score DESC, streak DESC, max_distance_px DESC, created_at ASC, id ASC
            LIMIT 1
            """,
            (clean_name,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT
                id, session_id, lanlan_name, score, streak, max_distance_px,
                line_in_count, net_touch_count, zone_in_count, mode, created_at
            FROM badminton_scores
            WHERE session_id = ?
            ORDER BY score DESC, streak DESC, max_distance_px DESC, created_at ASC, id ASC
            LIMIT 1
            """,
            (clean_session,),
        ).fetchone()
    if not row:
        return None
    return {
        "rank": _badminton_rank_for_row(conn, row),
        "score": _normalize_badminton_non_negative_int(row["score"]),
    }


def _badminton_insert_score(data: dict, *, game_type: Any = "badminton") -> tuple[int, int, bool]:
    session_id = _normalize_short_text(data.get("session_id"), max_chars=120)
    lanlan_name = _normalize_short_text(data.get("lanlan_name"), max_chars=80)
    if not session_id:
        session_id = f"badminton-{uuid.uuid4().hex}"
    score = _normalize_badminton_non_negative_int(data.get("score"))
    streak = _normalize_badminton_non_negative_int(data.get("streak"))
    max_distance_px = _normalize_badminton_distance(data.get("max_distance_px"))
    line_in_count = _normalize_badminton_non_negative_int(data.get("line_in_count"))
    net_touch_count = _normalize_badminton_non_negative_int(data.get("net_touch_count"))
    zone_in_count = _normalize_badminton_non_negative_int(data.get("zone_in_count"))
    mode = _normalize_badminton_mode(data.get("mode"))
    with _open_badminton_scores_db(game_type) as conn:
        _ensure_badminton_scores_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                """
                INSERT INTO badminton_scores (
                    session_id, lanlan_name, score, streak, max_distance_px,
                    line_in_count, net_touch_count, zone_in_count, mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    lanlan_name,
                    score,
                    streak,
                    max_distance_px,
                    line_in_count,
                    net_touch_count,
                    zone_in_count,
                    mode,
                ),
            )
            inserted_id = cursor.lastrowid
            inserted_row = conn.execute(
                """
                SELECT
                    id, session_id, lanlan_name, score, streak, max_distance_px,
                    line_in_count, net_touch_count, zone_in_count, mode, created_at
                FROM badminton_scores
                WHERE id = ?
                """,
                (inserted_id,),
            ).fetchone()
            if not inserted_row:
                raise RuntimeError("badminton_score_insert_missing")
            rank = _badminton_rank_for_row(conn, inserted_row)
            inserted_score = _normalize_badminton_non_negative_int(inserted_row["score"])
            total_players = _badminton_leaderboard_total_players(conn)
            personal_best = _badminton_personal_best(conn, lanlan_name, session_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return rank, total_players, personal_best is not None and personal_best.get("rank") == rank and personal_best.get("score") == inserted_score


def _sanitize_badminton_duel_state(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    clean: dict[str, Any] = {}
    for src_key, dst_key in (
        ("player_score", "player_score"),
        ("playerScore", "player_score"),
        ("neko_score", "neko_score"),
        ("nekoScore", "neko_score"),
        ("player_misses", "player_misses"),
        ("playerMisses", "player_misses"),
        ("neko_misses", "neko_misses"),
        ("nekoMisses", "neko_misses"),
        ("max_misses", "max_misses"),
        ("maxMisses", "max_misses"),
        ("round", "round"),
        ("max_rounds", "max_rounds"),
        ("maxRounds", "max_rounds"),
    ):
        if src_key not in value:
            continue
        try:
            number_float = float(value.get(src_key))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number_float):
            continue
        number = int(number_float)
        clean[dst_key] = max(0, min(number, 999))
    active = _normalize_short_text(
        value.get("active_shooter", value.get("activeShooter")),
        max_chars=20,
    ).lower()
    if active in {"player", "neko"}:
        clean["active_shooter"] = active
    return clean or None


def _sanitize_badminton_attempts_results(value: Any, *, max_items: int = 12) -> list[dict]:
    if not isinstance(value, list):
        return []
    allowed_text = {"shooter", "shot_type"}
    allowed_numbers = {
        "distance", "distance_m", "score", "angle", "power", "round",
        "streak_before", "streak_after", "best_streak_after", "made_count_after",
        "attempt_number",
    }
    sanitized: list[dict] = []
    for item in value[-max_items:]:
        if not isinstance(item, dict):
            continue
        clean_item: dict[str, Any] = {}
        for key in allowed_text:
            if key in item:
                clean_item[key] = _normalize_short_text(item.get(key), max_chars=40)
        if "shot_type" in clean_item:
            normalized_shot_type = _normalize_badminton_shot_type(clean_item.get("shot_type"))
            if normalized_shot_type:
                clean_item["shot_type"] = normalized_shot_type
            else:
                clean_item.pop("shot_type", None)
        if "scored" in item:
            clean_item["scored"] = item.get("scored") is True
        for key in allowed_numbers:
            if key not in item:
                continue
            try:
                number = float(item[key])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(number):
                continue
            if key in {
                "score", "round", "streak_before", "streak_after",
                "best_streak_after", "made_count_after", "attempt_number",
            }:
                clean_item[key] = max(0, min(int(number), 999))
            elif key in {"angle", "power"}:
                clean_item[key] = max(-1000.0, min(number, 1000.0))
            else:
                clean_item[key] = max(-1000.0, min(number, 5000.0))
        if clean_item:
            sanitized.append(clean_item)
    return sanitized


def _sanitize_badminton_event(event: Any) -> tuple[dict | None, str]:
    if not isinstance(event, dict):
        return None, "event must be object"
    allowed_kinds = {
        "shot_result", "shot_missed", "game_over", "long_aim", "very_long_aim", "close_to_record",
        "new_record", "streak_5", "streak_10", "streak_15", "streak_20",
    }
    allowed_results = {"scored", "missed", ""}
    allowed_duel_outcomes = {"player_win", "neko_win"}
    kind = str(event.get("kind") or "").strip()
    if kind not in allowed_kinds:
        return None, "invalid kind"

    clean: dict[str, Any] = {"kind": kind}
    if "mode" in event:
        clean["mode"] = _normalize_badminton_mode(event.get("mode"))
    for keys in _game_memory_payload_keys("badminton").values():
        for key in keys:
            if key in event:
                clean[key] = event.get(key) is True

    result = str(event.get("result") or "").strip()
    duel_outcome = str(event.get("duel_outcome") or "").strip()
    shot_type = _normalize_badminton_shot_type(event.get("shot_type"))
    if result not in allowed_results:
        clean.pop("result", None)
    else:
        clean["result"] = result
    if duel_outcome in allowed_duel_outcomes:
        clean["duel_outcome"] = duel_outcome
    if "shot_type" in event and not shot_type:
        clean.pop("shot_type", None)
    elif shot_type:
        clean["shot_type"] = shot_type

    for key in (
        "streak", "distance", "shot_angle", "shot_power", "record_distance",
        "final_streak", "final_distance", "aim_duration", "aim_duration_seconds",
        "current_distance", "record", "best_streak", "made_count",
        "attempts_remaining", "attempts_total", "score", "total_score",
        "client_timeout_ms",
    ):
        if key not in event:
            continue
        try:
            value = float(event[key])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        if key in {
            "streak", "final_streak", "best_streak", "made_count",
            "attempts_remaining", "attempts_total",
        }:
            clean[key] = max(0, min(int(value), 999))
        elif key in {"score", "total_score"}:
            clean[key] = max(0, min(int(value), 999999))
        elif key == "client_timeout_ms":
            clean[key] = max(0, min(int(value), 60000))
        elif key in {"aim_duration", "aim_duration_seconds"}:
            clean[key] = max(0.0, min(value, 60.0))
        else:
            clean[key] = max(-1000.0, min(value, 5000.0))

    for key in ("was_perfect", "is_new_record"):
        if key in event:
            clean[key] = event.get(key) is True
    duel_state = _sanitize_badminton_duel_state(event.get("duel"))
    if duel_state:
        clean["duel"] = duel_state
    current_state = event.get("currentState")
    if isinstance(current_state, dict):
        state_clean = {}
        for key in ("game", "last_shot_type"):
            if key in current_state:
                state_clean[key] = _normalize_short_text(current_state.get(key), max_chars=40)
        if "last_shot_type" in state_clean:
            normalized_state_shot_type = _normalize_badminton_shot_type(state_clean.get("last_shot_type"))
            if normalized_state_shot_type:
                state_clean["last_shot_type"] = normalized_state_shot_type
        if "mode" in current_state:
            state_clean["mode"] = _normalize_badminton_mode(current_state.get("mode"))
        for key in (
            "streak", "distance", "record_distance", "final_streak", "final_distance",
            "best_streak", "made_count", "attempts_remaining", "attempts_total",
        ):
            if key not in current_state:
                continue
            try:
                value = float(current_state[key])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            if key in {
                "streak", "final_streak", "best_streak", "made_count",
                "attempts_remaining", "attempts_total",
            }:
                state_clean[key] = max(0, min(int(value), 999))
            else:
                state_clean[key] = max(-1000.0, min(value, 5000.0))
        score_state = current_state.get("score")
        if isinstance(score_state, dict):
            score_clean: dict[str, Any] = {}
            for key in ("player", "ai", "score", "best_streak", "made_count"):
                if key not in score_state:
                    continue
                try:
                    value = float(score_state[key])
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(value):
                    continue
                score_clean[key] = max(0, min(int(value), 999999))
            for src_key, dst_key in (("max_distance_px", "max_distance_px"), ("maxDistancePx", "max_distance_px")):
                if src_key not in score_state:
                    continue
                try:
                    value = float(score_state[src_key])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    score_clean[dst_key] = max(0.0, min(value, 5000.0))
            if "mode" in score_state:
                score_clean["mode"] = _normalize_badminton_mode(score_state.get("mode"))
            if score_clean:
                state_clean["score"] = score_clean
        duel_state = _sanitize_badminton_duel_state(current_state.get("duel"))
        if duel_state:
            state_clean["duel"] = duel_state
        attempts_results = _sanitize_badminton_attempts_results(
            current_state.get("attempts_results"),
        )
        if attempts_results:
            state_clean["attempts_results"] = attempts_results
        clean["currentState"] = state_clean
    for key in ("label", "textRaw", "userText", "userVoiceText"):
        if key in event:
            clean[key] = _normalize_short_text(event.get(key), max_chars=180)
    for key in ("mood", "difficulty", "i18n_language"):
        if key in event:
            clean[key] = _normalize_short_text(event.get(key), max_chars=40)
    return clean, ""


_POSTGAME_SESSION_MARKER = "::postgame::"
# Why: ``_make_postgame_session_id`` produces ``<session_id>::postgame::<uuid4.hex>``
# where ``uuid4().hex`` is exactly 32 lowercase hex chars. ``_route_session_id``
# strips ONLY this exact synthetic suffix shape so a legitimate client-supplied
# session_id that happens to contain ``::postgame::`` is left untouched.
_POSTGAME_UUID_TAIL_RE = re.compile(r"[0-9a-f]{32}\Z")


def _make_postgame_session_id(session_id: str) -> str:
    # Why: postgame's freshly-built session lives at a private cache key
    # so a racing ``/route/start`` reusing the same user-facing session_id
    # cannot land on the same ``_game_sessions`` slot. Without this, a
    # peer's first ``/game_chat`` would be handed back postgame's cached
    # entry by ``_get_or_create_session``, and postgame's ``finally``
    # close (still identity-matching since the cache wasn't replaced)
    # would tear down the active route's session mid-turn.
    return f"{str(session_id or '')}{_POSTGAME_SESSION_MARKER}{uuid.uuid4().hex}"


def _route_session_id(session_id: str) -> str:
    # Why: this helper is now defensive. The critical postgame paths
    # (``_build_and_register_game_session`` / ``_refresh_game_session_instructions``)
    # use a frozen ``postgame_snapshot`` instead of reverse-resolving live
    # route_state, so the marker no longer needs to round-trip through
    # ``_find_game_route_state_for_session`` for prompt context. We still
    # tolerate the synthetic shape elsewhere by stripping ONLY the exact
    # suffix produced by ``_make_postgame_session_id`` (marker + 32-char
    # uuid4 hex tail at end of string). A legitimate client session_id
    # that happens to contain ``::postgame::`` mid-string — or with a
    # non-uuid tail — is returned unchanged.
    raw = str(session_id or "")
    idx = raw.rfind(_POSTGAME_SESSION_MARKER)
    if idx == -1:
        return raw
    tail = raw[idx + len(_POSTGAME_SESSION_MARKER):]
    if not _POSTGAME_UUID_TAIL_RE.fullmatch(tail):
        return raw
    return raw[:idx]


def _parse_game_session_key(key: str) -> tuple[str, str, str]:
    parts = str(key or "").split(":", 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    game_type, _, session_id = str(key or "").partition(":")
    return "", game_type, session_id


async def _close_and_remove_session(
    game_type: str,
    session_id: str,
    lanlan_name: str = "",
) -> bool:
    """Close and remove the specified game session.

    B1: serialize against in-flight ``_run_game_chat`` work for the same
    entry by acquiring ``entry['lock']`` before popping + closing. Without
    this, a concurrent close (from ``/route/start`` finalize, heartbeat
    sweep, or ``/route/end``) would yank the session out of the cache and
    close it while a chat call still held a reference and was mid
    ``stream_text``, producing reads against a closed client.

    The entry-level lock keeps the wait bounded — chat work is capped by
    the 15s ``stream_text`` timeout. New chats arriving after we set the
    route's ``_exit_flow_started`` flag short-circuit before they ever
    touch the entry lock.

    codex P1 (PR #1127 r3182582714): identity-gate the cache eviction.
    Two close callers can read the same ``entry`` then queue on its
    lock; while they wait, a peer may pop the cache, a fresh
    ``/route/start`` may build ``entry_NEW`` under the same key, and we
    would otherwise wake up and pop ``entry_NEW`` — closing a live
    session a different route owns. Mirrors the postgame ownership gate
    in ``_deliver_postgame_text_bubble``'s finally. We always close OUR
    captured ``entry``'s session (it's ours since the top of this
    function) but only touch the cache / create-lock dicts if they
    still point at us.
    """
    keys = []
    if lanlan_name:
        keys.append(_game_session_key(lanlan_name, game_type, session_id))
    keys.append(_game_session_key("", game_type, session_id))

    # First locate the entry (without popping) to grab its lock, then pop
    # under the lock. Two close callers racing here both serialize on the
    # same lock and only one observes a non-None entry after pop.
    key = ""
    entry = None
    for candidate in keys:
        key = candidate
        entry = _game_sessions.get(candidate)
        if entry:
            break
    if not entry:
        return False

    entry_lock = entry.get('lock')
    if isinstance(entry_lock, asyncio.Lock):
        async with entry_lock:
            cache_owned = _game_sessions.get(key) is entry
            if cache_owned:
                _game_sessions.pop(key, None)
                _game_session_create_locks.pop(key, None)
    else:
        cache_owned = _game_sessions.get(key) is entry
        if cache_owned:
            _game_sessions.pop(key, None)
            _game_session_create_locks.pop(key, None)

    # Why: ``entry`` was captured at the top of this function; we own its
    # lifecycle even if a peer closer rotated the cache to ``entry_NEW``
    # while we waited on the lock. Always close OUR session so the
    # client cannot leak. ``OmniOfflineClient.close`` is idempotent
    # (omni_offline_client.py:1815-1835), so any peer's prior close on
    # the same object is safe to re-run.
    session = entry.get('session')
    if session:
        try:
            await session.close()
        except Exception as e:
            logger.debug("🎮 关闭游戏 session 失败: key=%s err=%s", key, e, exc_info=True)

    logger.info("🎮 结束游戏 session: %s cache_owned=%s", key, cache_owned)
    return True


async def _run_game_chat(
    game_type: str,
    session_id: str,
    event: Any,
    *,
    allow_postgame: bool = False,
    postgame_snapshot: Optional[dict] = None,
    postgame_meta_out: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run A-layer game LLM for both HTTP game events and hijacked external text.

    B1/B2/B3: short-circuit if the route is mid-exit (or already
    inactive). Otherwise the chat would call ``stream_text`` against a
    session that finalize is about to close, and ``_append_game_dialog``
    afterwards would write into an already-archived state slot.

    ``allow_postgame=True`` is the legitimate exception: postgame text
    bubble runs *after* finalize on purpose (designed teardown step).
    The caller (``_deliver_postgame_text_bubble``) is responsible for
    closing the freshly-built session afterwards via
    ``_close_and_remove_session`` so the bypass doesn't leak a client.

    Postgame uses a private ``::postgame::<uuid>``-suffixed cache key so
    a racing ``/route/start`` reusing the user-facing ``session_id``
    cannot land on the same ``_game_sessions`` slot. The user-facing
    ``session_id`` is preserved for route_state lookups via
    ``_route_session_id``.
    """
    request_started_at = time.perf_counter()

    if not event:
        return {"error": "缺少 event 字段"}
    lanlan_name = ""
    if isinstance(event, dict):
        lanlan_name = str(event.get("lanlan_name") or event.get("lanlanName") or "").strip()
    _append_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        category="llm",
        event="game_chat_requested",
        message="小游戏主 LLM 请求开始",
        details={
            "allow_postgame": allow_postgame,
            "kind": event.get("kind") if isinstance(event, dict) else "",
            "round": event.get("round") if isinstance(event, dict) else None,
            "event_type": type(event).__name__,
        },
        sensitive_possible=isinstance(event, dict) and any(event.get(key) for key in ("textRaw", "userText", "userVoiceText")),
    )

    if game_type == "soccer" and isinstance(event, dict):
        balance_hint = _build_soccer_balance_hint(event)
        if balance_hint:
            event = dict(event)
            event['balanceHint'] = balance_hint
    elif _is_badminton_game_type(game_type) and isinstance(event, dict):
        route_state = _find_game_route_state_for_session(game_type, session_id, lanlan_name)
        event_mode = _normalize_badminton_mode(event.get("mode") or (route_state.get("mode") if isinstance(route_state, dict) else ""))
        if event_mode == "duel":
            balance_hint = _build_badminton_duel_balance_hint(event)
            if balance_hint:
                event = dict(event)
                event["mode"] = "duel"
                event["balanceHint"] = balance_hint

    chat_session_id = _make_postgame_session_id(session_id) if allow_postgame else session_id

    # B1/B2: pre-create short-circuit. If the route is mid-exit (or
    # already inactive) we must not spawn a fresh ``OmniOfflineClient``
    # — that would survive past the finalize and become a permanent leak
    # since nothing else in the lifecycle would close it.
    if not allow_postgame:
        pre_state = _find_game_route_state_for_session(game_type, session_id, lanlan_name)
        if isinstance(pre_state, dict) and (
            pre_state.get("_exit_flow_started")
            or pre_state.get("game_route_active") is False
        ):
            logger.info(
                "🎮 chat short-circuit (pre-create): route exiting/inactive game=%s sid=%s lanlan=%s",
                game_type, session_id, lanlan_name,
            )
            return {"line": "", "control": {}, "skipped": "route_inactive"}

    try:
        entry = await _get_or_create_session(
            game_type, chat_session_id, lanlan_name,
            postgame_snapshot=postgame_snapshot if allow_postgame else None,
        )
    except Exception as e:
        logger.error("🎮 创建游戏 session 失败: %s", e)
        return {"error": f"创建 session 失败: {e}"}

    # Re-resolve canonical lanlan_name for state lookups.
    lanlan_name = str(entry.get("lanlan_name") or lanlan_name or "").strip()

    # Why: caller's ``finally`` (``_deliver_postgame_text_bubble``) needs
    # to reach this entry even if the awaits below raise
    # ``asyncio.CancelledError`` — which is ``BaseException``, not
    # ``Exception``, so it bypasses the structured error-result paths
    # that attach ``_postgame_entry``/``_postgame_cache_session_id``.
    # We populate a shared out-dict the caller pre-allocates so the
    # metadata is observable on every termination path (success,
    # exception, cancellation).
    if allow_postgame and postgame_meta_out is not None:
        postgame_meta_out["_postgame_entry"] = entry
        postgame_meta_out["_postgame_cache_session_id"] = chat_session_id

    # CR Major (PR #1127 r3182158697): when the post-lock route_inactive
    # short-circuit trips and our build won the race against a finalize
    # that ran during ``session.connect``, the freshly-registered entry
    # would otherwise survive until the 30-min idle sweep. We capture
    # the orphan inside the lock and close it AFTER releasing
    # ``entry['lock']`` — ``_close_and_remove_session`` re-acquires that
    # same lock, so closing through it under the lock would deadlock.
    orphan_session_to_close = None
    short_circuit_route_inactive = False
    async with entry['lock']:
        # B2: short-circuit if a finalize already kicked off (heartbeat
        # sweep, character switch, /route/end). Without this guard the
        # chat call below would still ``stream_text`` against an
        # already-closed ``OmniOfflineClient`` and append to a
        # ``pending_outputs`` / ``game_dialog_log`` slot whose archive
        # has already been written.
        if not allow_postgame:
            route_state = _find_game_route_state_for_session(game_type, session_id, lanlan_name)
            if isinstance(route_state, dict) and (
                route_state.get("_exit_flow_started")
                or route_state.get("game_route_active") is False
            ):
                logger.info(
                    "🎮 chat short-circuit: route exiting/inactive game=%s sid=%s lanlan=%s",
                    game_type, session_id, lanlan_name,
                )
                # Evict our entry IFF the cache still points at us. If
                # a peer creator already overwrote our slot, they own
                # the close.
                key = _game_session_key(lanlan_name, game_type, chat_session_id)
                cached = _game_sessions.get(key)
                if cached is entry:
                    _game_sessions.pop(key, None)
                    create_lock = _game_session_create_locks.get(key)
                    waiters = getattr(create_lock, "_waiters", None) if create_lock else None
                    if not waiters:
                        _game_session_create_locks.pop(key, None)
                    orphan_session_to_close = entry.get('session')
                short_circuit_route_inactive = True

        if not short_circuit_route_inactive:
            # B1: bail if our entry has been popped from the cache (peer
            # creator overwrote us, or finalize closed the session while
            # we were waiting on entry['lock']). Continuing would call
            # ``stream_text`` on a closed client.
            current_entry = _game_sessions.get(_game_session_key(lanlan_name, game_type, chat_session_id))
            if current_entry is not entry:
                logger.info(
                    "🎮 chat short-circuit: entry no longer cached game=%s sid=%s lanlan=%s",
                    game_type, session_id, lanlan_name,
                )
                evicted_result: Dict[str, Any] = {"line": "", "control": {}, "skipped": "entry_evicted"}
                if allow_postgame:
                    evicted_result["_postgame_entry"] = entry
                    evicted_result["_postgame_cache_session_id"] = chat_session_id
                return evicted_result

            session = entry['session']
            reply_chunks = entry['reply_chunks']
            try:
                await _refresh_game_session_instructions(
                    entry, game_type, chat_session_id, lanlan_name,
                    postgame_snapshot=postgame_snapshot if allow_postgame else None,
                )
            except Exception as e:
                logger.error("🎮 更新游戏 session 指令失败: %s", e)
                err_result: Dict[str, Any] = {
                    "error": f"更新 session 指令失败: {e}",
                    "line": "",
                    "control": {},
                }
                if allow_postgame:
                    err_result["_postgame_entry"] = entry
                    err_result["_postgame_cache_session_id"] = chat_session_id
                return err_result

            if not allow_postgame:
                history_state = _find_game_route_state_for_session(game_type, session_id, lanlan_name)
                _reset_game_session_text_history_for_turn(entry, history_state)

            # 清空上一次的回复
            reply_chunks.clear()

            if game_type == "soccer" and isinstance(event, dict):
                route_state = _find_game_route_state_for_session(game_type, session_id, lanlan_name)
                anger_pressure_cap = _build_soccer_anger_pressure_cap(
                    event,
                    route_state,
                    lanlan_prompt=str(entry.get("lanlan_prompt") or ""),
                    language=str(entry.get("user_language") or ""),
                )
                if anger_pressure_cap:
                    event = dict(event)
                    event["angerPressureCap"] = anger_pressure_cap
            elif _is_badminton_game_type(game_type) and isinstance(event, dict):
                route_state = _find_game_route_state_for_session(game_type, session_id, lanlan_name)
                event_mode = _normalize_badminton_mode(event.get("mode") or (route_state.get("mode") if isinstance(route_state, dict) else ""))
                if event_mode == "duel":
                    anger_pressure_cap = _build_badminton_duel_anger_pressure_cap(
                        event,
                        route_state,
                        lanlan_prompt=str(entry.get("lanlan_prompt") or ""),
                        language=str(entry.get("user_language") or ""),
                    )
                    if anger_pressure_cap:
                        event = dict(event)
                        event["mode"] = "duel"
                        event["angerPressureCap"] = anger_pressure_cap

            # 格式化事件为文本发送给 LLM
            import json as _json
            llm_visible_event = _build_game_llm_visible_event(game_type, event)
            if isinstance(llm_visible_event, dict):
                event_payload = _json.dumps(llm_visible_event, ensure_ascii=False)
            else:
                event_payload = str(llm_visible_event)
            event_text = get_game_chat_event_user_prompt(entry.get("user_language")).format(event=event_payload)

            llm_started_at = time.perf_counter()
            try:
                await asyncio.wait_for(
                    session.stream_text(event_text),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                logger.warning("🎮 游戏 LLM 响应超时: game=%s sid=%s", game_type, session_id)
                _append_game_session_debug_log(
                    game_type,
                    session_id,
                    lanlan_name=lanlan_name,
                    level="warning",
                    category="llm",
                    event="game_chat_timeout",
                    message="小游戏主 LLM 响应超时，返回空台词",
                    details={"timeout_seconds": 15.0},
                )
                err_result: Dict[str, Any] = {"error": "LLM 响应超时", "line": "", "control": {}}
                if allow_postgame:
                    err_result["_postgame_entry"] = entry
                    err_result["_postgame_cache_session_id"] = chat_session_id
                return err_result
            except Exception as e:
                logger.error("🎮 游戏 LLM 调用失败: %s", e)
                _append_game_session_debug_log(
                    game_type,
                    session_id,
                    lanlan_name=lanlan_name,
                    level="error",
                    category="llm",
                    event="game_chat_exception",
                    message="小游戏主 LLM 调用失败",
                    details={"error_type": type(e).__name__, "error": str(e)},
                )
                err_result = {"error": f"LLM 调用失败: {e}", "line": "", "control": {}}
                if allow_postgame:
                    err_result["_postgame_entry"] = entry
                    err_result["_postgame_cache_session_id"] = chat_session_id
                return err_result

            llm_elapsed_ms = int((time.perf_counter() - llm_started_at) * 1000)
            full_reply = ''.join(reply_chunks)

    if short_circuit_route_inactive:
        # Close the orphan session OUTSIDE entry['lock'] to avoid
        # deadlocking against any future caller that takes the same
        # lock. ``orphan_session_to_close`` is None when a peer beat us
        # to the eviction.
        if orphan_session_to_close is not None:
            try:
                await orphan_session_to_close.close()
            except Exception as e:
                logger.debug(
                    "🎮 关闭短路 game session 失败: game=%s sid=%s err=%s",
                    game_type, session_id, e, exc_info=True,
                )
        return {"line": "", "control": {}, "skipped": "route_inactive"}

    result = _parse_control_instructions(full_reply, game_type=game_type)
    if game_type == "soccer" and isinstance(event, dict):
        result = _apply_soccer_anger_pressure_cap(result, event)
    elif _is_badminton_game_type(game_type) and isinstance(event, dict) and _normalize_badminton_mode(event.get("mode")) == "duel":
        result = _apply_badminton_anger_pressure_cap(result, event)
    if isinstance(event, dict) and event.get('balanceHint'):
        result['balance_hint'] = event['balanceHint']
    total_elapsed_ms = int((time.perf_counter() - request_started_at) * 1000)
    result['metrics'] = {
        'llm_ms': llm_elapsed_ms,
        'total_ms': total_elapsed_ms,
    }
    result['llm_source'] = dict(entry.get('source') or {})
    if allow_postgame:
        # Why: postgame teardown owns the lifecycle of the entry it used.
        # Hand the caller the exact entry object (identity-gated close)
        # AND the private cache key it lives under so the bubble's
        # ``finally`` evicts the correct slot — a fresh ``/route/start``
        # cannot collide with this private slot.
        result['_postgame_entry'] = entry
        result['_postgame_cache_session_id'] = chat_session_id
    logger.info(
        "🎮 [%s:%s] LLM耗时=%sms 后端总耗时=%sms 事件=%s → 台词=%s",
        game_type, session_id, llm_elapsed_ms, total_elapsed_ms,
        event_text[:80], result['line'][:60],
    )
    _append_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        category="llm",
        event="game_chat_completed",
        message="小游戏主 LLM 返回完成",
        details={
            "llm_ms": llm_elapsed_ms,
            "total_ms": total_elapsed_ms,
            "line_length": len(result.get("line") or ""),
            "control_keys": sorted((result.get("control") or {}).keys()) if isinstance(result.get("control"), dict) else [],
            "kind": event.get("kind") if isinstance(event, dict) else "",
            "round": event.get("round") if isinstance(event, dict) else None,
        },
    )
    return result

# ── 路由端点 ───────────────────────────────────────────────────────

@router.post("/{game_type}/chat")
async def game_chat(game_type: str, request: Request):
    """Generic game LLM chat endpoint.

    Request body:
        session_id: str  — match/round ID
        event: dict      — game event (format defined by the frontend, passed through to the LLM)

    Response:
        line: str        — catgirl line
        control: dict    — optional game control instructions (mood, difficulty)
    """
    try:
        data = await request.json()
    except Exception:
        return {"error": "无效的请求体"}

    session_id = str(data.get('session_id', 'default'))
    event = data.get('event', {})
    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    # 把请求体里的 i18n 真值同步进 mgr.user_language，让本次 game_chat → _run_game_chat
    # → _get_character_info 链上 _resolve_game_prompt_language 拿到的 user_language
    # 与前端 i18n 保持一致，而不是被早期 start_session 覆盖回去的全局缓存值。
    _absorb_request_language(data, lanlan_name)
    state = _get_active_game_route_state(lanlan_name, game_type) if lanlan_name else None
    if state and state.get("session_id") == session_id:
        _update_game_memory_enabled_from_payload(state, data, game_type=game_type)
        if isinstance(event, dict):
            _update_game_memory_enabled_from_payload(state, event, game_type=game_type)
            event = _attach_game_memory_flag_to_event(event, state, game_type=game_type)
    if _is_badminton_game_type(game_type):
        stale_result = _game_route_stale_session_response(
            state,
            session_id,
            lanlan_name=lanlan_name,
            method="game_chat",
        )
        if stale_result is not None:
            return {**stale_result, "line": "", "control": {}}
        if lanlan_name and state is None:
            return {
                "ok": True,
                "skipped": "route_inactive",
                "reason": "route_not_active",
                "handled": False,
                "line": "",
                "control": {},
                "lanlan_name": lanlan_name,
                "method": "game_chat",
            }
        if not _check_badminton_chat_rate(lanlan_name, session_id):
            return {"error": "rate_limited", "line": "", "control": {}, "retry_after": 2}
        event, validation_error = _sanitize_badminton_event(event)
        if event is None:
            return {"error": validation_error or "invalid_event", "line": "", "control": {}}
    if isinstance(event, dict) and lanlan_name:
        event = dict(event)
        event.setdefault("lanlan_name", lanlan_name)
    result = await _run_game_chat(game_type, session_id, event)

    if state and state.get("session_id") == session_id and isinstance(event, dict):
        current_state = event.get("currentState")
        if isinstance(current_state, dict):
            state["last_state"] = current_state
        client_timeout_ms = event.get("client_timeout_ms")
        try:
            client_timeout_ms = int(float(client_timeout_ms))
        except (TypeError, ValueError):
            client_timeout_ms = 0
        metrics = result.get("metrics") if isinstance(result, dict) else {}
        try:
            total_ms = int(float(metrics.get("total_ms"))) if isinstance(metrics, dict) else 0
        except (TypeError, ValueError):
            total_ms = 0
        if client_timeout_ms > 0 and total_ms >= client_timeout_ms:
            result["skipped_memory"] = "client_timeout"
        else:
            _append_game_dialog(state, {
                "type": "game_event",
                "kind": event.get("kind"),
                "text": event.get("textRaw") or event.get("label") or "",
                "result_line": result.get("line", ""),
                "control": result.get("control", {}),
            })
    return result


@router.post("/{game_type}/route/start")
async def game_route_start(game_type: str, request: Request):
    """Declare that the game window is open and main external inputs are hijacked."""
    if str(game_type or "") == "new_user_icebreaker":
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "reason": "not_a_game_route",
                "route": "/api/icebreaker/route/start",
            },
        )
    try:
        data = await request.json()
    except Exception:
        data = {}

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}
    # 把请求体里的 i18n 真值同步进 mgr.user_language（详见 _absorb_request_language
    # 文档）：route/start 是 game-route 整段生命周期的入口，越早 heal 越多下游受益。
    _absorb_request_language(data, lanlan_name)

    session_id = str(data.get("session_id") or "default")
    # 同一角色同一时刻只允许一个 active 游戏路由：启动新路由前先结束所有其它仍活跃的
    # 路由（同 game_type 旧 session、不同 game_type、未来跨游戏并存均覆盖）。否则
    # is_game_route_active(lanlan_name) / _get_active_game_route_state(lanlan_name)
    # 这些不带 game_type 的查询会拿到 dict 迭代顺序里"先出现"的那个 route，导致
    # 文本/语音输入归属不确定。
    #
    # B1: serialize the supersede + activation block under the per-(lanlan,
    # game_type) route lock so heartbeat-sweep finalize and /route/end
    # finalize cannot interleave the close + activate steps. The pregame
    # context build (network call, can take seconds) is intentionally
    # *outside* the lock — by then the new state is already activated, so
    # peers see the new slot via ``_get_active_*`` helpers; holding the
    # lock for the whole pregame would block heartbeat sweep with no
    # benefit.
    #
    # Cross-game_type concurrency (CodeRabbit follow-up):
    # The per-(lanlan, game_type) route lock alone is too narrow for the
    # supersede scan, which iterates `_game_route_states` for ANY active
    # route belonging to `lanlan_name` regardless of `game_type`. Two
    # concurrent /route/start calls for SAME lanlan_name but DIFFERENT
    # game_type acquire different per-key locks, so each scan misses the
    # other's pending activation and both end up activating in parallel,
    # breaking the "one active game route per character" invariant.
    #
    # Fix: take the per-lanlan_name supersede lock as the OUTER lock
    # before the per-(lanlan, game_type) route lock. Acquisition order
    # (documented in `utils/game_route_state.py`) is OUTER->INNER; only
    # the start-flow goes outer->inner, never the other direction, so no
    # deadlock with finalize/end paths that only take the inner lock.
    supersede_lock = _get_supersede_lock(lanlan_name)
    route_lock = _get_route_lock(lanlan_name, game_type)
    async with supersede_lock:
        async with route_lock:
            for old_state in [
                candidate
                for candidate in list(_game_route_states.values())
                if candidate.get("game_route_active")
                and str(candidate.get("lanlan_name") or "") == lanlan_name
            ]:
                old_game_type = str(old_state.get("game_type") or "")
                old_session_id = str(old_state.get("session_id") or "default")
                logger.warning(
                    "🎮 新游戏路由启动前发现旧 active route，先结束旧局: old_game=%s old_session=%s new_game=%s new_session=%s lanlan=%s",
                    old_game_type,
                    old_session_id,
                    game_type,
                    session_id,
                    lanlan_name,
                )
                await _finalize_game_route_state(
                    old_state,
                    reason="superseded_by_route_start",
                    close_game_session=True,
                )

            if game_type == "soccer":
                _enable_game_session_debug_log(game_type, session_id, lanlan_name=lanlan_name)
            _mark_game_session_debug_log_active(game_type, session_id, lanlan_name=lanlan_name)
            _append_game_session_debug_log(
                game_type,
                session_id,
                lanlan_name=lanlan_name,
                category="route",
                event="route_start_requested",
                message="小游戏路由开始请求",
                details={
                    "neko_initiated": bool(data.get("nekoInitiated")),
                    "mode": data.get("mode") or "",
                    "memory_tail_count": data.get("game_memory_tail_count", data.get("gameMemoryTailCount")),
                },
            )
            neko_initiated = bool(data.get("nekoInitiated"))
            neko_invite_text = _normalize_short_text(data.get("nekoInviteText"), max_chars=120) if neko_initiated else ""
            state = _activate_game_route(
                game_type,
                session_id,
                lanlan_name,
                data.get("game_last_full_dialogue_count"),
            )
            # Take over the SessionManager: ordinary chat LLM output handlers must
            # stay silent during the game, and any voice transcript that reaches
            # the SessionManager must be redirected into route_external_voice_transcript.
            mgr = get_session_manager().get(lanlan_name)
            if mgr is not None:
                async def _takeover_dispatcher(_lan, transcript_text, *, request_id):
                    return await route_external_voice_transcript(
                        _lan,
                        transcript_text,
                        request_id=request_id,
                        game_type=game_type,
                        session_id=session_id,
                    )
                mgr._takeover_active = True
                mgr._takeover_input_dispatcher = _takeover_dispatcher
            state["game_memory_tail_count"] = _normalize_game_memory_tail_count(
                data.get("game_memory_tail_count", data.get("gameMemoryTailCount"))
            )
            _update_game_memory_enabled_from_payload(state, data, game_type=game_type)
            state["nekoInitiated"] = neko_initiated
            state["nekoInviteText"] = neko_invite_text
            if _is_badminton_game_type(game_type):
                state["mode"] = _normalize_badminton_mode(data.get("mode"))
            _update_route_start_state_from_payload(state, data)
    # 推 WS 让多窗口前端联动收缩 chat.html（触发其内部 collapse 按钮态 + 移
    # 至工作区左下角）+ 隐藏 pet (live2d/vrm/mmd) 容器。这只是 UX 联动事件，
    # 不参与 game-route 状态判定；前端在 game_window_state_change=closed 时
    # 还原。注意：要在 supersede + activate 锁外推送，避免阻塞锁；soccer
    # pregame 上下文构建可能耗几秒，那段期间前端已经看到游戏窗口在加载，
    # 越早收缩越平滑——所以放在 pregame build 之前。
    #
    # 锁外 stale-opened 防护（codex P1）：start 释放锁后到 push 之间，并发的
    # /route/end 或新 /route/start supersede 可能已把 state.game_route_active
    # 翻 false 并推过 closed。如果不 recheck，stale opened 会在 closed 后 land，
    # 让前端 UI 卡死收缩态再无 closed 抵消。recheck state 自身的 active 标志 +
    # session_id 双重匹配（防 state 字典里同 (lanlan,game_type) key 已被新一轮
    # supersede 替换为新 state）。
    mgr_for_ws = get_session_manager().get(lanlan_name)
    if (
        state.get("game_route_active")
        and str(state.get("session_id") or "") == session_id
    ):
        await _push_game_window_state_change(
            mgr_for_ws,
            action="opened",
            lanlan_name=lanlan_name,
            game_type=game_type,
            session_id=session_id,
        )
    else:
        logger.info(
            "🎮 game_window_state_change=opened 跳过推送（route 已被 supersede / "
            "end 抵消）: lanlan=%s game=%s session=%s",
            lanlan_name, game_type, session_id,
        )
    if game_type == "soccer" or _is_badminton_game_type(game_type):
        state["heartbeat_enabled"] = False
        try:
            if game_type == "soccer":
                context, source, error = await _build_soccer_pregame_context(
                    game_type=game_type,
                    session_id=session_id,
                    lanlan_name=lanlan_name,
                    neko_initiated=neko_initiated,
                    neko_invite_text=neko_invite_text,
                )
            else:
                context, source, error = await _build_badminton_pregame_context(
                    game_type=game_type,
                    session_id=session_id,
                    lanlan_name=lanlan_name,
                    neko_initiated=neko_initiated,
                    neko_invite_text=neko_invite_text,
                    mode=str(state.get("mode") or data.get("mode") or "spectator"),
                )
        except Exception as exc:
            logger.warning("🎮 开局上下文构建异常，使用普通陪玩兜底: lanlan=%s err=%s", lanlan_name, exc)
            _append_game_session_debug_log(
                game_type,
                session_id,
                lanlan_name=lanlan_name,
                level="warning",
                category="route",
                event="pregame_context_exception",
                message="开局上下文构建异常，使用兜底上下文",
                details={"error_type": type(exc).__name__, "error": str(exc)},
            )
            if _is_badminton_game_type(game_type):
                context = _default_badminton_pregame_context(mode=str(state.get("mode") or data.get("mode") or "spectator"))
            else:
                context = _default_soccer_pregame_context()
            source, error = "fallback", "ai_failed"
        now = time.time()
        state["preGameContext"] = context
        state["pre_game_context_source"] = source
        state["pre_game_context_error"] = error
        state["heartbeat_enabled"] = True
        state["last_heartbeat_at"] = now
        state["last_activity"] = now
        _append_game_session_debug_log(
            game_type,
            session_id,
            lanlan_name=lanlan_name,
            category="route",
            event="route_start_completed",
            message="小游戏路由开始完成",
            details={
                "pre_game_context_source": source,
                "pre_game_context_error": error,
                "before_game_external_mode": state.get("before_game_external_mode"),
                "before_game_external_active": state.get("before_game_external_active"),
                "heartbeat_enabled": state.get("heartbeat_enabled"),
            },
        )
    if state.get("before_game_external_mode") == "audio" and state.get("before_game_external_active"):
        await route_external_stream_message(lanlan_name, {"input_type": "audio"})
    if not (game_type == "soccer" or _is_badminton_game_type(game_type)):
        _append_game_session_debug_log(
            game_type,
            session_id,
            lanlan_name=lanlan_name,
            category="route",
            event="route_start_completed",
            message="小游戏路由开始完成",
            details={
                "before_game_external_mode": state.get("before_game_external_mode"),
                "before_game_external_active": state.get("before_game_external_active"),
                "heartbeat_enabled": state.get("heartbeat_enabled"),
            },
        )
    return {"ok": True, "state": _public_route_state(state)}


@router.get("/{game_type}/route/state")
async def game_route_state(game_type: str, lanlan_name: str = ""):
    resolved = _resolve_lanlan_name(lanlan_name)
    state = _get_active_game_route_state(resolved, game_type) if resolved else None
    return {"ok": True, "state": _public_route_state(state)}


@router.get("/route/active")
async def game_route_any_active(lanlan_name: str = ""):
    """Reconcile late subscribers with the current game window route state.

    ``game_window_state_change`` is edge-triggered, so a newly loaded or
    reconnected chat/pet subscriber can miss the historical ``opened`` event
    while a route is already active. This read-only endpoint lets init code
    query the current state and dispatch its local opened event if needed.
    """
    resolved = _resolve_lanlan_name(lanlan_name)
    state = _get_active_game_route_state(resolved) if resolved else None
    if state is None:
        return {"ok": True, "active": False}
    return {
        "ok": True,
        "active": True,
        "game_type": str(state.get("game_type") or ""),
        "session_id": str(state.get("session_id") or ""),
        "lanlan_name": str(state.get("lanlan_name") or ""),
    }


@router.post("/{game_type}/route/drain")
async def game_route_drain(game_type: str, request: Request):
    """Drain backend outputs caused by hijacked main-window input for the game page."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    _absorb_request_language(data, lanlan_name)
    state = _get_active_game_route_state(lanlan_name, game_type) if lanlan_name else None
    if not state:
        return {"ok": True, "outputs": [], "state": {"game_route_active": False}}

    session_id = str(data.get("session_id") or "")
    if session_id and session_id != str(state.get("session_id") or ""):
        return {"ok": True, "outputs": [], "state": _public_route_state(state)}

    _update_game_memory_enabled_from_payload(state, data, game_type=game_type)
    outputs = list(state.get("pending_outputs") or [])
    state["pending_outputs"] = []
    return {"ok": True, "outputs": outputs, "state": _public_route_state(state)}


@router.post("/{game_type}/route/voice-transcript")
async def game_route_voice_transcript(game_type: str, request: Request):
    """Accept final text from an independent STT gate and route it into the game."""
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}

    transcript = str(data.get("transcript") or data.get("text") or "").strip()
    if not transcript:
        return {"ok": False, "reason": "missing_transcript"}

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}
    _absorb_request_language(data, lanlan_name)

    session_id = str(data.get("session_id") or "")
    state = _get_active_game_route_state(lanlan_name, game_type)
    if not state:
        return {"ok": True, "handled": False, "reason": "game_route_inactive"}
    if session_id and session_id != str(state.get("session_id") or ""):
        return {"ok": True, "handled": False, "reason": "session_id_mismatch"}

    current_state = data.get("currentState")
    if isinstance(current_state, dict):
        state["last_state"] = current_state
    _update_route_start_state_from_payload(state, data)
    _update_game_memory_enabled_from_payload(state, data, game_type=game_type)

    handled = await route_external_voice_transcript(
        lanlan_name,
        transcript,
        request_id=str(data.get("request_id") or "") or None,
        game_type=game_type,
        session_id=session_id or None,
    )
    return {"ok": True, "handled": handled, "state": _public_route_state(state)}


@router.post("/{game_type}/route/heartbeat")
async def game_route_heartbeat(game_type: str, request: Request):
    """Refresh the game page heartbeat used to detect missed exit cleanup."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    _absorb_request_language(data, lanlan_name)
    state = _get_active_game_route_state(lanlan_name, game_type) if lanlan_name else None
    if not state:
        return {"ok": True, "active": False, "state": {"game_route_active": False}}

    session_id = str(data.get("session_id") or "")
    if session_id and session_id != str(state.get("session_id") or ""):
        return {"ok": True, "active": False, "reason": "session_id_mismatch", "state": _public_route_state(state)}

    now = time.time()
    state["last_heartbeat_at"] = now
    state["last_activity"] = now
    _touch_game_session_debug_log(game_type, str(state.get("session_id") or session_id or "default"), lanlan_name=lanlan_name)
    _update_route_visibility_from_payload(state, data)
    _update_route_start_state_from_payload(state, data)
    _update_game_memory_enabled_from_payload(state, data, game_type=game_type)
    current_state = data.get("currentState")
    if isinstance(current_state, dict):
        state["last_state"] = current_state

    heartbeat_timeout = _route_heartbeat_timeout_seconds(state)
    return {
        "ok": True,
        "active": True,
        "heartbeat_interval_seconds": _GAME_ROUTE_HEARTBEAT_INTERVAL_SECONDS,
        "heartbeat_timeout_seconds": heartbeat_timeout,
        "foreground_heartbeat_timeout_seconds": _GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS,
        "hidden_heartbeat_timeout_seconds": _GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS,
        "state": _public_route_state(state),
    }


@router.post("/{game_type}/route/end")
async def game_route_end(game_type: str, request: Request):
    """End the game route using the same cleanup contract as the public game end."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    return await _complete_game_end_from_payload(game_type, data, default_reason="route_end")


async def _speak_game_line_via_project_tts(
    mgr: Any,
    line: str,
    *,
    request_id: str | None = None,
    game_type: str = "",
    session_id: str = "",
    mirror_text: bool = True,
    emit_turn_end: bool = True,
    interrupt_audio: bool = False,
    event: dict | None = None,
) -> Dict[str, Any]:
    speak = getattr(mgr, "mirror_assistant_speech", None)
    if not callable(speak):
        return {"ok": False, "reason": "project_tts_method_unavailable", "audio_sent": False}
    metadata = build_mirror_meta(
        source="game_route",
        kind=game_type,
        session_id=session_id,
        event=event if isinstance(event, dict) else {},
    )
    before_state = _project_tts_pipeline_state(mgr)
    try:
        result = await speak(
            line,
            metadata=metadata,
            request_id=request_id,
            mirror_text=mirror_text,
            emit_turn_end_after=emit_turn_end,
            interrupt_audio=interrupt_audio,
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": "project_tts_exception",
            "audio_sent": False,
            "audio_queued": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "tts_pipeline": {
                "before": before_state,
                "after": _project_tts_pipeline_state(mgr),
            },
            "voice_source": {"provider": "project_tts", "method": "project_tts"},
        }
    if isinstance(result, dict):
        result.setdefault("tts_pipeline", {})
        result["tts_pipeline"] = {
            "before": before_state,
            "after": _project_tts_pipeline_state(mgr),
        }
    return result


def _project_tts_pipeline_state(mgr: Any) -> dict[str, Any]:
    tts_thread = getattr(mgr, "tts_thread", None)
    pending_chunks = getattr(mgr, "tts_pending_chunks", None)
    try:
        pending_count = len(pending_chunks) if pending_chunks is not None else 0
    except Exception:
        pending_count = None
    return {
        "tts_thread_alive": bool(tts_thread and tts_thread.is_alive()),
        "tts_ready": bool(getattr(mgr, "tts_ready", False)),
        "tts_pending_chunks": pending_count,
        "tts_done_queued_for_turn": bool(getattr(mgr, "_tts_done_queued_for_turn", False)),
        "tts_done_pending_until_ready": bool(getattr(mgr, "_tts_done_pending_until_ready", False)),
        "current_speech_id": str(getattr(mgr, "current_speech_id", "") or ""),
    }


def _game_route_event_has_user_input(event: dict | None) -> bool:
    if not isinstance(event, dict):
        return False
    return (
        event.get("hasUserSpeech") is True
        or event.get("hasUserText") is True
        or event.get("kind") in {"user-voice", "user-text"}
    )


async def _mirror_game_assistant_text(
    mgr: Any,
    line: str,
    *,
    request_id: str | None = None,
    game_type: str = "",
    session_id: str = "",
    source: str = "game_llm",
    turn_id: str | None = None,
    event: dict | None = None,
    finalize_turn: bool = False,
) -> Dict[str, Any]:
    mirror = getattr(mgr, "mirror_assistant_output", None)
    if not callable(mirror):
        return {"ok": False, "reason": "project_text_mirror_method_unavailable", "mirrored": False}
    metadata = build_mirror_meta(
        source=source,
        kind=game_type,
        session_id=session_id,
        event=event if isinstance(event, dict) else {},
    )
    return await mirror(
        line,
        metadata=metadata,
        request_id=request_id,
        turn_id=turn_id,
        finalize_turn=finalize_turn,
    )


@router.post("/{game_type}/mirror-assistant")
async def game_project_mirror_assistant(game_type: str, request: Request):
    """Mirror A.line into the normal chat display without invoking TTS."""
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}

    line = _strip_ssml_like_tags(str(data.get("line") or "").strip())
    if not line:
        return {"ok": False, "reason": "missing_line"}

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}
    _absorb_request_language(data, lanlan_name)

    mgr = get_session_manager().get(lanlan_name)
    if not mgr:
        return {"ok": False, "reason": "no_session_manager", "lanlan_name": lanlan_name}

    session_id = str(data.get("session_id") or "")
    state = _get_active_game_route_state(lanlan_name, game_type)
    if not state:
        closed_response = _game_route_closed_session_response(
            data,
            session_id=session_id,
            lanlan_name=lanlan_name,
            method="project_text_mirror",
        )
        if closed_response:
            return closed_response
    stale_response = _game_route_stale_session_response(
        state,
        session_id,
        lanlan_name=lanlan_name,
        method="project_text_mirror",
    )
    if stale_response:
        return stale_response
    event = _attach_game_memory_flag_to_event(
        data.get("event") if isinstance(data.get("event"), dict) else {},
        state,
        game_type=game_type,
    )
    finalize_raw = data.get("finalize_turn")
    finalize_turn = _game_route_event_has_user_input(event) if finalize_raw is None else finalize_raw is not False
    result = await _mirror_game_assistant_text(
        mgr,
        line,
        request_id=str(data.get("request_id") or "") or None,
        game_type=game_type,
        session_id=session_id,
        source=str(data.get("source") or "game_llm"),
        turn_id=str(data.get("turn_id") or "") or None,
        event=event,
        finalize_turn=finalize_turn,
    )
    if result.get("ok") and str(event.get("kind") or "") == "opening-line":
        session_id = str(data.get("session_id") or "")
        state = _get_active_game_route_state(lanlan_name, game_type)
        if state and (not session_id or session_id == str(state.get("session_id") or "")):
            _append_game_dialog(state, {
                "type": "assistant",
                "source": "opening_line",
                "kind": "opening-line",
                "line": line,
                "request_id": str(data.get("request_id") or "") or "",
            })
    result.setdefault("lanlan_name", lanlan_name)
    result.setdefault("method", "project_text_mirror")
    return result


@router.post("/{game_type}/speak")
async def game_project_speak(game_type: str, request: Request):
    """Formal B-layer output: speak A.line through the existing project TTS pipeline."""
    if str(game_type or "") == "new_user_icebreaker":
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "reason": "not_a_game_route",
                "route": "/api/icebreaker/speak",
            },
        )
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}

    line = str(data.get("line") or "").strip()
    if not line:
        return {"ok": False, "reason": "missing_line"}

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}
    _absorb_request_language(data, lanlan_name)

    mgr = get_session_manager().get(lanlan_name)
    if not mgr:
        return {"ok": False, "reason": "no_session_manager", "lanlan_name": lanlan_name}

    interrupt_audio = _coerce_payload_bool(data.get("interrupt_audio")) is True
    session_id = str(data.get("session_id") or "")
    state = _get_active_game_route_state(lanlan_name, game_type)
    if not state:
        closed_response = _game_route_closed_session_response(
            data,
            session_id=session_id,
            lanlan_name=lanlan_name,
            method="project_tts",
        )
        if closed_response:
            return closed_response
    stale_response = _game_route_stale_session_response(
        state,
        session_id,
        lanlan_name=lanlan_name,
        method="project_tts",
    )
    if stale_response:
        _append_game_session_debug_log(
            game_type,
            session_id,
            lanlan_name=lanlan_name,
            level="warning",
            category="speech",
            event="project_speech_skipped",
            message="小游戏项目语音请求被跳过",
            details={"reason": stale_response.get("reason"), "method": "project_tts"},
        )
        return stale_response
    _append_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        category="speech",
        event="project_speech_requested",
        message="小游戏项目语音请求开始",
        details={
            "request_id": str(data.get("request_id") or ""),
            "line_length": len(line),
            "interrupt_audio": interrupt_audio,
            "mirror_text": data.get("mirror_text", True) is not False,
            "emit_turn_end": data.get("emit_turn_end", True) is not False,
            "event_kind": data.get("event", {}).get("kind") if isinstance(data.get("event"), dict) else "",
        },
        sensitive_possible=True,
    )
    result = await _speak_game_line_via_project_tts(
        mgr,
        line,
        request_id=str(data.get("request_id") or "") or None,
        game_type=game_type,
        session_id=session_id,
        mirror_text=data.get("mirror_text", True) is not False,
        emit_turn_end=data.get("emit_turn_end", True) is not False,
        interrupt_audio=interrupt_audio,
        event=_attach_game_memory_flag_to_event(
            data.get("event") if isinstance(data.get("event"), dict) else {},
            state,
            game_type=game_type,
        ),
    )
    result.setdefault("lanlan_name", lanlan_name)
    result.setdefault("method", "project_tts")
    result.setdefault("voice_source", {"provider": "project_tts", "method": "project_tts"})
    _append_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        level="info" if result.get("ok", True) else "warning",
        category="speech",
        event="project_speech_result",
        message="小游戏项目语音请求结束",
        details={
            "ok": result.get("ok"),
            "reason": result.get("reason"),
            "audio_sent": result.get("audio_sent"),
            "audio_queued": result.get("audio_queued"),
            "speech_id": result.get("speech_id"),
            "turn_end_emitted": result.get("turn_end_emitted"),
            "interrupt_audio": result.get("interrupt_audio"),
            "error_type": result.get("error_type"),
            "error": result.get("error"),
            "tts_pipeline": result.get("tts_pipeline"),
            "voice_source": result.get("voice_source"),
        },
        preserve_details=True,
    )
    return result


def _build_external_text_event(state: dict, text: str) -> dict:
    return _build_external_user_event(state, text, kind="user-text", source="external_text_route")


def _build_external_voice_event(state: dict, text: str) -> dict:
    return _build_external_user_event(state, text, kind="user-voice", source="external_voice_route")


def _build_external_user_event(state: dict, text: str, *, kind: str, source: str) -> dict:
    current_state = state.get("last_state") if isinstance(state.get("last_state"), dict) else {}
    score = current_state.get("score") if isinstance(current_state.get("score"), dict) else {"player": 0, "ai": 0}
    try:
        score_diff = int(score.get("ai", 0)) - int(score.get("player", 0))
    except (TypeError, ValueError):
        score_diff = 0
    event_type = "user_text" if kind == "user-text" else "user_voice"
    game_type = _normalize_game_memory_type(state.get("game_type") or "soccer")
    policy = _game_memory_policy(game_type, state)
    fields = _game_memory_policy_fields(game_type)
    master = policy[fields[0]]
    player_interaction = policy[fields[1]]
    event_reply = policy[fields[2]]
    return {
        "kind": kind,
        "lanlan_name": state.get("lanlan_name") or "",
        "type": event_type,
        "source": source,
        "badmintonGameMemoryEnabled": master,
        "badminton_game_memory_enabled": master,
        "badmintonGameMemoryPlayerInteractionEnabled": player_interaction,
        "badminton_game_memory_player_interaction_enabled": player_interaction,
        "badmintonGameMemoryEventReplyEnabled": event_reply,
        "badminton_game_memory_event_reply_enabled": event_reply,
        "soccerGameMemoryEnabled": master,
        "soccer_game_memory_enabled": master,
        "soccerGameMemoryPlayerInteractionEnabled": player_interaction,
        "soccer_game_memory_player_interaction_enabled": player_interaction,
        "soccerGameMemoryEventReplyEnabled": event_reply,
        "soccer_game_memory_event_reply_enabled": event_reply,
        "gameMemoryEnabled": player_interaction,
        "game_memory_enabled": player_interaction,
        "gameMemoryPlayerInteractionEnabled": player_interaction,
        "game_memory_player_interaction_enabled": player_interaction,
        "gameMemoryEventReplyEnabled": event_reply,
        "game_memory_event_reply_enabled": event_reply,
        "textRaw": text,
        "userText": text if kind == "user-text" else "",
        "userVoiceText": text if kind == "user-voice" else "",
        "round": current_state.get("round"),
        "mood": current_state.get("mood"),
        "score": score,
        "scoreDiff": score_diff,
        "difficulty": current_state.get("difficulty"),
        "currentState": current_state,
        "pendingItems": [{
            "type": event_type,
            "kind": kind,
            "textRaw": text,
            "snapshot": current_state,
            "round": current_state.get("round"),
        }],
    }


async def _route_external_transcript_to_game(
    lanlan_name: str,
    state: dict,
    text: str,
    *,
    source: str,
    mode: str,
    kind: str,
    request_id: str | None = None,
) -> bool:
    text = str(text or "").strip()
    if not text:
        return True

    # B3: state may have flipped to exiting/inactive between the caller's
    # active-check and this call (the SessionManager dispatcher path in
    # ``main_logic/core.py`` checks once at the dispatcher gate, then
    # awaits us). Re-check here and short-circuit cleanly with no
    # side-effects on a half-archived state. We treat short-circuit as
    # "handled=True" (return True) so the caller does not also drive the
    # transcript through the ordinary chat flow — the route was active at
    # the dispatch gate, so the right semantic is "drop on the floor with
    # no ordinary mirror" not "fall back to ordinary chat".
    if state.get("_exit_flow_started") or state.get("game_route_active") is False:
        logger.info(
            "🎮 transcript short-circuit: route exiting/inactive lanlan=%s mode=%s kind=%s",
            lanlan_name, mode, kind,
        )
        return True

    now = time.time()
    if kind == "user-voice":
        # Idempotency on request_id with a bounded TTL set rather than a
        # single "last seen" slot — single-slot would let an out-of-order
        # replay through (voice-1 → voice-2 → voice-1 retry: the second
        # voice-1 passes because last is now voice-2). Each transcript
        # carries its own request_id, so two genuinely-distinct shouts of
        # the same phrase (e.g. "再来！再来！") arrive with different
        # request_ids and both deliver.
        #
        # Fallback for callers that don't send request_id (legacy paths /
        # unit-test scaffolding): key on text alone and gate by the time
        # since last-seen so tight retransmits collapse but a genuine
        # repeat 1s+ later still delivers. (An earlier "text:int(ts)"
        # bucketing missed cross-second close pairs like 0.95s → 1.05s.)
        seen_ids = state.get("_external_voice_seen_request_ids")
        if not isinstance(seen_ids, OrderedDict):
            seen_ids = OrderedDict()
            state["_external_voice_seen_request_ids"] = seen_ids
        # 1. Prune expired entries (TTL) — opportunistic cleanup, always safe.
        ttl_cutoff = now - _EXTERNAL_VOICE_DEDUP_TTL_SECONDS
        while seen_ids:
            oldest_id = next(iter(seen_ids))
            if seen_ids[oldest_id] < ttl_cutoff:
                seen_ids.pop(oldest_id, None)
                continue
            break
        # 2. Decide if the incoming key is a duplicate BEFORE touching the
        #    LRU cap — otherwise an existing-but-oldest entry could be
        #    LRU-evicted in step 3 right before its retry, breaking
        #    request-id idempotency at 64+ unique-id high throughput.
        current_request_id = str(request_id or "")
        idempotency_key = current_request_id or f"__no_id__:{text}"
        last_seen_at = seen_ids.get(idempotency_key)
        is_duplicate = last_seen_at is not None and (
            bool(current_request_id)
            # no request_id → 1s window
            or (now - last_seen_at) < 1.0
        )
        if is_duplicate:
            logger.info(
                "🎮 游戏语音转写去重: lanlan=%s key=%s text=%s",
                lanlan_name, idempotency_key, text[:40],
            )
            return True
        # 3. Inserting a new key (or a no_id repeat past 1s window) — only
        #    now enforce the LRU cap.
        while len(seen_ids) >= _EXTERNAL_VOICE_DEDUP_MAX_ENTRIES:
            seen_ids.popitem(last=False)
        seen_ids[idempotency_key] = now
        seen_ids.move_to_end(idempotency_key)

    mgr = get_session_manager().get(lanlan_name)
    game_type = str(state.get("game_type") or "soccer")
    session_id = str(state.get("session_id") or "default")
    memory_enabled = _game_memory_player_interaction_enabled(state)
    _append_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        category="external_input",
        event="external_input_routed",
        message="外部输入已转入小游戏路由",
        details={
            "source": source,
            "mode": mode,
            "kind": kind,
            "request_id": request_id or "",
            "text_length": len(text),
            "memory_enabled": memory_enabled,
        },
        sensitive_possible=True,
    )
    memory_fields = _game_memory_policy_fields(game_type)
    memory_player_camel_key = _game_memory_camel_key(
        _normalize_game_memory_type(game_type),
        memory_fields[1],
    )
    memory_player_snake_key = memory_fields[1]
    _append_route_activation(
        state,
        "external_voice_hijacked_by_game" if kind == "user-voice" else "external_text_hijacked_by_game",
        mode,
        {"request_id": request_id or ""},
    )
    if mgr and hasattr(mgr, "mirror_user_input"):
        await mgr.mirror_user_input(
            text,
            metadata=build_mirror_meta(
                source=source,
                kind=game_type,
                session_id=session_id,
                event={"memory_enabled": memory_enabled},
            ),
            request_id=request_id,
            input_type=(
                MIRROR_USER_VOICE_TRANSCRIPT_INPUT_TYPE
                if kind == "user-voice"
                else MIRROR_USER_TEXT_INPUT_TYPE
            ),
            send_to_frontend=kind == "user-voice",
        )
    if mgr and hasattr(mgr, "send_user_activity"):
        try:
            await mgr.send_user_activity()
        except Exception as exc:
            logger.debug("🎮 游戏外部输入打断当前语音失败: %s", exc)

    event = (
        _build_external_voice_event(state, text)
        if kind == "user-voice"
        else _build_external_text_event(state, text)
    )
    _append_game_dialog(state, {
        "type": "user",
        "source": source,
        "text": text,
        "request_id": request_id or "",
    })
    _append_game_output(state, {
        "type": "game_external_input",
        "source": source,
        "request_id": request_id or "",
        "ts": now,
        "input_ts": now,
        "event": event,
        "meta": {
            "kind": kind,
            "round": event.get("round"),
            "priority": 8,
            "itemCount": 1,
            "inputText": text,
            "hasUserSpeech": kind == "user-voice",
            "hasUserText": kind == "user-text",
            # 玩家输入和 NEKO 对该输入的直接回应共用这个游戏记忆开关。
            memory_player_camel_key: memory_enabled,
            memory_player_snake_key: memory_enabled,
            "gameMemoryEnabled": memory_enabled,
            "game_memory_enabled": memory_enabled,
            "inputTs": now,
        },
    })
    llm_started_at = time.time()
    result = await _run_game_chat(game_type, session_id, event)
    result_ts = time.time()
    _append_game_dialog(state, {
        "type": "assistant",
        "source": "game_llm",
        "line": result.get("line", ""),
        "control": result.get("control", {}),
        "request_id": request_id or "",
    })
    output = {
        "type": "game_llm_result",
        "source": source,
        "request_id": request_id or "",
        "ts": result_ts,
        "input_ts": now,
        "llm_started_ts": llm_started_at,
        "llm_elapsed_ms": int(max(0.0, result_ts - llm_started_at) * 1000),
        "event": event,
        "result": result,
        "meta": {
            "kind": kind,
            "round": event.get("round"),
            "priority": 8,
            "itemCount": 1,
            "hasUserSpeech": kind == "user-voice",
            "hasUserText": kind == "user-text",
            # 同上：玩家交互开关同时覆盖用户输入镜像和 NEKO 直接回复。
            memory_player_camel_key: memory_enabled,
            memory_player_snake_key: memory_enabled,
            "gameMemoryEnabled": memory_enabled,
            "game_memory_enabled": memory_enabled,
            "voiceAlreadyHandled": False,
            "inputTs": now,
            "llmStartedTs": llm_started_at,
            "llmElapsedMs": int(max(0.0, result_ts - llm_started_at) * 1000),
        },
    }
    _append_game_output(state, output)

    line = str(result.get("line") or "").strip()
    if not line and mgr and hasattr(mgr, "send_status"):
        await mgr.send_status(json.dumps({
            "code": "GAME_ROUTE_LLM_FAILED",
            "details": {"source": source, "error": result.get("error", "empty_line")},
        }))
    return True


async def route_external_voice_transcript(
    lanlan_name: str,
    transcript: str,
    *,
    request_id: str | None = None,
    game_type: str | None = None,
    session_id: str | None = None,
) -> bool:
    """Route a voice transcript into the active game route, if any.

    Also registered with ``utils.game_route_state`` so ``main_logic/core.py``
    can dispatch transcripts via the generic helper without taking a
    ``main_logic → main_routers`` import.
    """
    state = _get_active_game_route_state(lanlan_name, game_type)
    if not state:
        return False
    if session_id and str(state.get("session_id") or "") != str(session_id):
        return False
    return await _route_external_transcript_to_game(
        lanlan_name,
        state,
        transcript,
        source="external_voice_route",
        mode="voice",
        kind="user-voice",
        request_id=request_id,
    )


# Plug the heavy implementation into the shared dispatcher so main_logic/
# can call ``utils.game_route_state.route_external_voice_transcript`` instead
# of importing from ``main_routers``.
register_voice_transcript_handler(route_external_voice_transcript)


async def finalize_game_routes_for_character(old_lanlan_name: str) -> int:
    """Finalize every active game route for ``old_lanlan_name`` synchronously.

    B8: when the user switches the active character via
    ``POST /api/characters/current_catgirl``, the previous character may
    still own an active game route. Without this hook, the route's heartbeat
    keeps the slot live for up to 10-60s while the now-irrelevant
    ``OmniOfflineClient`` keeps consuming events (and the stale
    SessionManager takeover keeps muting the new character's ordinary
    chat output). Finalizing immediately at switch time releases the
    takeover and closes the LLM session.

    Concurrency (codex P2 follow-up): the snapshot + iterate + finalize
    block runs under the per-``lanlan_name`` supersede lock (the same OUTER
    lock ``game_route_start`` takes). Without it, a concurrent
    ``/route/start`` for the same ``lanlan_name`` can activate a NEW route
    AFTER we snapshot ``_game_route_states`` and escape cleanup — the
    character switch then completes with an old-character route still
    active (takeover, session, heartbeat all live), defeating B8's
    "immediate teardown on switch" guarantee. Holding the supersede lock
    across the whole sweep forces any concurrent ``/route/start`` for the
    same ``lanlan_name`` to land strictly before (in which case our
    snapshot includes it) or strictly after (in which case our cleanup
    completed first and the new route is intentional post-switch state
    the caller can deal with separately).

    Lock ordering: OUTER ``_route_supersede_locks[lanlan_name]`` then
    INNER ``_route_state_locks[(lanlan, game_type)]`` per iteration.
    Same direction as ``game_route_start`` — no deadlock window.

    Returns the number of routes finalized.
    """
    target = str(old_lanlan_name or "")
    if not target:
        return 0
    supersede_lock = _get_supersede_lock(target)
    finalized_count = 0
    async with supersede_lock:
        candidates = [
            candidate
            for candidate in list(_game_route_states.values())
            if candidate.get("game_route_active")
            and str(candidate.get("lanlan_name") or "") == target
        ]
        for old_state in candidates:
            old_game_type = str(old_state.get("game_type") or "")
            logger.warning(
                "🎮 角色切换前结束旧角色游戏路由: lanlan=%s game=%s session=%s",
                target,
                old_game_type,
                old_state.get("session_id") or "",
            )
            route_lock = _get_route_lock(target, old_game_type)
            try:
                async with route_lock:
                    if not old_state.get("game_route_active"):
                        if old_state.get("_exit_task"):
                            await asyncio.shield(old_state["_exit_task"])
                        continue
                    await _finalize_game_route_state(
                        old_state,
                        reason="character_switch",
                        close_game_session=True,
                    )
                    finalized_count += 1
            except Exception as exc:
                logger.warning(
                    "🎮 角色切换收尾失败: lanlan=%s game=%s err=%s",
                    target,
                    old_game_type,
                    exc,
                    exc_info=True,
                )
    return finalized_count


async def route_external_stream_message(lanlan_name: str, message: dict) -> bool:
    """Return True when a main WebSocket stream_data message was consumed by game routing."""
    state = _get_active_game_route_state(lanlan_name)
    if not state:
        return False

    mgr = get_session_manager().get(lanlan_name)
    input_type = message.get("input_type")
    game_type = str(state.get("game_type") or "soccer")
    request_id = str(message.get("request_id") or "") or None

    if input_type == "text":
        text = str(message.get("data") or "").strip()
        return await _route_external_transcript_to_game(
            lanlan_name,
            state,
            text,
            source="external_text_route",
            mode="text",
            kind="user-text",
            request_id=request_id,
        )

    if input_type == "audio":
        transcript = str(message.get("transcript") or message.get("text") or "").strip()
        if transcript:
            return await route_external_voice_transcript(
                lanlan_name,
                transcript,
                request_id=request_id,
                game_type=game_type,
                session_id=str(state.get("session_id") or ""),
            )
        _append_route_activation(state, "external_voice_hijacked_by_game", "voice")
        if not state.get("_voice_stt_gate_active_notified"):
            state["_voice_stt_gate_active_notified"] = True
            status_payload = {
                "code": "GAME_VOICE_STT_GATE_ACTIVE",
                "details": {
                    "game_type": game_type,
                    "session_id": str(state.get("session_id") or ""),
                    "lanlan_name": lanlan_name,
                    "stt_provider": str(message.get("stt_provider") or "realtime"),
                    "message": "游戏期间主语音入口已被游戏路由接管。复用原 Realtime 作为 STT provider；最终转写交给游戏路由，普通 chat LLM 输出在 SessionManager 层被静音（session takeover）。",
                },
            }
            _append_game_output(state, {
                "type": "game_voice_stt_gate",
                "source": "external_voice_hijacked_by_game",
                "request_id": request_id or "",
                "ts": time.time(),
                "status": "active",
                "details": status_payload["details"],
            })
            if mgr and hasattr(mgr, "send_status"):
                await mgr.send_status(json.dumps(status_payload))
        return True

    if input_type in {"screen", "camera"}:
        if mgr and hasattr(mgr, "send_status"):
            await mgr.send_status(json.dumps({
                "code": "GAME_ROUTE_MEDIA_SKIPPED",
                "details": {"input_type": input_type, "game_type": game_type},
            }))
        return True

    return True


def _compact_realtime_context_text(game_type: str, payload: Dict[str, Any], language: str | None = None) -> str:
    """Build a short non-voice context block for an active Realtime session.

    This is intentionally not a semantic summary. The game side sends current
    state plus recent evidence; the Realtime model decides how to use it.
    """
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    items = payload.get("pendingItems") if isinstance(payload.get("pendingItems"), list) else []
    source = str(payload.get("source") or "game")
    texts = get_compact_realtime_context_texts(language)

    safe_items = []
    for item in items[-6:]:
        if not isinstance(item, dict):
            continue
        safe_items.append({
            "type": item.get("type"),
            "kind": item.get("kind"),
            "textRaw": item.get("textRaw"),
            "round": item.get("round"),
            "snapshot": item.get("snapshot"),
        })

    context = {
        "game": game_type,
        "source": source,
        "currentState": state,
        "recentItems": safe_items,
        "instruction": texts["instruction"],
    }
    return f"{texts['header']}\n" + json.dumps(context, ensure_ascii=False)


@router.post("/{game_type}/realtime-context")
async def game_realtime_context(game_type: str, request: Request):
    """Inject compact game context into the active Realtime voice session.

    This is the first, deliberately simple bridge for "non-voice information
    entering Realtime". It does not require provider function-calling support;
    for Qwen it falls back to session.update via OmniRealtimeClient.prime_context.
    """
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}
    if not isinstance(data, dict):
        return {"ok": False, "reason": "invalid_body"}

    from .system_router import _validate_local_mutation_request

    validation_error = _validate_local_mutation_request(
        request,
        payload=data,
        error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )
    if validation_error is not None:
        return validation_error

    lanlan_name = str(data.get("lanlan_name") or "").strip()
    if not lanlan_name:
        try:
            lanlan_name = _get_current_character_info().get("lanlan_name") or ""
        except Exception:
            lanlan_name = ""

    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}

    session_manager = get_session_manager()
    mgr = session_manager.get(lanlan_name)
    if not mgr:
        return {"ok": False, "reason": "no_session_manager", "lanlan_name": lanlan_name}

    try:
        from main_logic.omni_realtime_client import OmniRealtimeClient
    except Exception as e:
        return {"ok": False, "reason": f"realtime_unavailable: {e}", "lanlan_name": lanlan_name}

    session = getattr(mgr, "session", None)
    if not (getattr(mgr, "is_active", False) and isinstance(session, OmniRealtimeClient)):
        return {"ok": False, "reason": "no_active_realtime_session", "lanlan_name": lanlan_name}

    # 直接把 data 传进去，让请求体里的 i18n_language 走第一层优先级（兼带回写
    # mgr.user_language），与其他 soccer 端点的 _absorb_request_language 调用同形。
    language = _resolve_game_prompt_language(lanlan_name, data=data)
    text = _compact_realtime_context_text(game_type, data, language)
    session_id = str((data.get("state") or {}).get("sessionId") or data.get("session_id") or "")
    _log_game_debug_material(
        "realtime_context",
        text,
        game_type=game_type,
        session_id=session_id,
        lanlan_name=lanlan_name,
        source=str(data.get("source") or ""),
    )
    _append_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        category="realtime_context",
        event="realtime_context_requested",
        message="小游戏 Realtime 上下文注入请求",
        details={
            "source": data.get("source") or "",
            "bytes": len(text),
            "items": len(data.get("pendingItems") or []),
            "request_id": str(data.get("request_id") or ""),
        },
        sensitive_possible=True,
    )

    if _is_gemini_realtime_session(session):
        logger.info(
            "🎮 Realtime 上下文跳过: game=%s lanlan=%s reason=gemini_no_session_update bytes=%d",
            game_type,
            lanlan_name,
            len(text),
        )
        _append_game_session_debug_log(
            game_type,
            session_id,
            lanlan_name=lanlan_name,
            category="realtime_context",
            event="realtime_context_skipped",
            message="Realtime 上下文注入跳过",
            details={"reason": "gemini_no_session_update", "bytes": len(text)},
        )
        return {
            "ok": True,
            "action": "skip",
            "reason": "gemini_no_session_update",
            "lanlan_name": lanlan_name,
            "bytes": len(text),
            "items": len(data.get("pendingItems") or []),
        }

    append_context = getattr(mgr, "append_context", None)
    if not callable(append_context):
        return {"ok": False, "reason": "context_method_unavailable", "lanlan_name": lanlan_name}
    if _active_realtime_session(mgr) is not session:
        return {"ok": False, "reason": "realtime_session_changed", "lanlan_name": lanlan_name}
    try:
        append_result = await append_context(
            source="game.realtime_context",
            role="system",
            text=text,
            audience="model",
            timing="now",
            lifetime="current_session",
            request_id=str(data.get("request_id") or "") or None,
            ordering_key=str((data.get("state") or {}).get("sessionId") or data.get("session_id") or "") or None,
            metadata={
                "game_type": game_type,
                "lanlan_name": lanlan_name,
                "items": len(data.get("pendingItems") or []),
            },
        )
    except Exception as e:
        logger.warning("🎮 Realtime 上下文注入失败: game=%s lanlan=%s err=%s", game_type, lanlan_name, e)
        _append_game_session_debug_log(
            game_type,
            session_id,
            lanlan_name=lanlan_name,
            level="warning",
            category="realtime_context",
            event="realtime_context_failed",
            message="Realtime 上下文注入失败",
            details={"error_type": type(e).__name__, "error": str(e)},
        )
        return {"ok": False, "reason": f"inject_failed: {e}", "lanlan_name": lanlan_name}
    if not getattr(append_result, "appended", False) and not getattr(append_result, "deduped", False):
        reason = getattr(append_result, "reason", None) or "inject_failed"
        _append_game_session_debug_log(
            game_type,
            session_id,
            lanlan_name=lanlan_name,
            level="warning",
            category="realtime_context",
            event="realtime_context_failed",
            message="Realtime 上下文未写入",
            details={"reason": reason},
        )
        return {
            "ok": False,
            "reason": reason,
            "lanlan_name": lanlan_name,
        }

    logger.info("🎮 Realtime 上下文已注入: game=%s lanlan=%s bytes=%d", game_type, lanlan_name, len(text))
    _append_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        category="realtime_context",
        event="realtime_context_completed",
        message="Realtime 上下文已注入",
        details={
            "bytes": len(text),
            "items": len(data.get("pendingItems") or []),
            "deduped": getattr(append_result, "deduped", False),
        },
    )
    return {
        "ok": True,
        "lanlan_name": lanlan_name,
        "bytes": len(text),
        "items": len(data.get("pendingItems") or []),
    }


async def _complete_game_end_from_payload(
    game_type: str,
    data: dict,
    *,
    default_reason: str = "game_end",
) -> dict:
    if str(game_type or "") == "new_user_icebreaker":
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "reason": "not_a_game_route",
                "route": "/api/icebreaker/route/end",
            },
        )
    session_id = str(data.get('session_id', 'default'))
    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    # 包括 /route/end 与 /end 两条入口；postgame 投递依赖 mgr.user_language
    # 决定旁白语言，所以这里也要 heal 一次（详见 _absorb_request_language）。
    _absorb_request_language(data, lanlan_name)
    exit_reason = str(data.get("reason") or default_reason)
    postgame_options = _normalize_postgame_options(data.get("postgameProactive"), reason=exit_reason)
    state = _get_active_game_route_state(lanlan_name, game_type) if lanlan_name else None
    _append_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        category="route",
        event="route_end_requested",
        message="小游戏路由结束请求",
        details={
            "reason": exit_reason,
            "matched_active_route": bool(state and str(state.get("session_id") or "") == session_id),
            "postgame_enabled": postgame_options.get("enabled"),
        },
    )
    archive = None
    archive_memory = None
    postgame_result = None
    if state and str(state.get("session_id") or "") == session_id:
        score_session_mode = _normalize_badminton_mode(state.get("mode")) if _is_badminton_game_type(game_type) else ""
        _update_route_start_state_from_payload(state, data, exiting=True)
        current_state = data.get("currentState")
        if isinstance(current_state, dict):
            state["last_state"] = current_state
            if isinstance(current_state.get("score"), dict):
                state["finalScore"] = dict(current_state.get("score") or {})
        final_score = data.get("finalScore")
        if isinstance(final_score, dict):
            state["finalScore"] = final_score
        if "game_memory_tail_count" in data or "gameMemoryTailCount" in data:
            state["game_memory_tail_count"] = _normalize_game_memory_tail_count(
                data.get("game_memory_tail_count", data.get("gameMemoryTailCount"))
            )
        _update_game_memory_enabled_from_payload(state, data, game_type=game_type)
        # B1: serialize against /route/start supersede + heartbeat sweep
        # finalize. ``_finalize_game_route_state`` itself dedupes via the
        # state-attached ``_exit_task``, but ``/route/start`` scans across
        # all game types for this lanlan. Take the same per-lanlan OUTER
        # supersede lock before the per-(lanlan, game_type) INNER lock so a
        # late badminton end cannot clear the takeover for a freshly
        # started soccer route.
        supersede_lock = _get_supersede_lock(lanlan_name)
        end_route_lock = _get_route_lock(lanlan_name, game_type)
        try:
            async with supersede_lock:
                async with end_route_lock:
                    finalized = await _finalize_game_route_state(
                        state,
                        reason=exit_reason,
                        close_game_session=True,
                        close_debug_log=False,
                    )
            archive = finalized["archive"]
            archive_memory = finalized["archive_memory"]
            if (
                _is_badminton_game_type(game_type)
                and state.get("game_started") is True
                and _badminton_end_payload_completed_round(data)
            ):
                score_session_totals = _badminton_score_totals_from_data(state.get("finalScore"))
                if score_session_totals:
                    _remember_badminton_score_session(
                        lanlan_name,
                        session_id,
                        score_session_mode,
                        score_session_totals,
                    )
            if _game_memory_postgame_context_enabled(archive) is False:
                postgame_options["enabled"] = False
            if isinstance(archive_memory, dict) and archive_memory.get("status") == "skipped":
                postgame_options["enabled"] = False
            postgame_result = await _deliver_game_postgame(
                game_type,
                session_id,
                lanlan_name,
                archive,
                postgame_options,
                postgame_snapshot=finalized.get("postgame_context_snapshot"),
            )
            # B5: closing the LLM session is the inner finalize's job (now
            # that ``close_game_session=True`` reliably propagates via
            # OR-merge). Calling ``_close_and_remove_session`` again here
            # would race a finalize-from-heartbeat-sweep at the same key and
            # double-close the underlying ``OmniOfflineClient``.
            closed = bool(finalized.get("game_session_closed"))
        except BaseException:
            state["_exit_defer_debug_log_close"] = False
            raise
    else:
        # No active route matched — fall through to the legacy direct close
        # so an out-of-sync ``/game_end`` (e.g. page reloaded after the
        # backend already finalized via heartbeat sweep) still cleans up a
        # lingering LLM session if one exists.
        closed = await _close_and_remove_session(game_type, session_id, lanlan_name)
    result = {
        "ok": True,
        "closed": closed,
        "session_id": session_id,
        "route_closed": bool(archive),
        "archive": archive,
    }
    if archive_memory is not None:
        result["archive_memory"] = archive_memory
    if postgame_result is not None:
        result["postgame"] = postgame_result
    if state:
        result["should_resume_external_on_exit"] = state.get("should_resume_external_on_exit")
        result["before_game_external_mode"] = state.get("before_game_external_mode")
        result["state"] = _public_route_state(state)
    _append_game_session_debug_log(
        game_type,
        session_id,
        lanlan_name=lanlan_name,
        category="route",
        event="route_end_completed",
        message="小游戏路由结束完成",
        details={
            "reason": exit_reason,
            "closed": closed,
            "route_closed": bool(archive),
            "archive_memory_status": archive_memory.get("status") if isinstance(archive_memory, dict) else None,
            "postgame_status": postgame_result.get("status") if isinstance(postgame_result, dict) else None,
        },
    )
    _mark_game_session_debug_log_ended(game_type, session_id, lanlan_name=lanlan_name, reason=exit_reason)
    if state:
        state["_exit_defer_debug_log_close"] = False
        if "finalized" in locals() and isinstance(finalized, dict):
            finalized["debug_log_ended"] = True
    return result


@router.post("/{game_type}/end")
async def game_end(game_type: str, request: Request):
    """End a game round and clean up the matching LLM session."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    return await _complete_game_end_from_payload(game_type, data, default_reason="game_end")


@router.post("/{game_type}/quick-lines")
async def game_quick_lines(game_type: str, request: Request):
    """Generate character-specific quick lines when entering a game.

    Product-wise, this is part of in-game context initialization: the backend
    tells the LLM that the current character is about to play with the user and
    asks it to generate backup short lines for that persona. On success the
    frontend replaces built-in quick lines; on failure it keeps the built-ins.

    quick-lines is the first soccer endpoint that hits the LLM before
    /route/start, so this absorbs ``i18n_language`` from the request body and
    heals mgr.user_language. Otherwise the first quick lines can inherit English
    from the global cache populated during ``start_session``.
    """
    if game_type != "soccer" and not _is_badminton_game_type(game_type):
        return {"ok": False, "error": f"暂不支持 {game_type} 的快路径文案生成", "lines": {}}

    fallback_language = None
    session_id = ""
    requested_name = ""
    try:
        try:
            data = await request.json()
        except Exception:
            data = {}
        session_id = str(data.get("session_id") or data.get("sessionId") or "").strip()
        try:
            current_name = _get_current_character_info().get("lanlan_name") or ""
        except Exception:
            current_name = ""
        requested_name = _resolve_lanlan_name(data.get("lanlan_name") or current_name)
        # quick-lines 是 soccer 流程里第一个 LLM 端点：接住 _absorb_request_language
        # 的返回值，避免在 SessionManager 还没 ready / mgr 拿不到的窗口下，char_info 的
        # user_language 仍 stale 在全局缓存的旧值（首批 quick lines 落英文）。
        request_language = _absorb_request_language(data, requested_name)
        request_language_full = _extract_request_language_full(data) if _is_badminton_game_type(game_type) else None
        char_info = _get_character_info(requested_name)
        language = request_language_full or request_language or char_info.get("user_language")
        fallback_language = language
        cache_key = ""
        if _is_badminton_game_type(game_type):
            cache_lanlan = _normalize_short_text(
                char_info.get("lanlan_name") or requested_name or "",
                max_chars=80,
            )
            cache_lang = _normalize_short_text(language or "", max_chars=20)
            cache_mode = _normalize_badminton_mode(data.get("mode"))
            cache_key = f"{cache_lanlan}:{cache_lang}:{cache_mode}"
            cached = _badminton_quick_lines_cache.get(cache_key)
            if cached:
                _badminton_quick_lines_cache.move_to_end(cache_key)
                _append_game_session_debug_log(
                    game_type,
                    session_id,
                    lanlan_name=requested_name,
                    category="quick_lines",
                    event="quick_lines_cached",
                    message="游戏快路径台词命中缓存",
                    details={"character": char_info["lanlan_name"], "mode": cache_mode, "keys": sorted(cached.keys())},
                )
                return {
                    "ok": True,
                    "character": char_info["lanlan_name"],
                    "lines": cached,
                    "missing": [],
                    "cached": True,
                }
        if _is_badminton_game_type(game_type):
            prompt_template = get_badminton_quick_lines_prompt(language, mode=cache_mode)
            user_prompt = get_badminton_quick_lines_user_prompt(language, mode=cache_mode)
            allowed_keys = _BADMINTON_QUICK_LINE_KEYS
        else:
            prompt_template = get_soccer_quick_lines_prompt(language)
            user_prompt = get_soccer_quick_lines_user_prompt(language)
            allowed_keys = _SOCCER_QUICK_LINE_KEYS
        prompt = prompt_template.format(
            name=char_info['lanlan_name'],
            personality=char_info['lanlan_prompt'],
        )

        from utils.file_utils import robust_json_loads
        from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm_async
        from utils.token_tracker import set_call_type

        set_call_type("game_quick_lines")
        llm = await create_chat_llm_async(
            char_info['model'],
            char_info['base_url'],
            char_info['api_key'],
            provider_type=char_info.get('provider_type'),
            max_completion_tokens=800,
            timeout=20,
        )
        async with llm:
            result = await llm.ainvoke([  # noqa: LLM_INPUT_BUDGET  # game-session-scoped input (snapshot / history / archive / config), bounded by a single finite game; not external free-text. Deeper per-field truncation tracked as a game-domain follow-up.
                SystemMessage(content=prompt),
                HumanMessage(content=user_prompt),
            ])

        raw = _strip_json_fence(str(result.content or ""))
        parsed = robust_json_loads(raw)
        lines = _normalize_quick_lines(parsed, allowed_keys)
        if _is_badminton_game_type(game_type):
            if cache_key:
                _badminton_quick_lines_cache[cache_key] = lines
                _badminton_quick_lines_cache.move_to_end(cache_key)
                while len(_badminton_quick_lines_cache) > _BADMINTON_QUICK_LINES_CACHE_MAX:
                    _badminton_quick_lines_cache.popitem(last=False)
        missing = sorted(allowed_keys - set(lines.keys()))

        logger.info(
            "🎮 生成游戏快路径台词: game=%s character=%s keys=%d missing=%s",
            game_type, char_info['lanlan_name'], len(lines), missing,
        )
        _append_game_session_debug_log(
            game_type,
            session_id,
            lanlan_name=requested_name,
            category="quick_lines",
            event="quick_lines_completed",
            message="游戏快路径台词生成完成",
            details={
                "character": char_info["lanlan_name"],
                "keys": sorted(lines.keys()),
                "missing": missing,
                "raw_length": len(raw),
            },
            sensitive_possible=True,
        )
        return {
            "ok": bool(lines),
            "character": char_info['lanlan_name'],
            "lines": lines,
            "missing": missing,
            "raw": raw[:1200],
        }
    except Exception as e:
        logger.warning("🎮 生成游戏快路径台词失败: game=%s err=%s", game_type, e, exc_info=True)
        _append_game_session_debug_log(
            game_type,
            session_id,
            lanlan_name=requested_name,
            level="warning",
            category="quick_lines",
            event="quick_lines_failed",
            message="游戏快路径台词生成失败",
            details={"error_type": type(e).__name__, "error": str(e)},
        )
        if _is_badminton_game_type(game_type):
            return {
                "ok": True,
                "error": str(e),
                "lines": _get_badminton_quick_lines_fallback(fallback_language),
                "fallback": True,
            }
        return {"ok": False, "error": str(e), "lines": {}}


@router.get("/{game_type}/leaderboard")
async def game_badminton_leaderboard(
    game_type: str,
    session_id: str = "",
    lanlan_name: str = "",
    limit: int = 10,
    offset: int = 0,
):
    clean_limit = _normalize_badminton_non_negative_int(limit, default=10, max_value=100)
    clean_offset = _normalize_badminton_non_negative_int(offset, default=0, max_value=100000)
    if not _is_badminton_game_type(game_type):
        return {
            "ok": True,
            "top": [],
            "total_players": 0,
            "total_scores": 0,
            "limit": clean_limit,
            "offset": clean_offset,
            "has_more": False,
            "your_best": None,
        }
    with _open_badminton_scores_db(game_type) as conn:
        _ensure_badminton_scores_schema(conn)
        rows = _badminton_score_rows(conn, limit=clean_limit, offset=clean_offset)
        total_players = _badminton_leaderboard_total_players(conn)
        total_scores = _badminton_leaderboard_total_scores(conn)
        top = [_badminton_row_to_public_dict(row, _badminton_rank_for_row(conn, row)) for row in rows]
        your_best = _badminton_personal_best(conn, lanlan_name, session_id)
    return {
        "ok": True,
        "top": top,
        "total_players": total_players,
        "total_scores": total_scores,
        "limit": clean_limit,
        "offset": clean_offset,
        "has_more": clean_offset + len(top) < total_scores,
        "your_best": your_best,
    }


@router.post("/{game_type}/leaderboard")
async def game_badminton_leaderboard_submit(game_type: str, request: Request):
    if not _is_badminton_game_type(game_type):
        return {"ok": False, "reason": f"暂不支持 {game_type} 的排行榜"}
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}
    if not isinstance(data, dict):
        return {"ok": False, "reason": "invalid_body"}
    if not _badminton_score_submission_allowed(data, reserve=True):
        return {"ok": False, "reason": "invalid_session"}
    insert_data = _badminton_score_data_for_insert(data)
    try:
        rank, total_players, is_personal_best = _badminton_insert_score(insert_data, game_type=game_type)
    except Exception:
        _release_badminton_score_session_reservation(data)
        raise
    _consume_badminton_score_session(data)
    return {
        "ok": True,
        "rank": rank,
        "total_players": total_players,
        "is_personal_best": is_personal_best,
    }


@router.get("/{game_type}/character")
async def game_character(game_type: str, request: Request = None):
    """Return current character information for model replacement.

    The response includes the current model type and a frontend-addressable
    model path. Each mini game chooses Live2D, VRM, MMD, or an explicit fallback
    according to its own rendering support.
    """
    def normalize_live3d_path(raw: str, static_dir: str) -> str:
        if not raw or not isinstance(raw, str):
            return ''
        normalized = raw.strip().replace('\\', '/')
        if not normalized:
            return ''
        if normalized.startswith(('http://', 'https://', '/user_', '/static/', '/workshop/')):
            return normalized
        if normalized.startswith(f'{static_dir}/'):
            return f'/static/{normalized}'
        return f'/static/{static_dir}/{normalized}'

    try:
        config_manager = get_config_manager()
        characters = await asyncio.to_thread(config_manager.load_characters)
        requested_name = (
            str(request.query_params.get('lanlan_name') or '').strip()
            if request is not None
            else ''
        )
        all_nekos = characters.get('猫娘', {}) if isinstance(characters, dict) else {}
        current_name = (
            requested_name
            if requested_name and isinstance(all_nekos, dict) and requested_name in all_nekos
            else characters.get('当前猫娘', '')
        )
        neko_data = characters.get('猫娘', {}).get(current_name, {})

        # 获取 _reserved.avatar 配置
        reserved = neko_data.get('_reserved', {})
        avatar = reserved.get('avatar', {}) if isinstance(reserved, dict) else {}

        model_type = avatar.get('model_type', '') if isinstance(avatar, dict) else ''
        live3d_sub_type = avatar.get('live3d_sub_type', '') if isinstance(avatar, dict) else ''

        # 提取各类型模型路径
        live2d_path = ''
        mmd_path = ''
        vrm_path = ''

        if isinstance(avatar, dict):
            live2d_info = avatar.get('live2d', {})
            if isinstance(live2d_info, dict):
                raw = live2d_info.get('model_path', '')
                if raw:
                    # Live2D 可能来自 static、用户导入目录、CFA 回退目录或工坊。
                    # 足球 demo 复用主角色接口的解析逻辑，避免把用户模型误拼成 /static/...。
                    from .characters_router import get_current_live2d_model

                    model_response = await get_current_live2d_model(current_name)
                    response_body = getattr(model_response, 'body', b'')
                    if response_body:
                        model_payload = json.loads(response_body.decode('utf-8'))
                        model_info = model_payload.get('model_info') or {}
                        live2d_path = model_info.get('path', '')

            mmd_info = avatar.get('mmd', {})
            if isinstance(mmd_info, dict):
                mmd_path = normalize_live3d_path(mmd_info.get('model_path', ''), 'mmd')

            vrm_info = avatar.get('vrm', {})
            if isinstance(vrm_info, dict):
                raw = vrm_info.get('model_path', '')
                if raw:
                    from .config_router import _resolve_vrm_path

                    vrm_path = _resolve_vrm_path(raw, config_manager, current_name)

        return {
            'lanlan_name': current_name,
            'model_type': model_type,
            'live3d_sub_type': live3d_sub_type,
            'live2d_path': live2d_path,
            'mmd_path': mmd_path,
            'vrm_path': vrm_path,
        }
    except Exception as e:
        logger.error("🎮 获取角色信息失败: %s", e)
        return {"error": str(e)}


# ── 后台清理 ───────────────────────────────────────────────────────

async def cleanup_expired_sessions():
    """Clean up expired game sessions. Can be registered as a background task by the startup event."""
    next_session_cleanup_at = 0.0
    while True:
        await asyncio.sleep(_GAME_ROUTE_HEARTBEAT_SWEEP_SECONDS)
        now = time.time()

        heartbeat_expired_routes = [
            (k, v) for k, v in list(_game_route_states.items())
            if (
                v.get("game_route_active")
                and v.get("heartbeat_enabled", True)
                and not v.get("_exit_task")
                and _route_heartbeat_expired(v, now)
            )
        ]
        for key, state in heartbeat_expired_routes:
            last_heartbeat = float(state.get("last_heartbeat_at", state.get("created_at", 0)) or 0)
            last_activity = float(state.get("last_activity", state.get("created_at", 0)) or 0)
            idle_seconds = now - _route_liveness_at(state)
            timeout_seconds = _route_heartbeat_timeout_seconds(state)
            logger.warning(
                "🎮 游戏页心跳超时，执行退出兜底: key=%s idle=%.1fs timeout=%.1fs visible=%s visibility=%s heartbeat_idle=%.1fs activity_idle=%.1fs",
                key,
                idle_seconds,
                timeout_seconds,
                state.get("page_visible"),
                state.get("visibility_state"),
                now - last_heartbeat,
                now - last_activity,
            )
            # B2: serialize against any concurrent /route/start (which may
            # be supersede-finalizing this same slot) under the per-slot
            # route lock so we don't double-finalize or interleave with
            # an incoming route activation.
            sweep_lanlan = str(state.get("lanlan_name") or "")
            sweep_game_type = str(state.get("game_type") or "")
            sweep_lock = _get_route_lock(sweep_lanlan, sweep_game_type)
            try:
                async with sweep_lock:
                    # Peer (e.g. /route/start supersede or /route/end) may
                    # have already finalized the slot while we waited for
                    # the lock; recheck and skip if so.
                    if not state.get("game_route_active") or state.get("_exit_task"):
                        if state.get("_exit_task"):
                            await asyncio.shield(state["_exit_task"])
                        continue
                    # Why: a concurrent ``/route/heartbeat`` may have
                    # bumped ``last_heartbeat_at`` between the lock-free
                    # expired-scan and the lock acquisition above. The
                    # browser is alive; finalizing here would kill a
                    # live route. Re-check inside the lock with a fresh
                    # ``time.time()`` and skip if the route recovered.
                    if not _route_heartbeat_expired(state, time.time()):
                        continue
                    await _finalize_game_route_state(
                        state,
                        reason="heartbeat_timeout",
                        close_game_session=True,
                    )
            except Exception as e:
                logger.warning("🎮 游戏页心跳超时退出兜底失败: key=%s err=%s", key, e, exc_info=True)

        if now < next_session_cleanup_at:
            continue
        next_session_cleanup_at = now + _SESSION_CLEANUP_SWEEP_SECONDS

        expired = [
            k for k, v in list(_game_sessions.items())
            if now - v['last_activity'] > _SESSION_TIMEOUT_SECONDS
        ]
        for key in expired:
            lanlan_name, game_type, session_id = _parse_game_session_key(key)
            if await _close_and_remove_session(game_type, session_id, lanlan_name):
                logger.info("🎮 清理过期游戏 session: %s", key)

        expired_routes = [
            k for k, v in list(_game_route_states.items())
            if (
                not v.get("game_route_active")
                and now - float(v.get("exit_started_at", v.get("last_activity", 0)) or 0) > _SESSION_TIMEOUT_SECONDS
            )
        ]
        for key in expired_routes:
            state = _game_route_states.pop(key, None)
            if state:
                logger.info("🎮 清理过期游戏路由状态: %s", key)
