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

"""Durable pool for the tutorial choices a user makes during onboarding.

This is intentionally a *write-only* persistence layer for now: every effective
option the user picks in the new-user icebreaker (the A/B branch picks plus the
final handoff pick) is appended here so the data survives across sessions. The
choices currently feed into nothing — they do not enter the memory system and do
not influence the model. The point is to capture the signal now so future work
can consume it incrementally, without having to retrofit a store later.

The file lives next to ``tutorial_prompt.json`` (global config dir) but is keyed
by ``lanlan_name`` so choices stay attributed to the character the user
onboarded with.
"""
from __future__ import annotations

import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.prompt_state_core import (
    clamp_int as _clamp_int,
    clean_str as _clean_str,
    load_state_file as _load_state_file,
    now_ms as _now_ms,
)

TUTORIAL_CHOICES_STATE_FILENAME = "tutorial_choices.json"
TUTORIAL_CHOICES_STATE_KIND = "tutorial_choices"
TUTORIAL_CHOICES_SCHEMA_VERSION = 1

# 防御性上限：单棵破冰二叉树满打满算 5 跳/天 × 7 天 = 35 条，留足冗余即可，不需要无限增长。
MAX_CHARACTERS = 32
MAX_DAYS_PER_CHARACTER = 64
MAX_CHOICES_PER_DAY = 128

_LABEL_LIMIT = 200
_NODE_ID_LIMIT = 64
_CHOICE_LIMIT = 16
_SOURCE_LIMIT = 64
_NAME_LIMIT = 128
_DAY_LIMIT = 32
_SESSION_LIMIT = 128

DEFAULT_TUTORIAL_CHOICES_STATE = {
    "version": TUTORIAL_CHOICES_SCHEMA_VERSION,
    "kind": TUTORIAL_CHOICES_STATE_KIND,
    "characters": {},
}

_STATE_LOCK = threading.RLock()


def get_tutorial_choices_state_path(config_manager=None) -> Path:
    config_manager = config_manager or get_config_manager()
    return Path(config_manager.get_config_path(TUTORIAL_CHOICES_STATE_FILENAME))


def _normalize_choice(raw_choice: Any) -> dict[str, Any] | None:
    if not isinstance(raw_choice, dict):
        return None
    node_id = _clean_str(raw_choice.get("node_id"), limit=_NODE_ID_LIMIT)
    choice = _clean_str(raw_choice.get("choice"), limit=_CHOICE_LIMIT)
    if not node_id or not choice:
        return None
    return {
        "node_id": node_id,
        "choice": choice,
        "label": _clean_str(raw_choice.get("label"), limit=_LABEL_LIMIT),
        "handoff": bool(raw_choice.get("handoff")),
        "at": _clamp_int(raw_choice.get("at")),
    }


def _normalize_day(raw_day: Any, day_key: str) -> dict[str, Any]:
    raw_day = raw_day if isinstance(raw_day, dict) else {}

    choices: list[dict[str, Any]] = []
    raw_choices = raw_day.get("choices")
    if isinstance(raw_choices, list):
        for raw_choice in raw_choices:
            normalized = _normalize_choice(raw_choice)
            if normalized is not None:
                choices.append(normalized)
    if len(choices) > MAX_CHOICES_PER_DAY:
        choices = choices[-MAX_CHOICES_PER_DAY:]

    return {
        "day": _clean_str(raw_day.get("day") or day_key, limit=_DAY_LIMIT) or day_key,
        "session_id": _clean_str(raw_day.get("session_id"), limit=_SESSION_LIMIT),
        "source": _clean_str(raw_day.get("source"), limit=_SOURCE_LIMIT),
        "completed": bool(raw_day.get("completed")),
        "first_recorded_at": _clamp_int(raw_day.get("first_recorded_at")),
        "updated_at": _clamp_int(raw_day.get("updated_at")),
        "choices": choices,
    }


def _normalize_character(raw_character: Any) -> dict[str, Any]:
    raw_character = raw_character if isinstance(raw_character, dict) else {}
    raw_days = raw_character.get("days")
    raw_days = raw_days if isinstance(raw_days, dict) else {}

    days: dict[str, Any] = {}
    for raw_key, raw_day in raw_days.items():
        day_key = _clean_str(raw_key, limit=_DAY_LIMIT)
        if not day_key:
            continue
        days[day_key] = _normalize_day(raw_day, day_key)
        if len(days) >= MAX_DAYS_PER_CHARACTER:
            break

    return {"days": days}


