from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[4]
STS2_DIR = Path(__file__).resolve().parents[1]
STRATEGY_NAMES = ["defect", "ironclad", "silent_hunter", "necrobinder", "regent"]


class DummyLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.infos: list[str] = []

    def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self.warnings.append(str(message))

    def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self.errors.append(str(message))

    def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self.infos.append(str(message))


def load_module(module_name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(module_name, PROJECT_ROOT / relative_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def parser_module():
    return load_module("sts2_autoplay_parser_for_tests", "plugin/plugins/sts2_autoplay/parser.py")


@pytest.fixture(scope="module")
def strategy_module():
    return load_module("sts2_autoplay_strategy_for_tests", "plugin/plugins/sts2_autoplay/strategy.py")


@pytest.fixture(scope="module")
def combat_module():
    return load_module("sts2_autoplay_combat_for_tests", "plugin/plugins/sts2_autoplay/combat.py")


@pytest.fixture()
def parser(parser_module):
    return parser_module.StrategyParser(DummyLogger())


@pytest.fixture()
def selector(strategy_module):
    return strategy_module.HeuristicSelector(DummyLogger())


@pytest.fixture()
def combat_analyzer(combat_module):
    return combat_module.CombatAnalyzer(DummyLogger())


class SelectorStub:
    def __init__(self, parser: Any, strategy: str) -> None:
        self._parser = parser
        self._strategy = strategy

    def _configured_character_strategy(self) -> str:
        return self._strategy

    def _load_strategy_constraints(self, strategy: str) -> dict[str, Any]:
        return self._parser._load_strategy_constraints(strategy)

    def _score_defect_card_option(self, option: dict[str, Any], context: dict[str, Any]) -> int:
        return 0

    def _score_defect_map_option(self, option: dict[str, Any], context: dict[str, Any]) -> int:
        return 0

    def _shop_card_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return context.get("shop_cards", [])

    def _shop_relic_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return context.get("shop_relics", [])

    def _shop_potion_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return context.get("shop_potions", [])

    def _potion_slots(self, context: dict[str, Any]) -> int:
        return 2

    def _potions(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return []


def first_alias(bucket: dict[str, Any]) -> str:
    assert bucket
    first_entry = next(iter(bucket.values()))
    items = first_entry.get("items") if isinstance(first_entry, dict) else first_entry
    assert isinstance(items, list)
    assert items
    return str(items[0])


@pytest.mark.unit
@pytest.mark.parametrize("strategy", STRATEGY_NAMES)
def test_strategy_docs_have_frontmatter_and_standard_constraint_sections(strategy: str) -> None:
    path = STS2_DIR / "strategies" / f"{strategy}.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "\nconstraints:" in text
    assert "\n## 程序约束\n" in text


@pytest.mark.unit
@pytest.mark.parametrize("strategy", STRATEGY_NAMES)
def test_strategy_constraints_parse_non_empty_structured_buckets(parser: Any, strategy: str) -> None:
    constraints = parser._load_strategy_constraints(strategy)
    assert constraints["required"]
    assert constraints["high_priority"]
    assert constraints["map_preferences"]
    assert constraints["combat_preferences"]
    assert constraints["combat_estimators"]
    card_preferences = constraints["shop_preferences"]["card"]
    assert any(
        values
        for values in card_preferences.values()
        if isinstance(values, (list, dict, set, tuple))
    )


@pytest.mark.unit
def test_numeric_strategy_names_are_not_supported_before_plugin_release(parser: Any) -> None:
    assert parser._normalize_character_strategy_name(0) == "0"
    assert parser._normalize_character_strategy_name("1") == "1"
    assert parser._normalize_character_strategy_name("2") == "2"
    with pytest.raises(RuntimeError):
        parser._ensure_character_strategy_exists("1")
    with pytest.raises(RuntimeError):
        parser._ensure_character_strategy_exists("2")


@pytest.mark.unit
def test_strategy_frontmatter_accepts_utf8_bom(parser: Any) -> None:
    parsed = parser._parse_strategy_frontmatter("\ufeff---\nconstraints:\n  required:\n    zap: [zap]\n---\nbody")

    assert parsed == {"required": {"zap": ["zap"]}}


@pytest.mark.unit
def test_strategy_constraint_sections_include_supported_child_under_unrelated_parent(parser: Any) -> None:
    rendered = parser._strategy_sections_for_constraints("## 其他说明\n无关内容\n### 战斗偏好\n- 优先防御\n")

    assert "### 其他说明" not in rendered
    assert "#### 战斗偏好" in rendered
    assert "- 优先防御" in rendered


@pytest.mark.unit
def test_invalid_strategy_name_fails_fast_with_available_list(parser: Any) -> None:
    with pytest.raises(RuntimeError) as excinfo:
        parser._ensure_character_strategy_exists("missing_strategy")
    message = str(excinfo.value)
    assert "missing_strategy" in message
    assert "defect" in message
    assert "regent" in message


@pytest.mark.unit
def test_generic_card_scoring_returns_structured_reasons(parser: Any, selector: Any) -> None:
    constraints = parser._load_strategy_constraints("ironclad")
    alias = first_alias(constraints["required"])
    stub = SelectorStub(parser, "ironclad")
    details = selector.score_strategy_card_option_details({"index": 1, "texts": {alias}}, {}, stub)
    assert details["score"] > 0
    assert details["constraint_hits"]
    hit = details["constraint_hits"][0]
    assert hit["strategy"] == "ironclad"
    assert hit["scene"] == "card_reward"
    assert hit["category"] == "required"
    assert hit["score_delta"] > 0


@pytest.mark.unit
def test_shop_named_scoring_covers_relic_and_potion(parser: Any, selector: Any) -> None:
    constraints = parser._load_strategy_constraints("defect")
    relic_alias = first_alias(constraints["shop_preferences"]["relic"]["high_priority"])
    potion_alias = first_alias(constraints["shop_preferences"]["potion"]["high_priority"])
    stub = SelectorStub(parser, "defect")
    relic = selector.score_shop_named_option_details({"index": 1, "texts": {relic_alias}}, {}, "relic", stub)
    potion = selector.score_shop_named_option_details({"index": 2, "texts": {potion_alias}}, {}, "potion", stub)
    assert relic["score"] > 0
    assert potion["score"] > 0
    assert relic["constraint_hits"][0]["scene"] == "shop_relic"
    assert potion["constraint_hits"][0]["scene"] == "shop_potion"


@pytest.mark.unit
def test_low_priority_card_is_penalized(parser: Any, selector: Any) -> None:
    constraints = parser._load_strategy_constraints("silent_hunter")
    alias = first_alias(constraints["low_priority"])
    stub = SelectorStub(parser, "silent_hunter")
    details = selector.score_strategy_card_option_details({"index": 1, "texts": {alias}}, {}, stub)
    assert details["score"] < 0
    assert details["constraint_hits"][0]["category"] == "low_priority"


@pytest.mark.unit
def test_map_scoring_uses_top_level_map_preferences(parser: Any, selector: Any) -> None:
    constraints = parser._load_strategy_constraints("regent")
    alias = first_alias(constraints["map_preferences"])
    stub = SelectorStub(parser, "regent")
    details = selector.score_strategy_map_option_details({"index": 1, "texts": {alias}}, {}, stub)
    assert details["score"] > 0
    assert details["constraint_hits"][0]["category"] == "map_preferences"
    assert details["constraint_hits"][0]["scene"] == "map"


@pytest.mark.unit
def test_tactical_summary_includes_explicit_strategy_preferences_and_estimators(parser: Any, combat_analyzer: Any) -> None:
    summary = combat_analyzer.build_tactical_summary({"hand": [], "enemies": []}, parser._load_strategy_constraints, "regent")
    assert summary["character_strategy"] == "regent"
    assert summary["strategy_preferences"]
    assert summary["strategy_estimators"]


@pytest.mark.unit
def test_sanitize_combat_uses_explicit_strategy(parser: Any, combat_analyzer: Any) -> None:
    combat = {
        "hand": [{"index": 0, "name": "测试牌", "playable": True}],
        "enemies": [{"index": 0, "name": "测试敌人", "hp": 10, "intent": "attack", "intent_damage": 5}],
    }
    payload = combat_analyzer.sanitize_combat_for_prompt(combat, parser._load_strategy_constraints, "regent")
    assert payload["hand"][0]["index"] == 0
    assert "strategy_setup_score" in payload["hand"][0]
    assert payload["enemies"][0]["index"] == 0
