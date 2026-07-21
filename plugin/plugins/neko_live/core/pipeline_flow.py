"""Main post-safety flow for routed pipeline events."""

from __future__ import annotations

import asyncio
from typing import Any

from .contracts import InteractionResult, PipelineStep, ViewerEvent
from .pipeline_dispatch import dispatch_routed_request
from .pipeline_requests import build_request_for_route
from .pipeline_results import (
    fail_pipeline,
    skip_already_roasted,
    skip_module_disabled,
    skip_stale_live_event,
)
from .pipeline_viewers import resolve_viewer_context
from .runtime_timeline import record_timeline
from .runtime_live_session import is_current_live_session_event


async def run_event_flow(
    pipeline: Any,
    event: ViewerEvent,
    steps: list[PipelineStep],
) -> InteractionResult:
    ctx = pipeline.ctx
    session = pipeline.session
    try:
        is_transient_event = pipeline._is_transient_event(event)
        viewer = await resolve_viewer_context(
            ctx,
            event,
            steps,
            is_transient_event=is_transient_event,
        )
        identity = viewer.identity
        profile = viewer.profile
        if not viewer.session_current:
            return skip_stale_live_event(ctx, event, identity, profile, steps)

        uid_lock: asyncio.Lock | None = None
        needs_session_gate = pipeline._is_live_danmaku_with_text(event)
        if session.needs_uid_lock(
            ctx.config,
            is_live_danmaku_with_text=needs_session_gate,
            is_transient_event=is_transient_event,
        ):
            uid_lock = await session.acquire_uid_lock(identity.uid)
        try:
            if not is_current_live_session_event(ctx, event):
                return skip_stale_live_event(ctx, event, identity, profile, steps)
            already_roasted = False
            if uid_lock is not None:
                already_roasted = await session.already_roasted(
                    ctx,
                    event,
                    identity.uid,
                )
            has_uid_lock = uid_lock is not None
            route = pipeline._route_for_event(
                event,
                is_transient_event=is_transient_event,
                has_uid_lock=has_uid_lock,
                already_roasted=already_roasted,
            )
            module_flag = {
                "avatar_roast": "avatar_roast_enabled",
                "danmaku_response": "danmaku_response_enabled",
                "live_support_events": "live_support_events_enabled",
                "warmup_hosting": "warmup_hosting_enabled",
                "idle_hosting": "idle_hosting_enabled",
                "active_engagement": "active_engagement_enabled",
            }.get(route.response_module_id)
            if module_flag and not bool(getattr(ctx.config, module_flag, True)):
                record_timeline(
                    ctx,
                    event,
                    stage="module_gate",
                    status="skipped",
                    reason=f"{route.response_module_id}.disabled",
                    route=route.response_module_id,
                )
                return skip_module_disabled(
                    ctx,
                    event,
                    identity,
                    profile,
                    steps,
                    route.response_module_id,
                )
            record_timeline(
                ctx,
                event,
                stage="pipeline.route",
                status="ok",
                reason=route.viewer_gate_reason,
                route=route.response_module_id,
            )
            if (
                uid_lock is not None
                and already_roasted
                and (
                    route.response_module_id != "danmaku_response"
                    or not hasattr(ctx, "danmaku_response")
                )
            ):
                record_timeline(
                    ctx,
                    event,
                    stage="viewer_gate",
                    status="skipped",
                    reason="already_roasted",
                    route=route.response_module_id,
                )
                return skip_already_roasted(ctx, event, identity, profile, steps)

            if route.viewer_gate_reason:
                steps.append(
                    PipelineStep("viewer_gate", "ok", route.viewer_gate_reason)
                )
            else:
                steps.append(PipelineStep("viewer_gate", "ok"))
            request = build_request_for_route(ctx, route, event, identity, profile)
            should_mark_roasted = route.should_mark_roasted
            response_module_id = route.response_module_id
            steps.append(PipelineStep(response_module_id, "ok"))
            record_timeline(
                ctx,
                event,
                stage="request.build",
                status="ok",
                reason=request.reason,
                route=response_module_id,
            )
            if should_mark_roasted and uid_lock is not None:
                session.claim_roasted(identity.uid)
                steps.append(
                    PipelineStep("viewer_gate.session_claim", "ok", response_module_id)
                )

            return await dispatch_routed_request(
                ctx,
                session,
                event=event,
                identity=identity,
                profile=profile,
                request=request,
                steps=steps,
                response_module_id=response_module_id,
                should_mark_roasted=should_mark_roasted,
                mark_avatar_roast_sent=pipeline._record_avatar_roast_sent,
            )
        finally:
            if uid_lock is not None:
                session.release_uid_lock(identity.uid, uid_lock)
    except Exception as exc:
        return fail_pipeline(ctx, event, steps, exc)
    finally:
        ctx.safety_guard.after_event()
