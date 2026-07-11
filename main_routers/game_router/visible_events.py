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

"""Sanitizers for LLM-visible game events and internal control-line
stripping.

Split out of the former monolithic ``main_routers/game_router.py``.
"""

from ._shared import _normalize_short_text
from .badminton_scores import _BADMINTON_SHOT_TYPE_ALIASES, _normalize_badminton_mode
from .memory_policy import _game_memory_payload_keys, _normalize_game_memory_type

import math
import re
from typing import Any


def _normalize_badminton_shot_type(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _BADMINTON_SHOT_TYPE_ALIASES.get(raw, "")


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