def _normalize_state(raw_state: Any) -> dict[str, Any]:
    if not isinstance(raw_state, dict):
        return deepcopy(DEFAULT_TUTORIAL_CHOICES_STATE)

    raw_characters = raw_state.get("characters")
    raw_characters = raw_characters if isinstance(raw_characters, dict) else {}

    characters: dict[str, Any] = {}
    for raw_name, raw_character in raw_characters.items():
        name = _clean_str(raw_name, limit=_NAME_LIMIT)
        if not name:
            continue
        characters[name] = _normalize_character(raw_character)
        if len(characters) >= MAX_CHARACTERS:
            break

    return {
        "version": TUTORIAL_CHOICES_SCHEMA_VERSION,
        "kind": TUTORIAL_CHOICES_STATE_KIND,
        "characters": characters,
    }


def load_tutorial_choices_state(config_manager=None) -> dict[str, Any]:
    path = get_tutorial_choices_state_path(config_manager)
    data = _load_state_file(path)
    if data is None:
        return deepcopy(DEFAULT_TUTORIAL_CHOICES_STATE)
    return _normalize_state(data)


def save_tutorial_choices_state(state: dict[str, Any], config_manager=None) -> dict[str, Any]:
    normalized = _normalize_state(state)
    path = get_tutorial_choices_state_path(config_manager)
    atomic_write_json(path, normalized, ensure_ascii=False, indent=2)
    return normalized


def _is_duplicate_choice(existing: list[dict[str, Any]], candidate: dict[str, Any], session_id: str) -> bool:
    # 一棵分支树在同一 session 里不会重复经过同一节点，但网络重试 / 重复事件可能把
    # 同一次点击重发。按 (session_id, node_id, choice, handoff) 去重，挡掉精确重放，
    # 同时仍允许重定向后在同一节点改投另一个选项。
    if not session_id:
        return False
    for item in existing:
        if (
            item.get("node_id") == candidate["node_id"]
            and item.get("choice") == candidate["choice"]
            and bool(item.get("handoff")) == candidate["handoff"]
        ):
            return True
    return False


def record_tutorial_choice(
    payload: dict[str, Any] | None,
    *,
    config_manager=None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    now_ms = _clamp_int(now_ms if now_ms is not None else _now_ms())

    lanlan_name = _clean_str(payload.get("lanlan_name"), limit=_NAME_LIMIT)
    day_key = _clean_str(payload.get("day"), limit=_DAY_LIMIT)
    candidate = _normalize_choice({
        "node_id": payload.get("node_id"),
        "choice": payload.get("choice"),
        "label": payload.get("label"),
        "handoff": payload.get("handoff"),
        "at": now_ms,
    })

    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}
    if not day_key:
        return {"ok": False, "reason": "missing_day"}
    if candidate is None:
        return {"ok": False, "reason": "invalid_choice"}

    session_id = _clean_str(payload.get("session_id"), limit=_SESSION_LIMIT)
    source = _clean_str(payload.get("source"), limit=_SOURCE_LIMIT)
    completed = bool(payload.get("completed"))

    with _STATE_LOCK:
        state = load_tutorial_choices_state(config_manager)
        characters = state["characters"]
        character = characters.setdefault(lanlan_name, {"days": {}})
        days = character.setdefault("days", {})
        day = days.get(day_key)
        if not isinstance(day, dict):
            day = _normalize_day({"day": day_key}, day_key)
            days[day_key] = day

        deduped = _is_duplicate_choice(day["choices"], candidate, session_id)
        if not deduped:
            day["choices"].append(candidate)
            if len(day["choices"]) > MAX_CHOICES_PER_DAY:
                day["choices"] = day["choices"][-MAX_CHOICES_PER_DAY:]

        if session_id:
            day["session_id"] = session_id
        if source:
            day["source"] = source
        if completed:
            day["completed"] = True
        if not day.get("first_recorded_at"):
            day["first_recorded_at"] = now_ms
        day["updated_at"] = now_ms

        state = save_tutorial_choices_state(state, config_manager)

    return {
        "ok": True,
        "deduped": deduped,
        "lanlan_name": lanlan_name,
        "day": day_key,
    }


def get_tutorial_choices_for_character(
    lanlan_name: str,
    *,
    config_manager=None,
) -> dict[str, Any]:
    name = _clean_str(lanlan_name, limit=_NAME_LIMIT)
    state = load_tutorial_choices_state(config_manager)
    character = state["characters"].get(name) if name else None
    return character if isinstance(character, dict) else {"days": {}}
