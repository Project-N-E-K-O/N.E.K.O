"""Knowing whether the user is already sharing their screen.

When the main conversation has screen sharing on, the host is already carrying
a frame of the game every second. Taking our own screenshot on top of that
costs a tool round trip and, in voice mode, a whole extra vision-model call to
turn the picture back into words -- for a picture the character could have
been handed directly.

This module answers one question: can we lean on that? "Yes" means three
things at once, and all three have to hold or the frame never arrives:

* frames are actually arriving (sharing on, not stale, not paused);
* the share is a *screen*, not a phone camera pointed at the room;
* the conversation model takes pixels natively, because otherwise the host
  would route the frame through the vision model and we would have paid the
  same cost we were trying to avoid.

Reads never block. The pipeline asks this from the telemetry thread while a
battle is running, so a slow or dead host must not stall a call-out: callers
reuse a fresh cached answer, while an expired cache reads inactive until a
refresh finishes behind them. Polling -- unlike a subscription -- cannot get
stuck believing a share is still on because it missed the stop.
"""

from __future__ import annotations

import base64
import threading
import time
from typing import Any, Callable

SOURCE_SCREEN = "screen"

_UNKNOWN: dict[str, Any] = {
    "active": False,
    "source": "",
    "age_seconds": None,
    "native_vision": False,
    "role": "",
}


def _normalize(payload: Any) -> dict[str, Any]:
    """Coerce a host reply into the probe's shape; anything odd means "no"."""
    if not isinstance(payload, dict):
        return dict(_UNKNOWN)
    age = payload.get("age_seconds")
    age_seconds = None
    if isinstance(age, (int, float)) and not isinstance(age, bool):
        try:
            age_seconds = float(age)
        except (OverflowError, TypeError, ValueError):
            pass
    return {
        "active": bool(payload.get("active")),
        "source": str(payload.get("source") or ""),
        "age_seconds": age_seconds,
        "native_vision": bool(payload.get("native_vision")),
        "role": str(payload.get("role") or ""),
    }


def _spawn_thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="wows-live-vision-probe", daemon=True).start()


class LiveVisionProbe:
    """Cached, non-blocking view of the host's live screen share."""

    def __init__(
        self,
        fetch: Callable[..., Any],
        *,
        ttl: float = 2.0,
        logger=None,
        clock: Callable[[], float] = time.monotonic,
        spawn: Callable[[Callable[[], None]], None] = _spawn_thread,
    ) -> None:
        self._fetch = fetch
        self._ttl = max(0.0, float(ttl))
        self._logger = logger
        self._clock = clock
        self._spawn = spawn
        self._lock = threading.RLock()
        self._states: dict[str, dict[str, Any]] = {}
        self._fetched_at: dict[str, float] = {}
        self._refreshing: set[str] = set()

    # ------------------------------------------------------------------
    def snapshot(self, *, role: str = "") -> dict[str, Any]:
        """Fresh cached host state, or inactive while refreshing stale data."""
        role = str(role or "")
        with self._lock:
            fetched_at = self._fetched_at.get(role)
            due = (
                fetched_at is None
                or (self._clock() - fetched_at) >= self._ttl
            )
            state = (
                dict(_UNKNOWN)
                if due
                else dict(self._states.get(role, _UNKNOWN))
            )
            # Single-flight: a stalled refresh must not pile up one thread per
            # telemetry frame. Each delivery role has its own flight because
            # one character's share says nothing about another's.
            start = due and role not in self._refreshing
            if start:
                self._refreshing.add(role)
        if start:
            try:
                self._spawn(lambda: self._refresh(role))
            except Exception as exc:
                with self._lock:
                    self._refreshing.discard(role)
                self._log("debug", f"live vision refresh could not start: {exc}")
            else:
                # Test/in-process spawners may complete before returning. In
                # that case the cache is fresh already; only conceal the old
                # active state while a refresh is genuinely still in flight.
                with self._lock:
                    if role not in self._refreshing:
                        state = dict(self._states.get(role, _UNKNOWN))
        return state

    def is_active(self, *, role: str = "") -> bool:
        """Whether a call-out can rely on the host attaching the live frame.

        Deliberately the same three conditions the host checks before it
        attaches one. If this disagreed with the host, the character would be
        told she can see the screen on a turn where no frame was sent.
        """
        state = self.snapshot(role=role)
        return bool(
            state["active"]
            and state["source"] == SOURCE_SCREEN
            and state["native_vision"]
        )

    def is_sharing_screen(self, *, role: str = "") -> bool:
        """Whether a screen share is running, native vision or not.

        The screenshot tool can use the frame either way -- it hands pictures
        to the host, which transcribes them when the model cannot read pixels.
        Only the zero-round-trip path needs native vision.
        """
        state = self.snapshot(role=role)
        return bool(state["active"] and state["source"] == SOURCE_SCREEN)

    def fetch_frame(self, *, role: str = "") -> bytes | None:
        """Pull the current shared frame. Blocks; call it off the hot path.

        Used by the screenshot tool, which already runs in a worker thread, so
        the round trip is affordable there in a way it is not on the telemetry
        thread.
        """
        role = str(role or "")
        kwargs: dict[str, Any] = {"include_frame": True}
        if role:
            kwargs["role"] = role
        try:
            payload = self._fetch(**kwargs)
        except Exception as exc:
            self._log("debug", f"live vision frame fetch failed: {exc}")
            return None
        self._store(role, payload)
        if not isinstance(payload, dict):
            return None
        if not payload.get("active") or payload.get("source") != SOURCE_SCREEN:
            return None
        encoded = payload.get("frame_b64")
        if not isinstance(encoded, str) or not encoded:
            return None
        try:
            return base64.b64decode(encoded, validate=True)
        except Exception as exc:
            self._log("debug", f"live vision frame was not valid base64: {exc}")
            return None

    def status(self, *, role: str = "") -> dict[str, Any]:
        """Panel view, refreshed like any other read.

        The panel is often the only caller: outside a battle nothing else asks,
        so a status that merely read the cache would sit on whatever the last
        battle left and tell the user they are not sharing while they are. The
        panel polls at roughly the probe's own TTL, so this costs about one
        refresh per poll -- the rate it was designed for.
        """
        role = str(role or "")
        state = self.snapshot(role=role)
        with self._lock:
            state["polled"] = role in self._fetched_at
        state["usable"] = bool(
            state["active"]
            and state["source"] == SOURCE_SCREEN
            and state["native_vision"]
        )
        return state

    # ------------------------------------------------------------------
    def _refresh(self, role: str) -> None:
        kwargs = {"role": role} if role else {}
        try:
            payload = self._fetch(**kwargs)
        except Exception as exc:
            # An unreachable host means nobody is sharing anything with us,
            # which is the safe reading: the plugin falls back to its own
            # screenshot tool rather than telling her to look at nothing.
            self._log("debug", f"live vision probe failed: {exc}")
            payload = None
        self._store(role, payload)

    def _store(self, role: str, payload: Any) -> None:
        with self._lock:
            self._states[role] = _normalize(payload)
            # Stamped even on failure so an outage is polled at the same rate
            # as a healthy host instead of once per telemetry frame.
            self._fetched_at[role] = self._clock()
            self._refreshing.discard(role)

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        method = getattr(self._logger, level, None)
        if callable(method):
            try:
                method(message)
            except Exception:
                pass


__all__ = ["SOURCE_SCREEN", "LiveVisionProbe"]
