"""Recent audit ring for Neko Roast."""

from __future__ import annotations

import math
import re
from typing import Any

from ..core.contracts import utc_now_iso
from ..core.contracts_public import is_sensitive_public_key, public_topic_material

_MAX_TEXT = 240
_MAX_DEPTH = 4
_ALLOWED_LEVELS = {"debug", "info", "warning", "error"}
_SENSITIVE_AUTH_RE = re.compile(r"\bauthorization\s*:\s*(?:bearer|basic)?\s*[^\s;,]+", re.IGNORECASE)
_SENSITIVE_COOKIE_HEADER_RE = re.compile(r"\bcookie\s*:\s*[^\r\n]*", re.IGNORECASE)
_SENSITIVE_TEXT_RE = re.compile(
    r"\b(?:cookie|token|access_token|refresh_token|signature|webcast_sign|ttwid|odin_tt|sessionid|"
    r"sessdata|bili_jct|dedeuserid|buvid3|x-tt-token)\b\s*[:=]\s*[^;\s,&]+",
    re.IGNORECASE,
)


class AuditStore:
    def __init__(self, limit: int = 100) -> None:
        self.limit = max(1, limit)
        self._events: list[dict[str, Any]] = []

    def set_limit(self, limit: int) -> None:
        self.limit = max(1, limit)
        if len(self._events) > self.limit:
            self._events = self._events[-self.limit :]

    def record(self, op: str, message: str, *, level: str = "info", detail: dict[str, Any] | None = None) -> None:
        item = {
            "at": utc_now_iso(),
            "op": _safe_text(op) or "unknown",
            "level": _safe_level(level),
            "message": _safe_text(message),
            "detail": _safe_detail(detail),
        }
        self._events.append(item)
        if len(self._events) > self.limit:
            self._events = self._events[-self.limit :]

    def recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        cap = limit or self.limit
        return list(reversed(self._events[-cap:]))


def _safe_level(value: Any) -> str:
    text = _safe_text(value).lower()
    return text if text in _ALLOWED_LEVELS else "info"


def _safe_detail(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return _safe_public_dict(value, depth=0)


def _safe_public_dict(value: dict[Any, Any], *, depth: int) -> dict[str, Any]:
    if depth >= _MAX_DEPTH:
        return {}
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            continue
        key = _safe_text(raw_key)
        if not key:
            continue
        if is_sensitive_public_key(raw_key) or "[redacted]" in key:
            result[key] = "[redacted]"
            continue
        if key == "topic_material":
            result[key] = public_topic_material(raw_value, depth=depth + 1)
        else:
            result[key] = _safe_public_value(raw_value, depth=depth + 1)
    return result


def _safe_public_value(value: Any, *, depth: int) -> Any:
    if depth >= _MAX_DEPTH:
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
        return _safe_text(value)
    if isinstance(value, dict):
        return _safe_public_dict(value, depth=depth)
    if isinstance(value, (list, tuple)):
        return [_safe_public_value(item, depth=depth + 1) for item in value[:20]]
    return ""


def _safe_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = _SENSITIVE_COOKIE_HEADER_RE.sub("[redacted]", value)
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if not text:
        return ""
    text = _SENSITIVE_AUTH_RE.sub("[redacted]", text)
    text = _SENSITIVE_TEXT_RE.sub("[redacted]", text)
    if len(text) > _MAX_TEXT:
        return text[: _MAX_TEXT - 1] + "..."
    return text
