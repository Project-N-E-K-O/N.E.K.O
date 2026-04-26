"""Backward-compatible shim — re-exports from neko_plugin_cli.core.pack_rules."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from neko_plugin_cli.core.pack_rules import *  # noqa: E402, F401, F403
from neko_plugin_cli.core.pack_rules import PackRuleSet, should_skip_path  # noqa: E402, F401
