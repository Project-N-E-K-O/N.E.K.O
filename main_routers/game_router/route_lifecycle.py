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

"""Game route-state lifecycle helpers.

Owns route-state construction/activation, heartbeat/liveness bookkeeping,
route payload updaters, the per-route dialog/output logs and the
game-context organizer scheduling glue. The authoritative
``_game_route_states`` container itself lives in ``utils/game_route_state``
and is imported by reference; this module is the write side of that state
below the endpoint layer.

Split out of ``main_routers/game_router/runtime.py``.
"""

from ._shared import (
    _DEFAULT_LAST_FULL_DIALOGUE_COUNT,
    _coerce_payload_bool,
    _coerce_payload_float,
    _normalize_short_text,
    logger,
)
from .game_context import (
    _GAME_CONTEXT_FAILURE_FALLBACK_KEEP_COUNT,
    _GAME_CONTEXT_FAILURE_VISIBLE_WINDOW_MAX_COUNT,
    _GAME_CONTEXT_FINALIZE_WAIT_SECONDS,
    _GAME_CONTEXT_ORGANIZE_TRIGGER_COUNT,
    _GAME_CONTEXT_RECENT_KEEP_COUNT,
    _GAME_CONTEXT_RECENT_WINDOW_MAX_COUNT,
    _dialog_id_index,
    _empty_game_context_signals,
    _merge_game_context_signals,
    _normalize_game_context_organizer_state,
    _run_game_context_organizer_ai,
)
from .memory_policy import (
    _DEFAULT_BADMINTON_GAME_MEMORY_ENABLED,
    _DEFAULT_GAME_MEMORY_TAIL_COUNT,
    _DEFAULT_SOCCER_GAME_MEMORY_ENABLED,
    _game_memory_policy_fields,
    _game_memory_policy_from_payload,
    _normalize_game_memory_type,
)

import asyncio
import re
import time
from typing import Any
from ..shared_state import get_session_manager
from utils.game_route_state import _game_route_states, _route_state_key


_GAME_ROUTE_ACTIVATION_LOG_LIMIT = 32


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


_GAME_ROUTE_OUTPUT_LIMIT = 50


_GAME_ROUTE_HEARTBEAT_INTERVAL_SECONDS = 2.5


_GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS = 10.0


_GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS = 60.0


_GAME_ROUTE_HEARTBEAT_SWEEP_SECONDS = 2.0


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
        "window_lanlan_name": lanlan_name,
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
