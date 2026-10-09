"""Completed goal projections preserve actual events instead of rewriting their actor."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.theater.numeric_v2_actor import _completion_fact_prompt_context
from services.theater.numeric_v2_cast import NumericV2CastProjection


@pytest.mark.parametrize("description,actual", [
    ("玩家修好了角色的发饰。", "（收起工具）我自己修好了发饰。"),
    ("玩家接通了备用电源。", "（合上电闸）备用电源是我接通的。"),
])
def test_completed_goal_uses_recorded_evidence_not_authored_actor(description, actual):
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
    assert row["evidence_revision"] == 5
    evidence = context["completion_evidence"][0]
    assert evidence["revision"] == 5
    assert evidence["player_input"] == record["input_text"]
    action, dialogue = actual[1:].split("）", 1)
    assert {"type": "action", "text": action} in evidence["content"]
    assert {"type": "dialogue", "speaker_id": "active_catgirl", "text": dialogue} in evidence["content"]
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
    assert context.get("completion_evidence", []) == []
    if expected_satisfied:
        assert "description" not in row
        assert row["evidence_revision"] == 8
    else:
        assert row["description"] == definition["description"]


def test_pending_goals_keep_their_definition_and_shared_evidence_is_not_duplicated():
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
    assert len(context["completion_evidence"]) == 1
    assert context["completion_evidence"][0]["revision"] == 2


def test_large_evidence_is_omitted_whole_without_inventing_or_truncating_a_fact():
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
    assert context["completion_evidence"] == []
    assert context["all"][0]["evidence_revision"] == 1
    assert "description" not in context["all"][0]
