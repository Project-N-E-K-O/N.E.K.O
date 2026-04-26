"""Backward-compatible shim — re-exports from neko_plugin_cli.core.plugin_source."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from neko_plugin_cli.core.plugin_source import *  # noqa: E402, F401, F403
from neko_plugin_cli.core.plugin_source import load_plugin_source, extract_runtime_config  # noqa: E402, F401
