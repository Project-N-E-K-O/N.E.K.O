from __future__ import annotations

from pathlib import Path

import pytest

from plugin.plugins.sts2_autoplay.companion_evaluator import STS2CompanionEvaluator
from plugin.plugins.sts2_autoplay.strategy_parser import STS2StrategyParser
from plugin.plugins.sts2_autoplay.strategy_repository import STS2StrategyRepository


class DummyLogger:
    def warning(self, *args, **kwargs):
        return None


class DummyPreferenceStore:
    def list_domain(self, domain: str):
        return []

    def get(self, domain: str, key: str):
        return None


@pytest.mark.unit
def test_strategy_parser_lists_role_directories(tmp_path: Path) -> None:
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    (strategies / "player_overrides.md").write_text("# overrides\n", encoding="utf-8")
    defect = strategies / "defect"
    defect.mkdir()
    (defect / "base.md").write_text("# base\n", encoding="utf-8")

    parser = STS2StrategyParser(DummyLogger(), strategies_dir=strategies)

    assert parser.available_strategies() == ["defect"]


@pytest.mark.unit
def test_strategy_parser_loads_base_scene_and_overrides(tmp_path: Path) -> None:
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    (strategies / "player_overrides.md").write_text("# legacy overrides\n", encoding="utf-8")
    defect = strategies / "defect"
    defect.mkdir()
    (defect / "base.md").write_text("# base\n", encoding="utf-8")
    (defect / "combat.md").write_text("# combat\n", encoding="utf-8")
    overrides = strategies / "user_overrides" / "defect"
    overrides.mkdir(parents=True)
    (overrides / "combat.md").write_text("# user combat override\n", encoding="utf-8")

    parser = STS2StrategyParser(DummyLogger(), strategies_dir=strategies)
    prompt = parser.load_prompt("defect", "combat")

    assert prompt is not None
    assert "# base" in prompt
    assert "# combat" in prompt
    assert "# user combat override" in prompt
    assert "# legacy overrides" in prompt


@pytest.mark.unit
def test_strategy_repository_maps_screen_to_scene() -> None:
    repo = STS2StrategyRepository(DummyLogger(), DummyPreferenceStore(), default_strategy="defect")



@pytest.mark.unit
def test_strategy_repository_resolves_default_strategy_name() -> None:
    repo = STS2StrategyRepository(DummyLogger(), DummyPreferenceStore(), default_strategy="defect")

    assert repo._resolve_strategy_name(None) == "defect"
    assert repo._resolve_strategy_name("the_defect") == "defect"


@pytest.mark.unit
def test_strategy_repository_prefers_record_preferred_option_index() -> None:
    class PreferenceStoreWithRecord(DummyPreferenceStore):
        def list_domain(self, domain: str):
            return [{"value": {"preferred_option_index": 2}}]

    repo = STS2StrategyRepository(DummyLogger(), PreferenceStoreWithRecord(), default_strategy="defect")
    context = repo.build_context({"screen": "reward", "classification": {}, "summary_context": {"payload": {}}})

    assert context["strategy_directives"]["preferred_option_index"] == 2


@pytest.mark.unit
def test_strategy_repository_falls_back_when_requested_strategy_missing(tmp_path: Path) -> None:
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    (strategies / "player_overrides.md").write_text("# overrides\n", encoding="utf-8")
    defect = strategies / "defect"
    defect.mkdir()
    (defect / "base.md").write_text("# base\n", encoding="utf-8")

    repo = STS2StrategyRepository(DummyLogger(), DummyPreferenceStore(), default_strategy="defect")
    repo._parser = STS2StrategyParser(DummyLogger(), strategies_dir=strategies)



@pytest.mark.unit
def test_strategy_repository_applies_event_override_to_prompt() -> None:
    class PreferenceStoreWithOverride(DummyPreferenceStore):
        def get(self, domain: str, key: str):
            if domain == "event_overrides" and key == "golden_idol":
                return {"value": {"instruction": "优先低代价路线"}}
            return None

    repo = STS2StrategyRepository(DummyLogger(), PreferenceStoreWithOverride(), default_strategy="defect")
    context = repo.build_context(
        {
            "screen": "event",
            "classification": {},
            "summary_context": {"payload": {"event_id": "golden_idol"}},
        }
    )

    assert context["event_override"] is not None
    assert "## 用户指点覆盖" in context["strategy_prompt"]
    assert "优先低代价路线" in context["strategy_prompt"]


@pytest.mark.unit
def test_companion_evaluator_primary_message_includes_card_cost() -> None:
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "combat",
            "payload": {
                "player": {"current_hp": 40, "max_hp": 80, "block": 2},
                "enemies": [{"intent_damage": 10}],
                "playable_card_summaries": [
                    {"name": "邪眼", "energy_cost": 1, "star_cost": 0, "costs_x": False, "star_costs_x": False, "requires_target": False, "playable": True}
                ],
            },
        },
        situation_summary={
            "kind": "combat",
            "text": "当前战斗状态。",
            "static_text": "当前战斗状态。",
            "source": "snapshot",
            "delta": {},
            "before": {},
            "after": {},
        },
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {},
        },
    )

    assert "邪眼（1费）" in result["primary_message"]
    assert "建议优先防御或找减伤线" in result["primary_message"]


