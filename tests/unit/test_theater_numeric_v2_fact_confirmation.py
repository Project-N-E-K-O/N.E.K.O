"""Uncommitted fact claims cannot become history or bypass the final review."""

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import (
    MetricChangeV2, NumericV2Engine, NumericV2Runtime, TurnRequestV2,
)
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "veto", "approve", "timeout", "review_off", "global", "formal", "formal_timeout",
    "rewrite", "budget_skip", "missing_audit", "fallback", "fallback_veto",
])
async def test_evaluator_claims_wait_for_final_review_without_extra_calls(tmp_path, monkeypatch, scenario):
    story = numeric_v2_story()
    key = "prop:device" if scenario == "global" else "scene:start:activated"
    story["fact_contract"] = {"facts": {key: {
        "value_type": "bool", "visibility": "public",
        "description": "角色操作装置且装置已启动。",
    }}}
    story["nodes"][0]["completion_contract"] = {"all": [{"key": key, "equals": True}]}
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(
        session_id="fact_confirmation", catgirl_binding=_binding(), opening_performance=_opening(),
    )
    formal = scenario in {"formal", "formal_timeout"}
    if formal:
        prepared = runtime.prepare_turn(current, TurnRequestV2("offer", 0, "接下来呢？"), ())
        prepared, performance = runtime.engine.finalize_transition_offer_state(
            prepared, {"performance": "要按这个安排离开吗？", "suggested_inputs": []}, new_offer=True,
        )
        current = await runtime.commit_turn(prepared, performance)
    operation = {"op": "set", "key": key, "value": True, "visibility": "public"}
    message = "（压下开关）" if not formal else "好，就按这个安排。"
    calls = {"evaluator": 0, "actor": 0, "review": 0}

    async def options():
        return {"evaluator": True, "review": scenario != "review_off"}

    async def evaluate(self, **kwargs):
        calls["evaluator"] += 1
        return evaluator.NumericV2EvaluationResult(
            (MetricChangeV2("trust", 2, "玩家兑现承诺", message),), False,
            transition_intent="accept" if formal else "unclear",
            fact_operations=(operation,),
            fact_audit=() if scenario == "missing_audit" else ({
                "key": key, "subject": "角色", "action": "操作", "object": "装置",
                "result": "装置已启动。", "evidence": [{"source": "player_input", "quote": message}],
            },),
        )

    async def generate(self, **kwargs):
        calls["actor"] += 1
        outcome = kwargs["outcome"]
        assert (key in outcome.session.story_state["facts"]) == (scenario == "review_off")
        assert outcome.session.metrics["trust"] == current.session.metrics["trust"] + 2
        if formal:
            return runtime.engine.finalize_transition_performance(outcome, _candidate(), target_opening="下一幕。")
        return {"performance": "（守在装置旁）还要观察实际结果。", "suggested_inputs": [],
                "transition_offered": False}

    async def review(self, **kwargs):
        calls["review"] += 1
        assert key not in kwargs["session"].story_state["facts"]
        claims = kwargs.get("evaluator_fact_claims", ())
        if scenario == "missing_audit":
            assert claims == ()
        else:
            assert claims[0]["evidence"] == [{"source": "player_input", "quote": message}]
            assert claims[0]["description"] == "角色操作装置且装置已启动。"
        if scenario in {"timeout", "formal_timeout"}:
            raise evaluator.NumericV2EvaluatorError("numeric_v2_transition_judge_timeout")
        first_rewrite = scenario in {"rewrite", "budget_skip"} and calls["review"] == 1
        # 末稿兜底：两稿都被判正文违规，事实审批只看玩家原话，与正文违规分别判断。
        needs_rewrite = first_rewrite or scenario in {"fallback", "fallback_veto"}
        return evaluator.NumericV2TransitionOfferReview(
            False, False, ("author_boundary",) if needs_rewrite else (), (),
            failure_reason="当前稿违反作者边界。" if needs_rewrite else "",
            acceptance_authorized=True if formal else None,
            approved_evaluator_fact_indexes=(
                (0,) if scenario in {"approve", "global", "formal", "fallback"} or first_rewrite else ()
            ),
        )

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    if scenario == "budget_skip":
        monkeypatch.setattr(workflow, "NUMERIC_V2_REVIEW_BUDGET_SECONDS", 0)
    turn = TurnRequestV2("confirmation", current.session.revision, message)
    if scenario in {"timeout", "formal_timeout", "budget_skip"}:
        with pytest.raises(workflow.NumericV2ActorOutputError, match="transition_review_failed"):
            await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
                                                  turn=turn, ensure_current_binding=lambda _: _binding())
        assert await runtime.restore_session(current.session.session_id) == current
        return
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current, turn=turn,
        ensure_current_binding=lambda _: _binding(),
    )
    accepted = scenario in {"approve", "review_off", "global", "formal", "fallback"}
    assert result.diagnostics["semantic_review_fallback"] is (
        scenario in {"fallback", "fallback_veto"}
    )
    assert (key in result.stored.session.story_state["facts"]) == accepted
    assert result.stored.ledger_events[-1].get("fact_operations", []) == ([operation] if accepted else [])
    assert result.stored.session.metrics["trust"] == current.session.metrics["trust"] + 2
    assert await runtime.restore_session(current.session.session_id) == result.stored
    projection = result.stored.ledger_events[-1]["player_action_projection"]
    assert {row["fact_key"] for row in projection["confirmed_actions"] if "fact_key" in row} == ({key} if accepted else set())
    forked = await runtime.fork_session_for_test(
        current.session.session_id, session_id="fact_confirmation_fork",
        through_revision=result.stored.session.revision,
    )
    assert forked.ledger_events[-1]["player_action_projection"] == projection
    assert forked.session.story_state == result.stored.session.story_state
    assert calls["evaluator"] == 1
    assert calls["actor"] == (2 if scenario in {"rewrite", "budget_skip", "fallback", "fallback_veto"} else 1)
    assert calls["review"] == (
        0 if scenario == "review_off" else 2 if scenario in {"rewrite", "fallback", "fallback_veto"} else 1
    )
    if formal:
        assert result.stored.session.current_node_id != current.session.current_node_id


@pytest.mark.asyncio
@pytest.mark.parametrize("review_on,approved", [(True, False), (True, True), (False, False)])
async def test_actor_candidate_cannot_bypass_enabled_review(tmp_path, monkeypatch, review_on, approved):
    story = numeric_v2_story()
    key = "scene:start:disclosed"
    story["fact_contract"] = {"facts": {key: {
        "value_type": "bool", "visibility": "public", "description": "身份与剩余能量均已公开。",
    }}}
    story["nodes"][0]["completion_contract"] = {"all": [{"key": key, "equals": True}]}
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(
        session_id="actor_fact_confirmation", catgirl_binding=_binding(), opening_performance=_opening(),
    )
    text = "我是救援员，能量还剩两成。" if approved else "能量还剩两成。"
    candidate = {"key": key, "value": True, "evidence_quote": text}

    async def options():
        return {"evaluator": True, "review": review_on}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        return {"performance": text, "suggested_inputs": [], "fact_candidates": [candidate]}

    async def review(self, **kwargs):
        return evaluator.NumericV2TransitionOfferReview(False, False, (), (),
                                                       fact_candidates=(candidate,) if approved else ())

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("disclosure", 0, "请介绍一下情况。"), ensure_current_binding=lambda _: _binding(),
    )
    assert (key in result.stored.session.story_state["facts"]) == (approved or not review_on)
    assert "fact_candidates" not in result.performance
