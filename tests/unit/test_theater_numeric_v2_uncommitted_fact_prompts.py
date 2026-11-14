"""Draft candidates cannot impersonate committed facts or erase real actions, routes and history."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_action_projection as projection
from services.theater import numeric_v2_actor as actor
from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening, _performance
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


KEY = "scene:start:inspection_done"
OTHER = "scene:start:other_done"
MESSAGE = "（拿起记录本）我先看看。明天再来。"


def _engine():
    story = numeric_v2_story()
    story["fact_contract"] = {"facts": {
        KEY: {"value_type": "bool", "visibility": "public", "description": "设备已经完成实际检查。"},
        OTHER: {"value_type": "bool", "visibility": "public", "description": "另一项实际结果。"},
    }}
    story["nodes"][0]["completion_contract"] = {"all": [{"key": KEY, "equals": True}]}
    return NumericV2Engine.from_mapping(story)


def _operations(value=True):
    return ({"op": "set", "key": KEY, "value": value, "visibility": "public"},)


def _payload(messages):
    text = messages[1].content
    return prompt_payload(text)


def _system_projection(messages):
    # 读取真正发给模型的 System JSON，而不是只测辅助函数的返回值。
    text = messages[0].content
    start = text.index('{"schema":"' + projection.PLAYER_ACTION_PROJECTION_SCHEMA + '"')
    return json.JSONDecoder().raw_decode(text[start:])[0]


def _actor_messages(engine, session, outcome, message=MESSAGE):
    return actor._turn_messages(engine, session, outcome, message, "温和克制。", "小岚", "林舟",
        player_action_projection=outcome.ledger_event["player_action_projection"])


def _no_runtime_result(value):
    return all(row["kind"] != "runtime_result" or row["source"] != "runtime_fact"
               for row in value["confirmed_actions"])


@pytest.mark.parametrize("intent", ['unclear', 'initiate'])
def test_prompt_copy_removes_only_current_runtime_fact_results(intent):
    # 两种值和多个键都属于本轮待结算结果；直接动作、未来约定与已授权路线不能一起丢失。
    source = projection.project_player_action_result(MESSAGE, revision=4, transition_intent=intent,
        route_changed=intent != "unclear", fact_operations=(*_operations(), {"key": OTHER, "value": False}))
    original = deepcopy(source)
    result = projection.player_action_projection_for_prompt(source)
    assert _no_runtime_result(result)
    assert result["source_revision"] == 4
    assert result["has_confirmed_player_action"] is True
    assert result["future_references"] == original["future_references"]
    assert result["confirmed_actions"] == [row for row in original["confirmed_actions"]
        if row["source"] in {"player_input", "runtime_transition"}]
    assert source == original
    # 持久化 normalizer 继续保留旧协议，不能把 Prompt 的过滤扩大成历史清洗。
    assert projection.normalize_player_action_projection(source) == original


@pytest.mark.parametrize("formal,value", [
    pytest.param(False, False, id="False-False"),
    pytest.param(False, True, id="True-False"),
    pytest.param(True, True, id="True-True"),
])
def test_actor_uses_committed_session_instead_of_current_fact_operation(formal, value):
    engine = _engine()
    session = engine.create_session(session_id="prompt-current-fact", catgirl_binding=_binding(),
        opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2("pending", 0, MESSAGE), (),
        fact_operations=_operations(value), transition_intent="initiate" if formal else "unclear")
    original = deepcopy((session, outcome))
    messages = _actor_messages(engine, session, outcome)
    current = _system_projection(messages)
    assert _no_runtime_result(current)
    assert any(row["source"] == "player_input" for row in current["confirmed_actions"])
    assert any(row["kind"] == "transition_committed" for row in current["confirmed_actions"]) is formal
    data = _payload(messages)
    if formal:
        assert "source_committed_completion" not in data["transition"]
    else:
        scene = data["current_scene"]
        start = scene.index("{", scene.index("结构化幕完成事实"))
        context = json.JSONDecoder().raw_decode(scene[start:])[0]
        row = next(row for row in context["all"] if row["key"] == KEY)
        assert row["committed"] is False and row["satisfied"] is False
        assert "committed_value" not in row and "registration_revision" not in row
        assert row["description"] == engine.fact_contract[KEY]["description"]
    assert (session, outcome) == original
    assert outcome.ledger_event["fact_operations"] == list(_operations(value))
    assert not _no_runtime_result(outcome.ledger_event["player_action_projection"])


@pytest.mark.asyncio
async def test_prior_registered_value_survives_while_new_value_remains_pending(tmp_path):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id="prior-value", catgirl_binding=_binding(), opening_performance=_opening())
    first = runtime.prepare_turn(current, TurnRequestV2("old", 0, "我还没检查。"), (), fact_operations=_operations(False))
    current = await runtime.commit_turn(first, _performance("那就先看记录。"))
    outcome = runtime.prepare_turn(current, TurnRequestV2("new", 1, MESSAGE), (), fact_operations=_operations(True))
    data = _payload(_actor_messages(runtime.engine, current.session, outcome))
    scene = data["current_scene"]
    start = scene.index("{", scene.index("结构化幕完成事实"))
    row = json.JSONDecoder().raw_decode(scene[start:])[0]["all"][0]
    assert row["committed"] is True and row["committed_value"] is False
    assert row["satisfied"] is False
    assert current.session.story_state["facts"][KEY]["value"] is False
    assert outcome.session.story_state["facts"][KEY]["value"] is True


def test_evaluator_current_input_copy_does_not_promote_a_model_result():
    engine = _engine()
    session = engine.create_session(session_id="evaluator-projection", catgirl_binding=_binding(), opening_performance=_opening())
    source = projection.project_player_action_result(MESSAGE, revision=1, fact_operations=_operations())
    original = deepcopy((session, source))
    data = _payload(evaluator._build_messages(engine, session, MESSAGE, player_action_projection=source))
    assert _no_runtime_result(data["player_action_projection"])
    assert data["player_input"] == MESSAGE
    assert data["current_story_beat"]["runtime_scene_facts"]["facts"]
    assert (session, source) == original


@pytest.mark.parametrize("visibility,target", [("public", True), ("story", True), ("public", False), ("story", False)])
def test_approved_completion_preserves_visibility_and_boolean_target(visibility, target):
    """Apply approved values within existing visibility without leaking quotes or claiming premature persistence."""
    engine = _engine()
    engine.fact_contract[KEY]["visibility"] = visibility
    engine.nodes["start"]["completion_contract"]["all"][0]["equals"] = target
    session = engine.create_session(session_id="approved-view", catgirl_binding=_binding(),
        opening_performance={"performance": "结果已经确认。", "suggested_inputs": []})
    operation = {"op": "set", "key": KEY, "value": target, "visibility": visibility}
    outcome = engine.resolve_turn(session, TurnRequestV2("view", 0, MESSAGE), (), fact_operations=(operation,))
    claim = {**operation, "evidence": [{"source": "actor_performance", "quote": "结果已经确认。"}]}
    messages = actor._turn_messages(engine, session, outcome, MESSAGE, "温和。", "小岚", "林舟",
        approved_completion_facts=(claim,))
    scene = _payload(messages)["current_scene"]
    start = scene.index("{", scene.index("结构化幕完成事实"))
    context = json.JSONDecoder().raw_decode(scene[start:])[0]
    row = context["all"][0]
    assert context["status"] == "satisfied"
    assert row["committed"] is False and row["satisfied"] is True
    assert row["approved_this_turn"] is True and row["approved_value"] is target
    assert ("evidence" in row) is (visibility == "public")
    assert KEY not in session.story_state["facts"]


@pytest.mark.parametrize("completion_only", [False, True])
def test_full_and_completion_reviews_receive_the_same_nonselfconfirming_copy(completion_only):
    engine = _engine()
    session = engine.create_session(session_id="review-projection", catgirl_binding=_binding(), opening_performance=_opening())
    source = projection.project_player_action_result(MESSAGE, revision=1, fact_operations=_operations())
    candidate = {"performance": "我先看看记录。", "suggested_inputs": [],
                 "fact_candidates": [{"key": KEY, "value": True, "evidence_quote": "我先看看记录。"}]}
    original = deepcopy((session, source, candidate))
    messages, _ = evaluator._build_transition_judge_messages(engine, session, actor_performance=candidate,
        player_input=MESSAGE, player_action_projection=source, completion_fact_review_only=completion_only)
    data = _payload(messages)
    assert _no_runtime_result(data["player_action_projection"])
    assert data["player_action_projection"]["confirmed_actions"][0]["source"] == "player_input"
    assert data["actor_fact_candidates"][0]["key"] == KEY
    assert data["pending_completion_facts"][0]["key"] == KEY
    assert (session, source, candidate) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("formal", [False, True])
async def test_prompt_filter_does_not_change_settlement_commit_cold_restore_or_fork(tmp_path, formal):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id="stored-projection", catgirl_binding=_binding(), opening_performance=_opening())
    outcome = runtime.prepare_turn(current, TurnRequestV2("commit", 0, MESSAGE), (),
        fact_operations=_operations(), transition_intent="initiate" if formal else "unclear")
    original = deepcopy(outcome)
    messages = _actor_messages(runtime.engine, current.session, outcome)
    assert _no_runtime_result(_system_projection(messages))
    assert outcome == original
    performance = runtime.engine.finalize_transition_performance(outcome, _candidate(), target_opening="雨后的长街。") if formal else _performance("先看记录。")
    result = await runtime.commit_turn(outcome, performance)
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id)
    forked = await runtime.fork_session_for_test(current.session.session_id, session_id="stored-projection-fork", through_revision=1)
    assert restored == result
    for stored in (result, restored, forked):
        assert stored.session.story_state["facts"][KEY]["value"] is True
        assert stored.ledger_events[-1]["fact_operations"] == list(_operations())
        assert not _no_runtime_result(stored.ledger_events[-1]["player_action_projection"])
        assert stored.session.performance_history[-1]["player_action_projection"] == original.ledger_event["player_action_projection"]
        assert stored.session.current_node_id == original.session.current_node_id


@pytest.mark.parametrize("consumer", ["actor", "evaluator"])
def test_requests_without_current_fact_operations_keep_native_normalized_wire(monkeypatch, consumer):
    engine = _engine()
    session = engine.create_session(session_id="unchanged-wire", catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2("no-facts", 0, MESSAGE), ())
    def build():
        return _actor_messages(engine, session, outcome) if consumer == "actor" else evaluator._build_messages(
            engine, session, MESSAGE, player_action_projection=outcome.ledger_event["player_action_projection"])
    actual = [message.content for message in build()]
    # 用原 normalizer 作为无候选对照；不在测试里复刻过滤算法或依赖仓库外快照。
    monkeypatch.setattr(actor if consumer == "actor" else evaluator, "player_action_projection_for_prompt",
                        projection.normalize_player_action_projection, raising=False)
    assert [message.content for message in build()] == actual
