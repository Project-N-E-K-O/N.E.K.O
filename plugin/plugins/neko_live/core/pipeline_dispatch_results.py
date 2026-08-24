"""Dispatcher-stage result helpers for the roast pipeline."""

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


def dry_run_result(
    ctx: Any,
    event: ViewerEvent,
    identity: ViewerIdentity,
    profile: ViewerProfile,
    request: InteractionRequest,
    steps: list[PipelineStep],
    output: str,
    dispatcher_latency_ms: int | None = None,
) -> InteractionResult:
    steps.append(PipelineStep("neko_dispatcher", "dry_run", output))
    result = InteractionResult(
        False,
        "dry_run",
        event,
        identity=identity,
        profile=profile,
        request=request,
        output=output,
        reason="dispatcher.dry_run",
        steps=steps,
        dispatcher_latency_ms=dispatcher_latency_ms,
    )
    ctx.audit.record(
        "dispatcher_dry_run",
        "roast request completed as dry_run",
        detail={"uid": identity.uid, "source": event.source},
    )
    ctx.record_result(result)
    return result


def skip_dispatcher(
    ctx: Any,
    event: ViewerEvent,
    identity: ViewerIdentity,
    profile: ViewerProfile,
    request: InteractionRequest,
    steps: list[PipelineStep],
    output: str,
    dispatcher_latency_ms: int | None = None,
) -> InteractionResult:
    reason = request.reason or "dispatcher.skipped"
    steps.append(PipelineStep("neko_dispatcher", "skipped", output or reason))
    result = InteractionResult(
        False,
        "skipped",
        event,
        identity=identity,
        profile=profile,
        request=request,
        output=output,
        reason=reason,
        steps=steps,
        dispatcher_latency_ms=dispatcher_latency_ms,
    )
    ctx.audit.record(
        "dispatcher_skipped",
        reason,
        level="info",
        detail={"uid": identity.uid, "source": event.source},
    )
    ctx.record_result(result)
    return result


def pushed_result(
    ctx: Any,
    event: ViewerEvent,
    identity: ViewerIdentity,
    profile: ViewerProfile,
    request: InteractionRequest,
    steps: list[PipelineStep],
    output: str,
    dispatcher_latency_ms: int | None = None,
) -> InteractionResult:
    result = InteractionResult(
        True,
        "pushed",
        event,
        identity=identity,
        profile=profile,
        request=request,
        output=output,
        steps=steps,
        dispatcher_latency_ms=dispatcher_latency_ms,
    )
    ctx.audit.record(
        "pipeline_pushed",
        "roast request pushed",
        detail={"uid": identity.uid, "source": event.source},
    )
    ctx.record_result(result)
    return result
