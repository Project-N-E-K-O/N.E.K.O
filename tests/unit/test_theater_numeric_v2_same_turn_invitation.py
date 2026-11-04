"""New public facts open the route before native control generates a reviewed invitation."""

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as wf
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_pending_offer import _commit
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for


KEY = "scene:start:done"
NPC_RESULT = "管事核对文书后盖了章，说：文书无误，可以通行。"
OFFER = "要现在一起去山径的避风棚吗？"
ACCEPT = "好，我们现在过去。"
TARGET_SUGGESTIONS = ["（环顾四周）这里能避风吗？", "（望向山径）接下来该留意什么？"]


async def _setup(tmp_path, monkeypatch, *, scenario="valid", review=False):
    story = story_for("山径的避风棚")
    source = story["nodes"][0]
    for route in source["route_gates"]:
        route["conditions"]["all"].append({"type": "fact_equals", "key": KEY, "value": True})
    story["fact_contract"]["facts"][KEY]["description"] = NPC_RESULT
    if scenario == "fixed_pending":
        from tests.unit.test_theater_numeric_v2_fixed_narration import _piece

        source["story_beat"]["fixed_narrations"] = [_piece("record", "完整核验记录。")]
        source["completion_contract"]["all"].append({"fixed_narration_id": "record"})
    if scenario == "forbidden":
        source["story_beat"]["acting_contract"] = {
            "cognition_state": "normal", "memory_state": "available",
            "self_reference_mode": "persona_allowed", "persona_scope": "full",
            "dialogue_policy": "forbidden", "allowed_behaviors": [], "forbidden_behaviors": [],
        }
    engine = NumericV2Engine.from_mapping(story)
    if scenario == "ending":
        engine.nodes["ending_leave"]["terminal"] = True
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="npc-result", catgirl_binding=_binding(),
        opening_performance={"performance": "（在柜台旁等候）", "suggested_inputs": []})
    if scenario in {"pending", "withdrawn"}:
        current = await _commit(runtime, current, OFFER, offer=True)
        if scenario == "withdrawn":
            current = await _commit(runtime, current, "好，先留在这里。", intent="reject")
    calls = {"evaluator": 0, "actor": 0, "review": 0}

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, "review": review, "actor_retry": False, "suggestion_fill": False}

    async def evaluate(self, **kwargs):
        calls["evaluator"] += 1
        return ev.NumericV2EvaluationResult((), False,
            transition_intent="reject" if scenario == "reject" else "unclear")

    async def generate(self, **kwargs):
        calls["actor"] += 1
        outcome = kwargs["outcome"]
        if kwargs.get("input_source") == "reinvite":
            assert kwargs["player_input"] == ""
            assert kwargs["allow_suggestion_fill"] is False
            assert outcome.ledger_event["from_node_id"] == outcome.ledger_event["to_node_id"]
            assert outcome.session.story_state["facts"][KEY]["value"] is True
            return {"performance": OFFER, "suggested_inputs": [], "transition_offered": True}
        if outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"]:
            candidate = {**_candidate(), "suggested_inputs": list(TARGET_SUGGESTIONS)}
            return engine.finalize_transition_performance(outcome, candidate, target_opening="山径入口。")
        # The route genuinely is unresolved when Actor begins this first result.
        if outcome.ledger_event["base_revision"] == 0:
            assert engine.preview_route("start", outcome.session.metrics,
                facts=outcome.session.story_state["facts"]) is None
        candidate = {"key": KEY, "value": scenario != "false_value", "evidence_quote": NPC_RESULT}
        if scenario == "invalid_quote":
            candidate["evidence_quote"] = "这段引文从未公开。"
        # 本轮新 NPC 结果才打开原先不可用的事实出口；首轮审批不批准尚未见过的去向。
        body = "（收起桌边的笔）" if scenario == "forbidden" else "（看了看盖好的章）这下文书齐全了。"
        return {"performance": body,
            "scene_narration": NPC_RESULT, "suggested_inputs": ["谢谢管事。"],
            "transition_offered": False, "fact_candidates": [] if scenario == "missing" else [candidate]}

    async def review_offer(self, **kwargs):
        calls["review"] += 1
        offered = OFFER in kwargs["actor_performance"].get("performance", "")
        return ev.NumericV2TransitionOfferReview(offered, offered, (), (),
            offer_quote=OFFER if offered else "", offer_kind="invitation" if offered else "",
            fact_candidates=tuple(kwargs["actor_performance"].get("fact_candidates", [])),
            approved_suggestion_texts=tuple(kwargs["actor_performance"].get("suggested_inputs", [])),
            offer_acceptance_valid=bool(offered
                and kwargs.get("require_offer_acceptance")
                and kwargs["actor_performance"].get("suggested_inputs", [])[:1] == [ACCEPT]))

    async def propose(self, **kwargs):
        return []

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review_offer)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "温和。")
    monkeypatch.setattr(wf.NumericV2NarrativeProposer, "propose", propose)
    return runtime, current, calls


