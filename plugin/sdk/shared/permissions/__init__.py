"""Plugin permission vocabulary, errors and test helpers."""

from __future__ import annotations

from .catalog import (
    LEGACY_PERMISSION_NAMES,
    LOCK_PERMISSIONS,
    PERMISSION_DENIED_REASONS,
    PERMISSION_IDS,
    PERMISSIONS,
    PLUGIN_PERMISSION_DENIED,
    UNENFORCED_PERMISSIONS,
    PermissionSpec,
    legacy_name,
    normalize_permission,
)

__all__ = [
    "LEGACY_PERMISSION_NAMES",
    "LOCK_PERMISSIONS",
    "PERMISSIONS",
    "PERMISSION_DENIED_REASONS",
    "PERMISSION_IDS",
    "PLUGIN_PERMISSION_DENIED",
    "PermissionSpec",
    "UNENFORCED_PERMISSIONS",
    "legacy_name",
    "normalize_permission",
]
