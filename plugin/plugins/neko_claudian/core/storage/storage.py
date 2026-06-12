# Ported from claudian/src/core/storage/storage.ts
# Original author: Claudian contributors
# License: MIT

"""
Storage — Core storage interface.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Storage:
    """Core storage interface.

    Ported from storage.ts
    """

    def __init__(self, base_path: Path):
        self._base_path = base_path

    @property
    def base_path(self) -> Path:
        return self._base_path

    async def get(self, key: str) -> Optional[Any]:
        """Get a value by key."""
        file_path = self._base_path / f"{key}.json"
        if not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to get {key}: {e}")
            return None

    async def set(self, key: str, value: Any) -> bool:
        """Set a value by key."""
        file_path = self._base_path / f"{key}.json"
        try:
            self._base_path.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(value, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Failed to set {key}: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """Delete a value by key."""
        file_path = self._base_path / f"{key}.json"
        try:
            if file_path.exists():
                file_path.unlink()
            return True
        except Exception as e:
            logger.error(f"Failed to delete {key}: {e}")
            return False

    async def has(self, key: str) -> bool:
        """Check if a key exists."""
        return (self._base_path / f"{key}.json").exists()
