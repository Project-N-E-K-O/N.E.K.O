"""Failure result helpers for dispatcher and pipeline exceptions."""

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


def fail_dispatcher(
    ctx: Any,
    event: ViewerEvent,
    identity: ViewerIdentity,
    profile: ViewerProfile,
    request: InteractionRequest,
    steps: list[PipelineStep],
    exc: Exception,
    dispatcher_latency_ms: int | None = None,
) -> InteractionResult:
    message = f"output_failed:{type(exc).__name__}"
    ctx.safety_guard.record_failure("output", message)
    steps.append(PipelineStep("neko_dispatcher", "failed", message))
    result = InteractionResult(
        False,
        "failed",
        event,
        identity=identity,
        profile=profile,
        request=request,
        reason=message,
        steps=steps,
        dispatcher_latency_ms=dispatcher_latency_ms,
    )
    ctx.record_result(result)
    return result


def fail_pipeline(
    ctx: Any,
    event: ViewerEvent,
    steps: list[PipelineStep],
    exc: Exception,
) -> InteractionResult:
    message = f"pipeline_failed: {type(exc).__name__}"
    ctx.safety_guard.record_failure("pipeline", message)
    steps.append(PipelineStep("pipeline", "failed", message))
    result = InteractionResult(False, "failed", event, reason=message, steps=steps)
    ctx.audit.record("pipeline_failed", message, level="error")
    ctx.record_result(result)
    return result
