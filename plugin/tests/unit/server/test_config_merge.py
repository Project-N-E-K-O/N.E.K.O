from __future__ import annotations

import copy

import pytest

from plugin.core.context import PluginContext
from plugin.server.infrastructure.config_merge import deep_merge


@pytest.mark.plugin_unit
def test_deep_merge_strips_nested_markers_on_missing_or_scalar_section() -> None:
    missing_base: dict[str, object] = {}
    missing_updates = {"sec": {"obsolete": "__DELETE__", "ok": True}}
    missing_base_snapshot = copy.deepcopy(missing_base)
    missing_updates_snapshot = copy.deepcopy(missing_updates)
    assert deep_merge(missing_base, missing_updates) == {"sec": {"ok": True}}
    assert missing_base == missing_base_snapshot
    assert missing_updates == missing_updates_snapshot

    scalar_base: dict[str, object] = {"sec": 1}
    scalar_updates = {"sec": {"obsolete": "__DELETE__", "ok": True}}
    scalar_base_snapshot = copy.deepcopy(scalar_base)
    scalar_updates_snapshot = copy.deepcopy(scalar_updates)
    assert deep_merge(scalar_base, scalar_updates) == {"sec": {"ok": True}}
    assert scalar_base == scalar_base_snapshot
    assert scalar_updates == scalar_updates_snapshot

    nested_base: dict[str, object] = {"sec": "old"}
    nested_updates = {
        "sec": {
            "nested": {"dead": "__DELETE__", "live": 2},
            "rep": {"__replace__": True, "x": 1},
            "gone": "__DELETE__",
        }
    }
    assert deep_merge(nested_base, nested_updates) == {
        "sec": {"nested": {"live": 2}, "rep": {"x": 1}}
    }

    assert deep_merge({}, {"sec": {}}) == {"sec": {}}
    assert deep_merge({"sec": {"a": 1}}, {"sec": {}}) == {"sec": {}}
    assert deep_merge(
        {"sec": {"keep": 1, "drop": 2}},
        {"sec": {"drop": "__DELETE__", "add": 3}},
    ) == {"sec": {"keep": 1, "add": 3}}


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("base", "updates"),
    [
        ({}, {"sec": {"obsolete": "__DELETE__", "ok": True}}),
        ({"sec": 1}, {"sec": {"obsolete": "__DELETE__", "ok": True}}),
        (
            {"sec": "old"},
            {
                "sec": {
                    "nested": {"dead": "__DELETE__", "live": 2},
                    "rep": {"__replace__": True, "x": 1},
                    "gone": "__DELETE__",
                }
            },
        ),
        ({"sec": {"old": 1, "keep": 2}}, {"sec": {"__replace__": True, "fresh": 3}}),
        ({}, {"sec": {}}),
        ({"sec": {"a": 1}}, {"sec": {}}),
        ({"sec": {"keep": 1, "drop": 2}}, {"sec": {"drop": "__DELETE__", "add": 3}}),
    ],
)
def test_deep_merge_matches_plugin_context_merge_config_copy(
    base: dict[str, object],
    updates: dict[str, object],
) -> None:
    expected = PluginContext._merge_config_copy(base, updates)
    assert deep_merge(base, updates) == expected
