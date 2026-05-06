"""Unit tests for plugin.server.application.actions.aggregation_service."""
from __future__ import annotations

import pytest

from plugin.server.application.actions.aggregation_service import ActionAggregationService
from plugin.server.domain.action_models import ActionDescriptor


class _OkProvider:
    def __init__(self, actions: list[ActionDescriptor]) -> None:
        self._actions = actions

    async def get_actions(self, plugin_id: str | None = None) -> list[ActionDescriptor]:
        if plugin_id:
            return [a for a in self._actions if a.plugin_id == plugin_id]
        return list(self._actions)


class _FailProvider:
    async def get_actions(self, plugin_id: str | None = None) -> list[ActionDescriptor]:
        raise RuntimeError("provider exploded")


def _make_action(action_id: str, plugin_id: str = "demo") -> ActionDescriptor:
    return ActionDescriptor(
        action_id=action_id,
        type="instant",
        label=action_id,
        category="Test",
        plugin_id=plugin_id,
        control="button",
    )


@pytest.mark.plugin_unit
@pytest.mark.asyncio
class TestAggregationService:
    async def test_merges_from_multiple_providers(self) -> None:
        svc = ActionAggregationService.__new__(ActionAggregationService)
        svc._providers = [
            _OkProvider([_make_action("a")]),
            _OkProvider([_make_action("b")]),
        ]
        actions = await svc.aggregate_actions()
        assert len(actions) == 2
        assert {a.action_id for a in actions} == {"a", "b"}

    async def test_failing_provider_does_not_block_others(self) -> None:
        svc = ActionAggregationService.__new__(ActionAggregationService)
        svc._providers = [
            _OkProvider([_make_action("ok")]),
            _FailProvider(),
        ]
        actions = await svc.aggregate_actions()
        assert len(actions) == 1
        assert actions[0].action_id == "ok"

    async def test_empty_providers(self) -> None:
        svc = ActionAggregationService.__new__(ActionAggregationService)
        svc._providers = []
        actions = await svc.aggregate_actions()
        assert actions == []

    async def test_plugin_id_filter_passed_through(self) -> None:
        svc = ActionAggregationService.__new__(ActionAggregationService)
        svc._providers = [
            _OkProvider([_make_action("a", "p1"), _make_action("b", "p2")]),
        ]
        actions = await svc.aggregate_actions(plugin_id="p1")
        assert len(actions) == 1
        assert actions[0].plugin_id == "p1"
