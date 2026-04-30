from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


from plugin.plugins.sts2_autoplay.tests._isolated_loader import load_isolated_sts2_module


PROJECT_ROOT = Path(__file__).resolve().parents[4]


ActionExecutionMixin = load_isolated_sts2_module("sts2_action_execution_test_pkg", "action_execution").ActionExecutionMixin


class DummyLogger:
    def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
        pass


class DummyContextAnalyzer:
    def _combat_state(self, context: dict[str, Any]) -> dict[str, Any]:
        return context["snapshot"]["raw_state"]["combat"]

    def _iter_option_candidates(self, raw: dict[str, Any]):
        return raw.get("options", [])


class ActionService(ActionExecutionMixin):
    def __init__(self) -> None:
        self.logger = DummyLogger()
        self._context_analyzer = DummyContextAnalyzer()

    def _find_playable_card_index(self, context: dict[str, Any]) -> int:
        return int(context.get("fallback_card_index", 0))

    def _find_card_target_index(self, context: dict[str, Any], card_index: int) -> int | None:
        return context.get("fallback_target_index")

    def _card_reward_options(self, raw: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def _character_selection_options(self, raw: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def _shop_card_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def _shop_relic_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def _shop_potion_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return []


def combat_context(hand: list[dict[str, Any]], actions: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    context = {
        "snapshot": {
            "raw_state": {
                "combat": {
                    "hand": hand,
                }
            }
        },
        "actions": actions,
    }
    context.update(extra)
    return context


@pytest.mark.unit
def test_validate_llm_decision_revalidates_play_card_fallback_target_combo() -> None:
    service = ActionService()
    hand = [
        {"index": 0, "name": "打击", "playable": True, "valid_target_indices": [0]},
    ]
    actions = [{"type": "play_card", "raw": {"type": "play_card", "requires_index": True}}]
    context = combat_context(hand, actions, fallback_card_index=0, fallback_target_index=99)

    validated = service._validate_llm_decision({"action_type": "play_card", "kwargs": {}}, context)

    assert validated is None


@pytest.mark.unit
def test_validate_play_card_target_combo_filters_dirty_valid_targets() -> None:
    service = ActionService()
    hand = [
        {"index": 0, "name": "全体攻击", "playable": True, "valid_target_indices": ["dirty"]},
    ]
    context = combat_context(hand, [])
    normalized_kwargs = {"card_index": 0}

    assert service._validate_play_card_target_combo(normalized_kwargs, context, {"action_type": "play_card", "kwargs": {}}) is True
    assert "target_index" not in normalized_kwargs


@pytest.mark.unit
def test_validate_llm_decision_fills_missing_play_card_card_index_when_target_present() -> None:
    service = ActionService()
    hand = [
        {"index": 0, "name": "打击", "playable": True, "valid_target_indices": [0]},
    ]
    actions = [{"type": "play_card", "raw": {"type": "play_card", "requires_index": True}}]
    context = combat_context(hand, actions, fallback_card_index=0)

    validated = service._validate_llm_decision({"action_type": "play_card", "kwargs": {"target_index": 0}}, context)

    assert validated is not None
    assert validated["raw"]["card_index"] == 0
    assert validated["raw"]["target_index"] == 0


@pytest.mark.unit
def test_validate_llm_decision_rejects_play_card_without_card_index_when_fallback_unavailable() -> None:
    service = ActionService()
    hand = [
        {"index": 0, "name": "打击", "playable": True, "valid_target_indices": [0]},
    ]
    actions = [{"type": "play_card", "raw": {"type": "play_card", "requires_index": True}}]
    context = combat_context(hand, actions, fallback_card_index=99)

    assert service._validate_llm_decision({"action_type": "play_card", "kwargs": {"target_index": 0}}, context) is None


@pytest.mark.unit
def test_unknown_index_action_does_not_expose_blind_default_index() -> None:
    service = ActionService()
    raw = {"type": "unknown_index_action", "requires_index": True}
    context = combat_context([], [])

    assert service._allowed_kwargs_impl("unknown_index_action", raw, context) == {}


@pytest.mark.unit
def test_unknown_index_action_does_not_fill_blind_default_index() -> None:
    service = ActionService()
    raw = {"type": "unknown_index_action", "requires_index": True}
    context = combat_context([], [])

    assert service._normalize_action_kwargs("unknown_index_action", raw, context) == {}


@pytest.mark.unit
def test_unknown_index_action_exposes_generic_candidate_indices() -> None:
    service = ActionService()
    raw = {
        "type": "unknown_index_action",
        "requires_index": True,
        "options": [[{"index": 2}, {"option_index": "4"}, {"label": "fallback index"}]],
    }
    context = combat_context([], [])

    assert service._allowed_kwargs_impl("unknown_index_action", raw, context) == {"index": [2, 4]}


@pytest.mark.unit
def test_unknown_index_action_fills_first_generic_candidate_index() -> None:
    service = ActionService()
    raw = {
        "type": "unknown_index_action",
        "requires_index": True,
        "options": [[{"index": 2}, {"index": 4}]],
    }
    context = combat_context([], [])

    assert service._normalize_action_kwargs("unknown_index_action", raw, context) == {"options": [[{"index": 2}, {"index": 4}]], "index": 2}


@pytest.mark.unit
def test_shop_remove_selection_uses_shop_remove_index_when_flagged() -> None:
    service = ActionService()
    raw = {"type": "select_deck_card", "shop_remove_selection": True}
    context = combat_context([], [], shop_remove_index=7)
    service._find_shop_remove_card_index_for_selection = lambda ctx: ctx.get("shop_remove_index")
    service._find_preferred_card_option_index = lambda raw_action, ctx: 1

    assert service._normalize_action_kwargs("select_deck_card", raw, context) == {"option_index": 7}
