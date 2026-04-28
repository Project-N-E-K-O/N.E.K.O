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


def load_decisioning_mixin():
    for name, path in {
        "plugin": PROJECT_ROOT / "plugin",
        "plugin.plugins": PROJECT_ROOT / "plugin" / "plugins",
        "plugin.plugins.sts2_autoplay": STS2_PACKAGE_DIR,
    }.items():
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(path)]
            sys.modules[name] = module
    return importlib.import_module("plugin.plugins.sts2_autoplay.decisioning").DecisioningMixin


DecisioningMixin = load_decisioning_mixin()


class DummyLogger:
    def __init__(self) -> None:
        self.infos: list[str] = []

    def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self.infos.append(str(message))

    def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
        pass


class DummyCombatAnalyzer:
    def build_tactical_summary(self, combat: dict[str, Any], strategy_constraints_loader, character_strategy: str | None = None) -> dict[str, Any]:
        incoming = sum(int(enemy.get("intent_attack", 0) or 0) for enemy in combat.get("enemies", []) if isinstance(enemy, dict))
        current_block = int(combat.get("player_block", 0) or 0)
        lethal_targets = []
        for enemy in combat.get("enemies", []):
            if not isinstance(enemy, dict):
                continue
            target_index = enemy.get("index")
            hp = int(enemy.get("hp", 0) or 0) + int(enemy.get("block", 0) or 0)
            best_damage = max(
                (
                    self._card_total_damage_value(card, combat, target_index=target_index, strategy_constraints={})
                    for card in combat.get("hand", [])
                    if isinstance(card, dict) and target_index in (card.get("valid_target_indices") or [])
                ),
                default=0,
            )
            if hp > 0 and best_damage >= hp:
                lethal_targets.append({"index": target_index, "effective_hp": hp, "best_targeted_damage": best_damage})
        return {
            "incoming_attack_total": incoming,
            "current_block": current_block,
            "remaining_block_needed": max(0, incoming - current_block),
            "should_prioritize_defense": incoming > current_block,
            "should_prioritize_lethal": bool(lethal_targets),
            "lethal_targets": lethal_targets,
            "recommended_target_index": lethal_targets[0]["index"] if lethal_targets else 0,
        }

    def _card_total_damage_value(self, card: dict[str, Any], combat: dict[str, Any], target_index: Any = None, strategy_constraints=None) -> int:
        return int(card.get("damage", 0) or 0)

    def _card_block_value(self, card: dict[str, Any]) -> int:
        return int(card.get("block", 0) or 0)

    def _card_orb_damage_value(self, card: dict[str, Any], combat: dict[str, Any], target_index: Any = None) -> int:
        return int(card.get("orb_damage", 0) or 0)

    def _combat_player_block(self, combat: dict[str, Any]) -> int:
        return int(combat.get("player_block", 0) or 0)

    def _best_playable_block_card(self, combat: dict[str, Any]) -> dict[str, Any] | None:
        playable = [card for card in combat.get("hand", []) if isinstance(card, dict) and bool(card.get("playable"))]
        return max(playable, key=lambda card: int(card.get("block", 0) or 0), default=None)


