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

"""Postgame delivery and the game-route finalize flow.

Owns postgame option normalization, postgame context/event builders, the
realtime-session and text-bubble delivery paths, and the finalize
entrypoints (``_finalize_game_route_state`` and its inner helper) that
archive the game and flip the route to inactive.

Split out of ``main_routers/game_router/runtime.py``.
"""

from ._shared import (
    _DEFAULT_LAST_FULL_DIALOGUE_COUNT,
    _log_game_debug_material,
    _normalize_short_text,
    logger,
)
from .archive import (
    _archive_game_context_degraded,
    _archive_last_assistant_line,
    _archive_last_user_text,
    _archive_prompt_language,
    _archive_score_text,
    _build_game_archive,
    _build_game_archive_memory_skipped_result,
    _fallback_game_archive_memory_highlights,
    _game_archive_memory_skip_reason,
    _normalize_game_archive_memory_highlights,
    _submit_game_archive_to_memory,
)
from .badminton_scores import _normalize_badminton_mode
from .game_context import (
    _build_game_context_prompt_payload,
    _dialog_id_index,
    _dialog_memory_line,
    _format_ts,
    _game_context_signals_text,
    _normalize_game_context_organizer_state,
)
from .route_lifecycle import (
    _cancel_game_context_organizer_before_disabled_archive,
    _push_game_window_state_change,
    _settle_game_context_organizer_before_archive,
)
from .session_pool import (
    _close_and_remove_session,
    _game_session_create_locks,
    _game_session_key,
    _game_sessions,
)

import asyncio
import json
import time
from typing import Any, Dict, Optional
from config.prompts.prompts_minigame_route import (
    get_game_postgame_context_labels,
    get_game_postgame_event_texts,
    get_game_postgame_realtime_nudge_labels,
)
from ..shared_state import get_session_manager
from utils.game_log import mark_game_session_debug_log_ended as _mark_game_session_debug_log_ended


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
    # Late import: ``_run_game_chat`` lives in ``.runtime``, the layer above
    # this module, so a module-level import would be a package import cycle.
    # Resolving through the module object at call time also keeps a
    # monkeypatch on ``runtime._run_game_chat`` effective for this caller.
    from . import runtime as _runtime

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
        llm_result = await _runtime._run_game_chat(
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
    resume_voice = getattr(
        mgr,
        "_resume_independent_voice_input_after_game",
        None,
    )
    if callable(resume_voice):
        realtime_restore["attempted"] = True
        try:
            await resume_voice()
            realtime_restore["reason"] = "voice_input_resumed"
        except Exception as exc:
            realtime_restore["ok"] = False
            realtime_restore["reason"] = "voice_input_resume_failed"
            logger.warning("⚠️ 游戏路由退出时恢复语音输入失败: %s", exc)
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
