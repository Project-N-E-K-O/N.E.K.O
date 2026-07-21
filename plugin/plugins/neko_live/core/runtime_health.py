"""Runtime health-row projections for the NEKO Live dashboard."""

from __future__ import annotations

from typing import Any

from .contracts_public import public_text


def runtime_health_rows(runtime: Any) -> list[dict[str, Any]]:
    ingest = _module_status(getattr(runtime, "live_provider", None))
    event_bus = _module_status(runtime.event_bus)
    selection = _module_status(runtime.live_events)
    latest = runtime.recent_results[-1] if runtime.recent_results else {}
    latest_status = _public_text(latest.get("status")) if isinstance(latest, dict) else ""
    latest_reason = _public_text(latest.get("reason")) if isinstance(latest, dict) else ""
    latest_age = (
        runtime._iso_age_sec(latest.get("created_at"))
        if isinstance(latest, dict)
        else None
    )
    latest_latency = _public_non_negative_number(latest.get("response_latency_ms")) if isinstance(latest, dict) else None
    steps = latest.get("steps") if isinstance(latest, dict) else []
    dispatcher_step = None
    if isinstance(steps, list):
        dispatcher_step = next(
            (
                step
                for step in reversed(steps)
                if isinstance(step, dict) and step.get("id") == "neko_dispatcher"
            ),
            None,
        )
    dispatcher_outcome = latest_status if dispatcher_step else ""
    safety_state = runtime.safety_guard.status()
    config_status = (
        "failed"
        if runtime._config_last_error
        else ("healthy" if runtime._config_last_persist_at else "idle")
    )
    output_channel = runtime.dispatcher.output_channel_status()
    output_channel_ready = bool(output_channel.get("ready"))
    output_channel_reason = _public_text(output_channel.get("reason"))
    output_channel_detail = _public_text(output_channel.get("detail"))
    event_bus_count = _public_non_negative_int(event_bus.get("publish_count"))
    selection_count = _public_non_negative_int(selection.get("last_candidate_count"))
    live_signal_at = getattr(runtime, "_last_live_danmaku_seen_at", 0)
    live_signal_outcome = getattr(runtime, "_last_live_danmaku_seen_type", "")
    return [
        {
            "id": "live_ingest",
            "stage": "ingest",
            "status": "healthy" if ingest.get("last_event_at") else "idle",
            "age_sec": runtime._age_sec(ingest.get("last_event_at")),
            "last_outcome": _public_text(ingest.get("last_event_type")),
            "last_published_outcome": _public_text(ingest.get("last_published_event_type")),
            "last_status_only_outcome": _public_text(ingest.get("last_status_only_event_type")),
        },
        {
            "id": "event_bus",
            "stage": "event_bus",
            "status": "healthy" if event_bus_count else "idle",
            "count": event_bus_count,
            "age_sec": runtime._age_sec(event_bus.get("last_publish_at")),
            "last_outcome": _public_text(event_bus.get("last_event_type")),
        },
        {
            "id": "selection",
            "stage": "selection",
            "status": "healthy" if selection.get("last_decision_at") and selection_count else "idle",
            "count": selection_count,
            "age_sec": runtime._age_sec(selection.get("last_decision_at")),
            "last_outcome": _public_text(selection.get("last_selected_type")),
            "last_skip_reason": _public_text(selection.get("last_skip_reason")),
            "reply_selection_policy": _public_text(selection.get("reply_selection_policy")),
        },
        {
            "id": "live_signal",
            "stage": "live_signal",
            "status": "healthy" if live_signal_at else "idle",
            "age_sec": runtime._age_sec(live_signal_at),
            "last_outcome": _public_text(live_signal_outcome),
        },
        {
            "id": "pipeline",
            "stage": "pipeline",
            "status": _status_from_outcome(latest_status),
            "age_sec": latest_age,
            "last_outcome": latest_status,
            "last_skip_reason": (
                latest_reason
                if latest_status in {"dry_run", "skipped", "failed"}
                else ""
            ),
            "last_latency_ms": latest_latency,
        },
        {
            "id": "safety_guard",
            "stage": "safety_guard",
            "status": (
                "healthy"
                if safety_state == "running"
                else ("degraded" if safety_state == "degraded" else "blocked")
            ),
            "current_state": safety_state,
            "cooldown_remaining": round(
                float(runtime.safety_guard.output_cooldown_remaining()), 1
            ),
        },
        {
            "id": "dispatcher",
            "stage": "dispatcher",
            "status": (
                "blocked"
                if not output_channel_ready
                else _status_from_outcome(dispatcher_outcome)
            ),
            "age_sec": latest_age if dispatcher_step else None,
            "last_outcome": dispatcher_outcome,
            "last_skip_reason": _dispatcher_skip_reason(
                output_channel_ready=output_channel_ready,
                output_channel_reason=output_channel_reason,
                dispatcher_outcome=dispatcher_outcome,
                latest_reason=latest_reason,
            ),
            "last_latency_ms": latest_latency if dispatcher_step else None,
            "output_channel_ready": output_channel_ready,
            "output_channel_detail": output_channel_detail,
        },
        {
            "id": "config_store",
            "stage": "config_store",
            "status": config_status,
            "age_sec": runtime._age_sec(runtime._config_last_persist_at),
            "last_error": _public_text(runtime._config_last_error),
        },
    ]


def _status_from_outcome(outcome: str) -> str:
    if outcome == "failed":
        return "failed"
    if outcome == "skipped":
        return "blocked"
    if outcome in {"dry_run", "pushed", "ok"}:
        return "healthy"
    return "idle"


def _module_status(module: Any) -> dict[str, Any]:
    status = getattr(module, "status", None)
    if not callable(status):
        return {}
    try:
        data = status()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _public_text(value: Any, *, limit: int = 200) -> str:
    return public_text(value, max_len=limit)


def _public_non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, str):
        text = value.strip()
        return int(text) if text.isdigit() else 0
    return 0


def _public_non_negative_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return value if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        return int(text) if text.isdigit() else None
    return None


def _dispatcher_skip_reason(
    *,
    output_channel_ready: bool,
    output_channel_reason: str,
    dispatcher_outcome: str,
    latest_reason: str,
) -> str:
    if not output_channel_ready:
        return output_channel_reason or "output_channel_unavailable"
    if dispatcher_outcome in {"dry_run", "skipped", "failed"}:
        return latest_reason
    return ""
