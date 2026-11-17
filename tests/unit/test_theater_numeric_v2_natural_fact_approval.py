"""Natural closure verifies new completion claims while ordinary turns stay fast."""

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["approve", "reject", "timeout", "ordinary", "omitted", "partial", "optional"])
async def test_new_completion_claim_cannot_end_the_story_without_bounded_approval(tmp_path, monkeypatch, mode):
    story = numeric_v2_story()
    key = "scene:start:decision"
    description = "女主决定将记录正式保存为馆藏。"
    quote = "先保持原样，等比对完成再决定。"
    story["fact_contract"] = {"facts": {key: {
        "value_type": "bool", "visibility": "story", "description": description,
    }}}
    story["nodes"][0]["min_turns"] = 1
    story["nodes"][0]["completion_contract"] = {"all": [{"key": key, "equals": True}]}
    other = "scene:start:inspection"
    if mode == "partial":
        story["fact_contract"]["facts"][other] = {
            "value_type": "bool", "visibility": "story", "description": "记录已完成比对。",
        }
        story["nodes"][0]["completion_contract"]["all"].append({"key": other, "equals": True})
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="ending-claim", catgirl_binding=_binding(),
        opening_performance={"performance": quote, "suggested_inputs": []})
    original = runtime.store._path(current.session.session_id).read_bytes()
    calls = []

    async def evaluate(self, **kwargs):
        if mode in {"omitted", "optional"}:
            return ev.NumericV2EvaluationResult((), True, natural_ending_ready=True)
        target = other if mode == "partial" else key
        return ev.NumericV2EvaluationResult((), True, natural_ending_ready=mode != "ordinary",
            fact_operations=({"op": "set", "key": target, "value": True, "visibility": "story"},),
            fact_audit=({"key": target, "subject": "catgirl", "action": "decide", "object": "record",
                         "result": "preserved", "evidence": [{"source": "actor_performance", "quote": quote}]},))

    async def approve(self, **kwargs):
        assert key not in kwargs["session"].story_state["facts"]
        assert runtime.store._path(current.session.session_id).read_bytes() == original
        assert (kwargs["ending_route"] is None) is (mode == "ordinary")
        calls.append("evidence_review")
        assert [claim["key"] for claim in kwargs["claims"]] == (
            [] if mode in {"omitted", "optional"} else [other if mode == "partial" else key]
        )
        if mode == "timeout":
            raise ev.NumericV2EvaluatorError("numeric_v2_transition_judge_timeout")
        return ev.NumericV2StoryEvidenceReview(
            (0,) if mode in {"approve", "partial"} else (), ending_ready=mode in {"approve", "optional"},
        )

    async def generate(self, **kwargs):
        calls.append("actor")
        outcome = kwargs["outcome"]
        if outcome.session.status == "ended":
            return engine.finalize_transition_performance(outcome, {
                "source_performance": "已经说定了。", "bridge_scene_narration": "",
                "target_performance": "好。", "target_scene_narration": "灯光暗下。",
                "suggested_inputs": [],
            }, target_opening="灯光暗下。")
        return {"performance": "我们先核对，处置仍未定。", "suggested_inputs": []}

    async def contract(*args, **kwargs): return ()

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", approve)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "温和克制。")
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("reply", 0, "同意，先回去整理数据。"), ensure_current_binding=lambda _: _binding())
    assert (result.stored.session.status == "ended") is (mode in {"approve", "optional"})
    assert (key in result.stored.session.story_state["facts"]) is (mode == "approve")
    assert calls == ["evidence_review", "actor"]
    if mode == "partial":
        assert result.stored.session.story_state["facts"][other]["value"] is True
    if mode in {"reject", "timeout", "omitted", "partial"}:
        assert result.performance["performance"] == "我们先核对，处置仍未定。"
        assert not result.stored.ledger_events[-1].get("natural_ending_ready")
    restored = await NumericV2Runtime(engine, tmp_path).restore_session(current.session.session_id)
    assert restored.session.story_state == result.stored.session.story_state
    assert restored.session.status == result.stored.session.status
