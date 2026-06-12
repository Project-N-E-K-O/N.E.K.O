# Ported from claudian/src/providers/claude/plugins/PluginManager.ts
# Original author: Claudian contributors
# License: MIT

"""
Plugin manager for Claude provider.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ClaudePluginManager:
    """Plugin manager for Claude provider.

    Ported from providers/claude/plugins/PluginManager.ts
    """

    def __init__(self):
        self._plugins: Dict[str, Dict[str, Any]] = {}

    def load_plugins(self, plugins_data: List[Dict[str, Any]]) -> None:
        """Load plugins from data."""
        for plugin in plugins_data:
            plugin_id = plugin.get("id", "")
            if plugin_id:
                self._plugins[plugin_id] = plugin

    def get_plugin(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """Get a plugin by ID."""
        return self._plugins.get(plugin_id)

    def get_all_plugins(self) -> List[Dict[str, Any]]:
        """Get all plugins."""
        return list(self._plugins.values())

    def get_enabled_plugins(self) -> List[Dict[str, Any]]:
        """Get enabled plugins."""
        return [p for p in self._plugins.values() if p.get("enabled", True)]

    def set_enabled(self, plugin_id: str, enabled: bool) -> bool:
        """Enable or disable a plugin."""
        if plugin_id in self._plugins:
            self._plugins[plugin_id]["enabled"] = enabled
            return True
        return False
