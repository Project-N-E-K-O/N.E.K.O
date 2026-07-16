"""Skip/reject result helpers for pipeline gates."""

from __future__ import annotations

from typing import Any

from .contracts import (
    InteractionRequest,
    InteractionResult,
    PipelineStep,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
)


def reject_missing_uid(
    ctx: Any, event: ViewerEvent, steps: list[PipelineStep]
) -> InteractionResult:
    steps.append(PipelineStep("input", "failed", "uid is required"))
    result = InteractionResult(
        False, "failed", event, reason="uid is required", steps=steps
    )
    ctx.audit.record("pipeline_rejected", "uid is required", level="warning")
    return result


def skip_permission(
    ctx: Any,
    event: ViewerEvent,
    steps: list[PipelineStep],
    reason: str,
) -> InteractionResult:
    steps.append(PipelineStep("permission_gate", "skipped", reason))
    result = InteractionResult(False, "skipped", event, reason=reason, steps=steps)
    ctx.audit.record(
        "pipeline_skipped",
        reason,
        level="info",
        detail={"source": event.source},
    )
    return result


def skip_stale_live_event(
    ctx: Any,
    event: ViewerEvent,
    identity: ViewerIdentity,
    profile: ViewerProfile,
    steps: list[PipelineStep],
    request: InteractionRequest | None = None,
) -> InteractionResult:
    reason = "live_session.stale"
    if not steps or steps[-1].id != "live_session":
        steps.append(PipelineStep("live_session", "skipped", reason))
    result = InteractionResult(
        False,
        "skipped",
        event,
        identity=identity,
        profile=profile,
        request=request,
        reason=reason,
        steps=steps,
    )
    ctx.audit.record(
        "stale_live_event_discarded",
        reason,
        level="info",
        detail={"uid": identity.uid, "source": event.source},
    )
    return result


def skip_before_event(
    ctx: Any,
    event: ViewerEvent,
    steps: list[PipelineStep],
    decision: Any,
    *,
    step_id: str = "safety_guard.before_event",
) -> InteractionResult:
    steps.append(PipelineStep(step_id, "skipped", decision.reason))
    result = InteractionResult(
        False, "skipped", event, reason=decision.reason, steps=steps
    )
    ctx.audit.record(
        "pipeline_safety_skip",
        decision.reason,
        level="warning",
        detail={"status": decision.status},
    )
    return result


def skip_already_roasted(
    ctx: Any,
    event: ViewerEvent,
    identity: ViewerIdentity,
    profile: ViewerProfile,
    steps: list[PipelineStep],
) -> InteractionResult:
    reason = "uid already roasted"
    steps.append(PipelineStep("viewer_gate", "skipped", reason))
    result = InteractionResult(
        False,
        "skipped",
        event,
        identity=identity,
        profile=profile,
        reason=reason,
        steps=steps,
    )
    ctx.audit.record(
        "pipeline_skipped",
        reason,
        level="info",
        detail={"uid": identity.uid},
    )
    return result


def skip_module_disabled(
    ctx: Any,
    event: ViewerEvent,
    identity: ViewerIdentity,
    profile: ViewerProfile,
    steps: list[PipelineStep],
    module_id: str,
) -> InteractionResult:
    reason = f"{module_id}.disabled"
    steps.append(PipelineStep("module_gate", "skipped", reason))
    result = InteractionResult(
        False,
        "skipped",
        event,
        identity=identity,
        profile=profile,
        reason=reason,
        steps=steps,
    )
    ctx.audit.record(
        "pipeline_module_disabled",
        reason,
        level="info",
        detail={"source": event.source, "module": module_id},
    )
    ctx.record_result(result)
    return result


def skip_before_output(
    ctx: Any,
    event: ViewerEvent,
    identity: ViewerIdentity,
    profile: ViewerProfile,
    request: InteractionRequest,
    steps: list[PipelineStep],
    decision: Any,
) -> InteractionResult:
    steps.append(PipelineStep("safety_guard.before_output", "skipped", decision.reason))
    result = InteractionResult(
        False,
        "skipped",
        event,
        identity=identity,
        profile=profile,
        request=request,
        reason=decision.reason,
        steps=steps,
    )
    ctx.audit.record(
        "pipeline_output_skipped",
        decision.reason,
        level="warning",
        detail={"uid": identity.uid},
    )
    return result
