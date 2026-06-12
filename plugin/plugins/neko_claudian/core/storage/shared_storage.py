# Ported from claudian/src/core/storage/SharedStorageService.ts
# Original author: Claudian contributors
# License: MIT

"""
Shared storage service — Shared storage across components.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class SharedStorageService:
    """Shared storage service.

    Ported from SharedStorageService.ts
    """

    def __init__(self, storage_path: Path):
        self._storage_path = storage_path
        self._cache: Dict[str, Any] = {}

    async def get(self, namespace: str, key: str) -> Optional[Any]:
        """Get a value from shared storage."""
        cache_key = f"{namespace}:{key}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        file_path = self._storage_path / namespace / f"{key}.json"
        if not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._cache[cache_key] = data
            return data
        except Exception as e:
            logger.error(f"Failed to get {cache_key}: {e}")
            return None

    async def set(self, namespace: str, key: str, value: Any) -> bool:
        """Set a value in shared storage."""
        cache_key = f"{namespace}:{key}"
        file_path = self._storage_path / namespace / f"{key}.json"

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(value, f, indent=2, ensure_ascii=False)
            self._cache[cache_key] = value
            return True
        except Exception as e:
            logger.error(f"Failed to set {cache_key}: {e}")
            return False

    async def delete(self, namespace: str, key: str) -> bool:
        """Delete a value from shared storage."""
        cache_key = f"{namespace}:{key}"
        file_path = self._storage_path / namespace / f"{key}.json"

        try:
            if file_path.exists():
                file_path.unlink()
            self._cache.pop(cache_key, None)
            return True
        except Exception as e:
            logger.error(f"Failed to delete {cache_key}: {e}")
            return False

    def clear_cache(self) -> None:
        """Clear the in-memory cache."""
        self._cache.clear()
