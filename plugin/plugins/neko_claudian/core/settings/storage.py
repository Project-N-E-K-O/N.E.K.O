# Ported from claudian/src/core/settings/storage/ClaudianSettingsStorage.ts
# Original author: Claudian contributors
# License: MIT

"""
Settings storage.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .claudian_settings import ClaudianSettings
from .defaults import get_default_settings

logger = logging.getLogger(__name__)


class ClaudianSettingsStorage:
    """Storage for Claudian settings.

    Ported from ClaudianSettingsStorage.ts
    """

    def __init__(self, storage_path: Path):
        self._storage_path = storage_path
        self._settings_file = storage_path / "settings.json"

    async def load(self) -> ClaudianSettings:
        """Load settings from storage."""
        if not self._settings_file.exists():
            return get_default_settings()

        try:
            with open(self._settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ClaudianSettings.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load settings: {e}")
            return get_default_settings()

    async def save(self, settings: ClaudianSettings) -> bool:
        """Save settings to storage."""
        try:
            self._storage_path.mkdir(parents=True, exist_ok=True)
            with open(self._settings_file, "w", encoding="utf-8") as f:
                json.dump(settings.to_dict(), f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            return False

    async def update(self, updates: Dict[str, Any]) -> Optional[ClaudianSettings]:
        """Update settings with partial data."""
        settings = await self.load()
        settings_dict = settings.to_dict()
        settings_dict.update(updates)
        new_settings = ClaudianSettings.from_dict(settings_dict)
        if await self.save(new_settings):
            return new_settings
        return None
