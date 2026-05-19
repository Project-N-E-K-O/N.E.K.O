"""Quick Actions Panel — action providers.

This package contains the three ``ActionProvider`` implementations that
feed the ``ActionAggregationService``:

* ``SettingsActionProvider``  – auto-generates instant actions from
  ``PluginSettings`` hot fields.
* ``ListActionsProvider``     – maps plugin ``list_actions`` to
  ``ActionDescriptor`` items.
* ``SystemActionProvider``    – generates lifecycle, toggle, entry,
  static-UI and profile actions for every registered plugin.
"""

from __future__ import annotations

from plugin.server.application.actions.aggregation_service import ActionAggregationService
from plugin.server.application.actions.execution_service import ActionExecutionService
from plugin.server.application.actions.list_actions_provider import ListActionsProvider
from plugin.server.application.actions.settings_provider import SettingsActionProvider
from plugin.server.application.actions.system_provider import SystemActionProvider

__all__ = [
    "ActionAggregationService",
    "ActionExecutionService",
    "ListActionsProvider",
    "SettingsActionProvider",
    "SystemActionProvider",
]
