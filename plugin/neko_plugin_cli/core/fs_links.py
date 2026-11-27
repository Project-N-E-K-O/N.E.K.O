"""Link detection shared by the CLI commands, core sync and templates."""

from __future__ import annotations

import os
from pathlib import Path


def is_link(path: Path) -> bool:
    """Symlink, or a Windows junction (which is_symlink() misses on 3.11)."""
    if path.is_symlink():
        return True
    try:
        os.readlink(path)
    except (OSError, ValueError):
        return False
    return True
