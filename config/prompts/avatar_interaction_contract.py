"""Validation contract for the supported avatar interaction tools.

Prompt wording and memory templates intentionally stay in
``prompts_avatar_interaction.py``.  This module owns only wire-level facts so
the runtime validator has one authoritative interaction contract.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any, Optional


TextContextSanitizer = Callable[[Any], str]

AVATAR_INTERACTION_ALLOWED_TOUCH_ZONES = frozenset({"ear", "head", "face", "body"})
AVATAR_INTERACTION_ROUND_GESTURES = frozenset({"rock", "scissors", "paper"})
AVATAR_INTERACTION_ROUND_RESULTS = frozenset({"user_win", "avatar_win", "draw"})
AVATAR_INTERACTION_TOOL_CONTRACT = {
    "lollipop": {
        "actions": {
            "offer": frozenset({"normal"}),
            "tease": frozenset({"normal"}),
            "tap_soft": frozenset({"rapid", "burst"}),
        },
        "touch_zone": False,
        "boolean_field": None,
        "round_choice": False,
    },
    "fist": {
        "actions": {
            "poke": frozenset({"normal", "rapid"}),
        },
        "touch_zone": True,
        "boolean_field": "reward_drop",
        "round_choice": False,
    },
    "hammer": {
        "actions": {
            "bonk": frozenset({"normal", "rapid", "burst", "easter_egg"}),
        },
        "touch_zone": True,
        "boolean_field": "easter_egg",
        "round_choice": False,
    },
    "rps": {
        "actions": {},
        "touch_zone": False,
        "boolean_field": None,
        "round_choice": True,
    },
}

AVATAR_INTERACTION_TOUCH_ZONE_TOOLS = frozenset(
    tool_id
    for tool_id, tool_contract in AVATAR_INTERACTION_TOOL_CONTRACT.items()
    if tool_contract["touch_zone"]
)


def normalize_avatar_interaction_intensity(
    tool_id: str, action_id: str, intensity: Any
) -> Optional[str]:
    normalized = str(intensity or "").strip().lower()
    tool_contract = AVATAR_INTERACTION_TOOL_CONTRACT.get(tool_id)
    allowed = tool_contract and tool_contract["actions"].get(action_id)
    if not allowed or normalized not in allowed:
        return None
    return normalized


def parse_avatar_interaction_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return None


def get_avatar_interaction_payload_value(
    payload: dict, snake_key: str, camel_key: str, default: Any = None
) -> Any:
    if snake_key in payload and payload.get(snake_key) is not None:
        return payload.get(snake_key)
    if camel_key in payload and payload.get(camel_key) is not None:
        return payload.get(camel_key)
    return default


def resolve_avatar_interaction_round_result(
    user_gesture: str, avatar_gesture: str
) -> Optional[str]:
    if user_gesture == avatar_gesture and user_gesture in AVATAR_INTERACTION_ROUND_GESTURES:
        return "draw"
    if (
        (user_gesture, avatar_gesture)
        in {("rock", "scissors"), ("scissors", "paper"), ("paper", "rock")}
    ):
        return "user_win"
    if (
        (avatar_gesture, user_gesture)
        in {("rock", "scissors"), ("scissors", "paper"), ("paper", "rock")}
    ):
        return "avatar_win"
    return None


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
    if tool_contract["round_choice"]:
        allowed_round_keys = {
            "action", "interaction_id", "interactionId", "tool_id", "toolId", "target",
            "pointer", "timestamp", "text_context", "textContext",
            "user_gesture", "userGesture", "avatar_gesture", "avatarGesture",
            "round_result", "roundResult",
        }
        if any(key not in allowed_round_keys for key in payload):
            return None
        user_gesture = str(get_avatar_interaction_payload_value(
            payload, "user_gesture", "userGesture", ""
        ) or "").strip().lower()
        avatar_gesture = str(get_avatar_interaction_payload_value(
            payload, "avatar_gesture", "avatarGesture", ""
        ) or "").strip().lower()
        round_result = str(get_avatar_interaction_payload_value(
            payload, "round_result", "roundResult", ""
        ) or "").strip().lower()
        expected_result = resolve_avatar_interaction_round_result(
            user_gesture, avatar_gesture
        )
        if (
            user_gesture not in AVATAR_INTERACTION_ROUND_GESTURES
            or avatar_gesture not in AVATAR_INTERACTION_ROUND_GESTURES
            or round_result not in AVATAR_INTERACTION_ROUND_RESULTS
            or round_result != expected_result
        ):
            return None
    else:
        if action_id not in tool_contract["actions"]:
            return None
        intensity = normalize_avatar_interaction_intensity(
            tool_id, action_id, payload.get("intensity")
        )
        if intensity is None:
            return None
    boolean_field = tool_contract["boolean_field"]
    boolean_value = False
    if boolean_field:
        field_parts = boolean_field.split("_")
        camel_field = field_parts[0] + "".join(
            part.capitalize() for part in field_parts[1:]
        )
        carries_boolean_field = boolean_field in payload or camel_field in payload
        if carries_boolean_field:
            parsed_boolean = parse_avatar_interaction_bool(
                get_avatar_interaction_payload_value(
                    payload, boolean_field, camel_field, None
                )
            )
            if parsed_boolean is None:
                return None
            boolean_value = parsed_boolean
    reward_drop = boolean_value if boolean_field == "reward_drop" else False
    easter_egg = boolean_value if boolean_field == "easter_egg" else False
    if tool_id == "hammer" and easter_egg != (intensity == "easter_egg"):
        return None

    raw_touch_zone_value = get_avatar_interaction_payload_value(
        payload, "touch_zone", "touchZone", None
    )
    carries_touch_zone = "touch_zone" in payload or "touchZone" in payload
    raw_touch_zone = str(raw_touch_zone_value or "").strip().lower()
    if tool_contract["touch_zone"]:
        if raw_touch_zone not in AVATAR_INTERACTION_ALLOWED_TOUCH_ZONES:
            return None
        touch_zone = raw_touch_zone
    else:
        if carries_touch_zone:
            return None
        touch_zone = ""

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

    normalized = {
        "interaction_id": interaction_id,
        "tool_id": tool_id,
        "target": "avatar",
        "text_context": text_context,
        "timestamp": timestamp_value,
        "pointer": pointer,
    }
    if tool_contract["round_choice"]:
        normalized.update({
            "user_gesture": user_gesture,
            "avatar_gesture": avatar_gesture,
            "round_result": round_result,
        })
    else:
        normalized.update({
            "action_id": action_id,
            "intensity": intensity,
            "reward_drop": reward_drop,
            "easter_egg": easter_egg,
            "touch_zone": touch_zone,
        })
    return normalized
