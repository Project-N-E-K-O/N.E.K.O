"""Plugin permission declarations in plugin.toml.

The implementation lives in ``plugin.sdk.shared.permissions.declarations`` so
the plugin process (component narrowing at registration) and the host tools
(``neko-plugin check``, the capability analyzer) share one parser. This module
keeps the host-side import path.
"""

from __future__ import annotations

from plugin.sdk.shared.permissions.declarations import (
    NETWORK_PERMISSION,
    SECTIONS,
    PermissionDeclaration,
    PermissionDeclarations,
    PermissionIssue,
    declared_permission_ids,
    narrowing_issues,
    parse_permission_declarations,
    resolve_text,
)

__all__ = [
    "NETWORK_PERMISSION",
    "SECTIONS",
    "PermissionDeclaration",
    "PermissionDeclarations",
    "PermissionIssue",
    "declared_permission_ids",
    "narrowing_issues",
    "parse_permission_declarations",
    "resolve_text",
]