async def _turn(runtime, current, key="inspect", message="请管事核验文书。", source="freeform"):
    return await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2(key, current.session.revision, message, source),
        ensure_current_binding=lambda _: _binding())


@pytest.mark.asyncio
async def test_first_public_npc_result_then_native_reinvite_survives_restore_fork(tmp_path, monkeypatch):
    runtime, current, calls = await _setup(tmp_path, monkeypatch)
    result = await _turn(runtime, current)
    assert result.stored.session.current_node_id == "start"
    assert result.stored.session.story_state["facts"][KEY]["value"] is True
    assert result.performance["scene_narration"] == NPC_RESULT
    assert OFFER not in result.performance["performance"]
    assert "program_invitation" not in result.stored.ledger_events[-1]
    assert not result.stored.session.transition_offered
    assert result.diagnostics["completion_fallback_offer_applied"] == 0
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}
    assert result.diagnostics["completion_fact_review_calls"] == 1
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session("npc-result")
    assert restored == result.stored
    offered = await _turn(runtime, restored, "reinvite", "", "reinvite")
    assert offered.performance["performance"] == OFFER
    assert offered.performance["suggested_inputs"] == []
    assert offered.stored.session.transition_offered
    assert offered.stored.session.node_turn_count == result.stored.session.node_turn_count
    assert calls == {"evaluator": 1, "actor": 2, "review": 2}
    receipt = offered.stored.ledger_events[-1]["program_invitation"]
    assert receipt["kind"] == "reviewed_proposal"
    assert receipt["performance"] == offered.performance["performance"]
    assert receipt["offer"] == OFFER and "accept_input" not in receipt
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session("npc-result")
    assert restored == offered.stored
    fork = await runtime.fork_session_for_test("npc-result", session_id="npc-fork", through_revision=2)
    assert fork.ledger_events[-1]["program_invitation"] == receipt
    assert fork.session.story_state == restored.session.story_state
    for stored, key in [(restored, "accept"), (fork, "fork-accept")]:
        accepted = await _turn(runtime, stored, key, "", "accept_invitation")
        assert accepted.stored.session.current_node_id == "ending_leave"
        assert accepted.performance["suggested_inputs"] == TARGET_SUGGESTIONS
        assert "transition_offer_invalidated" not in accepted.performance
        assert "transition_offer_invalidated" not in accepted.stored.ledger_events[-1]
        assert not accepted.stored.session.transition_offered
        assert calls["review"] == 2
        assert accepted.diagnostics["completion_fact_review_calls"] == 0
        assert await runtime.restore_session(stored.session.session_id) == accepted.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "invalid_quote", "missing", "false_value", "reject", "pending", "withdrawn", "ending", "forbidden", "fixed_pending",
])
async def test_same_turn_result_preserves_invitation_guards(tmp_path, monkeypatch, scenario):
    runtime, current, calls = await _setup(tmp_path, monkeypatch, scenario=scenario)
    result = await _turn(runtime, current)
    assert OFFER not in result.performance["performance"]
    assert "program_invitation" not in result.stored.ledger_events[-1]
    assert result.diagnostics["completion_fallback_offer_applied"] == 0
    assert result.stored.session.current_node_id == "start"
    fact_checks = int(scenario not in {"invalid_quote", "missing", "false_value"})
    assert calls == {"evaluator": 1, "actor": 1, "review": fact_checks}
    assert result.diagnostics["completion_fact_review_calls"] == fact_checks
    if scenario == "invalid_quote":
        assert KEY not in result.stored.session.story_state["facts"]
        assert result.diagnostics["fact_candidates_rejected"] == 1
    if scenario == "fixed_pending":
        assert result.stored.session.story_state["facts"][KEY]["value"] is True
        assert "fixed_narrations" not in result.performance
        assert runtime.engine.completion_contract_satisfied(result.stored.session) is False


