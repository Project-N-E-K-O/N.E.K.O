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

"""Per-game memory policy: payload keys, defaults, normalization and
flag attachment.

Split out of the former monolithic ``main_routers/game_router.py``.
"""

from ._shared import _coerce_payload_bool
from .badminton_scores import _BADMINTON_GAME_TYPES

from typing import Any


_DEFAULT_GAME_MEMORY_TAIL_COUNT = 6


_MAX_GAME_MEMORY_TAIL_COUNT = 50


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
