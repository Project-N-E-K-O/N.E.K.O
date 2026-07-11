"""Public projection helpers for NEKO Live contracts."""

from __future__ import annotations

import math
import re
from typing import Any

MAX_PUBLIC_TEXT = 240
MAX_PUBLIC_DEPTH = 4

_SENSITIVE_AUTH_RE = re.compile(r"\bauthorization\s*:\s*(?:bearer|basic)?\s*[^\s;,]+", re.IGNORECASE)
_SENSITIVE_TEXT_RE = re.compile(
    r"\b(?:cookie|token|access_token|refresh_token|signature|webcast_sign|ttwid|odin_tt|sessionid|"
    r"sessdata|bili_jct|dedeuserid|buvid3|x-tt-token)\b\s*[:=]\s*[^;\s,&]+",
    re.IGNORECASE,
)


def public_text(value: Any, *, max_len: int = MAX_PUBLIC_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.replace("\r", " ").replace("\n", " ").split())
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
        key = public_text(raw_key)
        if not key:
            continue
        result[key] = public_value(raw_value, depth=depth + 1)
    return result
