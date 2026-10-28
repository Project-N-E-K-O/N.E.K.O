"""Explicit route choices must survive invitation drift, persistence and replay."""

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, NumericV2RuntimeError, TurnRequestV2
from services.theater.numeric_v2_store import NumericV2StoreError
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening, _transition_performance


KEY = "scene:start:route_choice"


def fact_route_story():
    story = numeric_v2_story()
    story["fact_contract"] = {"facts": {KEY: {
        "value_type": "string", "visibility": "public",
        "description": "玩家明确选择的路线，只能按公开选择记录为官道或山径。",
    }}}
    for route, value in zip(story["nodes"][0]["route_gates"], ("官道", "山径")):
        route["conditions"] = {"all": [{"type": "fact_equals", "key": KEY, "value": value}]}
        route["priority"] = 10
    return story


def choice(value):
    return ({"op": "set", "key": KEY, "value": value, "visibility": "public"},)


@pytest.mark.parametrize("value,target", [("官道", "ending_stay"), ("山径", "ending_leave")])
def test_current_choice_routes_independently_of_personality_metrics(value, target):
    engine = NumericV2Engine.from_mapping(fact_route_story())
    session = engine.create_session(session_id="choice", catgirl_binding=_binding(), opening_performance=_opening())
    assert engine.preview_route("start", session.metrics) is None
    assert engine.preview_route("start", {"trust": 99}, facts={KEY: {"value": "unknown"}}) is None
    before = deepcopy(session)
    outcome = engine.resolve_turn(session, TurnRequestV2("go", 0, f"我选{value}，出发。"), (),
                                  transition_intent="initiate", fact_operations=choice(value))
    assert outcome.session.current_node_id == target
    assert outcome.ledger_event["routing_fact_operations"] == list(choice(value))
    assert session == before


def test_fact_equality_distinguishes_boolean_from_integer():
    conditions = {"all": [{"type": "fact_equals", "key": "choice", "value": True}]}
    assert not NumericV2Engine._conditions_match(conditions, {}, {"choice": {"value": 1}})
    assert NumericV2Engine._conditions_match(conditions, {}, {"choice": {"value": True}})
    assert not NumericV2Engine._conditions_match(conditions, {}, {})


def test_existing_metric_condition_metadata_does_not_become_a_predicate():
    from services.theater.numeric_v2_evaluator import _build_messages

    story = numeric_v2_story()
    story["nodes"][0]["route_gates"][0]["conditions"]["note"] = "作者说明"
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="metadata", catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2("go", 0, "你好。"), ())
    assert outcome.route is None
    assert "routing_fact_operations" not in outcome.ledger_event
    _build_messages(engine, session, "你好。")


async def commit_choice(runtime, current, value, *, offer):
    outcome = runtime.prepare_turn(current, TurnRequestV2(f"choose{current.session.revision}",
        current.session.revision, f"我选{value}。"), (), fact_operations=choice(value))
    outcome, performance = runtime.engine.finalize_transition_offer_state(outcome,
        {"performance": f"我们沿{value}出发，好吗？", "suggested_inputs": ["好，出发。"]}, new_offer=offer)
    return await runtime.commit_turn(outcome, performance)


@pytest.mark.asyncio
@pytest.mark.parametrize("change_before_accept", [False, True])
async def test_old_invitation_cannot_change_destination_after_choice_changes(tmp_path, change_before_accept):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(fact_route_story()), tmp_path)
    current = await runtime.start_session(session_id="drift", catgirl_binding=_binding(), opening_performance=_opening())
    current = await commit_choice(runtime, current, "官道", offer=True)
    if change_before_accept:
        current = await commit_choice(runtime, current, "山径", offer=False)
    current = await runtime.restore_session("drift")
    outcome = runtime.prepare_turn(current, TurnRequestV2("accept", current.session.revision, "好，出发。"), (),
        transition_intent="accept", fact_operations=() if change_before_accept else choice("山径"))
    assert outcome.route is None
    assert outcome.ledger_event["accepted_offer_route_id"] == "to_stay"
    assert outcome.ledger_event["transition_offer_invalidated"] is True
    outcome, performance = runtime.engine.finalize_transition_offer_state(outcome,
        {"performance": "路线改了，我们重新确认安排。", "suggested_inputs": []}, new_offer=False)
    current = await runtime.commit_turn(outcome, performance)
    assert await runtime.restore_session("drift") == current
    fork = await runtime.fork_session_for_test("drift", session_id="fork", through_revision=current.session.revision)
    assert fork.session.current_node_id == "start"
    assert fork.session.story_state["facts"][KEY]["value"] == "山径"


