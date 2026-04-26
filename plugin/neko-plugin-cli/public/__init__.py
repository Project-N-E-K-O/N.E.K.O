"""Backward-compatible shim — re-exports from neko_plugin_cli.core.

All existing code that does ``from public import pack_plugin`` continues
to work unchanged.  New code should import from ``neko_plugin_cli.core``
instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the new src/ layout is importable when running from the repo
# without pip-installing the package.
_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from neko_plugin_cli.core import (  # noqa: E402, F401
    analyze_bundle_plugins,
    inspect_package,
    pack_bundle,
    pack_plugin,
    PackResult,
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
