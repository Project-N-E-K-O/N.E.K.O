"""Pure routing rules for live/sandbox pipeline events."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ViewerEvent

ENTRANCE_ROAST_MIN_INTERVAL_SECONDS = 45.0
ENTRANCE_ROAST_INTERVAL_BY_ACTIVITY = {
    "quiet": 75.0,
    "standard": ENTRANCE_ROAST_MIN_INTERVAL_SECONDS,
    "active": 30.0,
}


@dataclass(frozen=True)
class PipelineRoute:
    response_module_id: str
    viewer_gate_reason: str
    should_mark_roasted: bool


def entrance_pacing_interval_seconds(activity_level: str) -> float:
    level = str(activity_level or "standard")
    return ENTRANCE_ROAST_INTERVAL_BY_ACTIVITY.get(
        level,
        ENTRANCE_ROAST_MIN_INTERVAL_SECONDS,
    )


def is_live_danmaku_with_text(event: ViewerEvent) -> bool:
    return event.source in {"live_danmaku", "manual_live_simulation"} and bool(
        (event.danmaku_text or "").strip()
    )


def is_transient_event(event: ViewerEvent) -> bool:
    return event.source in {
        "developer_sandbox",
        "idle_hosting",
        "active_engagement",
        "warmup_hosting",
    }


def support_event_type(event: ViewerEvent) -> str:
    raw = event.raw if isinstance(event.raw, dict) else {}
    event_type = str(raw.get("event_type") or "").strip().lower()
    if event_type == "sc":
        return "super_chat"
    if event_type in {"gift", "guard", "super_chat"}:
        return event_type
    return ""


def is_repeat_live_danmaku(
    event: ViewerEvent, *, has_uid_lock: bool, already_roasted: bool
) -> bool:
    return has_uid_lock and is_live_danmaku_with_text(event) and already_roasted


def is_entrance_paced_live_danmaku(
    event: ViewerEvent,
    *,
    has_uid_lock: bool,
    already_roasted: bool,
    entrance_pacing_active: bool,
) -> bool:
    return (
        has_uid_lock
        and is_live_danmaku_with_text(event)
        and event.live_mode == "solo_stream"
        and not already_roasted
        and entrance_pacing_active
    )


def route_for_event(
    event: ViewerEvent,
    *,
    is_transient_event_result: bool,
    has_uid_lock: bool,
    already_roasted: bool,
    entrance_pacing_active: bool,
    active_hook_answer: bool = False,
    avatar_roast_allowed: bool = True,
    avatar_roast_burst_active: bool = False,
    avatar_roast_batch_welcome: bool = False,
) -> PipelineRoute:
    support_type = support_event_type(event)
    if support_type:
        return PipelineRoute("live_support_events", f"support_event.{support_type}", False)
    if event.source == "warmup_hosting":
        return PipelineRoute("warmup_hosting", "warmup_hosting", False)
    if event.source == "active_engagement":
        return PipelineRoute("active_engagement", "active_engagement", False)
    if event.source == "idle_hosting":
        return PipelineRoute("idle_hosting", "idle_hosting", False)
    if active_hook_answer:
        return PipelineRoute("danmaku_response", "active_hook_answer", False)
    if is_repeat_live_danmaku(
        event,
        has_uid_lock=has_uid_lock,
        already_roasted=already_roasted,
    ):
        return PipelineRoute("danmaku_response", "repeat_danmaku", False)
    if is_entrance_paced_live_danmaku(
        event,
        has_uid_lock=has_uid_lock,
        already_roasted=already_roasted,
        entrance_pacing_active=entrance_pacing_active,
    ):
        return PipelineRoute("danmaku_response", "entrance_pacing", True)
    if is_live_danmaku_with_text(event) and not avatar_roast_allowed:
        if avatar_roast_burst_active and avatar_roast_batch_welcome:
            return PipelineRoute("danmaku_response", "batch_welcome", False)
        return PipelineRoute("danmaku_response", "avatar_roast_pressure", False)
    return PipelineRoute("avatar_roast", "", not is_transient_event_result)
