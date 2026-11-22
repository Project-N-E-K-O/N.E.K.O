"""Value types describing one permission as a plugin sees it."""

from __future__ import annotations

import contextlib
import inspect
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .catalog import PERMISSIONS

PermissionStatus = Literal["granted", "denied", "undecided", "once"]
PERMISSION_STATUSES: tuple[str, ...] = ("granted", "denied", "undecided", "once")


@dataclass(frozen=True, slots=True)
class PermissionState:
    """``status`` is one of ``PERMISSION_STATUSES``.

    ``background``: usable while the user is not using the plugin.
    ``enforced``: whether the host would refuse a use of this permission;
    False for disclosure-only permissions and while the host's permission
    checks are switched off. Display it, do not branch feature availability
    on it.
    """

    status: PermissionStatus
    background: bool = False
    enforced: bool = True

    def __post_init__(self) -> None:
        if self.status not in PERMISSION_STATUSES:
            raise ValueError(f"unknown permission status: {self.status!r}")


def known_permission(permission: str) -> str:
    """Return ``permission`` if it is a permission id; raises ``ValueError`` otherwise."""
    if not isinstance(permission, str) or permission not in PERMISSIONS:
        raise ValueError(f"unknown permission id: {permission!r}")
    return permission


def is_async_callable(callback: object) -> bool:
    """``async def`` functions (also behind ``functools.partial``) and
    objects whose ``__call__`` is ``async def``."""
    return inspect.iscoroutinefunction(callback) or inspect.iscoroutinefunction(
        getattr(callback, "__call__", None)
    )


class PermissionSubscription:
    """Handle returned by ``on_change``; ``cancel()`` (alias ``unsubscribe()``)
    removes the registration once, later calls do nothing."""

    __slots__ = ("_callbacks", "_callback", "_lock", "_cancelled")

    def __init__(
        self,
        callbacks: list[Callable[..., object]],
        callback: Callable[..., object],
        lock: threading.Lock | None = None,
    ) -> None:
        self._callbacks, self._callback, self._lock = callbacks, callback, lock
        self._cancelled = False

    def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        with self._lock if self._lock is not None else contextlib.nullcontext():
            # Match by identity, not ==: a distinct but equal callable must
            # stay subscribed. (Two registrations of the very same object are
            # interchangeable, so removing either one is the same.) The
            # registration may already be gone if the owner dropped its callbacks.
            for index, registered in enumerate(self._callbacks):
                if registered is self._callback:
                    del self._callbacks[index]
                    break

    unsubscribe = cancel


__all__ = [
    "PERMISSION_STATUSES",
    "PermissionState",
    "PermissionStatus",
    "PermissionSubscription",
    "is_async_callable",
    "known_permission",
]
