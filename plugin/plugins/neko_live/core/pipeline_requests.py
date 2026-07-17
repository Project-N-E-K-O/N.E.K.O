"""Request construction for routed pipeline events."""

from __future__ import annotations

from typing import Any

from .contracts import InteractionRequest, ViewerEvent, ViewerIdentity, ViewerProfile
from .pipeline_routing import PipelineRoute


def build_request_for_route(
    ctx: Any,
    route: PipelineRoute,
    event: ViewerEvent,
    identity: ViewerIdentity,
    profile: ViewerProfile,
) -> InteractionRequest:
    if route.response_module_id == "warmup_hosting":
        return ctx.warmup_hosting.build_request(event, identity, profile)
    if route.response_module_id == "active_engagement":
        return ctx.active_engagement.build_request(event, identity, profile)
    if route.response_module_id == "idle_hosting":
        # Idle hosting reuses the avatar roast request shape in this slice.
        return ctx.avatar_roast.build_request(event, identity, profile)
    if route.response_module_id == "live_support_events":
        return ctx.live_support_events.build_request(event, identity, profile)
    if route.response_module_id == "danmaku_response":
        return ctx.danmaku_response.build_request(event, identity, profile)
    return ctx.avatar_roast.build_request(event, identity, profile)
