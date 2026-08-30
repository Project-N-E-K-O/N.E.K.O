"""Narrow transaction primitives for plugin installation lifecycle operations."""

from .manual_takeover import (
    is_manual_takeover_entry,
    local_manual_takeover_confirmation_token,
    manual_takeover_snapshot_sha256,
)
from .ownership import (
    UninstallOwnershipError,
    can_neko_uninstall,
    require_uninstall_ownership,
)

__all__ = [
    "UninstallOwnershipError",
    "can_neko_uninstall",
    "is_manual_takeover_entry",
    "local_manual_takeover_confirmation_token",
    "manual_takeover_snapshot_sha256",
    "require_uninstall_ownership",
]
