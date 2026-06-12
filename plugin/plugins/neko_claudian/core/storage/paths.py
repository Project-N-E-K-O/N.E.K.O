# Ported from claudian/src/core/storage/StoragePaths.ts
# Original author: Claudian contributors
# License: MIT

"""
Storage paths — Define storage directory structure.
"""

from __future__ import annotations

from pathlib import Path


class StoragePaths:
    """Define storage directory structure.

    Ported from StoragePaths.ts
    """

    def __init__(self, base_path: Path):
        self._base_path = base_path

    @property
    def base_path(self) -> Path:
        return self._base_path

    @property
    def settings_path(self) -> Path:
        return self._base_path / "settings"

    @property
    def sessions_path(self) -> Path:
        return self._base_path / "sessions"

    @property
    def agents_path(self) -> Path:
        return self._base_path / "agents"

    @property
    def mcp_path(self) -> Path:
        return self._base_path / "mcp"

    @property
    def conversations_path(self) -> Path:
        return self._base_path / "conversations"

    def get_session_path(self, session_id: str) -> Path:
        """Get path for a session."""
        return self.sessions_path / session_id

    def get_conversation_path(self, conversation_id: str) -> Path:
        """Get path for a conversation."""
        return self.conversations_path / f"{conversation_id}.json"

    def ensure_dirs(self) -> None:
        """Ensure all directories exist."""
        self.settings_path.mkdir(parents=True, exist_ok=True)
        self.sessions_path.mkdir(parents=True, exist_ok=True)
        self.agents_path.mkdir(parents=True, exist_ok=True)
        self.mcp_path.mkdir(parents=True, exist_ok=True)
        self.conversations_path.mkdir(parents=True, exist_ok=True)
