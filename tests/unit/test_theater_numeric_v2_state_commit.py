"""Rejected or unreviewed scene changes must not become the next turn's history."""

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_actor import NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import MetricChangeV2, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    "（在棚外收起仪器）测量完成了。",
    "（在舱外收起探测器）检查完成了。",
])
async def test_failure_reason_alone_does_not_bypass_independent_review(tmp_path, monkeypatch, reply):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(
        session_id="catgirl_movement", catgirl_binding=_binding(), opening_performance=_opening(),
    )
    generations, reviews = [], []

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return {"performance": reply, "suggested_inputs": [], "transition_offered": False}

    async def review(self, **kwargs):
        reviews.append(kwargs)
        disputed = kwargs.get("dispute_review", False)
        return evaluator.NumericV2TransitionOfferReview(
            False, False, () if disputed else ("player_action",), (),
            "" if disputed else "猫娘未获授权移动，替玩家完成了行动。",
        )

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda _: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("one", 0, "我在这里等你完成检查。"),
        ensure_current_binding=lambda _: _binding(),
    )
    assert len(generations) == 1
    assert len(reviews) == 2 and reviews[1]["dispute_review"] is True
    assert result.performance["performance"] == reply
    assert result.diagnostics["semantic_rewrite_attempts"] == 0
    assert await runtime.restore_session("catgirl_movement") == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    "（收起票据）我们已经抵达山顶观景台。",
    "（收起终端）我们已经抵达轨道观测站。",
])
@pytest.mark.parametrize("failure", ["timeout", "protocol", "scene_boundary", "rewrite_unreviewed"])
async def test_unreviewed_or_rejected_scene_change_keeps_entire_transaction(
    tmp_path, monkeypatch, reply, failure,
):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(
        session_id="scene_change", catgirl_binding=_binding(), opening_performance=_opening(),
    )
    generations, reviews, diagnostics = [], [], {}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (MetricChangeV2("trust", 2, "玩家兑现承诺", "我等你。"),), False,
        )

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return {"performance": reply, "suggested_inputs": [], "transition_offered": False}

    async def review(self, **kwargs):
        reviews.append(kwargs)
        if failure in {"timeout", "protocol"}:
            raise evaluator.NumericV2EvaluatorError("numeric_v2_transition_judge_" + failure)
        return evaluator.NumericV2TransitionOfferReview(
            False, False, ("scene_boundary",), (), "旁白已进入当前幕未授权的新地点。",
        )

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda _: "温和。")
    if failure == "rewrite_unreviewed":
        monkeypatch.setattr(workflow, "NUMERIC_V2_REVIEW_BUDGET_SECONDS", 0.0)
    with pytest.raises(NumericV2ActorOutputError, match="numeric_v2_transition_review_failed"):
        await workflow.execute_numeric_v2_turn(
            config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("one", 0, "我等你。"),
            ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics,
        )
    assert await runtime.restore_session("scene_change") == current
    assert len(generations) == (1 if failure in {"timeout", "protocol"} else 2)
    assert diagnostics["semantic_review_fallback"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("target_fact", [
    "山顶观景台的北侧只有一座白色风向标。",
    "轨道观测站的北侧只有一座白色信号塔。",
])
@pytest.mark.parametrize("repaired", [False, True])
async def test_final_ordinary_draft_rechecks_target_facts_with_review_disabled(
    tmp_path, monkeypatch, target_fact, repaired,
):
    engine = _engine(ordinary=True)
    engine.nodes["middle"]["story_beat"]["opening_scene"] = target_fact
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="deterministic_scene", catgirl_binding=_binding(), opening_performance=_opening(),
    )
    generations = []

    async def options():
        return {"evaluator": False, "review": False, "review_contract": False}

    async def generate(self, **kwargs):
        generations.append(kwargs)
        candidate = {"performance": "（点头）我在这里等你。", "suggested_inputs": []}
        if len(generations) == 1 or not repaired:
            candidate["scene_narration"] = target_fact
        return candidate

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda _: "温和。")
    kwargs = dict(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("one", 0, "我在这里等你。"),
        ensure_current_binding=lambda _: _binding(),
    )
    if repaired:
        result = await workflow.execute_numeric_v2_turn(**kwargs)
        assert result.stored.session.current_node_id == "start"
        assert target_fact not in str(result.stored.session.performance_history)
        assert result.diagnostics["review_skipped"] is True
    else:
        with pytest.raises(NumericV2ActorOutputError, match="numeric_v2_transition_review_failed"):
            await workflow.execute_numeric_v2_turn(**kwargs)
        assert await runtime.restore_session("deterministic_scene") == current
    assert len(generations) == 2
