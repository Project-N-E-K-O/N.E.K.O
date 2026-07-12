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

"""Game chat dispatch and every HTTP endpoint of the game router.

Keeps the endpoint layer plus the ``_run_game_chat`` main chain: session
chat, route lifecycle endpoints, external transcript routing, end flow and
the periodic cleanup sweep. The mutable domain state now lives with its
owning submodule -- ``session_pool`` (``_game_sessions`` and the create
locks) and ``utils/game_route_state`` (``_game_route_states``) -- and is
shared by reference; ``route_lifecycle`` owns route-state transitions and
``postgame`` owns postgame delivery and the finalize flow.

Split out of the former monolithic ``main_routers/game_router.py``.
"""

from ._shared import (
    _coerce_payload_bool,
    _log_game_debug_material,
    _normalize_short_text,
    _strip_json_fence,
    logger,
    router,
)
from .badminton_scores import (
    _badminton_end_payload_completed_round,
    _badminton_score_totals_from_data,
    _is_badminton_game_type,
    _normalize_badminton_mode,
    _remember_badminton_score_session,
)
from .balance import (
    _apply_badminton_anger_pressure_cap,
    _apply_soccer_anger_pressure_cap,
    _build_badminton_duel_anger_pressure_cap,
    _build_badminton_duel_balance_hint,
    _build_soccer_anger_pressure_cap,
    _build_soccer_balance_hint,
)
from .char_info import (
    _absorb_request_language,
    _extract_request_language_full,
    _get_character_info,
    _get_current_character_info,
    _resolve_game_prompt_language,
)
from .game_context import (
    _GAME_CONTEXT_FAILURE_VISIBLE_WINDOW_MAX_COUNT,
    _game_context_recent_dialogues,
)
from .memory_policy import (
    _attach_game_memory_flag_to_event,
    _game_memory_camel_key,
    _game_memory_player_interaction_enabled,
    _game_memory_policy,
    _game_memory_policy_fields,
    _game_memory_postgame_context_enabled,
    _normalize_game_memory_tail_count,
    _normalize_game_memory_type,
)
from .pregame import (
    _SOCCER_DIFFICULTIES,
    _SOCCER_MOODS,
    _build_badminton_pregame_context,
    _build_soccer_pregame_context,
    _default_badminton_pregame_context,
    _default_soccer_pregame_context,
)
from .visible_events import _build_game_llm_visible_event, _sanitize_badminton_event, _sanitize_game_visible_line

