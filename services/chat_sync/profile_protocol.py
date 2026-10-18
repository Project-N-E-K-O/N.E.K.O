"""Portable v1 static character profiles, separate from chat and memory projections.

Only original user settings belong here. Clients must explicitly ask before
uploading or replacing local settings; this codec never derives effective prompts.
The fixed-key JSON representation is shared with the mobile and desktop codecs.
"""

import json
import re


MAX_PROFILE_BYTES = 65_536
RESERVED_PROFILE_FIELD_KEYS = frozenset((
    "_reserved", "character_id", "live2d", "voice_id", "system_prompt", "model_type",
    "live3d_sub_type", "vrm", "vrm_animation", "lighting", "vrm_rotation", "live2d_item_id",
    "live2d_idle_animation", "item_id", "idleAnimation", "idleAnimations", "mmd",
    "mmd_animation", "mmd_idle_animation", "mmd_idle_animations", "touch_set", "_field_order",
    "原始数据", "文件路径", "创意工坊物品ID", "description", "tags", "name", "描述", "标签", "关键词",
    "ownerId", "characterSyncId", "deviceId", "bindingId", "accessToken", "apiKey", "contextText",
))
_PROFILE_KEYS = frozenset(("schemaVersion", "name", "nickname", "systemPrompt", "fields", "persona"))
_PERSONA_KEYS = frozenset(("presetId", "promptGuidance", "fields"))


def _text(value, *, short=False, nonempty=False):
    """Keep exact strings while bounding portable names/keys and rejecting surrogates."""
    if not isinstance(value, str) or (nonempty and not value):
        raise ValueError("invalid_character_profile")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("invalid_character_profile") from None
    if short and (len(value) > 200 or len(encoded) > 800):
        raise ValueError("invalid_character_profile")


def _fields(value):
    """Preserve ordered user fields; reject system keys rather than silently dropping them."""
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("invalid_character_profile")
    seen = set()
    for item in value:
        if not isinstance(item, dict) or item.keys() != {"key", "value"}:
            raise ValueError("invalid_character_profile")
        key = item["key"]
        _text(key, short=True, nonempty=True)
        _text(item["value"])
        if key.startswith("_") or key in RESERVED_PROFILE_FIELD_KEYS or key in seen:
            raise ValueError("invalid_character_profile")
        seen.add(key)


def encode_character_profile(value: object) -> bytes:
    """Validate the complete static profile and return its exact canonical UTF-8 bytes."""
    if not isinstance(value, dict) or value.keys() != _PROFILE_KEYS:
        raise ValueError("invalid_character_profile")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ValueError("invalid_character_profile")
    _text(value["name"], short=True, nonempty=True)
    _text(value["nickname"], short=True)
    _text(value["systemPrompt"])
    _fields(value["fields"])
    persona = value["persona"]
    if persona is not None:
        if not isinstance(persona, dict) or persona.keys() != _PERSONA_KEYS:
            raise ValueError("invalid_character_profile")
        preset = persona["presetId"]
        if not isinstance(preset, str) or not re.fullmatch(r"[A-Za-z0-9_-]{0,128}", preset):
            raise ValueError("invalid_character_profile")
        _text(persona["promptGuidance"])
        _fields(persona["fields"])
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_PROFILE_BYTES:
        raise ValueError("invalid_character_profile")
    return encoded


def decode_character_profile(payload: bytes) -> dict:
    """Reject noncanonical bytes, duplicate JSON keys and alternate Unicode escapes."""
    if not isinstance(payload, bytes) or len(payload) > MAX_PROFILE_BYTES:
        raise ValueError("invalid_character_profile")
    try:
        value = json.loads(payload.decode("utf-8"))
        if encode_character_profile(value) != payload:
            raise ValueError("non_canonical_character_profile")
        return value
    except (json.JSONDecodeError, UnicodeError, RecursionError, TypeError):
        raise ValueError("invalid_character_profile") from None