@pytest.mark.asyncio
async def test_same_turn_program_invitation_is_not_reissued_after_expiry(tmp_path, monkeypatch):
    runtime, current, _ = await _setup(tmp_path, monkeypatch)
    offered = await _turn(runtime, current)
    offered = await _turn(runtime, offered.stored, "reinvite", "", "reinvite")
    assert offered.stored.ledger_events[-1]["program_invitation"]["offer"] == OFFER
    responses = iter(["（坐到窗边）先说说你的打算。", "（望向窗外）这阵风比刚才更急了。"])

    async def generate(self, **kwargs):
        return {"performance": next(responses), "suggested_inputs": [], "transition_offered": False}

    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    current = offered.stored
    for key, message in [("consider", "先等一下，我还有个问题。"), ("wait", "我再考虑一会儿。")]:
        result = await _turn(runtime, current, key, message)
        assert OFFER not in result.performance["performance"]
        assert not result.stored.session.transition_offered
        assert "program_invitation" not in result.stored.ledger_events[-1]
        current = result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["unclear", "reject"])
async def test_staying_after_program_offer_still_retracts_offer_and_acceptance(tmp_path, monkeypatch, intent):
    runtime, current, _ = await _setup(tmp_path, monkeypatch)
    offered = await _turn(runtime, current)
    offered = await _turn(runtime, offered.stored, "reinvite", "", "reinvite")

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False, transition_intent=intent)

    async def generate(self, **kwargs):
        assert kwargs["outcome"].ledger_event["from_node_id"] == kwargs["outcome"].ledger_event["to_node_id"]
        return {"performance": "（留在柜台旁）先把你的顾虑说清楚。",
            "suggested_inputs": [ACCEPT], "transition_offered": False}

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    result = await _turn(runtime, offered.stored, "stay", "先不出发，我还有个问题。")
    assert result.stored.session.current_node_id == "start"
    assert not result.stored.session.transition_offered
    assert result.performance["suggested_inputs"] == []
    assert result.performance["transition_offer_invalidated"] is True
    assert result.stored.ledger_events[-1]["transition_offer_invalidated"] is True


@pytest.mark.asyncio
async def test_successful_transition_preserves_suggestions_without_latching_actor_offer_flag(tmp_path, monkeypatch):
    runtime, current, _ = await _setup(tmp_path, monkeypatch)
    offered = await _turn(runtime, current)
    offered = await _turn(runtime, offered.stored, "reinvite", "", "reinvite")
    original_generate = wf.NumericV2Actor.generate_turn

    async def generate(self, **kwargs):
        return {**await original_generate(self, **kwargs), "transition_offered": True}

    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    result = await _turn(runtime, offered.stored, "accept", "", "accept_invitation")
    assert result.stored.session.current_node_id == "ending_leave"
    assert result.performance["suggested_inputs"] == TARGET_SUGGESTIONS
    assert not result.stored.session.transition_offered
    assert not result.performance["transition_offered"]
    assert "transition_offer_presented" not in result.performance
    assert "program_invitation" not in result.stored.ledger_events[-1]


@pytest.mark.asyncio
async def test_review_on_keeps_prior_completion_requirement_and_single_review(tmp_path, monkeypatch):
    runtime, current, calls = await _setup(tmp_path, monkeypatch, review=True)
    result = await _turn(runtime, current)
    assert result.stored.session.story_state["facts"][KEY]["value"] is True
    assert OFFER not in result.performance["performance"]
    assert "program_invitation" not in result.stored.ledger_events[-1]
    assert not result.stored.session.transition_offered
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}


@pytest.mark.asyncio
async def test_new_result_is_not_persisted_when_commit_fence_fails(tmp_path, monkeypatch):
    runtime, current, calls = await _setup(tmp_path, monkeypatch)
    path = runtime.store._path(current.session.session_id)
    previous_bytes = path.read_bytes()

    async def before_commit():
        raise RuntimeError("test_commit_fence")

    with pytest.raises(RuntimeError, match="test_commit_fence"):
        await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("fence", current.session.revision, "请管事核验文书。"),
            ensure_current_binding=lambda _: _binding(), before_commit=before_commit)
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}
    assert path.read_bytes() == previous_bytes
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_fact_cannot_use_unspoken_author_invitation_as_evidence(tmp_path, monkeypatch):
    runtime, current, _ = await _setup(tmp_path, monkeypatch)
    original_generate = wf.NumericV2Actor.generate_turn

    async def generate(self, **kwargs):
        performance = await original_generate(self, **kwargs)
        assert OFFER not in performance["performance"]
        performance["fact_candidates"][0]["evidence_quote"] = OFFER
        return performance

    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    result = await _turn(runtime, current)
    assert KEY not in result.stored.session.story_state["facts"]
    assert not result.stored.session.transition_offered
    assert OFFER not in result.performance["performance"]
    assert result.diagnostics["fact_candidates_rejected"] == 1