# Split domain modules (this file's former state-machine body). Runtime code
# consumes a subset of these names, but EVERY moved name is re-imported on
# purpose: callers and tests keep addressing them as ``runtime.<name>``
# (``gr_patch_all`` then patches every submodule holding the binding), which
# preserves the pre-split monkeypatch semantics. The mutable containers
# (``_game_sessions``, ``_game_session_create_locks``) stay the same object
# as in ``session_pool`` -- from-imports bind by reference, never copy.
from .postgame import (  # noqa: F401
    _POSTGAME_REALTIME_NUDGE_DELAYS,
    _POSTGAME_REALTIME_UNORGANIZED_LIMIT,
    _POSTGAME_REALTIME_UNORGANIZED_MAX_TOKENS,
    _POSTGAME_SKIP_REASONS,
    _active_realtime_session,
    _append_token_limited_lines,
    _archive_unorganized_dialogues,
    _build_game_postgame_context_text,
    _build_game_postgame_event,
    _build_game_postgame_realtime_nudge_instruction,
    _build_postgame_context_snapshot,
    _deliver_game_postgame,
    _deliver_postgame_text_bubble,
    _deliver_postgame_to_realtime,
    _finalize_game_route_state,
    _finalize_game_route_state_inner,
    _is_gemini_realtime_session,
    _normalize_postgame_options,
    _postgame_context_request_id,
    _postgame_last_signals,
    _run_postgame_realtime_nudge_task,
)
from .route_lifecycle import (  # noqa: F401
    _GAME_ROUTE_ACTIVATION_LOG_LIMIT,
    _GAME_ROUTE_HEARTBEAT_INTERVAL_SECONDS,
    _GAME_ROUTE_HEARTBEAT_SWEEP_SECONDS,
    _GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS,
    _GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS,
    _GAME_ROUTE_OUTPUT_LIMIT,
    _activate_game_route,
    _append_game_dialog,
    _append_game_output,
    _append_route_activation,
    _apply_game_context_failure_fallback,
    _apply_game_context_organizer_failure,
    _apply_game_context_organizer_success,
    _build_route_state,
    _cancel_game_context_organizer_before_disabled_archive,
    _detect_before_game_external_state,
    _find_game_route_state_for_session,
    _game_context_pending_dialogues,
    _game_context_recent_id_limit,
    _maybe_schedule_game_context_organizer,
    _next_game_dialog_id,
    _push_game_window_state_change,
    _route_heartbeat_expired,
    _route_heartbeat_timeout_seconds,
    _route_liveness_at,
    _run_game_context_organizer_task,
    _set_game_context_recent_ids,
    _settle_game_context_organizer_before_archive,
    _should_schedule_game_context_organizer,
    _sync_game_dialog_seq_from_id,
    _update_game_memory_enabled_from_payload,
    _update_route_start_state_from_payload,
    _update_route_visibility_from_payload,
)
from .session_pool import (  # noqa: F401
    _POSTGAME_SESSION_MARKER,
    _POSTGAME_UUID_TAIL_RE,
    _SESSION_CLEANUP_SWEEP_SECONDS,
    _SESSION_TIMEOUT_SECONDS,
    _build_and_register_game_session,
    _build_game_prompt,
    _close_and_remove_session,
    _game_session_create_locks,
    _game_session_key,
    _game_sessions,
    _get_or_create_session,
    _get_session_create_lock,
    _make_postgame_session_id,
    _parse_game_session_key,
    _refresh_game_session_instructions,
    _route_session_id,
)

import asyncio
import json
import re
import time
from collections import OrderedDict
from typing import Any, Dict, Optional
from fastapi import HTTPException, Request
from config.prompts.prompts_soccer import (
    get_soccer_quick_lines_prompt,
    get_soccer_quick_lines_user_prompt,
    get_soccer_system_prompt,  # noqa: F401  (re-export: paired with the badminton one below)
)
from config.prompts.prompts_badminton import (
    get_badminton_quick_lines_fallback,
    get_badminton_quick_lines_prompt,
    get_badminton_quick_lines_user_prompt,
    get_badminton_system_prompt,  # noqa: F401  (re-export: tests address it as ``runtime.get_badminton_system_prompt``)
)
from config.prompts.prompts_minigame_route import (
    get_compact_realtime_context_texts,
    get_game_chat_event_user_prompt,
    get_game_recent_history_message_labels,
)
from ..shared_state import get_config_manager, get_session_manager
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
    register_voice_transcript_handler,
)
from utils.game_log import (
    append_game_session_debug_log as _append_game_session_debug_log,
    enable_game_session_debug_log as _enable_game_session_debug_log,
    mark_game_session_debug_log_active as _mark_game_session_debug_log_active,
    mark_game_session_debug_log_ended as _mark_game_session_debug_log_ended,
    touch_game_session_debug_log as _touch_game_session_debug_log,
)


_EXTERNAL_VOICE_DEDUP_TTL_SECONDS = 30.0


_EXTERNAL_VOICE_DEDUP_MAX_ENTRIES = 64


_SSML_TAG_PATTERN = re.compile(
    r"</?(?:[a-z][\w-]*:)?(?:"
    r"speak|p|s|break|say-as|phoneme|sub|prosody|emphasis|voice|audio|mark|lang|w|token|express-as|effect"
    r")(?:\s+[^<>\n]{0,120})?\s*/?>",
    re.IGNORECASE,
)


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


def _resolve_lanlan_name(raw: Any = None) -> str:
    lanlan_name = str(raw or "").strip()
    if lanlan_name:
        return lanlan_name
    try:
        return str(_get_current_character_info().get("lanlan_name") or "").strip()
    except Exception:
        return ""


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

    from ..system_router import _validate_local_mutation_request

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
                    from ..characters_router import get_current_live2d_model

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
                    from ..config_router import _resolve_vrm_path

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
