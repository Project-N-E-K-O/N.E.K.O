"""Output cooldown helpers for the live safety guard."""

from __future__ import annotations

import time
from typing import Any

from .contracts import SafetyDecision, ViewerEvent


THROTTLED_SUPPORT_EVENT_TYPES = {"gift"}
UNTHROTTLED_SUPPORT_EVENT_TYPES = {"super_chat", "guard"}
SUPPORT_EVENT_COOLDOWN_SECONDS = 1.5


def _support_event_type(event: ViewerEvent | None) -> str:
    if event is None or not isinstance(event.raw, dict):
        return ""
    value = event.raw.get("event_type")
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower()
    return "super_chat" if normalized == "sc" else normalized


def before_output_cooldown(
    guard: Any, event: ViewerEvent | None = None
) -> SafetyDecision | None:
    if event is not None and event.source == "developer_sandbox":
        return None
    if guard.config.rate_limit_seconds <= 0:
        return None
    now = time.monotonic()
    support_type = _support_event_type(event)
    if support_type in THROTTLED_SUPPORT_EVENT_TYPES:
        last_support = float(getattr(guard, "_last_support_output_at", 0.0) or 0.0)
        if (now - last_support) < SUPPORT_EVENT_COOLDOWN_SECONDS:
            return SafetyDecision(False, guard.status(), "support event rate limited")
        guard._last_support_output_at = now
        return None
    if support_type in UNTHROTTLED_SUPPORT_EVENT_TYPES:
        return None
    if (now - guard._last_output_at) < guard.config.rate_limit_seconds:
        return SafetyDecision(False, guard.status(), "rate limited")
    guard._last_output_at = now
    return None


def output_cooldown_remaining(guard: Any, now: float | None = None) -> float:
    if guard.config.rate_limit_seconds <= 0:
        return 0.0
    current = time.monotonic() if now is None else now
    remaining = guard.config.rate_limit_seconds - (current - guard._last_output_at)
    return remaining if remaining > 0 else 0.0
