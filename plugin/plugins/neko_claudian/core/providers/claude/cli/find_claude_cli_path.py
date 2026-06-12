# Ported from claudian/src/providers/claude/cli/findClaudeCLIPath.ts
# Original author: Claudian contributors
# License: MIT

"""
Find Claude CLI path.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Optional


def find_claude_cli_path() -> Optional[str]:
    """Find the Claude CLI executable path.

    Ported from findClaudeCLIPath.ts
    """
    # Try to find 'claude' in PATH
    path = shutil.which("claude")
    if path:
        return path

    # On Windows, try 'claude.cmd'
    if sys.platform == "win32":
        path = shutil.which("claude.cmd")
        if path:
            return path

    # Check common installation locations
    home = os.path.expanduser("~")
    common_paths = [
        os.path.join(home, ".claude", "bin", "claude"),
        os.path.join(home, ".local", "bin", "claude"),
    ]

    for path in common_paths:
        if os.path.exists(path):
            return path

    return None
