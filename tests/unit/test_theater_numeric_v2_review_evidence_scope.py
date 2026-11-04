"""Evidence scope reaches every judge without changing sources or approval authority."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_narrative_proposer as proposer
from services.theater.numeric_v2_narrative import make_narrative_record, narrative_body_fingerprint
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_narrative_proposer import _client
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding


OBSERVATION = "仪表指针停在零位，尚未接通检测设备，原因未查明。"
WORLD_RULE = "这台仪表在接通自检并完成归零后，零位才表示校准通过。"
PUBLIC_MEMORY = "我以前在这个工作台旁学过装订。"
UNCERTAIN = "零位也许只是静止位置，现在还不能判断校准是否通过。"


@pytest.fixture(autouse=True)
def _offline_tokenizer(monkeypatch):
    # Contract tests must never fetch tokenizer data or invoke a provider.
    monkeypatch.setattr("utils.tokenize._get_encoder", lambda encoding: None)


def _case():
    story = deepcopy(_engine(ordinary=True).story)
    story["nodes"][0]["story_beat"]["summary"] = WORLD_RULE
    story["nodes"][0]["story_beat"]["opening_scene"] = OBSERVATION
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="evidence-scope", catgirl_binding=_binding(),
        opening_performance={"scene_narration": OBSERVATION, "performance": "先观察。",
                             "suggested_inputs": []},
    )
    state = deepcopy(session.story_state)
    state["revision"] = 1
    session = replace(session, revision=1, node_turn_count=1, story_state=state,
        performance_history=({
            "revision": 1, "from_node_id": "start", "to_node_id": "start",
            "input_text": PUBLIC_MEMORY, "performance": "你说的那次经历，我听到了。",
            "scene_narration": "管理员说：“这次还没有自检。”", "suggested_inputs": [],
        },))
    return engine, session


def _data(messages):
    text = messages[1].content
    return prompt_payload(text)


def test_evaluator_retains_world_rule_observation_and_public_memory_as_distinct_evidence():
    engine, session = _case()
    before = deepcopy((engine.story, session.to_dict()))
    messages = evaluator._build_messages(engine, session, "现在能确定原因吗？")
    data = _data(messages)
    text = json.dumps(data, ensure_ascii=False)
    for source in (OBSERVATION, WORLD_RULE, PUBLIC_MEMORY, "这次还没有自检"):
        assert source in text
    assert data["player_input"] == "现在能确定原因吗？"
    assert (engine.story, session.to_dict()) == before


@pytest.mark.parametrize('opening', [False])
def test_narrative_proposal_preserves_attribution_without_promoting_speech_to_object_state(opening):
    _, session = _case()
    performance = {"performance": UNCERTAIN, "suggested_inputs": []}
    messages = proposer._messages(performance, PUBLIC_MEMORY, session, opening)
    assert "人物判断不能仅因出现在对白中就成为对象的condition或stage_result" in messages[0].content
    data = _data(messages)
    assert data["player_input"] == ("" if opening else PUBLIC_MEMORY)
    assert UNCERTAIN in json.dumps(data["phase_texts"], ensure_ascii=False)
    assert "scene_complete" not in messages[0].content


def _speech_event():
    return {
        "phase": "turn", "subject": "character", "kind": "speech",
        "changes": [{"entity": "item:仪表", "attribute": "condition", "value": "校准通过"}],
        "source": "performance", "quote": "仪表也许已经校准通过，但还没有检测依据。",
        "known_to": ["player", "character"],
    }


@pytest.mark.parametrize('narrative_review', [True])
def test_body_and_state_approval_remain_separate_for_an_attributed_hypothesis(narrative_review):
    engine, session = _case()
    event = _speech_event()
    performance = {"performance": event["quote"], "suggested_inputs": []}
    messages, _ = evaluator._build_transition_judge_messages(
        engine, session, player_input="你怎么看？", actor_performance=performance,
        narrative_events=(event,) if narrative_review else None,
    )
    if narrative_review:
        assert "人物判断不能仅因出现在对白中就成为对象的condition或stage_result" in messages[0].content
        assert _data(messages)["narrative_events"][0]["quote"] == event["quote"]
    # This is an explicit mocked verdict, not proof that a model can judge the hypothesis.
    payload = {"offer_present": False, "valid": False, "body_violations": [],
               "unsafe_suggestion_indexes": [], "failure_reason": ""}
    if narrative_review:
        payload.update(approved_narrative_event_indexes=[], narrative_review_complete=False)
    review = evaluator._parse_transition_judge_output(
        json.dumps(payload), narrative_review=narrative_review,
        narrative_events=(event,) if narrative_review else (),
        narrative_body=narrative_body_fingerprint(performance),
    )
    assert review.body_violations == ()
    assert review.approved_narrative_event_indexes == ()
    assert make_narrative_record(
        {**performance, "narrative_events": [event]}, "你怎么看？", review,
    )["events"] == []


async def test_independent_narrative_review_uses_same_scope_and_one_existing_call(monkeypatch):
    _, session = _case()
    event = _speech_event()
    performance = {"performance": event["quote"], "narrative_events": [event]}
    instance, seen = _client(monkeypatch, {
        "approved_narrative_event_indexes": [], "narrative_review_complete": False,
    })
    before = deepcopy((session.to_dict(), performance))
    result = await instance.review(performance=performance, player_input="你怎么看？", session=session)
    assert len(seen["requests"]) == 1
    messages, _ = seen["requests"][0]
    assert "人物判断不能仅因出现在对白中就成为对象的condition或stage_result" in messages[0].content
    assert result.approved_narrative_event_indexes == ()
    assert result.narrative_review_complete is False
    assert seen["factory"][0][1]["timeout"] == 12.0
    assert seen["factory"][0][1]["max_retries"] == 0
    assert seen["factory"][0][1]["max_completion_tokens"] == 150
    assert (session.to_dict(), performance) == before
