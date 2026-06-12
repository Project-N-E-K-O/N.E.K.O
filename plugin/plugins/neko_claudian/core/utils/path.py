# Ported from claudian/src/utils/path.ts
# Original author: Claudian contributors
# License: MIT

"""
Path utilities.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def normalize_path(path: str) -> str:
    """Normalize a file path."""
    return path.replace("\\", "/")


def is_absolute_path(path: str) -> bool:
    """Check if a path is absolute."""
    return os.path.isabs(path)


def join_paths(*parts: str) -> str:
    """Join path parts."""
    return str(Path(*parts))


def get_parent_path(path: str) -> str:
    """Get parent directory."""
    return str(Path(path).parent)


def get_filename(path: str) -> str:
    """Get filename from path."""
    return Path(path).name


def get_extension(path: str) -> str:
    """Get file extension."""
    return Path(path).suffix
