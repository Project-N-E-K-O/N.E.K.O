"""The plugin process's copy of its own permission grants (``ctx.permissions``).

The host resolves the grants and sends them down: once with the launch
options, so the copy is there before plugin code runs, and again as a
``PERMISSIONS_UPDATE`` command whenever they change. ``get()`` only reads this
copy; it never asks the host.

Wire shape (both paths)::

    {"revision": <int>, "states": {"<permission id>": {
        "status": "granted" | "denied" | "undecided" | "once",
        "background": <bool>, "enforced": <bool>}}}

``revision`` grows with every snapshot the host builds; an older one than
the copy already holds is ignored, so delivery order does not matter.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable, Mapping
from typing import Any

from .catalog import PERMISSIONS
from .state import (
    PERMISSION_STATUSES,
    PermissionState,
    PermissionSubscription,
    is_async_callable,
    known_permission,
)

PERMISSIONS_UPDATE = "PERMISSIONS_UPDATE"

ChangeCallback = Callable[[str, PermissionState], object]


def unavailable_state(permission: str, *, checks_disabled: bool = False) -> PermissionState:
    """The state of a permission whose grant could never be read.

    An enforced permission counts as denied (the host would refuse it); a
    disclosure-only one as undecided.
    """
    spec = PERMISSIONS[permission]
    return PermissionState(
        "denied" if spec.enforced else "undecided",
        background=False,
        enforced=spec.enforced and not checks_disabled,
    )


def state_to_wire(state: PermissionState) -> dict[str, object]:
    return {"status": state.status, "background": state.background, "enforced": state.enforced}


def _state_from_wire(value: object) -> PermissionState:
    if not isinstance(value, Mapping):
        raise ValueError("permission state must be a mapping")
    status = value.get("status")
    background = value.get("background", False)
    enforced = value.get("enforced", True)
    if status not in PERMISSION_STATUSES:
        raise ValueError("unknown permission status")
    if not isinstance(background, bool) or not isinstance(enforced, bool):
        raise ValueError("permission flags must be booleans")
    return PermissionState(status, background=background, enforced=enforced)  # type: ignore[arg-type]


def parse_snapshot(payload: object) -> tuple[int, dict[str, PermissionState]]:
    """Validate a snapshot; raises ``ValueError`` on a malformed one.

    Ids outside the vocabulary are dropped (a newer host may know more).
    """
    if not isinstance(payload, Mapping):
        raise ValueError("permission snapshot must be a mapping")
    revision = payload.get("revision")
    states = payload.get("states")
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise ValueError("permission snapshot revision must be an integer")
    if not isinstance(states, Mapping):
        raise ValueError("permission snapshot states must be a mapping")
    parsed = {
        permission: _state_from_wire(value)
        for permission, value in states.items()
        if permission in PERMISSIONS
    }
    return revision, parsed


class PluginPermissions:
    """``ctx.permissions`` in a plugin process.

    ``get`` / ``on_change`` behave like ``FakePermissions`` (shared behaviour
    tests cover both). Differences that come from running in a real process:
    callbacks run on the plugin's command loop when the host sends a change,
    and one that raises is logged instead of stopping the others.
    """

    def __init__(self, logger: Any = None) -> None:
        self._logger = logger
        self._lock = threading.Lock()
        self._revision: int | None = None
        self._states: dict[str, PermissionState] = {}
        self._callbacks: list[ChangeCallback] = []

    @property
    def revision(self) -> int | None:
        """Revision of the snapshot held, ``None`` before the first one."""
        return self._revision

    def get(self, permission: str) -> PermissionState:
        permission = known_permission(permission)
        with self._lock:
            state = self._states.get(permission)
        return state if state is not None else unavailable_state(permission)

    def on_change(self, callback: ChangeCallback) -> PermissionSubscription:
        if is_async_callable(callback):
            raise TypeError("ctx.permissions.on_change callbacks must be synchronous")
        with self._lock:
            self._callbacks.append(callback)
        return PermissionSubscription(self._callbacks, callback, self._lock)

    def apply(self, payload: object) -> bool:
        """Take a snapshot from the host; returns whether it was taken.

        A malformed snapshot is logged and leaves the copy unchanged; an
        older revision than the one held is ignored. ``on_change`` callbacks
        run for each permission whose state changed, after the copy is
        updated.
        """
        try:
            revision, parsed = parse_snapshot(payload)
        except ValueError as exc:
            self._log("error", "permission snapshot rejected: {}", exc)
            return False
        with self._lock:
            if self._revision is not None and revision <= self._revision:
                return False
            old = {permission: self._states.get(permission) for permission in PERMISSIONS}
            self._revision = revision
            self._states = parsed
            callbacks = list(self._callbacks)
        for permission in PERMISSIONS:
            before = old[permission] or unavailable_state(permission)
            after = parsed.get(permission) or unavailable_state(permission)
            if before != after:
                for callback in callbacks:
                    self._notify(callback, permission, after)
        return True

    def _notify(self, callback: ChangeCallback, permission: str, state: PermissionState) -> None:
        try:
            result = callback(permission, state)
        except (Exception, asyncio.CancelledError):
            # A synchronous callback cannot be hit by a real task cancellation
            # (that only lands at an await), so a CancelledError here is the
            # callback's own: keep the other subscribers and the command loop going.
            self._log("exception", "ctx.permissions.on_change callback failed for {}", permission)
            return
        if inspect.iscoroutine(result):
            result.close()
            self._log("error", "ctx.permissions.on_change callbacks must be synchronous; {} was not called", permission)

    def _log(self, level: str, message: str, *args: object) -> None:
        if self._logger is not None:
            getattr(self._logger, level)(message, *args)


__all__ = [
    "PERMISSIONS_UPDATE",
    "PluginPermissions",
    "parse_snapshot",
    "state_to_wire",
    "unavailable_state",
]
