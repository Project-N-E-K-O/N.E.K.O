from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
STS2_PACKAGE_DIR = Path(__file__).resolve().parents[1]


def load_action_execution_mixin():
    for name, path in {
        "plugin": PROJECT_ROOT / "plugin",
        "plugin.plugins": PROJECT_ROOT / "plugin" / "plugins",
        "plugin.plugins.sts2_autoplay": STS2_PACKAGE_DIR,
    }.items():
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(path)]
            sys.modules[name] = module
    return importlib.import_module("plugin.plugins.sts2_autoplay.action_execution").ActionExecutionMixin


ActionExecutionMixin = load_action_execution_mixin()


class DummyLogger:
    def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
        pass


class DummyContextAnalyzer:
    def _combat_state(self, context: dict[str, Any]) -> dict[str, Any]:
        return context["snapshot"]["raw_state"]["combat"]

    def _iter_option_candidates(self, raw: dict[str, Any]):
        return []


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
