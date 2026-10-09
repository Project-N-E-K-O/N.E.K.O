"""Compare the bridge with the authored and the actually delivered target opening, including rewrites."""

import pytest

from services.theater import numeric_v2_evaluator as evaluator, numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


@pytest.mark.asyncio
@pytest.mark.parametrize("genre", ["daily", "science"])
@pytest.mark.parametrize(
    "scenario",
    ["authored_duplicate", "authored_bridge_exempt", "actual_duplicate", "fixed_rewrite", "new_duplicate"])
async def test_bridge_overlap_with_authored_or_delivered_opening_rewrites_or_blocks(tmp_path, monkeypatch, genre, scenario):
    case = initiation_case()
    engine = case["engine"]
    first = "她已经收回轻触纸面的手指" if genre == "daily" else "扫描台上的指示灯已经全部熄灭"
    second = "窗旁的阅信桌上只剩展开的信纸" if genre == "daily" else "记录台旁的舷窗映出远处的星光"
    other = "外面的街道十分安静。" if genre == "daily" else "远处的行星显出轮廓。"
    # authored_duplicate: the bridge copies the author's opening even though the delivered
    # target opening differs; the author-written opening still counts as target content.
    # authored_bridge_exempt: the author's bridge contract itself carries that clause, so it is allowed.
    authored = scenario in {"authored_duplicate", "authored_bridge_exempt"}
    engine.nodes["ending_leave"]["story_beat"]["opening_scene"] = first if authored else other
    if scenario == "authored_bridge_exempt":
        engine.nodes["start"]["route_gates"][1]["transition_contract"]["bridge_scene_narration"] = first + "。"
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="actual_overlap", catgirl_binding=_binding(), opening_performance=case["session"].opening_performance)
    calls = {"actor": 0, "review": 0}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False, transition_intent="initiate", )

    async def generate(self, **kwargs):
        calls["actor"] += 1
        marker = second if scenario == "new_duplicate" and calls["actor"] == 2 else first
        candidate = _candidate()
        candidate["bridge_scene_narration"] = marker + "。"
        fixed = authored or (scenario == "fixed_rewrite" and calls["actor"] == 2)
        candidate["target_scene_narration"] = other if fixed else marker + "。"
        return engine.finalize_transition_performance(kwargs["outcome"], candidate, target_opening=other)

    async def review(self, **kwargs):
        calls["review"] += 1
        return evaluator.NumericV2TransitionOfferReview(False, False, (), (), initiation_authorized=True, delivery_matches_route=True)

    async def options():
        return {"evaluator": True, "review": True}

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    diagnostics = {}
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
                  turn=TurnRequestV2("go", 0, "带路吧。"), ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics)
    if scenario in {"authored_duplicate", "actual_duplicate", "new_duplicate"}:
        with pytest.raises(workflow.NumericV2ActorOutputError, match="transition_segment_overlap"):
            await workflow.execute_numeric_v2_turn(**kwargs)
        assert calls == {"actor": 2, "review": 0}
        assert diagnostics["transition_bridge_leak_markers_after_rewrite"] == [second if scenario == "new_duplicate" else first]
        assert await runtime.restore_session("actual_overlap") == current
    else:
        result = await workflow.execute_numeric_v2_turn(**kwargs)
        assert calls == {"actor": 1 if scenario == "authored_bridge_exempt" else 2, "review": 1}
        assert result.stored.session.revision == 1
        assert result.performance["segments"][2]["scene_narration"] == other
        assert await runtime.restore_session("actual_overlap") == result.stored
