from __future__ import annotations
from theater_workshop.host import InProcessPackageGateway

import pytest

from theater_workshop.sdk.numeric_v2 import (
    NumericV2Compiler,
    allocate_metric_id,
    metrics_to_package,
    normalize_metric_drafts,
    preset_metric_catalog,
)
from .numeric_v2_fixture import numeric_v2_story


def test_numeric_v2_presets_apply_confirmed_defaults():
    presets = preset_metric_catalog()

    assert len(presets) == 10
    assert {item["id"] for item in presets} >= {"affection", "trust", "disgust"}
    assert all(item["min"] == 0 and item["max"] == 50 for item in presets)
    assert all(item["visibility"] == "hidden" for item in presets)
    by_id = {item["id"]: item for item in presets}
    assert by_id["affection"]["relationship_effect"] == "positive"
    assert by_id["guard"]["relationship_effect"] == "negative"
    assert by_id["clue_progress"]["relationship_effect"] == "none"
    assert by_id["affection"]["increase_limit"] == by_id["guard"]["increase_limit"] == 3
    assert by_id["clue_progress"]["increase_limit"] == 5
    assert by_id["trust"]["bands"] == [
        {"min": 0, "max": 19, "label": "保持戒备"},
        {"min": 20, "max": 34, "label": "愿意试探"},
        {"min": 35, "max": 50, "label": "愿意托付秘密"},
    ]


def test_numeric_v2_custom_metric_gets_stable_non_conflicting_id():
    first = normalize_metric_drafts([{"name": "归乡意愿"}])[0]
    renamed = normalize_metric_drafts([{**first, "name": "留下的意愿"}])[0]

    assert first["id"].startswith("metric_")
    assert renamed["id"] == first["id"]
    assert allocate_metric_id("trust", {"trust"}) == "trust_2"


def test_numeric_v2_metrics_are_always_hidden_from_players():
    normalized = normalize_metric_drafts([{
        "id": "trust",
        "preset": "trust",
        "name": "信任度",
        "visibility": "public",
    }])
    schema, _ = metrics_to_package(normalized)

    assert normalized[0]["visibility"] == "hidden"
    assert schema["trust"]["visibility"] == "hidden"
    assert normalized[0]["relationship_effect"] == "positive"
    assert schema["trust"]["relationship_effect"] == "positive"


def test_numeric_v2_custom_metric_does_not_guess_relationship_semantics():
    normalized = normalize_metric_drafts([{"id": "warmth", "name": "关系热度"}])
    schema, _ = metrics_to_package(normalized)

    assert normalized[0]["relationship_effect"] == "none"
    assert schema["warmth"]["relationship_effect"] == "none"


def test_numeric_v2_relationship_metric_uses_slower_default_limit():
    normalized = normalize_metric_drafts([{
        "id": "bond",
        "name": "羁绊",
        "relationship_effect": "positive",
    }])

    assert normalized[0]["increase_limit"] == 3
    assert normalized[0]["decrease_limit"] == 3


def test_numeric_v2_rejects_more_than_four_metrics():
    with pytest.raises(ValueError, match="metric_limit_exceeded"):
        normalize_metric_drafts([{"name": str(index)} for index in range(5)])


def test_numeric_v2_generator_compiler_uses_neko_contract():
    source = numeric_v2_story()
    source["metric_schema"]["trust"]["visibility"] = "public"
    compiled = NumericV2Compiler(InProcessPackageGateway()).compile(source)

    assert compiled.story["schema"] == "neko.story.numeric.v2"
    assert compiled.story["metric_schema"]["trust"]["visibility"] == "hidden"
    assert source["metric_schema"]["trust"]["visibility"] == "public"
    assert compiled.package_hash.startswith("sha256:")


def test_numeric_v2_generator_compiler_rejects_soft_budget_below_minimum():
    source = numeric_v2_story()
    source["nodes"][0]["recommended_turns"] = 1

    with pytest.raises(Exception) as caught:
        NumericV2Compiler(InProcessPackageGateway()).compile(source)

    issues = caught.value.details.get("issues", [])
    assert any(issue["code"] == "invalid_node_recommended_turns" for issue in issues)
