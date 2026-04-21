"""UserActionPreferences persistence service.

Stores per-user command palette preferences (pinned / hidden / recent)
in a JSON file under the plugin config directory.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from plugin.logging_config import get_logger
from plugin.server.domain.action_models import UserActionPreferences

logger = get_logger("server.application.actions.preferences")

_MAX_RECENT = 10


def _preferences_path() -> Path:
    """Return the path to the preferences JSON file."""
    from plugin.settings import USER_PLUGIN_CONFIG_ROOT

    return Path(USER_PLUGIN_CONFIG_ROOT) / ".action_preferences.json"


def _load_sync() -> UserActionPreferences:
    """Load preferences from disk (called from worker thread)."""
    path = _preferences_path()
    if not path.exists():
        return UserActionPreferences()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return UserActionPreferences.model_validate(data)
    except Exception as exc:
        logger.warning("Failed to load action preferences: {}", str(exc))
        return UserActionPreferences()


def _save_sync(prefs: UserActionPreferences) -> None:
    """Save preferences to disk (called from worker thread)."""
    path = _preferences_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(prefs.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to save action preferences: {}", str(exc))


class PreferencesService:
    """Manage user action preferences (pinned / hidden / recent)."""

    def __init__(self) -> None:
        self._write_lock = asyncio.Lock()

    async def load(self) -> UserActionPreferences:
        return await asyncio.to_thread(_load_sync)

    async def save(self, prefs: UserActionPreferences) -> UserActionPreferences:
        async with self._write_lock:
            prefs.recent = prefs.recent[:_MAX_RECENT]
            await asyncio.to_thread(_save_sync, prefs)
            return prefs

    async def touch_recent(self, action_id: str) -> None:
        """Move *action_id* to the front of the recent list."""
        async with self._write_lock:
            prefs = await asyncio.to_thread(_load_sync)
            if action_id in prefs.recent:
                prefs.recent.remove(action_id)
            prefs.recent.insert(0, action_id)
            prefs.recent = prefs.recent[:_MAX_RECENT]
            await asyncio.to_thread(_save_sync, prefs)
