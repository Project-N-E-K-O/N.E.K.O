# Ported from claudian/src/providers/claude/history/ClaudeHistoryStore.ts
# Original author: Claudian contributors
# License: MIT

"""
History store for Claude provider.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ClaudeHistoryStore:
    """History store for Claude conversations.

    Ported from ClaudeHistoryStore.ts
    """

    def __init__(self, storage_path: Path):
        self._storage_path = storage_path
        self._history_file = storage_path / "history.json"

    async def load(self) -> List[Dict[str, Any]]:
        """Load history from storage."""
        if not self._history_file.exists():
            return []

        try:
            with open(self._history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Failed to load history: {e}")
            return []

    async def save(self, history: List[Dict[str, Any]]) -> None:
        """Save history to storage."""
        try:
            self._storage_path.mkdir(parents=True, exist_ok=True)
            with open(self._history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save history: {e}")

    async def add_entry(self, entry: Dict[str, Any]) -> None:
        """Add an entry to history."""
        history = await self.load()
        history.append(entry)
        await self.save(history)

    async def clear(self) -> None:
        """Clear all history."""
        await self.save([])