class DecisionService(DecisioningMixin):
    def __init__(self) -> None:
        self._cfg = {"neko_desperate_enabled": True, "neko_desperate_hp_threshold": 0.5}
        self.logger = DummyLogger()
        self._combat_analyzer = DummyCombatAnalyzer()

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _configured_character_strategy(self) -> str:
        return "defect"

    def _load_strategy_constraints(self, strategy: str) -> dict[str, Any]:
        return {}

    def _combat_state(self, context: dict[str, Any]) -> dict[str, Any]:
        return context["snapshot"]["raw_state"]["combat"]

    def _combat_player_block(self, combat: dict[str, Any]) -> int:
        return self._combat_analyzer._combat_player_block(combat)

    def _enemy_intent_attack_total(self, enemy: dict[str, Any]) -> int:
        return int(enemy.get("intent_attack", 0) or 0)

    def _combat_orbs(self, combat: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def _find_defensive_action(self, actions: list[dict[str, Any]], combat: dict[str, Any], tactical_summary: dict[str, Any]) -> dict[str, Any] | None:
        block_card = self._best_playable_block_card(combat)
        if block_card is None:
            return None
        return self._action_for_card(actions, block_card)

    def _action_for_card(self, actions: list[dict[str, Any]], card: dict[str, Any], *, target_index: Any = None) -> dict[str, Any] | None:
        for action in actions:
            raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            if raw.get("card_index") == card.get("index"):
                selected = dict(action)
                selected_raw = dict(raw)
                if target_index is not None:
                    selected_raw["target_index"] = target_index
                selected["raw"] = selected_raw
                return selected
        return None


def combat_context(hand: list[dict[str, Any]], *, hp: int = 4, max_hp: int = 20, block: int = 0, incoming: int = 8, enemy_hp: int = 30) -> dict[str, Any]:
    return {
        "snapshot": {
            "raw_state": {
                "combat": {
                    "player": {"hp": hp, "max_hp": max_hp},
                    "player_block": block,
                    "player_energy": 3,
                    "hand": hand,
                    "enemies": [{"index": 0, "hp": enemy_hp, "intent_attack": incoming}],
                }
            }
        }
    }


@pytest.mark.unit
def test_desperate_prefers_defense_when_no_lethal() -> None:
    service = DecisionService()
    strike = {"index": 0, "name": "打击", "type": "attack", "card_type": "attack", "playable": True, "damage": 6, "valid_target_indices": [0]}
    defend = {"index": 1, "name": "防御", "type": "skill", "card_type": "skill", "playable": True, "block": 8}
    actions = [
        {"type": "play_card", "raw": {"card_index": 0}},
        {"type": "play_card", "raw": {"card_index": 1}},
    ]

    selected = service._select_desperate_action(actions, combat_context([strike, defend], enemy_hp=30))

    assert selected is not None
    assert selected["raw"]["card_index"] == 1


@pytest.mark.unit
def test_desperate_uses_attack_when_lethal_exists() -> None:
    service = DecisionService()
    strike = {"index": 0, "name": "打击", "type": "attack", "card_type": "attack", "playable": True, "damage": 12, "valid_target_indices": [0]}
    defend = {"index": 1, "name": "防御", "type": "skill", "card_type": "skill", "playable": True, "block": 8}
    actions = [
        {"type": "play_card", "raw": {"card_index": 0}},
        {"type": "play_card", "raw": {"card_index": 1}},
    ]

    selected = service._select_desperate_action(actions, combat_context([strike, defend], enemy_hp=10))

    assert selected is not None
    assert selected["raw"]["card_index"] == 0
    assert selected["raw"]["target_index"] == 0


@pytest.mark.unit
def test_marginal_benefit_uses_remaining_cards_for_setup_synergy() -> None:
    service = DecisionService()
    bash = {"index": 0, "name": "痛击 易伤", "type": "skill", "card_type": "skill", "playable": True, "cost": 1, "description": "给予易伤"}
    strike = {"index": 1, "name": "打击", "type": "attack", "card_type": "attack", "playable": True, "cost": 1, "damage": 20, "valid_target_indices": [0]}
    combat = {"player_energy": 3, "player_block": 0, "hand": [bash, strike], "enemies": [{"index": 0, "hp": 40, "intent_attack": 0}]}
    tactical = {"recommended_target_index": 0, "incoming_attack_total": 0}
    state = {"energy": 3, "block": 0, "str_stacks": 0, "weaken_stacks": 0, "vulnerable_stacks": 0}

    without_followup = service._calc_marginal_benefit(bash, state, combat, tactical, {}, remaining_cards=[])
    with_followup = service._calc_marginal_benefit(bash, state, combat, tactical, {}, remaining_cards=[strike])

    assert with_followup > without_followup
