"""ActionAggregationService — merge actions from all providers.

Holds instances of the three ``ActionProvider`` implementations and
exposes a single ``aggregate_actions`` method that collects, merges and
returns a flat list of ``ActionDescriptor`` items.

Per-provider errors are caught and logged so that a single failing
provider does not block the others.
"""

from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.actions.list_actions_provider import ListActionsProvider
from plugin.server.application.actions.settings_provider import SettingsActionProvider
from plugin.server.application.actions.system_provider import SystemActionProvider
from plugin.server.domain.action_models import ActionDescriptor
from plugin.server.domain.action_provider import ActionProvider

logger = get_logger("server.application.actions.aggregation")


class ActionAggregationService:
    """Aggregate ``ActionDescriptor`` items from all providers."""

    def __init__(self) -> None:
        self._providers: list[ActionProvider] = [
            SettingsActionProvider(),
            ListActionsProvider(),
            SystemActionProvider(),
        ]

    async def aggregate_actions(
        self,
        plugin_id: str | None = None,
    ) -> list[ActionDescriptor]:
        """Call all providers, merge results, return flat list.

        A single provider failure is logged as a warning and does not
        prevent the other providers from contributing their actions.
        """
        all_actions: list[ActionDescriptor] = []
        for provider in self._providers:
            try:
                actions = await provider.get_actions(plugin_id=plugin_id)
                all_actions.extend(actions)
            except Exception as exc:
                logger.warning(
                    "ActionProvider {} failed: {}",
                    type(provider).__name__,
                    str(exc),
                )
        return all_actions
