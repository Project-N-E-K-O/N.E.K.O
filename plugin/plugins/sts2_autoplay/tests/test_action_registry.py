from __future__ import annotations

import pytest

from plugin.plugins.sts2_autoplay.action_registry import STS2ActionRegistry


@pytest.mark.unit
def test_action_registry_builds_ids_categories_and_defaults() -> None:
    snapshot = {
        "available_actions": [
            {
                "type": "choose_event_option",
                "label": "Option A",
                "raw": {"name": "choose_event_option", "index": 2, "requires_index": True},
            },
            {
                "type": "play_card",
                "label": "Strike",
                "raw": {"name": "play_card", "card_index": 1, "target_index": 0, "requires_target": True},
            },
        ]
    }

    registry = STS2ActionRegistry().build(snapshot)

    assert len(registry) == 2
    assert registry[0]["category"] == "event"
    assert registry[0]["default_kwargs"] == {"option_index": 2}
    assert registry[1]["category"] == "combat"
    assert registry[1]["default_kwargs"] == {"card_index": 1, "target_index": 0}
    assert registry[0]["id"]
    assert registry[1]["id"]


@pytest.mark.unit
def test_action_registry_finders_work() -> None:
    registry = [
        {"id": "abc", "type": "play_card"},
        {"id": "def", "type": "end_turn"},
    ]
    action_registry = STS2ActionRegistry()

    assert action_registry.find_by_id(registry, "abc") == registry[0]
    assert action_registry.find_by_type(registry, "end_turn") == registry[1]
    assert action_registry.find_by_type(registry, "missing") is None
