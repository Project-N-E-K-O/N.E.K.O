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
        request = ctx.warmup_hosting.build_request(event, identity, profile)
    elif route.response_module_id == "active_engagement":
        request = ctx.active_engagement.build_request(event, identity, profile)
    elif route.response_module_id == "idle_hosting":
        # Idle hosting reuses the avatar roast request shape in this slice.
        request = ctx.avatar_roast.build_request(event, identity, profile)
    elif route.response_module_id == "live_support_events":
        request = ctx.live_support_events.build_request(event, identity, profile)
    elif route.response_module_id == "danmaku_response":
        request = ctx.danmaku_response.build_request(event, identity, profile)
    else:
        request = ctx.avatar_roast.build_request(event, identity, profile)
    # The pipeline route owns module identity. Visual availability is an input
    # capability and must never silently reclassify avatar_roast as a normal
    # danmaku response at the Dispatcher contract boundary.
    request.metadata["response_module_hint"] = route.response_module_id
    return request
