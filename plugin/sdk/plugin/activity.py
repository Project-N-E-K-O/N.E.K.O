"""Plugin-facing activity snapshot helpers."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import logging
import sys
import threading
import time
from typing import Any, Literal
from weakref import WeakValueDictionary

_logger = logging.getLogger(__name__)

PrivacyState = Literal["visible", "private", "unavailable"]


@dataclass(frozen=True, slots=True)
class OsActivitySnapshot:
    """Narrow OS activity view exposed to plugins."""

    os_signals_available: bool
    foreground_category: str | None = None
    system_idle_seconds: float | None = None
    privacy_state: PrivacyState = "unavailable"


_import_lock = threading.Lock()
_helpers_imported = False
_loop_locks: WeakValueDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakValueDictionary()


def _ensure_activity_helpers_imported() -> None:
    global _helpers_imported
    if _helpers_imported:
        return
    with _import_lock:
        if _helpers_imported:
            return
        import main_logic.activity  # noqa: F401
        import main_logic.activity.state_machine  # noqa: F401
        _helpers_imported = True


def _get_loop_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _loop_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _loop_locks[loop] = lock
    return lock


def _privacy_mode_enabled() -> bool:
    """Return True unless proactiveVisionEnabled is explicitly persisted as True."""
    try:
        from utils.preferences import load_global_conversation_settings

        settings = load_global_conversation_settings(strict=True)
        return settings.get("proactiveVisionEnabled") is not True
    except Exception as exc:
        _logger.debug("Privacy mode check failed, defaulting to enabled: %s", exc)
        return True


def _read_system_signal_snapshot() -> Any:
    from main_logic.activity import get_system_signal_collector

    return get_system_signal_collector().snapshot()


_DEFAULT_READ_SYSTEM_SIGNAL_SNAPSHOT = _read_system_signal_snapshot


async def _read_system_signal_snapshot_async() -> Any:
    await asyncio.to_thread(_ensure_activity_helpers_imported)
    activity_mod = sys.modules.get("main_logic.activity")
    if activity_mod is None:
        return await asyncio.to_thread(_read_system_signal_snapshot)
    collector = activity_mod.get_system_signal_collector()
    snap = collector.snapshot()
    task = getattr(collector, "_task", None)
    is_running = task is not None and not task.done()
    if (
        not is_running
        and bool(getattr(collector, "os_signals_available", False))
        and callable(getattr(collector, "_tick", None))
    ):
        poll_interval = max(0.1, float(getattr(collector, "_poll_interval", 5.0) or 5.0))
        snap_ts = float(getattr(snap, "timestamp", 0.0) or 0.0)
        if snap_ts <= 0.0 or (time.time() - snap_ts) >= poll_interval:
            async with _get_loop_lock():
                snap = collector.snapshot()
                snap_ts = float(getattr(snap, "timestamp", 0.0) or 0.0)
                if snap_ts <= 0.0 or (time.time() - snap_ts) >= poll_interval:
                    psutil_mod = getattr(collector, "_psutil", None)
                    if psutil_mod is not None and not getattr(collector, "_sdk_psutil_primed", False):
                        with suppress(Exception):
                            psutil_mod.cpu_percent(interval=None)
                        with suppress(Exception):
                            setattr(collector, "_sdk_psutil_primed", True)
                    try:
                        await collector._tick()
                    except Exception as exc:
                        _logger.debug("On-demand system signal tick failed: %s", exc)
                    snap = collector.snapshot()
    return snap


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
    if _read_system_signal_snapshot is _DEFAULT_READ_SYSTEM_SIGNAL_SNAPSHOT:
        if await asyncio.to_thread(_privacy_mode_enabled):
            return OsActivitySnapshot(
                os_signals_available=False,
                foreground_category=None,
                system_idle_seconds=None,
                privacy_state="private",
            )
        system_snapshot = await _read_system_signal_snapshot_async()
    else:
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
