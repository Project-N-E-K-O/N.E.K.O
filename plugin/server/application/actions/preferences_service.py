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
    """Atomically save preferences to disk (called from worker thread).

    Writes to a temporary file first, then renames to the target path.
    This prevents half-written JSON on crash.  Raises on failure so the
    caller can propagate the error to the client.
    """
    import os
    import tempfile

    path = _preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(prefs.model_dump(), ensure_ascii=False, indent=2)

    # Write to a temp file in the same directory, then atomic rename.
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".action_prefs_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except BaseException:
        # Clean up the temp file on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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
