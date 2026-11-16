"""Review receives authored player background without granting actions or knowledge.

These are projection/transport tests, not proof of a model's semantic judgments.
"""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2 import MAX_ACTOR_PROMPT_FIELD_TOKENS
from services.theater.numeric_v2_budget import numeric_v2_actor_budget
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_author_entry_plan import _case as _entry_case
from tests.unit.test_theater_numeric_v2_runtime import _binding


IDENTITY = "周宁，曾参与资料整理；关于那封信只问过“是谁写的？”，并不知道答案，也未向林雨提起。"
PROJECTED_IDENTITY = "哥哥，曾参与资料整理；关于那封信只问过“是谁写的？”，并不知道答案，也未向Lan提起。"
MESSAGE = "你说的“我认识信中的人”是猜测吗？我现在还不知道。"


def _case(identity=IDENTITY, *, restricted=False):
    source, _ = _entry_case(restricted=restricted)
    story = deepcopy(source.story)
    story["intro"].update(
        player_name="周宁", catgirl_name="林雨", player_identity=identity,
        catgirl_identity="林雨，值班管理员，未听说周宁的旧事。",
        background="作者背景中另有一场尚未举行的展览。",
    )
    for node in story["nodes"]:
        state = node["story_beat"]["character_state"]
        state["player_state"] = state["player_state"].replace("男主", "周宁")
        state["catgirl_state"] = state["catgirl_state"].replace("女主", "林雨")
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="review-player-background", catgirl_binding=_binding(),
        opening_performance={"performance": "先在这里商量。", "suggested_inputs": []},
    )
    return engine, session


def _data(messages):
    human = messages[1].content
    return prompt_payload(human)


def _review_messages(engine, session, mode="ordinary", *, performance=None):
    kwargs = {
        "route_changed": mode in {"opening", "formal"},
        "completion_fact_review_only": mode == "completion",
    }
    performance = performance or {"performance": "你可以先说说。", "suggested_inputs": []}
    if mode == "formal":
        session = replace(session, transition_offered=True)
        kwargs["transition_outcome"] = engine.resolve_turn(
            session, TurnRequestV2("enter", 0, "我们进去。"), (), transition_intent="accept",
        )
        performance = {"segments": [
            {"phase": "source_response", "performance": ""},
            {"phase": "transition_bridge", "scene_narration": "两人来到门边。"},
            {"phase": "target_opening", "performance": "可以先在这里商量。"},
        ], "suggested_inputs": []}
    return evaluator._build_transition_judge_messages(
        engine, session, player_input=MESSAGE, actor_performance=performance, **kwargs,
    )[0]


@pytest.mark.parametrize('mode', ['ordinary', 'formal'])
@pytest.mark.parametrize("restricted", [False, True])
def test_review_keeps_full_cast_background_separate_from_actions_and_future_plans(mode, restricted):
    engine, session = _case(restricted=restricted)
    before = deepcopy((engine.story, session.to_dict()))
    messages = _review_messages(engine, session, mode)
    data = _data(messages)
    if restricted:
        assert "author_initial_player_background" not in data
        assert "曾参与资料整理" not in json.dumps(data, ensure_ascii=False)
    else:
        assert data["author_initial_player_background"] == {
            "source": "intro.player_identity", "text": PROJECTED_IDENTITY,
        }
        # Quotes/questions remain authored text, not parsed facts or current actions.
        assert "是谁写的？" in data["author_initial_player_background"]["text"]
        assert "并不知道答案" in data["author_initial_player_background"]["text"]
    assert data["player_input"] == MESSAGE
    assert PROJECTED_IDENTITY not in json.dumps(data["scene_context"], ensure_ascii=False)
    assert "尚未举行的展览" not in json.dumps(data, ensure_ascii=False)
    assert data["current_scene"]["author_entry_plan"]["player_state"] == "哥哥留在门边，尚未领取工具。"
    if mode == "formal":
        assert data["target_scene"]["author_entry_plan"]["player_state"] == "哥哥拿着测试仪，正在校验线路。"
    system = messages[0].content
    assert system.count("作者初始玩家身份/背景") == 1
    assert "不是本轮行动、完成证据或角色知情许可" in system
    assert "问句、引文与否定按原意核对" in system
    assert "当前角色及NPC仍须遵守各自认知限制和实际公开范围" in system
    assert "字段缺省不证明背景不存在" in system
    assert (engine.story, session.to_dict()) == before


@pytest.mark.parametrize('phase', ['ordinary'])
def test_review_background_does_not_restore_restricted_actor_knowledge(phase):
    engine, session = _case(restricted=True)
    if phase == "opening":
        messages = actor._opening_messages(engine, "谨慎。", "Lan", "哥哥", max_tokens=10000)
    else:
        session = replace(session, transition_offered=phase == "formal")
        outcome = engine.resolve_turn(
            session, TurnRequestV2("next", 0, "我们进去。"), (),
            transition_intent="accept" if phase == "formal" else "unclear",
        )
        messages = actor._turn_messages(engine, session, outcome, "我们进去。", "谨慎。", "Lan", "哥哥")
    text = json.dumps(_data(messages), ensure_ascii=False)
    assert "曾参与资料整理" not in text
    assert "未听说" not in text
    assert "limited" in text and "partial" in text
    assert "author_initial_player_background" not in _data(_review_messages(engine, session))


