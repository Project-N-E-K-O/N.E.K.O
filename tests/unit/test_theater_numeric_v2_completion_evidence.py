"""Completed values retain registration metadata without inventing event provenance."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.theater.numeric_v2_actor import _completion_fact_prompt_context
from services.theater.numeric_v2_cast import NumericV2CastProjection


@pytest.mark.parametrize("description,actual", [
    ("玩家修好了角色的发饰。", "（收起工具）我自己修好了发饰。"),
    ("玩家接通了备用电源。", "（合上电闸）备用电源是我接通的。"),
])
def test_completed_goal_keeps_value_without_assigning_its_actor_from_registration(description, actual):
    key = "scene:start:completed"
    definition = {"value_type": "bool", "visibility": "public", "description": description}
    engine = SimpleNamespace(fact_contract={key: definition})
    record = {"revision": 5, "input_text": "我在旁边等你。", "performance": actual,
              "scene_narration": "装置的状态已经稳定。"}
    facts = {key: {"value": True, "source_revision": 5, "updated_revision": 5}}
    session = SimpleNamespace(story_state={"facts": facts}, performance_history=(record,))
    node = {"completion_contract": {"all": [{"key": key, "equals": True}]}}
    before = deepcopy((definition, record, facts, node))
    context = _completion_fact_prompt_context(
        engine, node, SimpleNamespace(session=session),
        cast=NumericV2CastProjection("", "", "玩家", "角色"),
    )
    assert context["status"] == "satisfied"
    row = context["all"][0]
    assert row["satisfied"] is True and row["committed_value"] is True
    assert "description" not in row
    assert row["registration_revision"] == 5
    assert "evidence_revision" not in row
    assert "completion_evidence" not in context
    assert actual not in str(context)
    assert (definition, record, facts, node) == before


@pytest.mark.parametrize("value,expected_satisfied", [(False, False), (True, True)])
def test_missing_record_never_turns_author_plan_into_historical_evidence(value, expected_satisfied):
    key = "scene:start:completed"
    definition = {"value_type": "bool", "visibility": "public", "description": "玩家操作完成。"}
    session = SimpleNamespace(story_state={"facts": {key: {
        "value": value, "source_revision": 1, "updated_revision": 8,
    }}}, performance_history=({"revision": 1, "performance": "旧的操作结果。"},))
    context = _completion_fact_prompt_context(
        SimpleNamespace(fact_contract={key: definition}),
        {"completion_contract": {"all": [{"key": key, "equals": True}]}},
        SimpleNamespace(session=session), cast=NumericV2CastProjection("", "", "玩家", "角色"),
    )
    row = context["all"][0]
    assert row["satisfied"] is expected_satisfied
    assert "completion_evidence" not in context
    if expected_satisfied:
        assert "description" not in row
        assert row["registration_revision"] == 8
        assert "evidence_revision" not in row
    else:
        assert row["description"] == definition["description"]


def test_pending_goals_keep_definition_and_completed_values_do_not_attach_unbound_prose():
    definitions = {key: {"value_type": "bool", "visibility": "public", "description": f"目标{key}"}
                   for key in ("first", "second", "pending")}
    session = SimpleNamespace(
        story_state={"facts": {key: {"value": True, "source_revision": 1, "updated_revision": 2}
                               for key in ("first", "second")}},
        performance_history=({"revision": 1, "performance": "旧结果。"},
                             {"revision": 2, "performance": "新的实际结果。", "input_text": "请确认。"}),
    )
    context = _completion_fact_prompt_context(
        SimpleNamespace(fact_contract=definitions),
        {"completion_contract": {"all": [{"key": key, "equals": True} for key in definitions]}},
        SimpleNamespace(session=session), cast=NumericV2CastProjection("", "", "玩家", "角色"),
    )
    assert context["status"] == "pending"
    assert context["all"][-1]["description"] == "目标pending"
    assert [row["registration_revision"] for row in context["all"][:2]] == [2, 2]
    assert "completion_evidence" not in context
    assert "新的实际结果" not in str(context)


def test_large_registration_round_cannot_be_attached_as_completion_evidence():
    key = "scene:start:completed"
    session = SimpleNamespace(
        story_state={"facts": {key: {"value": True, "source_revision": 1, "updated_revision": 1}}},
        performance_history=({"revision": 1, "performance": "尚未确认实际结果。" * 3000},),
    )
    context = _completion_fact_prompt_context(
        SimpleNamespace(fact_contract={key: {
            "value_type": "bool", "visibility": "public", "description": "玩家完成了操作。",
        }}),
        {"completion_contract": {"all": [{"key": key, "equals": True}]}},
        SimpleNamespace(session=session), cast=NumericV2CastProjection("", "", "玩家", "角色"),
    )
    assert "completion_evidence" not in context
    assert context["all"][0]["registration_revision"] == 1
    assert "description" not in context["all"][0]


@pytest.mark.parametrize("revision", [0, 3])
def test_legacy_source_revision_is_only_registration_metadata(revision):
    key = "scene:start:completed"
    session = SimpleNamespace(story_state={"facts": {key: {
        "value": True, "source_revision": revision,
    }}}, performance_history=())
    context = _completion_fact_prompt_context(
        SimpleNamespace(fact_contract={key: {
            "value_type": "bool", "visibility": "public", "description": "玩家完成了操作。",
        }}),
        {"completion_contract": {"all": [{"key": key, "equals": True}]}},
        SimpleNamespace(session=session), cast=NumericV2CastProjection("", "", "玩家", "角色"),
    )
    assert context["status"] == "satisfied"
    assert context["all"][0]["registration_revision"] == revision
    assert "evidence_revision" not in context["all"][0]
    assert "completion_evidence" not in context
