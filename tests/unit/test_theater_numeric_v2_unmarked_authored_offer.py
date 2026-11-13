"""A visible author invitation remains answerable after a lost offer marker."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult, NumericV2StoryEvidenceReview, _parse_output
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_authored_invitation_recovery import _stay_case, OFFER, ACCEPT
from tests.unit.test_theater_numeric_v2_runtime import _binding


async def _published(tmp_path, *, complete=True, body=None):
    runtime, current = await _stay_case(tmp_path)
    outcome = runtime.prepare_turn(current, TurnRequestV2("publish", current.session.revision, "接下来呢？"), (),
                                   scene_complete=complete)
    current = await runtime.commit_turn(outcome, {
        "performance": body or "设备安排已确认。\n" + OFFER, "suggested_inputs": [],
    })
    assert not current.session.transition_offered
    return runtime, current


def _install(monkeypatch, runtime, *, intent="accept", decision="accept", recorded=False):
    calls = {"evaluator": 0, "actor": 0, "contract": 0, "offer": 0}

    async def module_options():
        return {**options.default_options(), "evaluator": True, "review": False,
                "review_contract": True, "review_delivery": True}

    async def evaluate(self, **kwargs):
        calls["evaluator"] += 1
        if recorded:
            result = _parse_output(json.dumps({
                "history_query": "", "public_destination_index": -1, "exit_matches": True,
                "exit_comparison": "玩家接受已经公开的下一阶段安排。", "scene_complete": True,
                "transition_intent": "accept", "transition_reply_target": "unclear",
                "metric_changes": {}, "fact_candidates": [],
            }), runtime.engine, kwargs["message"], kwargs["session"])
            assert result.transition_intent == "unclear" and result.needs_offer_review
            return result
        return NumericV2EvaluationResult((), True, transition_intent=intent)

    async def evidence(self, **kwargs):
        calls["offer"] += 1
        assert OFFER in kwargs["pending_offer"]
        return NumericV2StoryEvidenceReview(offer_decision=decision, offer_quote=kwargs["message"])

    async def actor(self, **kwargs):
        calls["actor"] += 1
        outcome = kwargs["outcome"]
        if outcome.route is None:
            return {"performance": "我在这里等你。", "suggested_inputs": []}
        return runtime.engine.finalize_transition_performance(outcome, {
            "source_performance": "好，一起过去。", "bridge_scene_narration": "两人走到工作台另一侧。",
            "target_scene_narration": "", "target_performance": "", "suggested_inputs": [],
        }, target_opening="", bridge_required=True, target_dialogue_policy=outcome.session.dialogue_policy)

    async def contract(self, **kwargs):
        calls["contract"] += 1
        return ()

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", evidence)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", actor)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "克制、自然。")
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded", [False, True])
async def test_public_unmarked_invitation_is_reviewed_and_crosses_once(tmp_path, monkeypatch, recorded):
    runtime, current = await _published(tmp_path)
    calls = _install(monkeypatch, runtime, recorded=recorded)
    facts = deepcopy(current.session.story_state["facts"])
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("accept-public", current.session.revision, ACCEPT),
        ensure_current_binding=lambda _: _binding())
    assert result.stored.session.current_node_id == "work"
    assert all(result.stored.session.story_state["facts"][key] == value
               for key, value in facts.items() if key in runtime.engine.fact_contract)
    assert result.stored.ledger_events[-1].get("fact_operations", []) == []
    assert len([row for row in result.stored.ledger_events if row["from_node_id"] != row["to_node_id"]]) == 1
    assert calls == {"evaluator": 1, "actor": 1, "contract": 1, "offer": 1}
    assert await runtime.restore_session(current.session.session_id) == result.stored
    continued = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=result.stored,
        turn=TurnRequestV2("continue-local", result.stored.session.revision, "我先看看这里。"),
        ensure_current_binding=lambda _: _binding())
    assert continued.stored.session.current_node_id == "work"
    assert len([row for row in continued.stored.ledger_events if row["from_node_id"] != row["to_node_id"]]) == 1
    assert calls == {"evaluator": 2, "actor": 2, "contract": 1, "offer": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["unfinished", "unpublished", "refused", "deferred", "not_accepted", "review_reject",
                                   "withdrawn_later", "different_offer_later"])
async def test_missing_marker_does_not_authorize_unfinished_or_unaccepted_action(tmp_path, monkeypatch, reason):
    body = "设备安排已确认。" if reason == "unpublished" else None
    if reason in {"withdrawn_later", "different_offer_later"}:
        body = OFFER + "\n（摇头）" + ("不过通道封闭了，请先留在这里。" if reason == "withdrawn_later"
                                     else "改为先去前台签字，好吗？")
    runtime, current = await _published(tmp_path, complete=reason != "unfinished",
                                       body=body)
    if reason == "refused":
        prepared = runtime.prepare_turn(current,
            TurnRequestV2("reject", current.session.revision, "我不想过去。"), (), transition_intent="reject")
        current = await runtime.commit_turn(prepared, {"performance": "好，我们先留在这里。", "suggested_inputs": []})
    calls = _install(monkeypatch, runtime, intent="unclear" if reason == "not_accepted" else "accept",
        decision="reject" if reason == "review_reject" else "accept")
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("guard", current.session.revision,
            "先别过去，我还想留在这里。" if reason == "deferred" else ACCEPT),
        ensure_current_binding=lambda _: _binding())
    assert result.stored.session.current_node_id == "start"
    assert calls["actor"] == 1
