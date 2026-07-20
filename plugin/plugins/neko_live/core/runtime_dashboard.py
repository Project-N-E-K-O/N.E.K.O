"""Dashboard and health projections for the NEKO Live runtime."""

from __future__ import annotations

from typing import Any

from .runtime_dashboard_actions import dashboard_actions
from .runtime_dashboard_explain import live_explanation
from .runtime_health import runtime_health_rows


async def dashboard_state(runtime: Any) -> dict[str, Any]:
    profiles = await runtime.viewer_store.recent_profiles(runtime.config.recent_limit)
    storage = runtime.viewer_store.storage_status()
    live_connection = runtime.live_connection_snapshot()
    live_status = runtime.live_status_summary(live_connection)
    health_rows = runtime.runtime_health_rows()
    live_state = runtime.live_state_summary(live_status, health_rows)
    idle_hosting_status = runtime.idle_hosting_status(live_state)
    active_engagement_status = runtime.active_engagement_status(live_status, live_state)
    live_director_status = runtime.live_director_status(
        live_status,
        live_state,
        idle_hosting_status,
        active_engagement_status,
    )
    solo_test_readiness = runtime.solo_test_readiness(
        live_status,
        live_state,
        live_director_status,
        profile_count=len(profiles),
    )
    speech_explanation = runtime.speech_explanation(live_status, live_state)
    return {
        "config": runtime.config.to_public_dict(),
        "live_connection": live_connection,
        "live_status": live_status,
        "live_state": live_state,
        "idle_hosting_status": idle_hosting_status,
        "active_engagement_status": active_engagement_status,
        "live_director_status": live_director_status,
        "solo_test_readiness": solo_test_readiness,
        "speech_explanation": speech_explanation,
        "live_explain": live_explanation(
            runtime,
            profiles=profiles,
            health_rows=health_rows,
            live_status=live_status,
            live_state=live_state,
            live_director_status=live_director_status,
            speech_explanation=speech_explanation,
        ),
        "store_enabled": bool(storage.get("writable")),
        "viewer_store": storage,
        "modules": runtime.registry.snapshot(),
        "safety": runtime.safety_guard.snapshot(),
        "recent_profiles": profiles,
        "live_session": runtime.live_audience_session.snapshot(),
        "recent_results": list(reversed(runtime.recent_results)),
        "recent_sandbox_results": list(reversed(runtime.recent_sandbox_results)),
        "recent_audit": runtime.audit.recent(runtime.config.recent_limit),
        "avatar_cache": runtime.avatar_cache.status(),
        "health_rows": health_rows,
        "actions": runtime.dashboard_actions(),
    }
