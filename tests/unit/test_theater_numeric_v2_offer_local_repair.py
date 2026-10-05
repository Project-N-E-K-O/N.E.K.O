"""Delete only an isolated rejected offer; preserve the reviewed response and choices."""

from dataclasses import replace
from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow


def _review(**kwargs):
    return replace(evaluator.NumericV2TransitionOfferReview(
        offer_present=True, valid=False, body_violations=(), unsafe_suggestion_indexes=(0,),
        offer_quote="明天去另一处见面吧。",
    ), **kwargs)


def _candidate(**kwargs):
    return {"performance": "（点头）你的说明我听到了。（停顿）明天去另一处见面吧。",
            "suggested_inputs": ["（安静地等候）", "（询问现状）现在如何？"],
            "transition_offered": True, "fact_candidates": [], **kwargs}


def test_only_the_final_complete_dialogue_block_is_removed():
    candidate = _candidate()
    original = dict(candidate)
    result = workflow._safe_drop_invalid_offer(candidate, _review())
    assert result["performance"] == "（点头）你的说明我听到了。（停顿）"
    assert result["suggested_inputs"] == candidate["suggested_inputs"]
    assert result["transition_offered"] is False
    assert "fact_candidates" not in result
    assert candidate == original


@pytest.mark.parametrize("change", [
    {"performance": "明天去另一处见面吧。"},
    {"performance": "（点头）明天去另一处见面吧。"},
    {"performance": "你的说明我听到了。明天去另一处见面吧。"},
    {"performance": "（点头）明天去另一处见面吧。（坐下）先聊聊。"},
    {"performance": "（点头）明天去另一处见面吧。（坐下）明天去另一处见面吧。"},
    {"performance": "（点头）你的说明我听到了。（停顿）明天一起去见面。"},
    {"scene_narration": "另外一段场景变化。"},
    {"segments": [{"phase": "source_response", "performance": "原文。"}]},
])
def test_partial_stale_or_dependent_output_cannot_use_local_crop(change):
    assert workflow._safe_drop_invalid_offer(_candidate(**change), _review()) is None


def test_withdrawn_suggestion_batch_does_not_force_a_body_rewrite():
    candidate, _ = workflow._drop_reported_unsafe_suggestions(_candidate(), (0,))
    repaired = workflow._safe_drop_invalid_offer(candidate, _review())
    assert repaired["suggested_inputs"] == []
    assert repaired["performance"] == "（点头）你的说明我听到了。（停顿）"
    assert not repaired["transition_offered"]


@pytest.mark.parametrize("change", [
    {"valid": True}, {"offer_present": False}, {"unsafe_suggestion_indexes": ()},
    {"body_violations": ("player_action",)}, {"missed_initiation": True},
    {"fact_candidates": ({"key": "scene:start:new", "value": True, "evidence_quote": "原文"},)},
    {"approved_evaluator_fact_indexes": (0,)},
    {"fixed_narration_triggers": ({"id": "letter", "evidence": "原文"},)},
])
def test_body_facts_authorization_and_fixed_narration_block_local_crop(change):
    assert workflow._safe_drop_invalid_offer(_candidate(), _review(**change)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix,offer", [
    ("（调整接收器）我还在听，舱体很稳定。", "明天去别的空间站见面吧。"),
    ("（收起羽毛笔）我记下了，你的信还在这里。", "明天去远处的王城见面吧。"),
])
async def test_local_offer_repair_commits_once_without_rewrite_or_fallback(
    tmp_path, monkeypatch, prefix, offer,
):
    from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
    from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
    from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening

    story = numeric_v2_story()
    story["fact_contract"] = {"facts": {"scene:start:done": {
        "value_type": "bool", "visibility": "public", "description": "当前工作已经完成。",
    }}}
    story["nodes"][0]["completion_contract"] = {"all": [{"key": "scene:start:done", "equals": True}]}
    contract = story["nodes"][0]["route_gates"][1]["transition_contract"]
    middle = deepcopy(story["nodes"][2])
    middle.update(id="middle", type="scene", min_turns=1, route_gates=[{
        "id": "middle_to_leave", "target_node_id": "ending_leave", "priority": 100,
        "conditions": {"all": []}, "transition_contract": deepcopy(contract),
    }])
    middle.pop("terminal")
    middle.pop("ending_id")
    story["nodes"].append(middle)
    story["nodes"][0]["route_gates"][1]["target_node_id"] = "middle"
    contract["fallback_offer"] = "要现在离开吗？"
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id="offer_crop", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    setup = runtime.prepare_turn(current, TurnRequestV2("finished_work", 0, "已经完成。"), (),
                                 fact_operations=({"op": "set", "key": "scene:start:done",
                                                   "value": True, "visibility": "public"},))
    current = await runtime.commit_turn(setup, {"performance": "已经完成。", "suggested_inputs": [],
                                                "transition_offered": False})
    assert runtime.engine.completion_contract_satisfied(current.session) is True
    calls = {"actor": 0, "review": 0}

    async def options():
        return {"evaluator": True, "review": True, "suggestion_fill": True}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        calls["actor"] += 1
        assert calls["actor"] == 1, "The reviewed body must not be regenerated"
        return _candidate(performance=prefix + "（停顿）" + offer,
                          suggested_inputs=["好，我们去那里。", "（安静地等候）", "（询问现状）现在如何？"])

    async def review(self, **kwargs):
        calls["review"] += 1
        assert calls["review"] == 1
        return _review(offer_quote=offer)

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(evaluator.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(evaluator.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("offer_crop_turn", current.session.revision, "我知道了。", input_source="suggestion"),
        ensure_current_binding=lambda _: _binding(),
    )
    assert calls == {"actor": 1, "review": 1}
    assert result.performance["performance"] == prefix + "（停顿）"
    assert result.performance["suggested_inputs"] == []
    assert not result.stored.session.transition_offered
    assert result.stored.session.current_node_id == current.session.current_node_id
    assert result.stored.session.metrics == current.session.metrics
    assert result.diagnostics["invalid_offer_local_crops"] == 1
    assert result.diagnostics["semantic_rewrite_attempts"] == 0
    assert result.diagnostics["completion_fallback_offer_applied"] == 0
    assert offer not in json.dumps(result.stored.session.performance_history, ensure_ascii=False)
    assert await runtime.restore_session("offer_crop") == result.stored
    fork = await runtime.fork_session_for_test("offer_crop", session_id="offer_crop_fork",
                                               through_revision=result.stored.session.revision)
    assert fork.session.story_state == result.stored.session.story_state
    assert fork.session.performance_history == result.stored.session.performance_history
