# Ported from claudian/src/core/storage/HomeFileAdapter.ts
# Original author: Claudian contributors
# License: MIT

"""
Home file adapter — File operations in user home directory.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class HomeFileAdapter:
    """File adapter for user home directory.

    Ported from HomeFileAdapter.ts
    """

    def __init__(self, base_path: Path):
        self._base_path = base_path

    @property
    def base_path(self) -> Path:
        return self._base_path

    def get_path(self, *parts: str) -> Path:
        """Get a path relative to base."""
        return self._base_path.joinpath(*parts)

    async def read_json(self, *parts: str) -> Optional[Any]:
        """Read a JSON file."""
        path = self.get_path(*parts)
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read {path}: {e}")
            return None

    async def write_json(self, data: Any, *parts: str) -> bool:
        """Write a JSON file."""
        path = self.get_path(*parts)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Failed to write {path}: {e}")
            return False

    async def read_text(self, *parts: str) -> Optional[str]:
        """Read a text file."""
        path = self.get_path(*parts)
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to read {path}: {e}")
            return None

    async def write_text(self, text: str, *parts: str) -> bool:
        """Write a text file."""
        path = self.get_path(*parts)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return True
        except Exception as e:
            logger.error(f"Failed to write {path}: {e}")
            return False

    async def delete(self, *parts: str) -> bool:
        """Delete a file."""
        path = self.get_path(*parts)
        try:
            if path.exists():
                path.unlink()
            return True
        except Exception as e:
            logger.error(f"Failed to delete {path}: {e}")
            return False

    async def exists(self, *parts: str) -> bool:
        """Check if a file exists."""
        return self.get_path(*parts).exists()

    async def list_dir(self, *parts: str) -> List[str]:
        """List directory contents."""
        path = self.get_path(*parts)
        if not path.exists() or not path.is_dir():
            return []

        try:
            return [item.name for item in path.iterdir()]
        except Exception as e:
            logger.error(f"Failed to list {path}: {e}")
            return []