@pytest.mark.asyncio
async def test_late_actor_facts_do_not_retroactively_choose_route_on_replay(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(fact_route_story()), tmp_path)
    current = await runtime.start_session(session_id="late", catgirl_binding=_binding(), opening_performance=_opening())
    outcome = runtime.prepare_turn(current, TurnRequestV2("choose", 0, "我选山径，走吧。"), (), transition_intent="initiate")
    assert outcome.route is None
    outcome = runtime.engine.finalize_fact_operations(current.session, outcome, operations=choice("山径"))
    current = await runtime.commit_turn(outcome, {"performance": "山径的安排已经说清。", "suggested_inputs": []})
    assert current.ledger_events[0]["routing_fact_operations"] == []
    assert current.ledger_events[0]["fact_operations"] == list(choice("山径"))
    assert await runtime.restore_session("late") == current
    fork = await runtime.fork_session_for_test("late", session_id="late_fork", through_revision=1)
    assert fork.session.current_node_id == "start"
    outcome = runtime.prepare_turn(current, TurnRequestV2("go", 1, "出发。"), (), transition_intent="initiate")
    assert outcome.session.current_node_id == "ending_leave"
    current = await runtime.commit_turn(outcome, _transition_performance("ending_leave"))
    assert (await runtime.restore_session("late")).session.current_node_id == "ending_leave"
    path = tmp_path / "numeric_v2/sessions/late.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["ledger_events"][0].pop("routing_fact_operations")
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(NumericV2StoreError, match="numeric_ledger_replay_invalid"):
        await runtime.restore_session("late")


@pytest.mark.asyncio
async def test_invitation_fact_replay_retains_declared_runtime_scene_events(tmp_path):
    story = fact_route_story()
    event_key = "event:scene.entered:start:r0"
    story["fact_contract"]["facts"][event_key] = {
        "value_type": "bool", "visibility": "public", "description": "开场已进入。"}
    routes = story["nodes"][0]["route_gates"]
    routes[0]["conditions"] = {"all": [{"type": "fact_equals", "key": event_key, "value": True}]}
    routes[1]["conditions"] = {"all": [{"type": "fact_equals", "key": event_key, "value": False}]}
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id="scene_event", catgirl_binding=_binding(), opening_performance=_opening())
    current = await commit_choice(runtime, current, "官道", offer=True)
    outcome = runtime.prepare_turn(current, TurnRequestV2("accept", 1, "好，出发。"), (), transition_intent="accept")
    assert outcome.ledger_event["accepted_offer_route_id"] == "to_stay"
    assert outcome.session.current_node_id == "ending_stay"
    current = await runtime.commit_turn(outcome, _transition_performance("ending_stay"))
    assert await runtime.restore_session("scene_event") == current


def test_actor_preview_uses_choice_from_prepared_outcome():
    from services.theater.numeric_v2_actor import _turn_messages

    engine = NumericV2Engine.from_mapping(fact_route_story())
    session = engine.create_session(session_id="preview", catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2("choose", 0, "我选山径。"), (), fact_operations=choice("山径"))
    messages = _turn_messages(engine, session, outcome, "我选山径。", "简洁", "Lan", "哥哥")
    data = prompt_payload(messages[-1].content)
    assert "runtime_unresolved" not in data["next_scene"]
    assert engine.nodes["ending_leave"]["chapter"] in data["next_scene"]


