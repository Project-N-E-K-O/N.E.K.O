"""Event and skip-result helpers for warmup/idle hosting."""

from __future__ import annotations

from typing import Any

from .contracts import InteractionResult, PipelineStep, ViewerEvent


def idle_hosting_event(
    runtime: Any,
    live_state: dict[str, Any],
    host_beat: dict[str, Any],
) -> ViewerEvent:
    return ViewerEvent(
        uid="__neko_idle__",
        nickname="NEKO",
        danmaku_text="",
        source="idle_hosting",
        live_mode=runtime.config.live_mode,
        raw={
            "trigger": "manual_idle_hosting",
            "live_state": dict(live_state),
            "host_beat": host_beat,
        },
    )


def warmup_hosting_event(runtime: Any, live_state: dict[str, Any]) -> ViewerEvent:
    return ViewerEvent(
        uid="__neko_warmup__",
        nickname="NEKO",
        danmaku_text="",
        source="warmup_hosting",
        live_mode=runtime.config.live_mode,
        raw={
            "trigger": "auto_warmup_hosting",
            "live_state": dict(live_state),
        },
    )


def record_idle_hosting_skip(
    runtime: Any,
    event: ViewerEvent,
    reason: str,
) -> InteractionResult:
    return _record_hosting_skip(
        runtime,
        event,
        reason,
        audit_event="idle_hosting_skipped",
        gate_step_id="idle_hosting_gate",
    )


def record_warmup_hosting_skip(
    runtime: Any,
    event: ViewerEvent,
    reason: str,
) -> InteractionResult:
    return _record_hosting_skip(
        runtime,
        event,
        reason,
        audit_event="warmup_hosting_skipped",
        gate_step_id="warmup_hosting_gate",
    )


def _record_hosting_skip(
    runtime: Any,
    event: ViewerEvent,
    reason: str,
    *,
    audit_event: str,
    gate_step_id: str,
) -> InteractionResult:
    result = InteractionResult(
        accepted=False,
        status="skipped",
        event=event,
        reason=reason,
        steps=[PipelineStep(gate_step_id, "skipped", reason)],
    )
    runtime.audit.record(
        audit_event,
        reason,
        level="info",
        detail={"mode": runtime.config.live_mode},
    )
    runtime.record_result(result)
    return result
