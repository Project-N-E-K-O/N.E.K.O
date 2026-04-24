"""Tests for @quick_action decorator and QuickActionConfig."""

from __future__ import annotations

from plugin.sdk.shared.core.decorators import plugin_entry, quick_action
from plugin.sdk.shared.core.events import EventMeta, QuickActionConfig
from plugin.sdk.shared.constants import EVENT_META_ATTR


def test_quick_action_decorator_sets_meta():
    @plugin_entry(id="test_entry", name="Test")
    @quick_action(icon="🌤️", priority=10)
    async def handler(self, **_):
        pass

    meta = getattr(handler, EVENT_META_ATTR)
    assert isinstance(meta, EventMeta)
    assert meta.quick_action is True
    assert isinstance(meta.quick_action_config, QuickActionConfig)
    assert meta.quick_action_config.icon == "🌤️"
    assert meta.quick_action_config.priority == 10


def test_quick_action_inline_via_plugin_entry():
    @plugin_entry(id="inline_test", name="Inline", quick_action=True)
    async def handler(self, **_):
        pass

    meta = getattr(handler, EVENT_META_ATTR)
    assert meta.quick_action is True
    assert isinstance(meta.quick_action_config, QuickActionConfig)


def test_quick_action_default_config():
    @plugin_entry(id="default_test", name="Default")
    @quick_action()
    async def handler(self, **_):
        pass

    meta = getattr(handler, EVENT_META_ATTR)
    assert meta.quick_action is True
    assert meta.quick_action_config.icon is None
    assert meta.quick_action_config.priority == 0


def test_no_quick_action():
    @plugin_entry(id="normal", name="Normal")
    async def handler(self, **_):
        pass

    meta = getattr(handler, EVENT_META_ATTR)
    assert meta.quick_action is False
