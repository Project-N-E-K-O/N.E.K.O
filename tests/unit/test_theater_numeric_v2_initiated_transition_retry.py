"""Initiated transitions repair one actual historical replay within the turn deadline."""

from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_actor_output import NumericV2ActorError, NumericV2ActorOutputError
from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding


OLDER = "（合上资料）手续已经全部核验完毕，阅览室通道向我们开放。"
LATEST = "（收回手）入口方向就在左侧，过去后先停在门口看一看。"
FRESH_SOURCE = "（点头示意左侧）这次先带你到阅览室入口。"
FRESH_TARGET = "（留出通道）我们先看目录，再商量下一步。"


async def _setup(tmp_path, monkeypatch, *, guard="transition_source", permanent=False,
                 intent="initiate", blocked=False, exhaust_budget=False):
    # The destination is really public and the player explicitly requests it;
    # Runtime still chooses the route. No pending invitation or fake acceptance.
    case = initiation_case(public=True, message="带我去阅览室吧。")
    engine = case["engine"]
    if blocked:
        # Keep the valid high-trust route only; the current state cannot reach it.
        engine.nodes["start"]["route_gates"] = engine.nodes["start"]["route_gates"][:1]
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="initiated-retry", catgirl_binding=_binding(),
                                          opening_performance=case["session"].opening_performance)
    for index, text in enumerate((OLDER, LATEST)):
        previous = runtime.prepare_turn(current, TurnRequestV2(f"previous-{index}",
            current.session.revision, "先在原地等一下。"), ())
        current = await runtime.commit_turn(previous, {"performance": text, "suggested_inputs": []})

    calls = {"evaluator": 0, "actor": 0, "formal": [], "messages": []}
    clock = SimpleNamespace(now=0.0)
    if exhaust_budget:
        # Replace only Workflow's clock binding, not asyncio's actual clock.
        # The first response consumes the original generation allowance without
        # sleep; retry admission must check the existing review-reserved deadline.
        monkeypatch.setattr(workflow, "time", SimpleNamespace(monotonic=lambda: clock.now))

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, "actor_retry": False}

    async def evaluate(self, **kwargs):
        calls["evaluator"] += 1
        return NumericV2EvaluationResult((), False, transition_intent=intent)

    async def invoke(self, messages, **kwargs):
        calls["actor"] += 1
        calls["formal"].append(kwargs["transition_required"])
        calls["messages"].append(messages)
        failing = permanent or calls["actor"] == 1
        if exhaust_budget and calls["actor"] == 1:
            clock.now = (workflow.NUMERIC_V2_TURN_MODEL_BUDGET_SECONDS
                         - workflow.NUMERIC_V2_TURN_REVIEW_RESERVE_SECONDS)
        if failing and guard == "format":
            raise NumericV2ActorOutputError("numeric_v2_actor_invalid_json")
        if not kwargs["transition_required"]:
            # Exercise a real same-visit earlier_session guard on a staying turn.
            return {"performance": OLDER, "suggested_inputs": [], "transition_offered": False}
        return {
            "source_performance": ((OLDER if guard == "earlier_session" else LATEST)
                                   if failing and guard != "transition_target" else FRESH_SOURCE),
            "bridge_scene_narration": "两人沿左侧走廊来到阅览室入口。",
            "target_scene_narration": "两人停在阅览室入口，后续操作尚未开始。",
            "target_performance": LATEST if failing and guard == "transition_target" else FRESH_TARGET,
            "suggested_inputs": [],
        }

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_invoke", invoke)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和克制。")
    diagnostics = {}

    async def execute():
        return await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime,
            current=current, turn=TurnRequestV2("go", current.session.revision, case["message"]),
            ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics)

    return runtime, current, calls, diagnostics, execute


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["transition_source", "transition_target", "earlier_session"])
async def test_initiated_transition_repairs_actual_replay_once_without_reevaluation(tmp_path, monkeypatch, guard):
    runtime, current, calls, diagnostics, execute = await _setup(tmp_path, monkeypatch, guard=guard)
    result = await execute()
    assert calls["evaluator"] == 1 and calls["actor"] == 2
    assert calls["formal"] == [True, True]
    assert diagnostics["actor_repeated_output_guards"] == {guard: 1}
    assert diagnostics["actor_generation_attempts"] == 2
    assert result.stored.session.current_node_id == "ending_leave"
    assert result.stored.session.status == "active"
    assert result.stored.session.revision == current.session.revision + 1
    assert result.stored.session.metrics == current.session.metrics
    assert result.stored.ledger_events[-1]["transition_intent"] == "initiate"
    assert len(result.stored.ledger_events) == len(current.ledger_events) + 1
    assert result.performance["segments"][0]["performance"] == FRESH_SOURCE
    assert result.performance["segments"][2]["performance"] == FRESH_TARGET
    field = "target_performance" if guard == "transition_target" else "source_performance"
    assert f"重复位置：{field}" in calls["messages"][1][0].content
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
async def test_permanent_initiated_replay_stops_after_one_retry_and_rolls_back(tmp_path, monkeypatch):
    runtime, current, calls, diagnostics, execute = await _setup(tmp_path, monkeypatch, permanent=True)
    with pytest.raises(NumericV2ActorOutputError, match="repeated_output"):
        await execute()
    assert calls["evaluator"] == 1 and calls["actor"] == 2
    assert diagnostics["actor_repeated_output_guards"] == {"transition_source": 2}
    assert diagnostics["actor_repeated_output_retry_aborted"] == 1
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_initiated_format_error_does_not_enable_general_actor_retry(tmp_path, monkeypatch):
    runtime, current, calls, diagnostics, execute = await _setup(tmp_path, monkeypatch, guard="format")
    with pytest.raises(NumericV2ActorOutputError, match="invalid_json"):
        await execute()
    assert calls["evaluator"] == 1 and calls["actor"] == 1
    assert diagnostics["actor_generation_attempts"] == 1
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
@pytest.mark.parametrize("intent, blocked", [("unclear", False), ("initiate", True)])
async def test_staying_or_runtime_unavailable_initiation_does_not_retry_replay(tmp_path, monkeypatch, intent, blocked):
    runtime, current, calls, diagnostics, execute = await _setup(tmp_path, monkeypatch,
                                                                intent=intent, blocked=blocked)
    with pytest.raises(NumericV2ActorOutputError, match="repeated_output"):
        await execute()
    assert calls["formal"] == [False]
    assert calls["evaluator"] == 1 and calls["actor"] == 1
    assert diagnostics["actor_repeated_output_guards"] == {"earlier_session": 1}
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_original_generation_deadline_exhaustion_never_starts_second_actor(tmp_path, monkeypatch):
    runtime, current, calls, diagnostics, execute = await _setup(tmp_path, monkeypatch,
                                                                exhaust_budget=True)
    with pytest.raises(NumericV2ActorError, match="numeric_v2_actor_timeout"):
        await execute()
    assert calls["evaluator"] == 1 and calls["actor"] == 1
    assert diagnostics["actor_generation_attempts"] == 1
    assert diagnostics["actor_repeated_output_guards"] == {"transition_source": 1}
    assert await runtime.restore_session(current.session.session_id) == current
