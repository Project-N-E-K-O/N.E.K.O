"""Backward-compatible public surface for plugin packaging helpers."""

from __future__ import annotations

from ..core import (
    PackResult,
    analyze_bundle_plugins,
    inspect_package,
    pack_bundle,
    pack_plugin,
    unpack_package,
)

__all__ = [
    "PackResult",
    "analyze_bundle_plugins",
    "inspect_package",
    "pack_bundle",
    "pack_plugin",
    "unpack_package",
]
