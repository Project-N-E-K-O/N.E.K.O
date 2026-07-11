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

"""Game context signals: normalization, merging, prompt formatting and
the organizer AI payload/call (pure parts; task orchestration lives in
runtime next to the session state).

Split out of the former monolithic ``main_routers/game_router.py``.
"""

from ._shared import _infer_service_source, _normalize_short_text, _strip_json_fence, logger
from .char_info import _get_game_route_summary_llm_info
from .memory_policy import _game_memory_event_reply_enabled, _game_memory_player_interaction_enabled

import json
import re
import time
from typing import Any
from config.prompts.prompts_minigame_route import (
    GAME_CONTEXT_SIGNAL_GROUP_KEYS,
    get_game_context_formatter_labels,
    get_game_context_organizer_system_prompt,
    get_game_context_organizer_user_prompt,
    get_game_dialog_memory_line_labels,
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
