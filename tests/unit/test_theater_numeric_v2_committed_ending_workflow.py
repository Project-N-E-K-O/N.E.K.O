"""Committed completion can end naturally without repeating a future-target preaudit."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_store import NumericV2StoreRevisionConflictError
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


KEY = "scene:start:inspection_done"
OTHER = "state:label_selected"


async def _case(tmp_path, monkeypatch, *, subject="旧钟", progress=False, pending_offer=False):
    story = numeric_v2_story()
    source = story["nodes"][0]
    source["recommended_turns"] = 2 if progress else 4
    source["story_beat"].update(summary=f"{subject}已完成检验，角色回应当前安排。",
        must_happen=[f"回应已完成的{subject}检验。"], must_not_happen=[], transition_goal="检验完成后自然收束。")
    source["completion_contract"] = {"all": [{"key": KEY, "equals": True}]}
    story["fact_contract"] = {"facts": {
        KEY: {"value_type": "bool", "visibility": "public", "evidence_source": "performance",
              "description": f"角色实际完成{subject}检验。"},
        OTHER: {"value_type": "string", "visibility": "public", "evidence_source": "player_input",
                "description": "玩家明确选择的标签颜色。"},
    }}
    for route in source["route_gates"]:
        route["conditions"]["all"].append({"type": "fact_equals", "key": KEY, "value": True})
    for node in story["nodes"][1:]:
        node["story_beat"].update(summary=f"{subject}的检验告一段落，房间安静下来。",
            must_happen=["回应已完成的安排。"], must_not_happen=[], transition_goal="自然收束。")
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id="committed-ending", catgirl_binding=_binding(),
        opening_performance={"performance": f"{subject}放在检验台上。", "suggested_inputs": []})
    prior = runtime.prepare_turn(current, TurnRequestV2("inspection", 0, "请检查一下。"), (),
        fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},))
    performance = {"performance": f"（完成逐项检验）{subject}的检验已完成。", "suggested_inputs": []}
    offer = "我们现在去隔壁整理工具，好吗？"
    if pending_offer:
        performance["performance"] += offer
        prior, performance = runtime.engine.finalize_transition_offer_state(prior, performance, new_offer=True)
    current = await runtime.commit_turn(prior, performance)
    if progress:
        prior = runtime.prepare_turn(current, TurnRequestV2("response", current.session.revision,
            "检验记录也请收好。"), ())
        current = await runtime.commit_turn(prior,
            {"performance": "（放好记录）记录收在台旁。", "suggested_inputs": []})
    path = runtime.store._path(current.session.session_id)
    calls = {"actor": [], "contract": [], "evidence": [], "before": path.read_bytes(), "offer": offer}

    async def module_options():
        return {**options.default_options(), "evaluator": True, "review": False, "dispute": False,
                "review_contract": True, "review_delivery": True, "actor_retry": False,
                "history_lookup": False, "suggestion_fill": False}

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), True, natural_ending_ready=True,
            needs_offer_review=pending_offer)

    async def generate(self, **kwargs):
        calls["actor"].append(deepcopy(kwargs["outcome"]))
        assert path.read_bytes() == calls["before"]
        assert kwargs["session"].story_state["facts"][KEY]["value"] is True
        outcome = kwargs["outcome"]
        if outcome.route is None:
            return {"performance": "（点头）检验已经完成，我先把工具放在台旁。", "suggested_inputs": []}
        return runtime.engine.finalize_transition_performance(outcome, {
            "source_performance": "（收起检验记录）检查已经完成，辛苦了。",
            "bridge_scene_narration": "", "target_scene_narration": f"{subject}的检验告一段落，房间安静下来。",
            "target_performance": "今天就先到这里吧。", "suggested_inputs": [],
        }, target_opening=f"{subject}的检验告一段落，房间安静下来。", bridge_required=False)

    async def contract(self, **kwargs):
        calls["contract"].append(deepcopy(kwargs))
        assert path.read_bytes() == calls["before"]
        return ()

    async def review(self, **kwargs):
        calls["evidence"].append(deepcopy(kwargs))
        # Represents the observed source_ready=true/target_ready=false result:
        # the existing combined review returns ending_ready=false.
        return ev.NumericV2StoryEvidenceReview(approved_indexes=tuple(range(len(kwargs["claims"]))),
            ending_ready=False, offer_decision="hold")

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", module_options)
    monkeypatch.setattr(wf, "_can_combine_ordinary_turn", lambda *args: False)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", review)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "自然克制。")
    return runtime, current, calls


async def _turn(runtime, current, *, message="嗯，今天辛苦了。", binding=None):
    return await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("finish", current.session.revision, message),
        ensure_current_binding=binding or (lambda _: _binding()))


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", ["旧钟", "通讯设备"])
async def test_committed_completion_stable_ending_needs_only_actual_delivery_check(tmp_path, monkeypatch, subject):
    runtime, current, calls = await _case(tmp_path, monkeypatch, subject=subject)
    result = await _turn(runtime, current)
    assert result.stored.session.status == "ended"
    assert result.stored.session.current_node_id == "ending_leave"
    assert result.stored.session.revision == current.session.revision + 1
    assert not calls["evidence"]
    assert len(calls["actor"]) == 1
    assert calls["contract"]
    assert all(call["target_node"]["id"] == "ending_leave" for call in calls["contract"])
    assert [segment["phase"] for segment in result.performance["segments"]] == [
        "source_response", "transition_bridge", "target_opening"]
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_unrelated_new_player_fact_keeps_its_independent_review(tmp_path, monkeypatch, approved):
    runtime, current, calls = await _case(tmp_path, monkeypatch)
    message = "我选择蓝色标签。"

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), True, natural_ending_ready=True,
            fact_operations=({"op": "set", "key": OTHER, "value": "蓝色", "visibility": "public"},),
            fact_audit=({"key": OTHER, "subject": "player", "action": "select", "object": "标签",
                         "result": "蓝色", "evidence": [{"source": "player_input", "quote": message}]},))

    async def review(self, **kwargs):
        calls["evidence"].append(deepcopy(kwargs))
        assert runtime.store._path(current.session.session_id).read_bytes() == calls["before"]
        assert kwargs["ending_route"] is None
        assert [claim["key"] for claim in kwargs["claims"]] == [OTHER]
        return ev.NumericV2StoryEvidenceReview(approved_indexes=(0,) if approved else (), ending_ready=False)

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", review)
    result = await _turn(runtime, current, message=message)
    assert result.stored.session.status == "ended"
    assert len(calls["evidence"]) == 1
    facts = result.stored.session.story_state["facts"]
    assert (OTHER in facts) is approved
    if approved:
        assert facts[OTHER]["value"] == "蓝色"
    assert calls["contract"][-1]["target_node"]["id"] == "ending_leave"


@pytest.mark.asyncio
async def test_due_progress_review_is_retained_without_reauditing_committed_ending(tmp_path, monkeypatch):
    runtime, current, calls = await _case(tmp_path, monkeypatch, progress=True)
    result = await _turn(runtime, current)
    assert result.stored.session.status == "ended"
    assert len(calls["evidence"]) == 1
    assert calls["evidence"][0]["review_progress"] is True
    assert calls["evidence"][0]["ending_route"] is None


@pytest.mark.asyncio
async def test_pending_offer_hold_still_suppresses_natural_ending(tmp_path, monkeypatch):
    runtime, current, calls = await _case(tmp_path, monkeypatch, pending_offer=True)
    assert current.session.transition_offered is True
    result = await _turn(runtime, current, message="我先看看工具放在哪。")
    assert result.stored.session.status == "active"
    assert result.stored.session.current_node_id == "start"
    assert len(calls["evidence"]) == 1
    assert calls["offer"] in calls["evidence"][0]["pending_offer"]
    assert calls["evidence"][0]["ending_route"] is None
    assert all(outcome.route is None for outcome in calls["actor"])
    assert result.stored.ledger_events[-1].get("natural_ending_ready", False) is False


@pytest.mark.asyncio
async def test_final_contract_rejection_cannot_commit_a_ready_ending(tmp_path, monkeypatch):
    runtime, current, calls = await _case(tmp_path, monkeypatch)

    async def contract(self, **kwargs):
        calls["contract"].append(deepcopy(kwargs))
        assert kwargs["target_node"]["id"] == "ending_leave"
        return ("最终正文违反作者边界。",)

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    with pytest.raises(wf.NumericV2ActorOutputError, match="numeric_v2_contract_violation"):
        await _turn(runtime, current)
    assert calls["contract"]
    assert not calls["evidence"]
    assert runtime.store._path(current.session.session_id).read_bytes() == calls["before"]
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_second_identity_check_prevents_ending_commit(tmp_path, monkeypatch):
    runtime, current, calls = await _case(tmp_path, monkeypatch)
    checks = []

    def binding(_):
        checks.append(True)
        if len(checks) > 1:
            assert calls["contract"][-1]["target_node"]["id"] == "ending_leave"
            raise ValueError("character_changed_requires_retry")
        return _binding()

    with pytest.raises(ValueError, match="character_changed_requires_retry"):
        await _turn(runtime, current, binding=binding)
    assert len(checks) == 2
    assert runtime.store._path(current.session.session_id).read_bytes() == calls["before"]
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_store_cas_rejects_ready_ending_without_overwriting_concurrent_turn(tmp_path, monkeypatch):
    runtime, current, calls = await _case(tmp_path, monkeypatch)
    original_commit = runtime.commit_turn
    raced = []

    async def commit(outcome, performance):
        assert calls["contract"][-1]["target_node"]["id"] == "ending_leave"
        competitor = runtime.prepare_turn(current, TurnRequestV2("concurrent", current.session.revision,
            "我再看一眼检验记录。"), ())
        stored = await original_commit(competitor,
            {"performance": "检验记录还放在台上。", "suggested_inputs": []})
        raced.append((stored, runtime.store._path(current.session.session_id).read_bytes()))
        return await original_commit(outcome, performance)

    monkeypatch.setattr(runtime, "commit_turn", commit)
    with pytest.raises(NumericV2StoreRevisionConflictError):
        await _turn(runtime, current)
    assert len(raced) == 1
    stored, committed_bytes = raced[0]
    assert stored.session.status == "active"
    assert stored.session.revision == current.session.revision + 1
    assert runtime.store._path(current.session.session_id).read_bytes() == committed_bytes
    assert await runtime.restore_session(current.session.session_id) == stored
    assert stored.ledger_events[-1]["client_turn_id"] == "concurrent"
