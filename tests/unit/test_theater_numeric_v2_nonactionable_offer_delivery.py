"""A nonactionable future proposal cannot discard an independently cleared body."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_context import pending_transition_record
from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


BODY = "（轻轻点头）柜台旁两份材料放回原位后，在登记簿记上数量和位置即可。今晚就这一项。"
OFFER = "等今晚交接告一段落，明天下午一起整理角落里的旧箱，如何？"
ACCEPT_NEW = "好，那就明天下午见。我先去归位材料。"
OLD_OFFER = "我们现在去隔壁清点台看看，好吗？"
ACCEPT_OLD = "好，现在去隔壁清点台。"
RULE = "不得替玩家完成材料归位或登记，也不得编造玩家已同意。"


async def _case(tmp_path, pending):
    story = numeric_v2_story()
    source = story["nodes"][0]
    source["story_beat"].update(
        summary="材料暂放柜台，角色说明归位登记方法，玩家自行决定是否操作。",
        must_happen=["角色说明当前交接内容。"], must_not_happen=[RULE],
    )
    route = deepcopy(source["route_gates"][1])
    route.update(id="to_work", target_node_id="work")
    route["transition_contract"].update(reason="交接后可商量是否去隔壁清点台。", accept_input=ACCEPT_OLD)
    source["route_gates"] = [source["route_gates"][0], route]
    work = deepcopy(source)
    work.update(id="work", type="scene", chapter="清点台")
    work["story_beat"].update(summary="清点台旁放着旧物，等待继续查看。", must_happen=["角色回应查看安排。"])
    work["route_gates"] = [deepcopy(story["nodes"][0]["route_gates"][0])]
    work["route_gates"][0].update(id="work_to_ending", target_node_id="ending_leave", conditions={"all": []})
    story["nodes"].insert(1, work)
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id="nonactionable-offer", catgirl_binding=_binding(),
        opening_performance={"performance": "柜台旁有两份待归位的材料。", "suggested_inputs": []})
    if pending != "none":
        outcome = runtime.prepare_turn(current, TurnRequestV2("old-offer", 0, "我们接下来去哪里？"), ())
        outcome, performance = runtime.engine.finalize_transition_offer_state(
            outcome, {"performance": OLD_OFFER, "suggested_inputs": [ACCEPT_OLD]}, new_offer=True)
        current = await runtime.commit_turn(outcome, performance)
        assert pending_transition_record(current.session, ledger_events=current.ledger_events)["revision"] == 1
    return runtime, current


def _install(monkeypatch, *, pending, body_violation=False):
    calls = {"actor": 0, "contract": []}
    performance = BODY + "\n" + OFFER
    if body_violation:
        performance = "（替你把材料归位并完成登记）已经替你全部办好了。\n" + OFFER

    async def module_options():
        return {**options.default_options(), "evaluator": True, "review": False,
                "review_contract": True, "review_delivery": True, "actor_retry": False}

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), False, transition_intent="reject" if pending == "reject" else "unclear")

    async def generate(self, **kwargs):
        calls["actor"] += 1
        assert kwargs["outcome"].route is None
        return {"performance": performance, "suggested_inputs": [ACCEPT_NEW],
                "transition_offered": True, "_transition_offer_provided": True,
                "_published_transition_offer": OFFER, "fact_candidates": []}

    async def contract(self, **kwargs):
        calls["contract"].append(deepcopy(kwargs["actor_performance"]))
        if len(calls["contract"]) == 1:
            assert kwargs["proposed_offer"]["text"] == OFFER
        diagnostics = kwargs["diagnostics"]
        diagnostics.update(offer_actionable=False, author_coverage_complete=True, player_authority_complete=True)
        if body_violation:
            diagnostics["findings"] = [{"rule": RULE, "quote": "替你把材料归位并完成登记",
                                       "candidate": {"phase": "turn", "text": performance}}]
            return (RULE,)
        if len(calls["contract"]) > 1:
            # The live failure: removing a nonactionable offer resampled a
            # previously clear author rule as uncertain, blocking the whole reply.
            diagnostics["unresolved_author_rules"] = [RULE]
            return (RULE,)
        return ()

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "克制、自然。")
    return calls, performance


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", ["none", "keep", "reject"])
async def test_nonactionable_offer_preserves_cleared_body_without_resampling(tmp_path, monkeypatch, pending):
    runtime, current = await _case(tmp_path, pending)
    calls, performance = _install(monkeypatch, pending=pending)
    before_facts = deepcopy(current.session.story_state["facts"])
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("handover", current.session.revision, "今晚有什么需要交接的？"),
        ensure_current_binding=lambda _: _binding())
    assert calls["actor"] == 1 and len(calls["contract"]) == 1
    assert calls["contract"][0]["performance"] == performance
    assert result.performance["performance"] == performance
    assert result.stored.session.current_node_id == "start"
    assert result.stored.session.story_state["facts"] == before_facts
    assert result.stored.session.revision == current.session.revision + 1
    assert result.stored.ledger_events[-1].get("program_invitation") is None
    assert result.stored.ledger_events[-1].get("fact_operations", []) == []
    assert result.performance.get("transition_offer_presented", False) is False
    assert ACCEPT_NEW not in result.performance["suggested_inputs"]
    keep_old = pending == "keep"
    assert result.stored.session.transition_offered is keep_old
    assert result.performance["transition_offered"] is keep_old
    invitation = pending_transition_record(result.stored.session, ledger_events=result.stored.ledger_events)
    if keep_old:
        assert invitation is not None and invitation["revision"] == 1
        assert OLD_OFFER in str(invitation) and OFFER not in str(invitation)
    else:
        assert invitation is None
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
async def test_body_violation_is_not_reclassified_as_only_an_invalid_offer(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path, "none")
    calls, _ = _install(monkeypatch, pending="none", body_violation=True)
    before = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(wf.NumericV2ActorOutputError, match="contract_violation"):
        await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("bad-body", current.session.revision, "今晚有什么需要交接的？"),
            ensure_current_binding=lambda _: _binding())
    assert 1 <= calls["actor"] <= 2 and len(calls["contract"]) == 1
    assert runtime.store._path(current.session.session_id).read_bytes() == before
