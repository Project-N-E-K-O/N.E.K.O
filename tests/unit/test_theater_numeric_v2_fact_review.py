"""Evaluator fact approval shares the existing Review call and cannot fail open."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


def _payload(**fields):
    return {
        "offer_present": False, "valid": False, "body_violations": [],
        "unsafe_suggestion_indexes": [1], "failure_reason": "",
        **fields,
    }


def _context():
    story = numeric_v2_story()
    story["fact_contract"] = {"facts": {
        "scene:start:ready": {
            "value_type": "bool", "visibility": "public",
            "description": "女主已经接通线路并确认指示灯亮起。",
        },
    }}
    story["nodes"][0]["completion_contract"] = {
        "all": [{"key": "scene:start:ready", "equals": True}],
    }
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="fact_review", catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "出口在前面。"},
    )
    claims = ({
        "op": "set", "key": "scene:start:ready", "value": True, "visibility": "public",
        "description": "女主已经接通线路并确认指示灯亮起。",
        "subject": "玩家", "action": "按下", "object": "开关", "result": "按下开关",
        "evidence": [{"source": "player_input", "quote": "我按下开关。"}],
    },)
    return engine, session, claims


@pytest.mark.parametrize("description", ["阿原与霜叶已同意共同寻找春晶。", "阿原与霜叶已核对飞船的警报读数。"])
def test_fact_descriptions_share_cast_with_body_without_mutating_keys_or_values(description):
    story = numeric_v2_story()
    story["intro"]["player_name"] = "阿原"
    story["intro"]["catgirl_name"] = "霜叶"
    story["intro"]["player_identity"] = "阿原，参与任务的玩家。"
    story["intro"]["catgirl_identity"] = "霜叶，参与任务的同伴。"
    key = "scene:start:ready"
    story["fact_contract"] = {"facts": {key: {
        "value_type": "bool", "visibility": "public", "description": description,
    }}}
    story["nodes"][0]["completion_contract"] = {"all": [{"key": key, "equals": True}]}
    engine = NumericV2Engine.from_mapping(story)
    original = deepcopy(engine.fact_contract)
    session = engine.create_session(
        session_id="cast_fact", catgirl_binding={"catgirl_name": "小葵", "player_address": "小林"},
        opening_performance={"performance": "开场。"},
    )
    projected = description.replace("阿原", "小林").replace("霜叶", "小葵")
    pending = evaluator._pending_completion_facts(engine, session)
    assert pending == [{"key": key, "value": True, "description": projected}]
    messages = evaluator._build_messages(engine, session, "我同意。")
    data = json.loads(messages[1].content[messages[1].content.index("{"):])
    assert data["current_story_beat"]["fact_contract"]["facts"][key]["description"] == projected
    messages, _ = evaluator._build_transition_judge_messages(
        engine, session, actor_performance={"performance": "我也同意。"}, player_input="我同意。",
        evaluator_fact_claims=({"key": key, "value": True, "description": description},),
    )
    data = json.loads(messages[1].content[messages[1].content.index("{"):])
    assert data["evaluator_fact_claims"][0]["description"] == projected
    assert data["evaluator_fact_claims"][0]["key"] == key
    outcome = engine.resolve_turn(session, TurnRequestV2("cast_fact_turn", 0, "我同意。"), ())
    messages = _turn_messages(engine, session, outcome, "我同意。", "温和。", "小葵", "小林")
    text = "\n".join(message.content for message in messages)
    assert projected in text
    assert description not in text
    assert engine.fact_contract == original


@pytest.mark.parametrize("approved", [None, False, 0, "0", {}, [True], [-1], [1], [0, 0], [0, "1"]])
def test_invalid_fact_approval_preserves_body_review_but_approves_nothing(approved):
    review = evaluator._parse_transition_judge_output(
        json.dumps(_payload(approved_evaluator_fact_indexes=approved)),
        evaluator_fact_claim_count=1,
    )
    assert review.approved_evaluator_fact_indexes == ()
    assert review.body_violations == ()
    assert review.unsafe_suggestion_indexes == (1,)


def test_fact_approval_missing_is_empty_and_valid_indexes_stay_bound_to_this_request():
    missing = evaluator._parse_transition_judge_output(
        json.dumps(_payload()), evaluator_fact_claim_count=2,
    )
    valid = evaluator._parse_transition_judge_output(
        json.dumps(_payload(approved_evaluator_fact_indexes=[1, 0])),
        evaluator_fact_claim_count=2,
    )
    stale = evaluator._parse_transition_judge_output(
        json.dumps(_payload(approved_evaluator_fact_indexes=[0])),
        evaluator_fact_claim_count=0,
    )
    assert missing.approved_evaluator_fact_indexes == ()
    assert valid.approved_evaluator_fact_indexes == (1, 0)
    assert stale.approved_evaluator_fact_indexes == ()


@pytest.mark.parametrize("formal", [False, True])
@pytest.mark.parametrize("with_claims", [False, True])
def test_claims_and_restricted_evidence_survive_ordinary_and_formal_projection(formal, with_claims):
    engine, session, claims = _context()
    supplied = claims if with_claims else ()
    original = deepcopy(supplied)
    outcome = engine.resolve_turn(
        session, TurnRequestV2("fact_review_turn", 0, "我按下开关。"), (),
        transition_intent="initiate",
    ) if formal else None
    messages, _ = evaluator._build_transition_judge_messages(
        engine, session, actor_performance={"performance": "（点头）让我确认结果。", "suggested_inputs": []},
        player_input="我按下开关。", route_changed=formal, transition_outcome=outcome,
        evaluator_fact_claims=supplied,
    )
    system = messages[0].content
    data = json.loads(messages[1].content.split("：", 1)[1])
    assert ("evaluator_fact_claims" in data) == with_claims
    assert ("approved_evaluator_fact_indexes" in system) == with_claims
    assert supplied == original
    if with_claims:
        assert data["evaluator_fact_claims"] == [{**claims[0], "index": 0}]
        assert "未确认提议" in system
        assert "不能用 Actor" in system
        assert "全部条件" in system
        assert "引文存在不等于" in system
    if formal:
        assert "candidate_segments" in data
        assert "initiation_authorized" in system
    else:
        assert "pending_completion_facts" in data
        assert "已直接、完整证明目标结果" in system


@pytest.mark.asyncio
@pytest.mark.parametrize("formal", [False, True])
@pytest.mark.parametrize("dispute", [False, True])
async def test_fact_approval_uses_one_existing_call_and_existing_output_allowance(monkeypatch, formal, dispute):
    engine, session, claims = _context()
    outcome = engine.resolve_turn(
        session, TurnRequestV2("fact_review_turn", 0, "我按下开关。"), (),
        transition_intent="initiate",
    ) if formal else None
    calls = []
    requests = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            requests.append(messages)
            return SimpleNamespace(content=json.dumps(_payload(approved_evaluator_fact_indexes=[0],
                **({"delivery_matches_route": True} if formal else {}))))

    async def config(_):
        return {"model": "test", "base_url": "http://test.invalid"}

    async def factory(*args, **kwargs):
        calls.append(kwargs)
        return Client()

    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    monkeypatch.setattr(evaluator, "focus_extra_body", lambda _: {"enable_thinking": True})
    # Claims need their own output allowance even if the scene has no pending body facts.
    monkeypatch.setattr(evaluator, "_pending_completion_facts", lambda *_: [])
    review = await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(
        engine=engine, session=session, message="我按下开关。",
        actor_performance={"performance": "（点头）让我确认结果。", "suggested_inputs": []},
        route_changed=formal, transition_outcome=outcome, dispute_review=dispute,
        evaluator_fact_claims=claims,
    )
    assert review.approved_evaluator_fact_indexes == (0,)
    assert len(calls) == len(requests) == 1
    assert calls[0]["max_completion_tokens"] == (4096 if dispute else 512 if formal else 350)
    assert calls[0]["timeout"] == (
        evaluator.NUMERIC_V2_DISPUTE_JUDGE_TIMEOUT_SECONDS if dispute
        else evaluator.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS
    )
    assert calls[0]["max_retries"] == 0
    data = json.loads(requests[0][1].content.split("：", 1)[1])
    assert data["evaluator_fact_claims"][0]["evidence"] == claims[0]["evidence"]
