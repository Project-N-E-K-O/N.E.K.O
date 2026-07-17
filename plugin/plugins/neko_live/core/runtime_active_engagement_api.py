"""Runtime compatibility API for active engagement actions."""

from __future__ import annotations

from typing import Any

from . import runtime_active_engagement
from .contracts import InteractionResult, ViewerEvent
from .runtime_active_topic_api import RuntimeActiveTopicApiMixin
from .runtime_active_topic_rules_api import RuntimeActiveTopicRulesApiMixin


class RuntimeActiveEngagementApiMixin(
    RuntimeActiveTopicApiMixin,
    RuntimeActiveTopicRulesApiMixin,
):
    async def trigger_active_engagement(self) -> InteractionResult:
        return await runtime_active_engagement.trigger_active_engagement(self)

    async def maybe_trigger_active_engagement(self) -> InteractionResult | None:
        return await runtime_active_engagement.maybe_trigger_active_engagement(self)

    async def _active_engagement_event(self, live_state: dict[str, Any]) -> ViewerEvent:
        return await runtime_active_engagement.active_engagement_event(self, live_state)

    def _active_engagement_basic_event(self, live_state: dict[str, Any]) -> ViewerEvent:
        return runtime_active_engagement.active_engagement_basic_event(self, live_state)

    def _record_active_engagement_skip(self, event: ViewerEvent, reason: str) -> InteractionResult:
        return runtime_active_engagement.record_active_engagement_skip(self, event, reason)
