"""Malformed optional facts cannot erase independent, evidenced route choices."""

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2_evaluator import _parse_output
from services.theater.numeric_v2_runtime import (
    _FACT_CANDIDATE_MAX_ITEMS, NumericV2Engine, NumericV2RuntimeError, validate_fact_candidates,
)
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


ROUTE = "scene:start:route_decided"
DEPARTURE = "scene:start:departure_confirmed"
MESSAGE = "我选官道，现在就出发，先去官道旁的驿站。"


@pytest.fixture
def context():
    story = numeric_v2_story()
    story["fact_contract"] = {"facts": {
        ROUTE: {"value_type": "string", "visibility": "public", "description": "玩家确定行进路线。"},
        DEPARTURE: {"value_type": "bool", "visibility": "public", "description": "玩家明确开始出发。"},
        "scene:next:arrived": {"value_type": "bool", "visibility": "public", "description": "到达下一幕。"},
        **{f"prop:fact_{index}": {"value_type": "string", "visibility": "public"}
           for index in range(_FACT_CANDIDATE_MAX_ITEMS + 1)},
    }}
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="fact_isolation", catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "可以走官道，也可以走山径。"},
    )
    return engine, session


def _route_candidate():
    # Same valid quadruple and quote as the captured official-road Qwen response.
    return {
        "op": "set", "key": ROUTE, "value": "官道", "visibility": "public", "confidence": "confirmed",
        "subject": "老谢", "action": "选定路线", "object": "官道", "result": "确定起行路线为官道",
        "evidence": [{"source": "player_input", "quote": "我选官道"}],
    }


def _departure_candidate():
    return {
        "op": "set", "key": DEPARTURE, "value": True, "visibility": "public", "confidence": "confirmed",
        "subject": "老谢", "action": "同意启程", "object": "启程", "result": "明确表达出发意图",
        "evidence": [{"source": "player_input", "quote": "现在就出发"}],
    }


def _evaluate(context, candidates):
    engine, session = context
    return _parse_output(json.dumps({
        "scene_complete": False, "transition_intent": "unclear", "metric_changes": {},
        "fact_candidates": candidates,
    }, ensure_ascii=False), engine, MESSAGE, session)


@pytest.mark.parametrize("invalid_first", [False, True])
def test_null_object_from_real_response_does_not_erase_valid_route(context, invalid_first):
    bad = {**_departure_candidate(), "object": None}
    candidates = [bad, _route_candidate()] if invalid_first else [_route_candidate(), bad]
    original = deepcopy(candidates)
    result = _evaluate(context, candidates)

    assert result.fact_operations == ({"op": "set", "key": ROUTE, "value": "官道", "visibility": "public"},)
    assert [row["key"] for row in result.fact_audit] == [ROUTE]
    assert result.fact_audit[0]["evidence"] == [{"source": "player_input", "quote": "我选官道"}]
    assert not result.scene_complete and result.transition_intent == "unclear"
    assert candidates == original
    # The shared strict validator itself must still reject this batch atomically.
    with pytest.raises(NumericV2RuntimeError, match="story_fact_candidate_tuple_invalid"):
        validate_fact_candidates(candidates, fact_contract={"facts": context[0].fact_contract},
                                 evidence_sources={"player_input": MESSAGE})


@pytest.mark.parametrize("patch", [
    {"unexpected": "field"}, {"confidence": "uncertain"}, {"visibility": "story"}, {"value": "true"},
    {"key": []}, {"key": "prop:unknown"}, {"key": "scene:next:arrived"},
    {"evidence": [{"source": "player_input", "quote": "未出现的原话"}]},
    {"evidence": [{"source": "actor_performance", "quote": "现在就出发"}]},
])
def test_each_invalid_candidate_keeps_original_guards_without_poisoning_others(context, patch):
    result = _evaluate(context, [{**_departure_candidate(), **patch}, _route_candidate()])

    assert [row["key"] for row in result.fact_operations] == [ROUTE]
    assert [row["key"] for row in result.fact_audit] == [ROUTE]


@pytest.mark.parametrize("bad", [None, [], "invalid"])
def test_nonobject_candidate_does_not_erase_valid_fact(context, bad):
    result = _evaluate(context, [bad, _route_candidate()])

    assert [row["key"] for row in result.fact_operations] == [ROUTE]


@pytest.mark.parametrize("patch", [{}, {"value": "山径"}, {"object": None}])
@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_or_conflicting_key_rejects_entire_key_group(context, patch, reverse):
    duplicates = [_route_candidate(), {**_route_candidate(), **patch}]
    if reverse:
        duplicates.reverse()
    result = _evaluate(context, [*duplicates, _departure_candidate()])

    assert [row["key"] for row in result.fact_operations] == [DEPARTURE]
    assert [row["key"] for row in result.fact_audit] == [DEPARTURE]


@pytest.mark.parametrize("count", [_FACT_CANDIDATE_MAX_ITEMS, _FACT_CANDIDATE_MAX_ITEMS + 1])
def test_fact_batch_limit_is_preserved_before_individual_validation(context, count):
    candidates = [{**_route_candidate(), "key": f"prop:fact_{index}"} for index in range(count)]
    result = _evaluate(context, candidates)

    expected = count if count <= _FACT_CANDIDATE_MAX_ITEMS else 0
    assert len(result.fact_operations) == len(result.fact_audit) == expected


def test_oversized_batch_cannot_evade_limit_by_hiding_future_scene_items(context):
    result = _evaluate(context, [
        _route_candidate(),
        *[{**_departure_candidate(), "key": "scene:next:arrived"} for _ in range(_FACT_CANDIDATE_MAX_ITEMS)],
    ])

    assert result.fact_operations == result.fact_audit == ()
