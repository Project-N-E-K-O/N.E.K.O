"""Output dispatch stage for routed pipeline requests."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .contracts import (
    InteractionRequest,
    InteractionResult,
    PipelineStep,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
)
from .pipeline_results import (
    dry_run_result,
    fail_dispatcher,
    pushed_result,
    skip_before_output,
    skip_dispatcher,
    skip_stale_live_event,
)
from .runtime_live_session import is_current_live_session_event
from .runtime_timeline import record_timeline


async def dispatch_routed_request(
    ctx: Any,
    session: Any,
    *,
    event: ViewerEvent,
    identity: ViewerIdentity,
    profile: ViewerProfile,
    request: InteractionRequest,
    steps: list[PipelineStep],
    response_module_id: str,
    should_mark_roasted: bool,
    mark_avatar_roast_sent: Callable[[], None],
) -> InteractionResult:
    if not is_current_live_session_event(ctx, event):
        record_timeline(
            ctx,
            event,
            stage="live_session",
            status="skipped",
            reason="live_session.stale",
            route=response_module_id,
        )
        return skip_stale_live_event(
            ctx,
            event,
            identity,
            profile,
            steps,
            request=request,
        )

    output_decision = ctx.safety_guard.before_output(event)
    if not output_decision.allowed:
        record_timeline(
            ctx,
            event,
            stage="safety_guard.before_output",
            status="skipped",
            reason=output_decision.status,
            route=response_module_id,
        )
        return skip_before_output(
            ctx,
            event,
            identity,
            profile,
            request,
            steps,
            output_decision,
        )
    steps.append(
        PipelineStep("safety_guard.before_output", "ok", output_decision.status)
    )
    record_timeline(
        ctx,
        event,
        stage="safety_guard.before_output",
        status="ok",
        reason=output_decision.status,
        route=response_module_id,
    )

    dispatcher_start = time.perf_counter()
    dispatcher_latency_ms: int | None = None
    try:
        output = await ctx.dispatcher.push_roast(request)
    except Exception as exc:
        dispatcher_latency_ms = int(round((time.perf_counter() - dispatcher_start) * 1000))
        record_timeline(
            ctx,
            event,
            stage="dispatcher.push",
            status="failed",
            reason=type(exc).__name__,
            route=response_module_id,
        )
        return fail_dispatcher(
            ctx,
            event,
            identity,
            profile,
            request,
            steps,
            exc,
            dispatcher_latency_ms=dispatcher_latency_ms,
        )
    dispatcher_latency_ms = int(round((time.perf_counter() - dispatcher_start) * 1000))

    if request.dry_run:
        if should_mark_roasted:
            session.mark_dry_run_roasted(identity.uid)
        record_timeline(
            ctx,
            event,
            stage="dispatcher.push",
            status="dry_run",
            reason=str(output),
            route=response_module_id,
        )
        result = dry_run_result(
            ctx,
            event,
            identity,
            profile,
            request,
            steps,
            output,
            dispatcher_latency_ms=dispatcher_latency_ms,
        )
        if response_module_id == "avatar_roast":
            mark_avatar_roast_sent()
        return result

    if not request.should_push or str(output).startswith("skipped_to_neko"):
        record_timeline(
            ctx,
            event,
            stage="dispatcher.push",
            status="skipped",
            reason=str(output),
            route=response_module_id,
        )
        return skip_dispatcher(
            ctx,
            event,
            identity,
            profile,
            request,
            steps,
            output,
            dispatcher_latency_ms=dispatcher_latency_ms,
        )

    steps.append(PipelineStep("neko_dispatcher", "ok", output))
    record_timeline(
        ctx,
        event,
        stage="dispatcher.push",
        status="ok",
        reason=str(output),
        route=response_module_id,
    )
    if response_module_id == "avatar_roast":
        mark_avatar_roast_sent()
    if should_mark_roasted:
        session.claim_roasted(identity.uid)
        try:
            persisted = await ctx.viewer_profile.mark_roasted(identity.uid, output)
            if persisted is False:
                raise OSError("viewer profile persistence failed")
            profile.roast_count = int(profile.roast_count or 0) + 1
            profile.last_result = output
            steps.append(PipelineStep("viewer_profile.mark_roasted", "ok"))
        except Exception as exc:
            mark_message = f"mark_roasted_failed: {type(exc).__name__}"
            steps.append(
                PipelineStep("viewer_profile.mark_roasted", "failed", mark_message)
            )
            ctx.audit.record(
                "viewer_profile_mark_failed",
                mark_message,
                level="error",
                detail={"uid": identity.uid},
            )
    return pushed_result(
        ctx,
        event,
        identity,
        profile,
        request,
        steps,
        output,
        dispatcher_latency_ms=dispatcher_latency_ms,
    )
