# Ported from claudian/src/providers/claude/history/sdkSessionPaths.ts
# Original author: Claudian contributors
# License: MIT

"""
Session path utilities.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def get_session_path(base_path: Path, session_id: str) -> Path:
    """Get the path for a session's data."""
    return base_path / "sessions" / session_id


def get_session_messages_path(base_path: Path, session_id: str) -> Path:
    """Get the path for a session's messages."""
    return get_session_path(base_path, session_id) / "messages.json"


def get_session_metadata_path(base_path: Path, session_id: str) -> Path:
    """Get the path for a session's metadata."""
    return get_session_path(base_path, session_id) / "metadata.json"