def test_target_leak_guards_follow_new_choice_without_treating_it_as_committed_history():
    from services.theater.numeric_v2_context import premature_target_scene_facts, premature_target_markers

    story = fact_route_story()
    target = story["nodes"][2]["story_beat"]
    target["opening_scene"] = "19:47，山径尽头的石桥下已经堆满被山洪冲散的木箱。"
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="leak", catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2("choose", 0, "我选山径。"), (), fact_operations=choice("山径"))
    candidate = {"scene_narration": target["opening_scene"], "performance": "我们先商量。"}
    assert premature_target_scene_facts(engine, session, outcome, candidate, "我选山径。")
    assert premature_target_markers(engine, session, outcome, candidate, "我选山径。") == ("19:47",)
    assert KEY not in session.story_state["facts"]


@pytest.fixture
def loop_at_fact_capacity():
    story = numeric_v2_story()
    key = "scene:loop:choice"
    story["metric_schema"]["trust"]["initial"] = 80
    story["initial_state"]["metrics"]["trust"] = 80
    story["fact_contract"] = {"facts": {
        key: {"value_type": "bool", "visibility": "public", "description": "玩家明确选择离开。"},
        "event:scene.entered:start:r0": {
            "value_type": "bool", "visibility": "public", "description": "开场已进入。",
        },
    }}
    start = story["nodes"][0]
    loop = deepcopy(start)
    loop.update(id="loop", type="scene")
    start["route_gates"][0]["target_node_id"] = "loop"
    loop["route_gates"][0].update(id="loop_back", target_node_id="start", priority=10,
        conditions={"all": [{"type": "metric_compare", "metric": "trust", "op": ">=", "value": 0}]})
    loop["route_gates"][1].update(id="loop_finish", target_node_id="ending_stay", priority=20,
        conditions={"all": [{"type": "fact_equals", "key": key, "value": True}]})
    story["nodes"].append(loop)
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="long_loop", catgirl_binding=_binding(), opening_performance=_opening())
    for index in range(129):
        session = engine.resolve_turn(session, TurnRequestV2(f"loop_{index}", session.revision, "去下一处。"), (),
                                      transition_intent="initiate").session
    assert session.current_node_id == "loop"
    assert len(session.story_state["facts"]) == 256
    return engine, session, {"op": "set", "key": key, "value": True, "visibility": "public"}


@pytest.mark.parametrize("intent,target", [("unclear", "loop"), ("initiate", "ending_stay")])
def test_fact_choice_at_capacity_prunes_old_events_without_losing_author_facts(loop_at_fact_capacity, intent, target):
    engine, session, operation = loop_at_fact_capacity
    before = deepcopy(session)
    outcome = engine.resolve_turn(session, TurnRequestV2("choice", session.revision, "我要离开。"), (),
                                  transition_intent=intent, fact_operations=(operation,))
    assert outcome.session.current_node_id == target
    assert len(outcome.session.story_state["facts"]) == 256
    assert outcome.session.story_state["facts"][operation["key"]]["value"] is True
    assert outcome.session.story_state["facts"]["event:scene.entered:start:r0"]["value"] is True
    assert outcome.ledger_event["routing_fact_operations"] == [operation]
    assert session == before


@pytest.mark.parametrize("change,error", [
    ("wrong_type", "story_state_fact_value_type_not_allowed"),
    ("unknown_key", "story_state_fact_key_not_allowed"),
    ("duplicate", "story_state_fact_duplicate_op"),
])
def test_fact_choice_at_capacity_keeps_invalid_operation_rejection_atomic(loop_at_fact_capacity, change, error):
    engine, session, operation = loop_at_fact_capacity
    before = deepcopy(session)
    if change == "wrong_type":
        operation["value"] = 1
    elif change == "unknown_key":
        operation["key"] = "scene:loop:undeclared"
    operations = (operation, operation) if change == "duplicate" else (operation,)
    with pytest.raises(NumericV2RuntimeError, match=error):
        engine.resolve_turn(session, TurnRequestV2("invalid_choice", session.revision, "我要离开。"), (),
                            transition_intent="initiate", fact_operations=operations)
    assert session == before
