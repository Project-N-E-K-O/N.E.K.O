"""Only a committed, stable typed ending can omit the redundant pre-draft review."""

from copy import deepcopy
from dataclasses import replace

import pytest

from services.theater import numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult
from services.theater.numeric_v2_runtime import MetricChangeV2, NumericV2Engine, NumericV2Runtime, NumericV2RuntimeError, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding

KEY = "scene:start:done"
SIDE = "scene:start:side"
MODULES = {"evaluator": True, "review": False, "review_contract": True, "review_delivery": True}


async def _frame(tmp_path, *, completed=True, declared=True, fact_route=False, ordinary=False):
    story = numeric_v2_story()
    story["fact_contract"] = {"facts": {key: {"value_type": "bool", "visibility": "public",
        "description": "已经交付的结果。"} for key in (KEY, SIDE)}}
    source = story["nodes"][0]
    if declared:
        source["completion_contract"] = {"all": [{"key": KEY, "equals": True}]}
    if fact_route:
        source["route_gates"][0]["conditions"] = {"all": [{"type": "fact_equals", "key": SIDE, "value": True}]}
    if ordinary:
        middle = deepcopy(source)
        middle.update(id="middle", type="scene")
        middle.pop("completion_contract", None)
        for route in middle["route_gates"]:
            route["id"] = "middle_" + route["id"]
        story["nodes"].append(middle)
        source["route_gates"][0]["target_node_id"] = "middle"
        source["route_gates"][0]["conditions"]["all"][0]["value"] = 0
        source["route_gates"][0]["transition_contract"]["fallback_offer"] = "现在一起去工作台另一侧吗？"
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id="stable-ending", catgirl_binding=_binding(),
        opening_performance={"performance": "先处理眼前的事。", "suggested_inputs": []})
    operations = ({"op": "set", "key": KEY, "value": True, "visibility": "public"},) if completed else ()
    prepared = runtime.prepare_turn(current, TurnRequestV2("result", 0, "结果已确认。"), (), fact_operations=operations)
    current = await runtime.commit_turn(prepared, {"performance": "当前事情已经处理完毕。", "suggested_inputs": []})
    turn = TurnRequestV2("close", current.session.revision, "谢谢，这段事情可以收束了。")
    evaluation = NumericV2EvaluationResult((), True, natural_ending_ready=True)
    route = runtime.engine.preview_route(current.session.current_node_id, current.session.metrics,
        facts=current.session.story_state["facts"])
    return runtime, current, turn, evaluation, route


@pytest.mark.asyncio
async def test_gate_is_read_only_and_keeps_unrelated_new_fact_review_possible(tmp_path, monkeypatch):
    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    runtime, current, turn, evaluation, route = await _frame(tmp_path)
    path = runtime.store._path(current.session.session_id)
    before = path.read_bytes()
    candidate = replace(evaluation, fact_operations=({"op": "set", "key": SIDE, "value": True, "visibility": "public"},))
    assert wf._committed_natural_ending_review_redundant(runtime, current, turn, candidate, route, MODULES)
    assert path.read_bytes() == before and SIDE not in current.session.story_state["facts"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [
    "legacy", "incomplete", "evaluator_off", "contract_off", "delivery_off", "full_review", "legacy_reviews",
    "scene_incomplete", "ending_unready", "reject", "defer", "ordinary", "wrong_route", "stale_request",
    "metric_route_changed", "fact_route_changed", "completion_revoked", "required_original", "prepare_error",
])
async def test_gate_retains_every_pre_review_boundary(tmp_path, monkeypatch, reason):
    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    runtime, current, turn, evaluation, route = await _frame(tmp_path, declared=reason != "legacy",
        completed=reason != "incomplete", fact_route=reason == "fact_route_changed", ordinary=reason == "ordinary")
    modules = dict(MODULES)
    if reason in {"evaluator_off", "contract_off", "delivery_off"}:
        modules[{"evaluator_off": "evaluator", "contract_off": "review_contract", "delivery_off": "review_delivery"}[reason]] = False
    elif reason == "full_review": modules["review"] = True
    elif reason == "legacy_reviews": monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", True)
    elif reason == "scene_incomplete": evaluation = replace(evaluation, scene_complete=False)
    elif reason == "ending_unready": evaluation = replace(evaluation, natural_ending_ready=False)
    elif reason == "reject": evaluation = replace(evaluation, transition_intent="reject")
    elif reason == "defer": turn = replace(turn, message="先别结束，我还有话要说。")
    elif reason == "wrong_route": route = {**route, "id": "not-the-selected-route"}
    elif reason == "stale_request": turn = replace(turn, base_revision=0)
    elif reason == "metric_route_changed":
        runtime.engine.nodes["start"]["route_gates"][0]["conditions"]["all"][0]["value"] = 22
        evaluation = replace(evaluation, metric_changes=(MetricChangeV2("trust", 3, "玩家兑现承诺", turn.message),))
    elif reason in {"fact_route_changed", "completion_revoked"}:
        evaluation = replace(evaluation, fact_operations=({"op": "set",
            "key": SIDE if reason == "fact_route_changed" else KEY,
            "value": reason == "fact_route_changed", "visibility": "public"},))
    elif reason == "required_original":
        monkeypatch.setattr(runtime.engine, "route_departure_blocker", lambda *a, **k: "required_narration_pending")
    elif reason == "prepare_error":
        def prepare(*args, **kwargs): raise NumericV2RuntimeError("numeric_v2_player_handoff_required")
        monkeypatch.setattr(runtime, "prepare_turn", prepare)
    before = runtime.store._path(current.session.session_id).read_bytes()
    assert not wf._committed_natural_ending_review_redundant(runtime, current, turn, evaluation, route, modules)
    assert runtime.store._path(current.session.session_id).read_bytes() == before
