"""Timing and age helpers for NEKO Live status calculations."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from . import recent_context


def activity_level(config: Any) -> str:
    return str(getattr(config, "activity_level", "standard"))


def active_engagement_min_interval_seconds(config: Any) -> float:
    return {
        "quiet": 300.0,
        "active": 45.0,
        "standard": 60.0,
    }.get(activity_level(config), 60.0)


def active_engagement_after_danmaku_interval_seconds(config: Any) -> float:
    return {
        "quiet": 210.0,
        "active": 30.0,
        "standard": 45.0,
    }.get(activity_level(config), 45.0)


def active_engagement_idle_grace_seconds(config: Any, default: float) -> float:
    return {
        "quiet": 45.0,
        "active": 15.0,
        "standard": float(default),
    }.get(activity_level(config), float(default))


def idle_hosting_min_interval_seconds(config: Any) -> float:
    return {
        "quiet": 180.0,
        "active": 45.0,
        "standard": 90.0,
    }.get(activity_level(config), 90.0)


def solo_warmup_timeout_seconds(config: Any, default: float) -> float:
    return {
        "quiet": 90.0,
        "active": 30.0,
        "standard": float(default),
    }.get(activity_level(config), float(default))


def live_state_threshold_seconds(config: Any, default_engaged: float, default_idle: float) -> tuple[float, float]:
    return {
        "quiet": (90.0, 300.0),
        "active": (30.0, 90.0),
        "standard": (float(default_engaged), float(default_idle)),
    }.get(activity_level(config), (float(default_engaged), float(default_idle)))


def age_sec(timestamp: Any) -> float | None:
    try:
        value = float(timestamp)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return round(max(0.0, time.time() - value), 1)


def iso_age_sec(value: Any) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return round(max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds()), 1)


IsoAgeFn = Callable[[Any], float | None]


def recent_live_danmaku_output_age_sec(recent_results: Any, iso_age_fn: IsoAgeFn = iso_age_sec) -> float | None:
    for result in reversed(list(recent_results or [])):
        if not isinstance(result, dict):
            continue
        if str(result.get("status") or "") != "pushed":
            continue
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        if str(event.get("source") or "") != "live_danmaku":
            continue
        route = recent_context.route_from_result(result)
        if route not in {"avatar_roast", "danmaku_response", "live_danmaku"}:
            continue
        age = iso_age_fn(result.get("created_at"))
        if age is not None:
            return float(age)
    return None


def recent_hosting_output_age_sec(recent_results: Any, iso_age_fn: IsoAgeFn = iso_age_sec) -> float | None:
    for result in reversed(list(recent_results or [])):
        if not isinstance(result, dict):
            continue
        if str(result.get("status") or "") != "pushed":
            continue
        route = recent_context.route_from_result(result)
        if route not in {"warmup_hosting", "idle_hosting", "active_engagement"}:
            continue
        age = iso_age_fn(result.get("created_at"))
        if age is not None:
            return float(age)
    return None


def recent_live_danmaku_event_age_sec(recent_results: Any, iso_age_fn: IsoAgeFn = iso_age_sec) -> float | None:
    for result in reversed(list(recent_results or [])):
        if not isinstance(result, dict):
            continue
        if str(result.get("status") or "") != "pushed":
            continue
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        if str(event.get("source") or "") != "live_danmaku":
            continue
        age = iso_age_fn(result.get("created_at"))
        if age is not None:
            return float(age)
    return None


def last_viewer_activity_age_sec(
    rows: list[dict[str, Any]],
    recent_results: Any = None,
    iso_age_fn: IsoAgeFn = iso_age_sec,
) -> float | None:
    ages: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("id") not in {"live_ingest", "event_bus", "selection", "live_signal"}:
            continue
        outcome = str(row.get("last_outcome") or "").strip()
        if outcome not in {"danmaku", "live_danmaku"}:
            continue
        age = row.get("age_sec")
        if age is None:
            continue
        try:
            ages.append(float(age))
        except (TypeError, ValueError):
            continue
    recent_age = recent_live_danmaku_event_age_sec(recent_results, iso_age_fn)
    if recent_age is not None:
        ages.append(float(recent_age))
    return min(ages) if ages else None


def last_output_age_sec(
    rows: list[dict[str, Any]],
    recent_results: Any = None,
    iso_age_fn: IsoAgeFn = iso_age_sec,
) -> float | None:
    ages: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("id") not in {"pipeline", "dispatcher"}:
            continue
        age = row.get("age_sec")
        if age is None:
            continue
        try:
            ages.append(float(age))
        except (TypeError, ValueError):
            continue
    for result in reversed(list(recent_results or [])):
        if not isinstance(result, dict):
            continue
        if str(result.get("status") or "") != "pushed":
            continue
        age = iso_age_fn(result.get("created_at"))
        if age is not None:
            ages.append(float(age))
            break
    return min(ages) if ages else None
