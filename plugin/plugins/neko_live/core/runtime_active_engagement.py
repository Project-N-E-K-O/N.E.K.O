"""Active engagement trigger flow for the runtime."""

from __future__ import annotations

from typing import Any

from .contracts import InteractionResult, PipelineStep, ViewerEvent


async def trigger_active_engagement(
    runtime: Any, *, automatic: bool = False
) -> InteractionResult:
    live_connection = runtime.live_connection_snapshot()
    live_status = runtime.live_status_summary(live_connection)
    health_rows = runtime.runtime_health_rows()
    live_state = runtime.live_state_summary(live_status, health_rows)
    active_status = runtime.active_engagement_status(live_status, live_state)
    skip_event = active_engagement_basic_event(runtime, live_state, automatic=automatic)

    if not bool(getattr(runtime.config, "active_engagement_enabled", True)):
        return record_active_engagement_skip(runtime, skip_event, "active_engagement.disabled")
    if runtime.config.live_mode != "solo_stream":
        return record_active_engagement_skip(runtime, skip_event, "active_engagement.not_solo_stream")
    state = str(live_state.get("state") or "")
    if state == "paused":
        return record_active_engagement_skip(runtime, skip_event, "active_engagement.paused")
    if state == "blocked":
        return record_active_engagement_skip(runtime, skip_event, "active_engagement.blocked")
    if state != "quiet" and not (state == "idle" and bool(active_status.get("candidate"))):
        return record_active_engagement_skip(runtime, skip_event, "active_engagement.not_quiet")
    if not bool(active_status.get("candidate")):
        return record_active_engagement_skip(runtime, skip_event, "active_engagement.not_candidate")
    if not bool(active_status.get("eligible")):
        reason = str(active_status.get("reason") or "not_eligible")
        return record_active_engagement_skip(runtime, skip_event, f"active_engagement.{reason}")
    if automatic:
        runtime._active_engagement_last_attempt_at = float(
            runtime._active_engagement_now()
        )
    event = await active_engagement_event(runtime, live_state, automatic=automatic)
    result = await runtime.pipeline.handle_event(event)
    if str(getattr(result, "status", "") or "") in {"pushed", "dry_run"}:
        runtime._active_engagement_last_attempt_at = float(runtime._active_engagement_now())
    return result


async def maybe_trigger_active_engagement(runtime: Any) -> InteractionResult | None:
    if not bool(getattr(runtime.config, "active_engagement_enabled", True)):
        return None
    now = float(runtime._active_engagement_now())
    if now - runtime._active_engagement_last_attempt_at < runtime._active_engagement_min_interval_seconds():
        return None
    live_connection = runtime.live_connection_snapshot()
    live_status = runtime.live_status_summary(live_connection)
    health_rows = runtime.runtime_health_rows()
    live_state = runtime.live_state_summary(live_status, health_rows)
    active_status = runtime.active_engagement_status(live_status, live_state)
    if not bool(active_status.get("eligible")):
        return None

    return await trigger_active_engagement(runtime, automatic=True)


async def active_engagement_event(
    runtime: Any, live_state: dict[str, Any], *, automatic: bool = False
) -> ViewerEvent:
    topic_material = await runtime._select_active_engagement_topic()
    event = active_engagement_basic_event(runtime, live_state, automatic=automatic)
    event.raw["topic_material"] = topic_material
    return event


def active_engagement_basic_event(
    runtime: Any, live_state: dict[str, Any], *, automatic: bool = False
) -> ViewerEvent:
    return ViewerEvent(
        uid="__neko_active__",
        nickname="NEKO",
        danmaku_text="",
        source="active_engagement",
        live_mode=runtime.config.live_mode,
        raw={
            "trigger": (
                "auto_active_engagement"
                if automatic
                else "manual_active_engagement"
            ),
            "live_state": dict(live_state),
        },
    )


def record_active_engagement_skip(runtime: Any, event: ViewerEvent, reason: str) -> InteractionResult:
    result = InteractionResult(
        accepted=False,
        status="skipped",
        event=event,
        reason=reason,
        steps=[PipelineStep("active_engagement_gate", "skipped", reason)],
    )
    runtime.audit.record("active_engagement_skipped", reason, level="info", detail={"mode": runtime.config.live_mode})
    runtime.record_result(result)
    return result
