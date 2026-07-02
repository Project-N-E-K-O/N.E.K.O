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

"""Game archive building and memory submission: memory text,
highlights, tail messages and the memory-server submission call.

Split out of the former monolithic ``main_routers/game_router.py``.
"""

from ._shared import (
    _ACCIDENTAL_GAME_ENTRY_GRACE_MS,
    _DEFAULT_LAST_FULL_DIALOGUE_COUNT,
    _coerce_payload_float,
    _infer_service_source,
    _log_game_debug_material,
    _normalize_short_text,
    _strip_json_fence,
    logger,
)
from .char_info import _get_game_route_summary_llm_info, _resolve_game_prompt_language
from .game_context import (
    _dialog_memory_line,
    _extract_score_text,
    _format_ts,
    _game_context_signals_text,
    _game_dialog_item_allowed_for_memory,
    _normalize_game_context_organizer_state,
    _normalize_game_context_signals,
)
from .memory_policy import _game_memory_archive_enabled, _game_memory_policy, _normalize_game_memory_tail_count

import json
import re
import time
from typing import Any
from config.prompts.prompts_minigame_route import (
    get_game_archive_fallback_highlight_labels,
    get_game_archive_highlight_source_labels,
    get_game_archive_memory_highlighter_system_prompt,
    get_game_archive_memory_highlighter_user_prompt,
    get_game_archive_memory_summary_labels,
    get_game_archive_memory_text_labels,
)
from ..shared_state import get_session_manager
from utils.language_utils import normalize_language_code


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


def _archive_score_text(archive: dict) -> str:
    return _extract_score_text({
        "finalScore": archive.get("finalScore") if isinstance(archive.get("finalScore"), dict) else {},
        "last_state": archive.get("last_state") if isinstance(archive.get("last_state"), dict) else {},
        "lanlan_name": archive.get("lanlan_name"),
    })


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
