"""Unit tests for plugin.server.application.actions.list_actions_provider."""
from __future__ import annotations

import threading
from typing import Any
from unittest.mock import patch

import pytest

from plugin.server.application.actions import list_actions_provider as module
from plugin.server.domain.action_models import ActionDescriptor


class _FakeState:
    def __init__(self, plugins: dict[str, Any] | None = None) -> None:
        self.plugins = plugins or {}

    def get_plugins_snapshot_cached(self, **_kw: Any) -> dict[str, Any]:
        return dict(self.plugins)


def _collect(fake_state: _FakeState, plugin_id: str | None = None) -> list[ActionDescriptor]:
    with patch("plugin.core.state.state", fake_state):
        return module._collect_list_actions_sync(plugin_id)


@pytest.mark.plugin_unit
class TestMapListAction:
    def test_chat_inject_with_target(self) -> None:
        d = module._map_list_action("demo", "Demo", {
            "id": "greet",
            "kind": "chat_inject",
            "label": "Greet",
            "target": "/hello",
        })
        assert d is not None
        assert d.type == "chat_inject"
        assert d.inject_text == "/hello"

    def test_chat_inject_default_target(self) -> None:
        d = module._map_list_action("demo", "Demo", {
            "id": "greet",
            "kind": "chat_inject",
            "label": "Greet",
        })
        assert d is not None
        assert d.inject_text == "@Demo /greet"

    def test_navigation_kinds(self) -> None:
        for kind in ("ui", "url", "route"):
            d = module._map_list_action("demo", "Demo", {
                "id": "open",
                "kind": kind,
                "label": "Open",
                "target": "http://example.com",
            })
            assert d is not None
            assert d.type == "navigation"
            assert d.open_in == "new_tab"

    def test_navigation_same_tab(self) -> None:
        d = module._map_list_action("demo", "Demo", {
            "id": "open",
            "kind": "url",
            "label": "Open",
            "target": "http://example.com",
            "open_in": "same_tab",
        })
        assert d is not None
        assert d.open_in == "same_tab"

    def test_navigation_requires_string_target(self) -> None:
        for target in (None, "", "   ", {"path": "/ui"}):
            d = module._map_list_action("demo", "Demo", {
                "id": "open",
                "kind": "ui",
                "label": "Open",
                "target": target,
            })
            assert d is None

    def test_quick_action_parses_string_false(self) -> None:
        d = module._map_list_action("demo", "Demo", {
            "id": "greet",
            "kind": "chat_inject",
            "label": "Greet",
            "quick_action": "false",
        })

        assert d is not None
        assert d.quick_action is False

    def test_invalid_priority_falls_back_to_zero(self) -> None:
        d = module._map_list_action("demo", "Demo", {
            "id": "open",
            "kind": "ui",
            "label": "Open",
            "target": "/ui",
            "priority": "not-a-number",
        })

        assert d is not None
        assert d.priority == 0

    def test_non_routable_kinds_are_skipped(self) -> None:
        for kind in ("toggle", "trigger", "action", "button", "run", "custom_kind"):
            d = module._map_list_action("demo", "Demo", {
                "id": f"do_{kind}",
                "kind": kind,
                "label": f"Do {kind}",
            })
            assert d is None, f"kind={kind} is not executable through the actions service yet"

    def test_empty_kind_returns_none(self) -> None:
        d = module._map_list_action("demo", "Demo", {
            "id": "noop",
            "kind": "",
            "label": "Noop",
        })
        assert d is None

    def test_missing_id_returns_none(self) -> None:
        d = module._map_list_action("demo", "Demo", {"kind": "action", "label": "X"})
        assert d is None

    def test_action_id_format(self) -> None:
        d = module._map_list_action("my-plugin", "My Plugin", {
            "id": "do_stuff",
            "kind": "ui",
            "label": "Do Stuff",
            "target": "/ui",
        })
        assert d is not None
        assert d.action_id == "my-plugin:do_stuff"
        assert d.plugin_id == "my-plugin"


@pytest.mark.plugin_unit
class TestCollectListActions:
    def test_empty_plugins(self) -> None:
        assert _collect(_FakeState()) == []

    def test_plugin_without_list_actions(self) -> None:
        state = _FakeState(plugins={"demo": {"name": "Demo"}})
        assert _collect(state) == []

    def test_plugin_with_list_actions(self) -> None:
        state = _FakeState(plugins={"demo": {
            "name": "Demo",
            "list_actions": [
                {"id": "a", "kind": "action", "label": "A"},
                {"id": "b", "kind": "chat_inject", "label": "B"},
            ],
        }})
        actions = _collect(state)
        assert len(actions) == 1
        assert actions[0].type == "chat_inject"

    def test_filter_by_plugin_id(self) -> None:
        state = _FakeState(plugins={
            "a": {"name": "A", "list_actions": [{"id": "x", "kind": "action", "label": "X"}]},
            "b": {"name": "B", "list_actions": [{"id": "y", "kind": "ui", "label": "Y", "target": "/ui"}]},
        })
        actions = _collect(state, plugin_id="b")
        assert len(actions) == 1
        assert actions[0].action_id == "b:y"

    def test_invalid_list_action_skipped(self) -> None:
        state = _FakeState(plugins={"demo": {
            "name": "Demo",
            "list_actions": [
                "not-a-dict",
                {"id": "ok", "kind": "action", "label": "OK"},
                {"id": "open", "kind": "ui", "label": "Open", "target": "/ui"},
            ],
        }})
        actions = _collect(state)
        assert len(actions) == 1
        assert actions[0].action_id == "demo:open"

    def test_non_mapping_meta_skipped(self) -> None:
        state = _FakeState(plugins={"bad": "not-a-dict"})
        assert _collect(state) == []
