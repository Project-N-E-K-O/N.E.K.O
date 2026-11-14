"""Completed in-act work can recover one real next choice, without executing it."""

from copy import deepcopy
from dataclasses import replace

import pytest

from services.theater import numeric_v2_workflow as wf
from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2, MetricChangeV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_source_delivery_routing import KEY
from tests.unit.test_theater_numeric_v2_authored_transition_recovery import _case, _install, SPOOF


OFFER = "设备去向已定，现在一起到工作台另一侧继续清点，好吗？"
ACCEPT = "好，一起去工作台另一侧。"
MESSAGE = "设备就留在这里，准备好了，我们继续吧。"


async def _stay_case(tmp_path, *, completed=True, pending=False):
    seed, _ = await _case(tmp_path / "seed")
    story = deepcopy(seed.engine.story)
    source = story["nodes"][0]
    contract = source["route_gates"][0]["transition_contract"]
    contract.pop("advance_on_completion")
    contract.update(fallback_offer=OFFER, accept_input=ACCEPT)
    if pending:
        source["story_beat"]["fixed_narrations"] = [{
            "id": "pending_record", "text": "尚待展示的记录。", "after": [], "required_before_exit": True,
            "trigger": {"type": "condition", "condition": "角色实际出示记录后。"},
        }]
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path / "live")
    current = await runtime.start_session(session_id="invitation-recovery", catgirl_binding=_binding(),
        opening_performance={"performance": "先决定设备去向。", "suggested_inputs": []})
    if completed:
        prepared = runtime.prepare_turn(current, TurnRequestV2("choice", 0, "设备就留在这里。"), (),
            fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},))
        current = await runtime.commit_turn(prepared, {"performance": "设备去向已经确定。", "suggested_inputs": []})
    return runtime, current


def _install_stay(monkeypatch, runtime, *, new_choice=False):
    _install(monkeypatch, runtime)
    calls = {"actor": 0, "contract": 0, "evaluator": 0}

    async def evaluate(self, **kwargs):
        calls["evaluator"] += 1
        changes = (MetricChangeV2("trust", 2, runtime.engine.metric_schema["trust"]["increase_criteria"][0], MESSAGE),) if kwargs["message"] == MESSAGE else ()
        if new_choice and kwargs["message"] == MESSAGE:
            return NumericV2EvaluationResult(changes, False,
                fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},),
                fact_audit=({"key": KEY, "subject": "player", "action": "choose", "object": "equipment",
                             "result": "chosen", "evidence": [{"source": "player_input", "quote": MESSAGE}]},))
        return NumericV2EvaluationResult(changes, False)

    async def generate(self, **kwargs):
        calls["actor"] += 1
        outcome = kwargs["outcome"]
        if outcome.route is None:
            return {"performance": "未审稿：我已经替你搬走所有设备。", "suggested_inputs": ["未审按钮。"],
                    "fact_candidates": [{"key": SPOOF, "value": True, "evidence_quote": "未审稿：我已经替你搬走所有设备。"}]}
        return runtime.engine.finalize_transition_performance(outcome, {
            "source_performance": "好，我们过去。", "bridge_scene_narration": "两人走到工作台另一侧。",
            "target_scene_narration": "", "target_performance": "", "suggested_inputs": [],
        }, target_opening="", bridge_required=True, target_dialogue_policy=outcome.session.dialogue_policy)

    async def contract(self, **kwargs):
        calls["contract"] += 1
        if "未审稿" in str(kwargs["actor_performance"]):
            return ("不得替玩家搬运。",)
        if kwargs.get("proposed_offer"):
            kwargs["diagnostics"]["offer_actionable"] = True
        return ()

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("new_choice", [False, True])
async def test_failed_in_act_draft_recovers_a_real_control_then_continues(tmp_path, monkeypatch, new_choice):
    runtime, current = await _stay_case(tmp_path, completed=not new_choice)
    calls = _install_stay(monkeypatch, runtime, new_choice=new_choice)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("floor", current.session.revision, MESSAGE), ensure_current_binding=lambda _: _binding())
    assert result.outcome.route is None
    assert result.stored.session.current_node_id == "start"
    assert result.performance["performance"] == OFFER
    assert result.performance["suggested_inputs"] == [ACCEPT]
    assert result.stored.session.story_state["facts"][KEY]["value"] is True
    assert result.stored.session.metrics["trust"] == current.session.metrics["trust"] + 2
    assert SPOOF not in result.stored.session.story_state["facts"]
    assert "未审" not in str(result.performance)
    assert result.diagnostics["authored_invitation_recoveries"] == 1
    record = result.stored.session.performance_history[-1]
    assert record["input_text"] == MESSAGE and record.get("input_source") != "reinvite"
    receipt = result.stored.ledger_events[-1]["program_invitation"]
    assert receipt["offer"] == OFFER and receipt["accept_input"] == ACCEPT
    assert calls["evaluator"] == 1
    accept = TurnRequestV2("accept", result.stored.session.revision,
        result.performance["suggested_inputs"][0], "suggestion")
    route_id = wf._confirmed_authored_acceptance(runtime.engine, result.stored, accept,
        require_program_invitation=True)
    assert route_id
    moved = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=result.stored,
        turn=accept, ensure_current_binding=lambda _: _binding())
    assert moved.stored.session.current_node_id == "work"
    assert moved.outcome.route["id"] == route_id
    assert moved.stored.session.revision == current.session.revision + 2
    assert len([event for event in moved.stored.ledger_events if event["from_node_id"] != event["to_node_id"]]) == 1
    assert moved.stored.session.metrics["trust"] == result.stored.session.metrics["trust"]
    # The accepted, self-contained fixed entry is assembled by the program and still audited.
    assert calls == {"actor": 2, "contract": 3, "evaluator": 1}
    assert moved.diagnostics["evaluator_skip_reason"] == "verified_invitation_acceptance"


