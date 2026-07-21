"""Public projection helpers for NEKO Live contracts."""

from __future__ import annotations

import math
import re
from typing import Any

MAX_PUBLIC_TEXT = 240
MAX_PUBLIC_DEPTH = 4

TOPIC_PRIVACY_PUBLIC = "public"
TOPIC_PRIVACY_VIEWER_DERIVED = "viewer_derived"
TOPIC_PRIVACY_PRIVATE = "private"

_PUBLIC_TOPIC_SOURCES = {"fallback", "bili_trending"}
_VIEWER_DERIVED_TOPIC_SOURCES = {"recent_danmaku", "live_thread"}

_SENSITIVE_AUTH_RE = re.compile(r"\bauthorization\s*:\s*(?:bearer|basic)?\s*[^\s;,]+", re.IGNORECASE)
_SENSITIVE_COOKIE_HEADER_RE = re.compile(r"\bcookie\s*:\s*[^\r\n]*", re.IGNORECASE)
_SENSITIVE_KEY_RE = re.compile(
    r"^(?:authorization|proxy_authorization|cookie|set-cookie|credentials?|auth|authentication|"
    r"password|passwd|secret|client_secret|api_key|apikey|token|access_token|refresh_token|"
    r"signature|webcast_sign|ttwid|odin_tt|sessionid|sessdata|bili_jct|dedeuserid|buvid3|"
    r"x-tt-token|.+_(?:token|secret|password|cookie|signature))$",
    re.IGNORECASE,
)
_SENSITIVE_TEXT_RE = re.compile(
    r"\b(?:cookie|token|access_token|refresh_token|signature|webcast_sign|ttwid|odin_tt|sessionid|"
    r"sessdata|bili_jct|dedeuserid|buvid3|x-tt-token|password|passwd|secret|client_secret|"
    r"api_key|apikey|credentials?)\b\s*[:=]\s*[^;\s,&]+",
    re.IGNORECASE,
)


def public_text(value: Any, *, max_len: int = MAX_PUBLIC_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    text = _SENSITIVE_COOKIE_HEADER_RE.sub("[redacted]", value)
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if not text:
        return ""
    text = _SENSITIVE_AUTH_RE.sub("[redacted]", text)
    text = _SENSITIVE_TEXT_RE.sub("[redacted]", text)
    if len(text) > max_len:
        if max_len <= 0:
            return ""
        suffix = "..."
        if max_len <= len(suffix):
            return suffix[:max_len]
        return text[: max_len - len(suffix)] + suffix
    return text


def public_bool(value: Any, *, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def public_int(value: Any, *, default: int = 0, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    if minimum is not None and value < minimum:
        return minimum
    return value


def public_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return _public_dict(value, depth=0)


def is_sensitive_public_key(value: Any) -> bool:
    return isinstance(value, str) and _SENSITIVE_KEY_RE.fullmatch(value.strip()) is not None


def topic_privacy_classification(value: Any) -> str:
    if not isinstance(value, dict):
        return TOPIC_PRIVACY_PRIVATE
    source = value.get("source")
    if source in _VIEWER_DERIVED_TOPIC_SOURCES:
        return TOPIC_PRIVACY_VIEWER_DERIVED
    if source not in _PUBLIC_TOPIC_SOURCES:
        return TOPIC_PRIVACY_PRIVATE
    classification = value.get("privacy_classification")
    if classification in {
        TOPIC_PRIVACY_PUBLIC,
        TOPIC_PRIVACY_VIEWER_DERIVED,
        TOPIC_PRIVACY_PRIVATE,
    }:
        return classification
    return TOPIC_PRIVACY_PUBLIC


def public_topic_material(value: Any, *, depth: int = 0) -> dict[str, Any]:
    """Project topic material, failing private unless it is explicitly public."""

    if depth >= MAX_PUBLIC_DEPTH or not isinstance(value, dict):
        return {}
    projected = _public_dict(value, depth=depth)
    classification = topic_privacy_classification(value)
    projected["privacy_classification"] = classification
    if classification != TOPIC_PRIVACY_PUBLIC:
        for key in ("title", "key", "hook", "evidence", "interest", "keywords"):
            projected.pop(key, None)
    return projected


def public_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= MAX_PUBLIC_DEPTH:
        return None
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, str):
        return public_text(value)
    if isinstance(value, dict):
        return _public_dict(value, depth=depth)
    if isinstance(value, (list, tuple)):
        return [public_value(item, depth=depth + 1) for item in value[:20]]
    return ""


def _public_dict(value: dict[Any, Any], *, depth: int) -> dict[str, Any]:
    if depth >= MAX_PUBLIC_DEPTH:
        return {}
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            continue
        if is_sensitive_public_key(raw_key):
            key = public_text(raw_key)
            if key:
                result[key] = "[redacted]"
            continue
        key = public_text(raw_key)
        if not key:
            continue
        if "[redacted]" in key:
            result[key] = "[redacted]"
            continue
        if key == "topic_material":
            result[key] = public_topic_material(raw_value, depth=depth + 1)
        else:
            result[key] = public_value(raw_value, depth=depth + 1)
    return result
