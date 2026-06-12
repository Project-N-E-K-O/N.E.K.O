# Ported from claudian/src/core/storage/SessionStorage.ts
# Original author: Claudian contributors
# License: MIT

"""
Session storage — Persist session data.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionStorage:
    """Storage for session data.

    Ported from SessionStorage.ts
    """

    def __init__(self, storage_path: Path):
        self._storage_path = storage_path
        self._sessions_dir = storage_path / "sessions"

    async def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load a session by ID."""
        session_file = self._sessions_dir / f"{session_id}.json"
        if not session_file.exists():
            return None

        try:
            with open(session_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load session {session_id}: {e}")
            return None

    async def save_session(self, session_id: str, data: Dict[str, Any]) -> bool:
        """Save session data."""
        session_file = self._sessions_dir / f"{session_id}.json"
        try:
            self._sessions_dir.mkdir(parents=True, exist_ok=True)
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Failed to save session {session_id}: {e}")
            return False

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        session_file = self._sessions_dir / f"{session_id}.json"
        try:
            if session_file.exists():
                session_file.unlink()
            return True
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            return False

    async def list_sessions(self) -> List[str]:
        """List all session IDs."""
        if not self._sessions_dir.exists():
            return []

        try:
            return [
                f.stem for f in self._sessions_dir.iterdir()
                if f.suffix == ".json"
            ]
        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")
            return []
