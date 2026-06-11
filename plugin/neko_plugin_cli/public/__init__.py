"""Public surface for plugin package helpers."""

from __future__ import annotations

from ..core import (
    BuildResult,
    analyze_bundle_plugins,
    inspect_package,
    build_bundle,
    build_plugin,
    install_package,
)

# Legacy aliases for backward compatibility
from .models import PackResult, UnpackResult, UnpackedPlugin
from .pack import pack_plugin, pack_bundle
from .unpack import unpack_package

__all__ = [
    "BuildResult",
    "analyze_bundle_plugins",
    "inspect_package",
    "build_bundle",
    "build_plugin",
    "install_package",
    # Legacy aliases
    "PackResult",
    "UnpackResult",
    "UnpackedPlugin",
    "pack_plugin",
    "pack_bundle",
    "unpack_package",
]
