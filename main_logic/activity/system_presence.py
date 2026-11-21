# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Is the local desktop session in a state where nobody should be spoken to?

Proactive chat is driven by a frontend timer. Without this module it keeps
talking while the display is off, while the session is locked, and fires the
tick that came due during sleep the moment the machine wakes up (#3552).

This module keeps a small, platform-neutral state machine fed by OS
notifications and answers one question for the proactive entry guard:
``get_system_inactive_reason()`` -> a short reason string, or ``None`` when
proactive chat may run.

Only Windows has a notification source today
(``main_logic.activity.system_presence_windows``, imported lazily and only on
Windows). On every other platform, and whenever anything goes wrong, the
answer is ``None`` (allow), which is exactly the behaviour before this module.

Fail-open rules (a stuck "inactive" would silence proactive chat forever,
which is worse than the bug being fixed):

* Nothing is suppressed until this process has *observed* the display on and
  the session unlocked at least once. A backend in a service session, on a
  headless box, or in a disconnected remote session never sees both and never
  suppresses.
* A session disconnect (console or remote) forgets everything and returns to
  the unarmed state; a reconnect re-queries the lock state from scratch.
* Watcher failures (registration, message loop, ctypes errors) mark the
  watcher as failed, which also allows.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

# After the display turns back on, the session is unlocked or the machine
# resumes from sleep, stay quiet this long before the next proactive turn.
SYSTEM_RETURN_QUIET_SECONDS = 60.0

# A suspend that has not been followed by a resume notification for this long
# is treated as resumed. Requests are only evaluated while the process runs,
# so a lingering "suspended" means the resume broadcast was missed; without
# this, one missed broadcast would silence proactive chat until restart.
SUSPEND_STALE_SECONDS = 120.0

REASON_DISPLAY_OFF = 'display_off'
REASON_SESSION_LOCKED = 'session_locked'
REASON_SUSPENDED = 'suspended'
REASON_RECENTLY_RETURNED = 'recently_returned'


class SystemPresenceState:
    """Thread-safe presence state fed by OS notifications.

    All handlers are pure in-memory updates under a ``threading.Lock`` and
    never block, so they may be called from the watcher thread while the
    event loop reads ``inactive_reason()``.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        quiet_seconds: float = SYSTEM_RETURN_QUIET_SECONDS,
    ) -> None:
        self._clock = clock
        self._quiet_seconds = float(quiet_seconds)
        self._lock = threading.Lock()
        self._failed = False
        self._reset_locked()

    def _reset_locked(self) -> None:
        # ``None`` = never observed since the last reset.
        self._display_on: bool | None = None
        self._session_locked: bool | None = None
        self._seen_display_on = False
        self._seen_unlocked = False
        self._suspended = False
        self._suspended_at: float | None = None
        self._returned_at: float | None = None

    def _armed_locked(self) -> bool:
        return self._seen_display_on and self._seen_unlocked

    def _mark_returned_locked(self) -> None:
        self._returned_at = self._clock()

    # ── notification handlers ──────────────────────────────────

    def on_display_state(self, display_on: bool) -> None:
        with self._lock:
            was_off = self._display_on is False
            self._display_on = bool(display_on)
            if display_on:
                self._seen_display_on = True
                if was_off:
                    self._mark_returned_locked()

    def on_session_lock_state(self, locked: bool) -> None:
        with self._lock:
            was_locked = self._session_locked is True
            self._session_locked = bool(locked)
            if not locked:
                self._seen_unlocked = True
                if was_locked:
                    self._mark_returned_locked()

    def on_suspend(self) -> None:
        with self._lock:
            self._suspended = True
            self._suspended_at = self._clock()

    def on_resume(self) -> None:
        with self._lock:
            self._suspended = False
            self._mark_returned_locked()

    def on_session_disconnected(self) -> None:
        """Forget everything: the session is no longer attached to a screen."""
        with self._lock:
            self._reset_locked()

    def on_session_connected(self) -> None:
        """Start over; the caller re-queries the lock state afterwards."""
        with self._lock:
            self._reset_locked()

    def mark_failed(self) -> None:
        with self._lock:
            self._failed = True

    # ── query ──────────────────────────────────────────────────

    def inactive_reason(self) -> str | None:
        with self._lock:
            if self._failed or not self._armed_locked():
                return None
            if self._suspended:
                if (
                    self._suspended_at is not None
                    and self._clock() - self._suspended_at >= SUSPEND_STALE_SECONDS
                ):
                    # Missed resume broadcast: we are evidently running, so
                    # treat it as resumed (quiet window starts now).
                    self._suspended = False
                    self._mark_returned_locked()
                else:
                    return REASON_SUSPENDED
            if self._display_on is False:
                return REASON_DISPLAY_OFF
            if self._session_locked is True:
                return REASON_SESSION_LOCKED
            if (
                self._returned_at is not None
                and self._clock() - self._returned_at < self._quiet_seconds
            ):
                return REASON_RECENTLY_RETURNED
            return None


# ── Windows notification codes ─────────────────────────────────
# Kept here (not in the Windows module) so the dispatch below is
# platform-neutral and unit-testable everywhere.

# WM_POWERBROADCAST wParam
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_POWERSETTINGCHANGE = 0x8013

# WM_WTSSESSION_CHANGE wParam
WTS_CONSOLE_CONNECT = 0x1
WTS_CONSOLE_DISCONNECT = 0x2
WTS_REMOTE_CONNECT = 0x3
WTS_REMOTE_DISCONNECT = 0x4
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8

# GUID_SESSION_DISPLAY_STATUS payload: 0 = off, 1 = on, 2 = dimmed (still visible).
DISPLAY_STATUS_OFF = 0


def apply_power_broadcast(
    state: SystemPresenceState, event: int, display_status: int | None = None,
) -> None:
    """Apply a ``WM_POWERBROADCAST`` notification.

    ``display_status`` is the decoded ``GUID_SESSION_DISPLAY_STATUS`` value for
    ``PBT_POWERSETTINGCHANGE``; ``None`` for other settings.
    """
    if event == PBT_APMSUSPEND:
        state.on_suspend()
    elif event in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
        state.on_resume()
    elif event == PBT_POWERSETTINGCHANGE and display_status is not None:
        state.on_display_state(display_status != DISPLAY_STATUS_OFF)


def apply_session_change(
    state: SystemPresenceState,
    event: int,
    query_lock_state: Callable[[], bool | None],
) -> None:
    """Apply a ``WM_WTSSESSION_CHANGE`` notification for this session."""
    if event == WTS_SESSION_LOCK:
        state.on_session_lock_state(True)
    elif event == WTS_SESSION_UNLOCK:
        state.on_session_lock_state(False)
    elif event in (WTS_CONSOLE_DISCONNECT, WTS_REMOTE_DISCONNECT):
        state.on_session_disconnected()
    elif event in (WTS_CONSOLE_CONNECT, WTS_REMOTE_CONNECT):
        state.on_session_connected()
        locked = query_lock_state()
        if locked is not None:
            state.on_session_lock_state(locked)


# ── process-wide singleton ─────────────────────────────────────

_state = SystemPresenceState()
_watcher_lock = threading.Lock()
_watcher_started = False


def get_system_presence_state() -> SystemPresenceState:
    return _state


def _watcher_supported() -> bool:
    if sys.platform != 'win32':
        return False
    # The OS signals describe the backend machine. When the deployment is
    # declared remote they say nothing about the user.
    from main_logic.activity.system_signals import is_remote_backend_deployment

    return not is_remote_backend_deployment()


def ensure_system_presence_watcher_started() -> None:
    """Start the OS notification watcher once. Never raises, never blocks."""
    global _watcher_started
    if _watcher_started:
        return
    with _watcher_lock:
        if _watcher_started:
            return
        _watcher_started = True
        try:
            if not _watcher_supported():
                return
            # Imported only here so non-Windows platforms never load it.
            from main_logic.activity import system_presence_windows

            system_presence_windows.start_watcher(_state)
        except Exception as exc:  # noqa: BLE001 - must fail open
            _state.mark_failed()
            logger.warning('System presence watcher unavailable, proactive chat is not gated by it: %s', exc)


def get_system_inactive_reason() -> str | None:
    """Reason the local session should not be spoken to, or ``None``."""
    ensure_system_presence_watcher_started()
    try:
        return _state.inactive_reason()
    except Exception:  # noqa: BLE001 - must fail open
        return None
