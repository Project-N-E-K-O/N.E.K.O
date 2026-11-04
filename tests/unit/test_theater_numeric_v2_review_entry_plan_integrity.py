"""Review transports compiler-valid entry plans whole, without granting facts."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2 import MAX_ACTOR_PROMPT_FIELD_TOKENS
from services.theater.numeric_v2_budget import numeric_v2_actor_budget
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.unit.test_theater_numeric_v2_review_player_background import _case, _data


FIELDS = ("catgirl_state", "player_state", "environment_state")
SUBJECTS = ("林雨", "周宁", "环境")
TAIL = "；但这一规划不表示操作已经完成，未经许可仍不能开启。"
MODES = ("ordinary", "opening", "completion", "formal")


def _long_state(subject, node_id):
    value = f"{subject}的入幕规划标记为{node_id}。"
    # Construct the actual compiler boundary by token count, then compile below;
    # repetition alone is not evidence that a package is legal.
    while evaluator.count_tokens(value + " detail" + TAIL) <= MAX_ACTOR_PROMPT_FIELD_TOKENS:
        value += " detail"
    value += TAIL
    assert evaluator.count_tokens(value) == MAX_ACTOR_PROMPT_FIELD_TOKENS == 384
    return value


def _fixture(*, restricted=False, legacy_state=False, continuity=False):
    original, session = _case(restricted=restricted)
    story = deepcopy(original.story)
    for node in story["nodes"]:
        if legacy_state:
            node["story_beat"].pop("character_state")
        else:
            node["story_beat"]["character_state"].update({
                field: _long_state(subject, node["id"])
                for field, subject in zip(FIELDS, SUBJECTS)
            })
            if continuity:
                node["story_beat"]["character_state"]["continuity_from_previous"] = [
                    "林雨与周宁此前只在门外交谈；不能据此认定已经进入。",
                    "记录只列待核对事项；未经双方确认仍不能公开。",
                    "位置须承接实际历史；若实际位置已变，不能退回原处。",
                    _long_state("环境", node["id"] + "连续性"),
                ]
    # This invokes the real compiler, including subject and per-field limits.
    engine = NumericV2Engine.from_mapping(story)
    return engine, replace(session, story_package_hash=engine.compiled.package_hash)


def _request(engine, session, mode, *, repetitions=0):
    message = "我们先进去，但不操作。" if mode == "formal" else "我还没有动手，先核对条件。"
    body = "（" + "她整理好资料。" * repetitions + "）" if repetitions else "我们先核对现有条件。"
    kwargs = dict(
        engine=engine, session=session, message=message,
        actor_performance={"performance": body, "suggested_inputs": []},
        route_changed=mode in {"opening", "formal"},
        completion_fact_review_only=mode == "completion",
    )
    if mode == "formal":
        session = replace(session, transition_offered=True)
        outcome = engine.resolve_turn(
            session, TurnRequestV2("entry-plan", session.revision, message), (),
            transition_intent="accept",
        )
        assert outcome.session.current_node_id == "middle"
        kwargs.update(session=session, transition_outcome=outcome, actor_performance={
            "segments": [
                {"phase": "source_response", "performance": body},
                {"phase": "transition_bridge", "scene_narration": "两人来到门边。"},
                {"phase": "target_opening", "performance": "可以先在这里商量。"},
            ],
            "suggested_inputs": [],
        })
    return kwargs


def _messages(kwargs):
    args = {**kwargs}
    args["player_input"] = args.pop("message")
    return evaluator._build_transition_judge_messages(**args)[0]


def _expected(engine, node_id):
    state = engine.nodes[node_id]["story_beat"]["character_state"]
    result = {field: state[field].replace("林雨", "Lan").replace("周宁", "哥哥") for field in FIELDS}
    if state.get("continuity_from_previous"):
        result["continuity_from_previous"] = [
            item.replace("林雨", "Lan").replace("周宁", "哥哥")
            for item in state["continuity_from_previous"]
        ]
    return result


def test_formal_target_keeps_its_own_complete_plan_separate_from_source_and_evidence():
    engine, session = _fixture()
    kwargs = _request(engine, session, "formal")
    data = _data(_messages(kwargs))
    target = data["target_scene"]["author_entry_plan"]
    assert {field: target[field] for field in FIELDS} == _expected(engine, "middle")
    assert "character_state" not in data["target_scene"]
    assert _expected(engine, "start") != _expected(engine, "middle")
    assert TAIL not in json.dumps(data["candidate_segments"], ensure_ascii=False)
    assert TAIL not in json.dumps(data["scene_context"], ensure_ascii=False)


def _fake_client(monkeypatch, requests, clients):
    async def config(_):
        return {"model": "test", "base_url": "http://test.invalid"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            if json.loads(messages[1].content[messages[1].content.index("{"):]).get("review_task") == "player_actions":
                return SimpleNamespace(content=json.dumps({"body_violations": [], "failure_reason": ""}))
            requests.append(messages)
            payload = {
                "offer_present": False, "offer_quote": "", "valid": False,
                "body_violations": [], "unsafe_suggestion_indexes": [],
                "failure_reason": "",
            }
            if "candidate_segments" in _data(messages):
                payload.update(delivery_matches_route=True, pending_invitation_invalid=False)
            return SimpleNamespace(content=json.dumps(payload))

    async def factory(*args, **kwargs):
        clients.append(kwargs)
        return Client()

    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['ordinary', 'formal'])
async def test_native_scene_review_keeps_full_plan(monkeypatch, mode):
    requests, clients = [], []
    _fake_client(monkeypatch, requests, clients)
    engine, session = _fixture(continuity=True)
    kwargs = _request(engine, session, mode)
    before = deepcopy((engine.story, kwargs["session"].to_dict(), kwargs["actor_performance"]))
    result = await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(**kwargs)
    assert len(requests) == 1  # This fake records the scene half; player-action coverage is in split_review.
    assert len(clients) == (2 if mode == "formal" else 1)
    assert _data(requests[0])["current_scene"]["author_entry_plan"] == _expected(engine, "start")
    assert clients[0]["timeout"] == evaluator.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS == 8.0
    assert clients[0]["max_retries"] == 0
    assert clients[0]["max_completion_tokens"] == (
        evaluator.NUMERIC_V2_FORMAL_TRANSITION_JUDGE_MAX_OUTPUT_TOKENS if mode == "formal"
        else 320
    )
    assert result.fact_candidates == () and result.body_violations == ()
    assert (engine.story, kwargs["session"].to_dict(), kwargs["actor_performance"]) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['formal'])
async def test_over_budget_review_preserves_plan_and_rejects_before_client(monkeypatch, mode):
    requests, clients = [], []
    _fake_client(monkeypatch, requests, clients)
    engine, session = _fixture(continuity=True)
    kwargs = _request(engine, session, mode, repetitions=2000)
    before = deepcopy((engine.story, kwargs["session"].to_dict(), kwargs["actor_performance"]))
    messages = _messages(kwargs)
    data = _data(messages)
    assert data["current_scene"]["author_entry_plan"] == _expected(engine, "start")
    budget = numeric_v2_actor_budget(session.actor_budget_profile)
    assert budget["judge_input_max_tokens"] == budget["formal_judge_input_max_tokens"] == 10000
    assert sum(evaluator.count_tokens(item.content) for item in messages) > 10000
    with pytest.raises(evaluator.NumericV2EvaluatorError, match="transition_review_budget_exceeded"):
        await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(**kwargs)
    assert requests == clients == []
    assert (engine.story, kwargs["session"].to_dict(), kwargs["actor_performance"]) == before
