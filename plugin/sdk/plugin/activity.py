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


def _read_system_signal_snapshot() -> Any:
    from main_logic.activity import get_system_signal_collector

    return get_system_signal_collector().snapshot()


def _active_window_from_system_snapshot(system_snapshot: Any) -> Any | None:
    from main_logic.activity.state_machine import observation_from_system

    return observation_from_system(system_snapshot)


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

    _ = (source, now)
    system_snapshot = _read_system_signal_snapshot()
    os_signals_available = bool(getattr(system_snapshot, "os_signals_available", True))
    active_window = (
        _active_window_from_system_snapshot(system_snapshot)
        if os_signals_available
        else None
    )
    foreground_category = (
        str(getattr(active_window, "category", "") or "").strip().lower() or None
        if active_window is not None
        else None
    )
    idle_seconds = (
        _coerce_idle_seconds(getattr(system_snapshot, "idle_seconds", None))
        if os_signals_available
        else None
    )
    return OsActivitySnapshot(
        os_signals_available=os_signals_available,
        foreground_category=foreground_category,
        system_idle_seconds=idle_seconds,
        privacy_state=_privacy_state(system_snapshot, foreground_category),
    )


__all__ = [
    "OsActivitySnapshot",
    "PrivacyState",
    "get_os_activity_snapshot",
]
