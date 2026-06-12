# Ported from claudian/src/utils/windowsCmdShim.ts
# Original author: Claudian contributors
# License: MIT

"""
Windows command shim — Handle Windows-specific command execution.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional


def is_windows() -> bool:
    """Check if running on Windows."""
    return sys.platform == "win32"


def get_shell_command() -> str:
    """Get the shell command for the current platform."""
    if is_windows():
        return "cmd"
    return "bash"


def wrap_command_for_windows(command: str, args: List[str]) -> List[str]:
    """Wrap a command for Windows execution.

    On Windows, .cmd files need to be executed via cmd /c
    """
    if not is_windows():
        return [command] + args

    # Check if command is a .cmd file
    if command.endswith(".cmd") or command.endswith(".bat"):
        return ["cmd", "/c", command] + args

    return [command] + args


def find_executable(name: str) -> Optional[str]:
    """Find an executable in PATH."""
    import shutil
    return shutil.which(name)
