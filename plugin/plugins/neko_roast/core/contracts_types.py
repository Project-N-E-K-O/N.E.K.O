"""Shared type aliases and time helpers for NEKO Live contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

LiveMode = Literal["co_stream", "solo_stream"]
RoastStrength = Literal["gentle", "normal", "sharp"]
ActivityLevel = Literal["quiet", "standard", "active"]
TriggerSource = Literal[
    "live_danmaku",
    "developer_sandbox",
    "manual_live_simulation",
    "idle_hosting",
    "active_engagement",
    "warmup_hosting",
]
SafetyStatus = Literal["running", "paused", "degraded", "tripped", "disconnected"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _response_latency_ms(seen_at: str, created_at: str) -> int | None:
    if not seen_at or not created_at:
        return None
    try:
        seen = datetime.fromisoformat(str(seen_at).replace("Z", "+00:00"))
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return max(0, int(round((created - seen).total_seconds() * 1000)))
