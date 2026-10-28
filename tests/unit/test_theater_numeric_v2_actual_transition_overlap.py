"""Compare the bridge with the authored and the actually delivered target opening, including rewrites."""

from copy import deepcopy
import json

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
    drafts = []

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False, transition_intent="initiate", )

    async def generate(self, **kwargs):
        calls["actor"] += 1
        if drafts:
            hint = kwargs["retry_hint"]
            assert json.dumps(drafts[-1], ensure_ascii=False, separators=(",", ":")) not in hint
            assert "bridge_scene_narration" in hint and "target_scene_narration" in hint
            material = kwargs['draft_repair_context']
            assert material['draft']['bridge_scene_narration'] == drafts[-1]['segments'][1]['scene_narration']
            assert material['draft']['target_scene_narration'] == drafts[-1]['segments'][2]['scene_narration']
            assert 'fixed_narrations' not in json.dumps(material, ensure_ascii=False)
            assert 'fact_candidates' not in material['draft']
        marker = second if scenario == "new_duplicate" and calls["actor"] == 2 else first
        candidate = _candidate()
        candidate["bridge_scene_narration"] = marker + "。"
        fixed = authored or (scenario == "fixed_rewrite" and calls["actor"] == 2)
        candidate["target_scene_narration"] = other if fixed else marker + "。"
        result = engine.finalize_transition_performance(kwargs["outcome"], candidate, target_opening=other)
        drafts.append(deepcopy(result))
        return result

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


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "opening_clock_calendar", "latest_ordinary", "latest_target_opening",
    "new_clock", "whole_target_clause", "noncurrent_mentions",
])
async def test_bridge_time_provenance_uses_only_latest_committed_source_narration(
    tmp_path, monkeypatch, scenario,
):
    """An already-current clock is shared context; dialogue, old visits and this draft cannot authorize it."""

    case = initiation_case()
    engine = case["engine"]
    current_clock = "墙上的挂钟显示18：30，台历标着10 月 22 日。"
    crossing = scenario in {"latest_target_opening", "noncurrent_mentions"}
    if crossing:
        # Make a real prior crossing, then take the return route for the measured turn.
        route = deepcopy(engine.nodes["start"]["route_gates"][1])
        route.update(id="return_to_desk", target_node_id="start")
        route["transition_contract"] = {
            "reason": "可以返回接待台。", "must_deliver": ["两人返回接待台"],
            "must_preserve": ["接待事务尚未开始。"], "tone": "自然",
            "bridge_scene_narration": "两人沿走廊返回接待台。",
        }
        engine.nodes["ending_leave"]["route_gates"] = [route]

    opening = {**case["session"].opening_performance,
               "scene_narration": current_clock if scenario != "latest_ordinary" else "挂钟显示17:10。"}
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="source_time", catgirl_binding=_binding(), opening_performance=opening)
    if scenario == "latest_ordinary":
        prepared = runtime.prepare_turn(current, TurnRequestV2("setup", 0, "先看看时钟。"), ())
        current = await runtime.commit_turn(prepared, {
            "scene_narration": current_clock, "performance": "我们可以慢慢商量。", "suggested_inputs": [],
        })
    elif crossing:
        prepared = runtime.prepare_turn(
            current, TurnRequestV2("setup", 0, "先去阅览室。"), (), transition_intent="initiate")
        previous = engine.finalize_transition_performance(prepared, {
            "source_performance": "刚才提到的18:30我还记得。",
            "source_scene_narration": current_clock,
            "bridge_scene_narration": "走廊的钟显示18:30。",
            "target_scene_narration": current_clock if scenario == "latest_target_opening" else "阅览室的台灯亮着。",
            "target_performance": "刚才说的是18:30，我们先聊聊。", "suggested_inputs": [],
        }, target_opening="阅览室的台灯亮着。")
        current = await runtime.commit_turn(prepared, previous)

    source_id = current.session.current_node_id
    target_id = "start" if crossing else "ending_leave"
    clock = "19:47" if scenario == "new_clock" else "18:30"
    target = f"阅览室的壁钟显示{clock}，门边的旧铜牌已经擦得锃亮。"
    bridge = f"走廊里的钟面显示{clock}，两人沿开放通道前行。"
    if scenario == "opening_clock_calendar":
        target += "台历标注10月22日。"
        bridge += "告示栏的日期是10月22日。"
    if scenario == "whole_target_clause":
        bridge = target
    engine.nodes[target_id]["story_beat"]["opening_scene"] = target
    if scenario == "noncurrent_mentions":
        # The author's plan, old source narration and current dialogue/input are not the current narrated clock.
        engine.nodes[source_id]["story_beat"]["summary"] = "作者计划在18:30离开，但当前时间尚未公开。"

    before_bytes = runtime.store._path("source_time").read_bytes()
    calls = {"actor": 0, "review": 0}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False, transition_intent="initiate")

    async def generate(self, **kwargs):
        calls["actor"] += 1
        candidate = {
            "source_performance": "那我们慢慢过去。", "bridge_scene_narration": bridge,
            "target_scene_narration": target, "target_performance": "我们在这里接着聊。",
            "suggested_inputs": [],
        }
        if scenario == "noncurrent_mentions":
            candidate["source_scene_narration"] = "挂钟显示18:30。"
        return engine.finalize_transition_performance(kwargs["outcome"], candidate, target_opening=target)

    async def review(self, **kwargs):
        calls["review"] += 1
        return evaluator.NumericV2TransitionOfferReview(
            False, False, (), (), initiation_authorized=True, delivery_matches_route=True)

    async def propose(self, **kwargs):
        return []

    async def options():
        return {"evaluator": True, "review": True}

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, "propose", propose)
    diagnostics = {}
    kwargs = dict(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("go", current.session.revision,
                           "刚才说的18:30我记住了，带路吧。" if scenario == "noncurrent_mentions" else "带路吧。"),
        ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics,
    )
    if scenario in {"opening_clock_calendar", "latest_ordinary", "latest_target_opening"}:
        result = await workflow.execute_numeric_v2_turn(**kwargs)
        assert calls == {"actor": 1, "review": 1}
        assert diagnostics["semantic_rewrite_attempts"] == 0
        assert result.performance["segments"][1]["scene_narration"] == bridge
        assert result.stored.session.current_node_id == target_id
        assert result.stored.session.revision == current.session.revision + 1
        assert await NumericV2Runtime(engine, tmp_path).restore_session("source_time") == result.stored
    else:
        with pytest.raises(workflow.NumericV2ActorOutputError, match="transition_segment_overlap"):
            await workflow.execute_numeric_v2_turn(**kwargs)
        assert calls == {"actor": 2, "review": 0}
        assert diagnostics["semantic_rewrite_attempts"] == 1
        assert diagnostics["transition_bridge_leak_markers_after_rewrite"]
        assert runtime.store._path("source_time").read_bytes() == before_bytes
        assert await NumericV2Runtime(engine, tmp_path).restore_session("source_time") == current
