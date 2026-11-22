"""Value types describing one permission as a plugin sees it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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


__all__ = ["PERMISSION_STATUSES", "PermissionState", "PermissionStatus"]