@pytest.mark.parametrize("field,value", [("cognition_state", "limited"), ("memory_state", "partial")])
def test_only_the_actual_formal_target_restricts_shared_background(field, value):
    source, _ = _case()
    story = deepcopy(source.story)
    target = next(node for node in story["nodes"] if node["id"] == "middle")
    target["story_beat"]["acting_contract"][field] = value
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="target-restriction", catgirl_binding=_binding(),
        opening_performance={"performance": "先在这里商量。", "suggested_inputs": []},
    )
    # A future preview target cannot hide the current Actor's legal background.
    assert _data(_review_messages(engine, session))["author_initial_player_background"]["text"] == PROJECTED_IDENTITY
    formal_messages = _review_messages(engine, session, "formal")
    assert "author_initial_player_background" not in _data(formal_messages)
    assert "曾参与资料整理" not in json.dumps(_data(formal_messages), ensure_ascii=False)


def _long_identity():
    value = "周宁，"
    suffix = "但没有参加任何检修，也不认识信中的人。"
    while evaluator.count_tokens(value + suffix) <= 365:
        value += "曾参与资料整理；"
    identity = value + suffix
    # Use a compiler-valid field that exceeds Review's per-field truncation cap.
    assert 360 < evaluator.count_tokens(identity) <= MAX_ACTOR_PROMPT_FIELD_TOKENS
    return identity


@pytest.mark.asyncio
async def test_review_sends_background_in_the_existing_single_call_without_mutating_state(monkeypatch):
    requests, factories = [], []

    async def config(_):
        return {"model": "test", "base_url": "http://test.invalid"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            requests.append(messages)
            return SimpleNamespace(content=json.dumps({
                "offer_present": False, "valid": False, "body_violations": [],
                "unsafe_suggestion_indexes": [], "failure_reason": "",
            }))

    async def factory(*args, **kwargs):
        factories.append(kwargs)
        return Client()

    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    engine, session = _case()
    before = deepcopy((engine.story, session.to_dict()))
    result = await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(
        engine=engine, session=session, message=MESSAGE,
        actor_performance={"performance": "你可以先说说。", "suggested_inputs": []},
    )
    assert len(requests) == len(factories) == 1
    assert _data(requests[0])["author_initial_player_background"]["text"] == PROJECTED_IDENTITY
    assert factories[0]["timeout"] == evaluator.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS
    assert factories[0]["max_retries"] == 0
    assert factories[0]["max_completion_tokens"] == 320
    assert result.body_violations == () and result.fact_candidates == ()
    assert (engine.story, session.to_dict()) == before


@pytest.mark.asyncio
async def test_full_background_counts_toward_real_total_budget_before_client_creation(monkeypatch):
    short_engine, short_session = _case("周宁，资料整理志愿者。")
    long_engine, long_session = _case(_long_identity())
    cap = numeric_v2_actor_budget(short_session.actor_budget_profile)["judge_input_max_tokens"]

    def performance(repetitions):
        return {"performance": "（" + "她整理好资料。" * repetitions + "）", "suggested_inputs": []}

    def packed(engine, session, repetitions):
        return _review_messages(engine, session, performance=performance(repetitions))

    # Locate the real 10000-token boundary using full wire messages, not a raised
    # budget or a stub tokenizer. The only changed input is legal author text.
    low, high = 0, 2000
    while low + 1 < high:
        mid = (low + high) // 2
        if sum(evaluator.count_tokens(m.content) for m in packed(short_engine, short_session, mid)) <= cap - 50:
            low = mid
        else:
            high = mid
    assert sum(evaluator.count_tokens(m.content) for m in packed(short_engine, short_session, low)) <= cap
    long_messages = packed(long_engine, long_session, low)
    assert sum(evaluator.count_tokens(m.content) for m in long_messages) > cap
    assert _data(long_messages)["author_initial_player_background"]["text"].endswith("也不认识信中的人。")
    calls = []

    async def config(_):
        return {"model": "test", "base_url": "http://test.invalid"}

    async def factory(*args, **kwargs):
        calls.append(kwargs)
        raise AssertionError("over-budget review must not create a client")

    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    before = deepcopy((long_engine.story, long_session.to_dict()))
    with pytest.raises(evaluator.NumericV2EvaluatorError, match="transition_review_budget_exceeded"):
        await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(
            engine=long_engine, session=long_session, message=MESSAGE,
            actor_performance=performance(low),
        )
    assert calls == []
    assert (long_engine.story, long_session.to_dict()) == before
