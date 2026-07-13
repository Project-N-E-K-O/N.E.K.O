"""Validation contract for the three supported avatar interaction tools.

Prompt wording and memory templates intentionally stay in
``prompts_avatar_interaction.py``.  This module owns only wire-level facts so
the runtime validator has one authoritative tool/action/intensity table.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any, Optional


TextContextSanitizer = Callable[[Any], str]

AVATAR_INTERACTION_ALLOWED_TOUCH_ZONES = frozenset({"ear", "head", "face", "body"})
AVATAR_INTERACTION_TOOL_CONTRACT = {
    "lollipop": {
        "actions": {
            "offer": frozenset({"normal"}),
            "tease": frozenset({"normal"}),
            "tap_soft": frozenset({"rapid", "burst"}),
        },
        "touch_zone": False,
        "boolean_field": None,
    },
    "fist": {
        "actions": {
            "poke": frozenset({"normal", "rapid"}),
        },
        "touch_zone": True,
        "boolean_field": "reward_drop",
    },
    "hammer": {
        "actions": {
            "bonk": frozenset({"normal", "rapid", "burst", "easter_egg"}),
        },
        "touch_zone": True,
        "boolean_field": "easter_egg",
    },
}

AVATAR_INTERACTION_TOUCH_ZONE_TOOLS = frozenset(
    tool_id
    for tool_id, tool_contract in AVATAR_INTERACTION_TOOL_CONTRACT.items()
    if tool_contract["touch_zone"]
)


def normalize_avatar_interaction_intensity(
    tool_id: str, action_id: str, intensity: str | None
) -> str:
    normalized = str(intensity or "").strip().lower()
    tool_contract = AVATAR_INTERACTION_TOOL_CONTRACT.get(tool_id)
    allowed = tool_contract and tool_contract["actions"].get(action_id)
    if not allowed or normalized not in allowed:
        return "normal"
    return normalized


def parse_avatar_interaction_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return default


def get_avatar_interaction_payload_value(
    payload: dict, snake_key: str, camel_key: str, default: Any = None
) -> Any:
    if snake_key in payload and payload.get(snake_key) is not None:
        return payload.get(snake_key)
    if camel_key in payload and payload.get(camel_key) is not None:
        return payload.get(camel_key)
    return default


def normalize_avatar_interaction_payload(
    payload: dict,
    *,
    sanitize_text_context: TextContextSanitizer | None = None,
    now_ms: int | None = None,
) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None

    interaction_id = str(
        payload.get("interaction_id") or payload.get("interactionId") or ""
    ).strip()
    tool_id = str(payload.get("tool_id") or payload.get("toolId") or "").strip().lower()
    action_id = (
        str(payload.get("action_id") or payload.get("actionId") or "").strip().lower()
    )
    target = str(payload.get("target") or "").strip().lower()
    tool_contract = AVATAR_INTERACTION_TOOL_CONTRACT.get(tool_id)

    if not interaction_id or target != "avatar" or not tool_contract:
        return None
    if action_id not in tool_contract["actions"]:
        return None

    intensity = normalize_avatar_interaction_intensity(
        tool_id,
        action_id,
        str(payload.get("intensity") or "").strip().lower(),
    )
    reward_drop = (
        parse_avatar_interaction_bool(
            get_avatar_interaction_payload_value(
                payload, "reward_drop", "rewardDrop", False
            )
        )
        if tool_contract["boolean_field"] == "reward_drop"
        else False
    )
    easter_egg = (
        parse_avatar_interaction_bool(
            get_avatar_interaction_payload_value(
                payload, "easter_egg", "easterEgg", False
            )
        )
        if tool_contract["boolean_field"] == "easter_egg"
        else False
    )
    if tool_id == "hammer" and (easter_egg or intensity == "easter_egg"):
        easter_egg = True
        intensity = "easter_egg"

    raw_touch_zone = (
        str(payload.get("touch_zone") or payload.get("touchZone") or "").strip().lower()
    )
    touch_zone = (
        raw_touch_zone
        if tool_contract["touch_zone"]
        and raw_touch_zone in AVATAR_INTERACTION_ALLOWED_TOUCH_ZONES
        else ""
    )

    pointer_payload = payload.get("pointer")
    pointer: Optional[dict[str, float]] = None
    if isinstance(pointer_payload, dict):
        raw_x = pointer_payload.get("client_x")
        if raw_x is None:
            raw_x = pointer_payload.get("clientX")
        raw_y = pointer_payload.get("client_y")
        if raw_y is None:
            raw_y = pointer_payload.get("clientY")
        try:
            client_x = float(raw_x)
            client_y = float(raw_y)
            if math.isfinite(client_x) and math.isfinite(client_y):
                pointer = {"client_x": client_x, "client_y": client_y}
        except (TypeError, ValueError):
            pointer = None

    try:
        timestamp_value = int(float(payload.get("timestamp")))
    except (TypeError, ValueError, OverflowError):
        timestamp_value = int(now_ms if now_ms is not None else time.time() * 1000)
    if timestamp_value <= 0:
        timestamp_value = int(now_ms if now_ms is not None else time.time() * 1000)

    raw_text_context = get_avatar_interaction_payload_value(
        payload, "text_context", "textContext", ""
    )
    text_context = (
        sanitize_text_context(raw_text_context)
        if sanitize_text_context is not None
        else str(raw_text_context or "").strip()
    )

    return {
        "interaction_id": interaction_id,
        "tool_id": tool_id,
        "action_id": action_id,
        "target": "avatar",
        "text_context": text_context,
        "timestamp": timestamp_value,
        "intensity": intensity,
        "reward_drop": reward_drop,
        "easter_egg": easter_egg,
        "touch_zone": touch_zone,
        "pointer": pointer,
    }