@pytest.mark.asyncio
async def test_missing_completion_keeps_the_rejection(tmp_path, monkeypatch):
    runtime, current = await _stay_case(tmp_path, completed=False)
    _install_stay(monkeypatch, runtime)
    original = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(wf.NumericV2ActorOutputError, match="contract_violation"):
        await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("not-ready", current.session.revision, MESSAGE), ensure_current_binding=lambda _: _binding())
    assert runtime.store._path(current.session.session_id).read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("condition_enabled", [False, True])
async def test_pending_condition_uses_the_same_gate_as_runtime(tmp_path, condition_enabled):
    runtime, current = await _stay_case(tmp_path, pending=True)
    outcome = runtime.prepare_turn(current, TurnRequestV2("condition-gate", current.session.revision, MESSAGE), (),
        condition_narrations_enabled=condition_enabled)
    assert (runtime.engine.route_departure_blocker(current.session,
        runtime.engine.preview_route(current.session.current_node_id, current.session.metrics,
            facts=current.session.story_state["facts"]), condition_narrations_enabled=condition_enabled) is not None) is condition_enabled
    recovered = wf._authored_invitation_recovery(runtime, current, outcome, player_input=MESSAGE)
    assert (recovered is None) is condition_enabled
    # Entry originals remain deliverable without the Review condition module.
    runtime.engine.nodes["start"]["story_beat"]["fixed_narrations"][0]["trigger"] = {"type": "entry"}
    assert wf._authored_invitation_recovery(runtime, current, outcome, player_input=MESSAGE) is None


@pytest.mark.asyncio
async def test_floor_is_not_issued_repeatedly_in_the_same_visit(tmp_path, monkeypatch):
    runtime, current = await _stay_case(tmp_path)
    _install_stay(monkeypatch, runtime)
    result = await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("once", current.session.revision, MESSAGE), ensure_current_binding=lambda _: _binding())
    original = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(wf.NumericV2ActorOutputError, match="contract_violation"):
        await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=result.stored,
            turn=TurnRequestV2("twice", result.stored.session.revision, "还有一点想问。"), ensure_current_binding=lambda _: _binding())
    assert runtime.store._path(current.session.session_id).read_bytes() == original
    assert wf._confirmed_authored_acceptance(runtime.engine, result.stored,
        TurnRequestV2("accept", result.stored.session.revision, result.performance["suggested_inputs"][0], "suggestion"),
        require_program_invitation=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["forbidden", "reject", "defer", "terminal"])
async def test_floor_never_overrides_silence_refusal_deferral_or_terminal_route(tmp_path, reason):
    runtime, current = await _stay_case(tmp_path)
    outcome = runtime.prepare_turn(current, TurnRequestV2("guard", current.session.revision, MESSAGE), ())
    message = MESSAGE
    if reason == "forbidden":
        outcome = replace(outcome, session=replace(outcome.session, dialogue_policy="forbidden"))
    elif reason == "reject":
        outcome = replace(outcome, ledger_event={**outcome.ledger_event, "transition_intent": "reject"})
    elif reason == "defer":
        message = "先别出发，我还想在这里待一会儿。"
    else:
        runtime.engine.nodes["work"]["type"] = "ending"
    assert wf._authored_invitation_recovery(runtime, current, outcome, player_input=message) is None
