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
from .uninstall import (
    UninstallPluginError,
    UninstallPluginResult,
    retry_deferred_plugin_code_cleanup_sync,
    retry_deferred_profile_cleanup_sync,
    uninstall_plugin,
)

__all__ = [
    "UninstallOwnershipError",
    "UninstallPluginError",
    "UninstallPluginResult",
    "can_neko_uninstall",
    "is_manual_takeover_entry",
    "local_manual_takeover_confirmation_token",
    "manual_takeover_snapshot_sha256",
    "require_uninstall_ownership",
    "retry_deferred_plugin_code_cleanup_sync",
    "retry_deferred_profile_cleanup_sync",
    "uninstall_plugin",
]
