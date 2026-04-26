"""Legacy CLI entry point — delegates to neko_plugin_cli.cli.

Existing invocations like ``python plugin/neko-plugin-cli/cli.py pack ...``
continue to work unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the new src/ layout is importable.
_SRC_DIR = str(Path(__file__).resolve().parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from neko_plugin_cli.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
