"""Plugin-facing activity snapshot helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PrivacyState = Literal["visible", "private", "unavailable"]


@dataclass(frozen=True, slots=True)
class OsActivitySnapshot:
    """Narrow OS activity view exposed to plugins."""

    os_signals_available: bool
    foreground_category: str | None = None
    system_idle_seconds: float | None = None
    privacy_state: PrivacyState = "unavailable"


_ACTIVITY_TRACKERS: dict[str, Any] = {}


def _tracker_for_source(source: str) -> Any:
    source_key = str(source or "").strip() or "plugin"
    tracker = _ACTIVITY_TRACKERS.get(source_key)
    if tracker is None:
        from main_logic.activity import UserActivityTracker

        tracker = UserActivityTracker(source_key)
        _ACTIVITY_TRACKERS[source_key] = tracker
    return tracker


def _coerce_idle_seconds(value: Any) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0.0, seconds)


def _privacy_state(core_snapshot: Any, foreground_category: str | None) -> PrivacyState:
    if not bool(getattr(core_snapshot, "os_signals_available", True)):
        return "unavailable"
    if getattr(core_snapshot, "state", "") == "private" or foreground_category == "private":
        return "private"
    return "visible"


async def get_os_activity_snapshot(
    source: str,
    *,
    now: float | None = None,
) -> OsActivitySnapshot:
    """Return the OS-only activity signals plugins are allowed to consume."""

    core_snapshot = await _tracker_for_source(source).get_snapshot(now=now)
    os_signals_available = bool(getattr(core_snapshot, "os_signals_available", True))
    active_window = getattr(core_snapshot, "active_window", None)
    foreground_category = (
        str(getattr(active_window, "category", "") or "").strip().lower() or None
        if os_signals_available and active_window is not None
        else None
    )
    idle_seconds = (
        _coerce_idle_seconds(getattr(core_snapshot, "system_idle_seconds", None))
        if os_signals_available
        else None
    )
    return OsActivitySnapshot(
        os_signals_available=os_signals_available,
        foreground_category=foreground_category,
        system_idle_seconds=idle_seconds,
        privacy_state=_privacy_state(core_snapshot, foreground_category),
    )


__all__ = [
    "OsActivitySnapshot",
    "PrivacyState",
    "get_os_activity_snapshot",
]
