"""Scope Evaluator facts and avoid duplicate writes without changing the rich protocol."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_context import project_scene_facts
from services.theater.numeric_v2_evaluator import _build_messages, _parse_output
from services.theater.numeric_v2_runtime import apply_fact_ops, NumericV2Engine
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


@pytest.fixture
def fact_context():
    story = numeric_v2_story()
    story["fact_contract"] = {"facts": {
        "scene:start:door_open": {
            "value_type": "bool", "visibility": "public", "description": "门已打开。",
        },
        "scene:next:arrived": {
            "value_type": "bool", "visibility": "public", "description": "已进入下一幕。",
        },
        "prop:letter": {"value_type": "string", "visibility": "public"},
        "prop:count": {"value_type": "int", "visibility": "public"},
    }}
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="scoped_fact", catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "开场。"},
    )
    state = apply_fact_ops(
        session.story_state, fact_contract={"facts": engine.fact_contract}, revision=1, client_turn_id="earlier_fact", ops=[
            {"op": "set", "key": "scene:start:door_open", "value": False, "visibility": "public"},
            {"op": "set", "key": "prop:letter", "value": "抽屉", "visibility": "public"},
        ],
    )
    return engine, replace(session, revision=1, story_state=state)


def _evaluate(engine, session, candidates, message="我已经打开门。"):
    return _parse_output(json.dumps({
        "scene_complete": False, "metric_changes": {}, "fact_candidates": candidates,
    }, ensure_ascii=False), engine, message, session)


def _candidate(key="scene:start:door_open", value=True, quote="我已经打开门。", source="player_input"):
    return {
        "op": "set", "key": key, "value": value, "visibility": "public",
        "confidence": "confirmed", "subject": "玩家", "action": "确认",
        "object": key, "result": "原文已明确交付该结果。",
        "evidence": [{"source": source, "quote": quote}],
    }


def test_evaluator_prompt_scopes_contract_and_exposes_committed_values(fact_context):
    engine, session = fact_context
    original_contract = deepcopy(engine.fact_contract)
    messages = _build_messages(engine, session, "我已经打开门。")
    data = prompt_payload(messages[1].content)
    beat = data["current_story_beat"]

    assert set(beat["fact_contract"]["facts"]) == {
        "scene:start:door_open", "prop:letter", "prop:count",
    }
    assert beat["committed_fact_values"] == {
        "scene:start:door_open": False, "prop:letter": "抽屉",
    }
    assert engine.fact_contract == original_contract
    assert "subject、action、object、result" in messages[0].content


@pytest.mark.parametrize("key,value_type,target,previous", [
    ("scene:start:selection", "string", "confirmed", "unselected"),
    ("scene:start:closed", "bool", False, True),
    ("global:remaining", "int", 0, 1),
])
def test_evaluator_sees_typed_unmet_completion_targets_as_plans(key, value_type, target, previous):
    story = numeric_v2_story()
    story["fact_contract"] = {"facts": {
        key: {"value_type": value_type, "visibility": "public", "description": "本幕约定结果。"},
        "scene:start:recorded": {"value_type": "bool", "visibility": "public", "description": "已记录。"},
        "scene:next:arrived": {"value_type": "bool", "visibility": "public", "description": "下一幕结果。"},
        "prop:letter": {"value_type": "string", "visibility": "public", "description": "信件当前存放位置。"},
    }}
    story["nodes"][0]["completion_contract"] = {"all": [
        {"key": key, "equals": target}, {"key": "scene:start:recorded", "equals": True},
    ]}
    story["nodes"][0]["route_gates"][0]["conditions"]["all"].append(
        {"type": "fact_equals", "key": "prop:letter", "value": "table"},
    )
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="typed_target", catgirl_binding={"catgirl_name": "测试猫娘"},
                                    opening_performance={"performance": "结果尚未确定。"})
    session = replace(session, story_state=apply_fact_ops(
        session.story_state, fact_contract={"facts": engine.fact_contract}, revision=1, client_turn_id="prior", ops=[
            {"op": "set", "key": key, "value": previous, "visibility": "public"},
            {"op": "set", "key": "scene:start:recorded", "value": True, "visibility": "public"},
        ],
    ))
    original = deepcopy(session)
    messages = _build_messages(engine, session, "我先看看，还没决定。")
    beat = prompt_payload(messages[1].content)["current_story_beat"]

    assert beat.get("completion_fact_targets") == {key: target}
    assert type(beat["completion_fact_targets"][key]) is type(target)
    assert beat["route_fact_values"] == {"prop:letter": ["table"]}
    assert beat["committed_fact_values"][key] == previous
    assert "scene:next:arrived" not in beat["fact_contract"]["facts"]
    assert "completion_fact_targets 是作者结果编码，不是已发生事实" in messages[0].content
    assert session == original


def test_rich_fact_preserves_new_value_and_runtime_audit(fact_context):
    engine, session = fact_context
    candidate = {**_candidate(), "action": "打开", "object": "门", "result": "门已打开。"}
    result = _evaluate(engine, session, [candidate])

    assert result.fact_operations == ({
        "op": "set", "key": "scene:start:door_open", "value": True, "visibility": "public",
    },)
    assert result.fact_audit[0]["subject"] == "玩家"
    assert result.fact_audit[0]["action"] == "打开"
    assert result.fact_audit[0]["result"] == "门已打开。"
    assert result.fact_audit[0]["evidence"] == [{
        "source": "player_input", "quote": "我已经打开门。",
    }]


def test_rich_fact_without_description_keeps_audit_and_accepts_new_value(fact_context):
    engine, session = fact_context
    candidate = _candidate("prop:letter", "桌面", "我把信放到桌面了。")
    candidate.update(action="放置", result="信在桌面上。")
    result = _evaluate(engine, session, [candidate], message="我把信放到桌面了。")

    assert result.fact_operations[0]["value"] == "桌面"
    assert result.fact_audit[0]["object"] == "prop:letter"
    assert result.fact_audit[0]["result"] == "信在桌面上。"
    assert result.fact_audit[0]["evidence"][0]["quote"] == "我把信放到桌面了。"


def test_rich_fact_accepts_only_real_runtime_fact_evidence(fact_context):
    engine, session = fact_context
    quote = json.dumps(project_scene_facts(session), ensure_ascii=False, separators=(",", ":"))
    result = _evaluate(engine, session, [_candidate("prop:count", 1, quote, "runtime_fact")])

    assert result.fact_operations[0]["value"] == 1
    assert result.fact_audit[0]["evidence"][0]["source"] == "runtime_fact"


def test_rich_fact_does_not_rewrite_same_committed_value(fact_context):
    engine, session = fact_context
    result = _evaluate(engine, session, [
        _candidate("prop:letter", "抽屉", "信仍在抽屉里。"),
    ], message="信仍在抽屉里。")

    assert result.fact_operations == ()
    assert result.fact_audit == ()


@pytest.mark.parametrize("patch,message", [
    ({"unknown": "field"}, "我已经打开门。"),
    ({"visibility": "story"}, "我已经打开门。"),
    ({"confidence": "unconfirmed"}, "我已经打开门。"),
    ({"evidence": [{"source": "actor_performance", "quote": "我已经打开门。"}]}, "我已经打开门。"),
    ({"evidence": [{"source": "runtime_fact", "quote": "我已经打开门。"}]}, "我已经打开门。"),
    ({"evidence": [{"source": "player_input", "quote": "别的原话"}]}, "我已经打开门。"),
    ({"key": "scene:next:arrived"}, "我已经打开门。"),
    ({"key": "prop:unknown"}, "我已经打开门。"),
    ({"key": []}, "我已经打开门。"),
    ({"value": "true"}, "我已经打开门。"),
    ({"key": "prop:count", "value": True}, "我已经打开门。"),
    ({"evidence": [{"source": "player_input", "quote": "我准备开始打开门。"}]}, "我准备开始打开门。"),
    ({"evidence": [{"source": "player_input", "quote": "我打算打开门。"}]}, "我打算打开门。"),
])
def test_rich_fact_keeps_scope_type_and_evidence_guards(fact_context, patch, message):
    engine, session = fact_context
    result = _evaluate(engine, session, [{**_candidate(), **patch}], message=message)

    assert result.fact_operations == ()
    assert result.fact_audit == ()


def test_duplicate_committed_fact_is_validated_without_erasing_independent_fact(fact_context):
    engine, session = fact_context
    result = _evaluate(engine, session, [
        _candidate("prop:letter", "抽屉", "不在输入中的原话"), _candidate(),
    ])

    assert result.fact_operations == ({
        "op": "set", "key": "scene:start:door_open", "value": True, "visibility": "public",
    },)
    assert [row["key"] for row in result.fact_audit] == ["scene:start:door_open"]
