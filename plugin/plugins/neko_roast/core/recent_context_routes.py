"""Route and event-signal helpers for recent live results."""

from __future__ import annotations

from typing import Any


_KNOWN_ROUTE_STEPS = {
    "danmaku_response",
    "avatar_roast",
    "idle_hosting",
    "active_engagement",
    "warmup_hosting",
    "live_support_events",
    "gift_signal",
    "super_chat_signal",
}


def route_from_result(result: dict[str, Any]) -> str:
    response_module = str(result.get("response_module") or "")
    if response_module:
        return response_module
    event = result.get("event") if isinstance(result.get("event"), dict) else {}
    source = str(event.get("source") or "")
    event_type = str(event.get("event_type") or "").strip().lower()
    if source in {"idle_hosting", "active_engagement", "warmup_hosting"}:
        return source
    steps = result.get("steps") if isinstance(result.get("steps"), list) else []
    for step in reversed(steps):
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "")
        if step_id in _KNOWN_ROUTE_STEPS:
            return step_id
    signal_route = signal_route_for_event_type(event_type)
    if signal_route:
        return signal_route
    return source or "unknown"


def signal_route_for_event_type(event_type: str) -> str:
    normalized = str(event_type or "").strip().lower()
    if normalized in {"gift", "guard"}:
        return "gift_signal"
    if normalized in {"super_chat", "sc"}:
        return "super_chat_signal"
    return ""


def event_signal_from_result(result: dict[str, Any]) -> str:
    event = result.get("event") if isinstance(result.get("event"), dict) else {}
    source = str(event.get("source") or "")
    if source != "live_danmaku":
        return source or "unknown"
    event_type = str(event.get("event_type") or "").strip().lower()
    if event_type in {"gift", "guard"}:
        return "gift_signal"
    if event_type in {"super_chat", "sc"}:
        return "super_chat_signal"
    return "danmaku_signal"