@pytest.mark.unit
def test_companion_evaluator_uses_strategy_context_for_commentary() -> None:
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "combat",
            "payload": {
                "player": {"current_hp": 18, "max_hp": 80, "block": 4},
                "enemies": [{"intent_damage": 16}],
            },
        },
        situation_summary={
            "kind": "combat",
            "text": "当前战斗状态，玩家血量 18/80。",
            "static_text": "当前战斗状态，玩家血量 18/80。",
            "source": "snapshot",
            "delta": {},
            "before": {},
            "after": {},
        },
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {"must": ["先保血"]},
        },
    )

    assert result["strategy_name"] == "defect"
    assert result["risk_level"] == "high"
    assert "当前局势偏危险" not in result["commentary"]
    assert "建议优先防御或找减伤线" in result["commentary"]


@pytest.mark.unit
def test_companion_evaluator_combat_intent_drives_advice() -> None:
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "combat",
            "payload": {
                "player": {"current_hp": 30, "max_hp": 80, "block": 0},
                "enemies": [{"name": "缩小甲虫", "intent": "SHRINKER_MOVE", "intent_damage": 0}],
            },
        },
        situation_summary={
            "kind": "combat",
            "text": "当前战斗状态。",
            "static_text": "当前战斗状态。",
            "source": "snapshot",
            "delta": {},
            "before": {},
            "after": {},
        },
        strategy_context={
            "strategy_name": "ironclad",
            "strategy_directives": {},
        },
    )



@pytest.mark.unit
def test_companion_evaluator_combat_uses_heuristic_named_card_suggestion() -> None:
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "combat",
            "payload": {
                "player": {"current_hp": 18, "max_hp": 80, "block": 0},
                "enemies": [{"name": "Louse", "intent_damage": 12}],
                "playable_card_summaries": [
                    {"name": "Strike", "energy_cost": 1, "star_cost": 0, "costs_x": False, "star_costs_x": False, "requires_target": True, "playable": True, "effect": "造成伤害"},
                    {"name": "Defend", "energy_cost": 1, "star_cost": 0, "costs_x": False, "star_costs_x": False, "requires_target": False, "playable": True, "effect": "获得格挡"},
                ],
            },
        },
        situation_summary={
            "kind": "combat",
            "text": "当前战斗状态。",
            "static_text": "当前战斗状态。",
            "source": "snapshot",
            "delta": {},
            "before": {},
            "after": {},
        },
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {},
        },
    )

    assert "Defend" in result["suggestion"]


@pytest.mark.unit
def test_companion_evaluator_combat_includes_target_and_reason_text() -> None:
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "combat",
            "payload": {
                "player": {"current_hp": 40, "max_hp": 80, "block": 0},
                "enemies": [{"name": "Louse", "intent_damage": 6, "current_hp": 6}],
                "playable_card_summaries": [
                    {"name": "Strike", "energy_cost": 1, "star_cost": 0, "costs_x": False, "star_costs_x": False, "requires_target": True, "playable": True, "effect": "造成6点伤害"},
                ],
            },
        },
        situation_summary={
            "kind": "combat",
            "text": "当前战斗状态。",
            "static_text": "当前战斗状态。",
            "source": "snapshot",
            "delta": {},
            "before": {},
            "after": {},
        },
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {},
        },
    )

    assert "Strike" in result["suggestion"]
    assert "Louse" in result["suggestion"]
    assert "理由：" in result["suggestion"]


@pytest.mark.unit
def test_companion_evaluator_map_prefers_preferred_route_index_over_first_node() -> None:
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "map",
            "payload": {
                "current_hp": 20,
                "max_hp": 80,
                "gold": 99,
                "travelable_nodes": [
                    {"index": 0, "type": "elite"},
                    {"index": 1, "type": "shop"},
                ],
                "travelable_node_types": ["elite", "shop"],
            },
        },
        situation_summary={"kind": "map", "text": "当前地图选择状态。"},
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {"preferred_option_index": 1},
        },
    )



@pytest.mark.unit
def test_companion_evaluator_shop_card_prefers_preferred_option_index_over_first_card() -> None:
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "shop",
            "payload": {
                "shop_cards": [
                    {"index": 0, "name": "Strike"},
                    {"index": 1, "name": "Glacier"},
                ],
                "shop_card_names": ["Strike", "Glacier"],
            },
        },
        situation_summary={"kind": "shop", "text": "当前商店状态。"},
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {"preferred_option_index": 1},
        },
    )

    assert "Glacier" in result["suggestion"]
    assert "Strike" not in result["suggestion"]


@pytest.mark.unit
def test_companion_evaluator_shop_relic_does_not_default_to_first_item_without_preference() -> None:
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "shop",
            "payload": {
                "shop_relics": [
                    {"index": 0, "name": "Burning Blood"},
                    {"index": 1, "name": "Frozen Core"},
                ],
                "shop_relic_names": ["Burning Blood", "Frozen Core"],
            },
        },
        situation_summary={"kind": "shop", "text": "当前商店状态。"},
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {},
        },
    )

    assert result["suggestion"] == "建议优先考虑高价值购买或删牌，而不是随手消费。"








@pytest.mark.unit
def test_strategy_parser_preserves_route_policy_frontmatter(tmp_path: Path) -> None:
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    defect = strategies / "defect"
    defect.mkdir()
    (defect / "base.md").write_text(
        """---
constraints:
  route_policy:
    lookahead_depth: 6
    weights:
      rest: 12
      shop: 8
      elite: -10
---
# base
""",
        encoding="utf-8",
    )

    parser = STS2StrategyParser(DummyLogger(), strategies_dir=strategies)
    constraints = parser.load_constraints("defect", "map")

    assert constraints["route_policy"]["lookahead_depth"] == 6
    assert constraints["route_policy"]["weights"]["rest"] == 12
    assert constraints["route_policy"]["weights"]["elite"] == -10
