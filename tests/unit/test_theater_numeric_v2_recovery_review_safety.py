"""Authored recovery remains an audited candidate, never approval by provenance."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_workflow as wf
from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult, NumericV2EvaluatorError, _FIXED_ORIGINAL_CAUSE_RULE
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_authored_transition_recovery import _case, _install
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_source_delivery_routing import INPUT, KEY


def _fixed_finding(performance, *, rule="作者原文与实际状态冲突。", prior=None):
    part = next(part for part in performance["segments"] if part.get("fixed_narrations"))
    text = part["fixed_narrations"][0]["text"]
    return {"rule": rule, "quote": text, "candidate": {
        "id": "c1", "type": "narration", "phase": part["phase"],
        "program_fixed_original": True, "text": text}, "prior": prior}


async def _execute(runtime, current):
    return await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("recovery-review", current.session.revision, INPUT),
        ensure_current_binding=lambda _: _binding())


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["ownership", "time", "player_action"])
async def test_generated_author_recovery_cannot_skip_actual_state_review(tmp_path, monkeypatch, conflict):
    runtime, current = await _case(tmp_path)
    texts = {"ownership": "作者原文：设备仍由角色拿在手里。", "time": "作者原文：现在已经是清晨。",
             "player_action": "作者原文：玩家已经操作终端并接受新任务。"}
    runtime.engine.nodes["work"]["story_beat"]["fixed_narrations"][0]["text"] = texts[conflict]
    calls = _install(monkeypatch, runtime, generation_failure="numeric_v2_actor_timeout")
    before = runtime.store._path(current.session.session_id).read_bytes()

    async def reject(self, **kwargs):
        calls["contract"] += 1
        finding = _fixed_finding(kwargs["actor_performance"], prior={"id": "h0", "text": "既有状态未改变。"})
        kwargs["diagnostics"]["findings"] = [finding]
        return (finding["rule"],)

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", reject)
    with pytest.raises(wf.NumericV2ActorOutputError, match="contract_violation"):
        await _execute(runtime, current)
    assert calls == {"actor": 1, "contract": 1}
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
async def test_fixed_original_finding_tries_one_program_recovery_without_actor_rewrite(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path)
    calls = _install(monkeypatch, runtime)
    before = runtime.store._path(current.session.session_id).read_bytes()

    async def reject(self, **kwargs):
        calls["contract"] += 1
        finding = _fixed_finding(kwargs["actor_performance"])
        kwargs["diagnostics"]["findings"] = [finding]
        return (finding["rule"],)

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", reject)
    with pytest.raises(wf.NumericV2ActorOutputError, match="contract_violation"):
        await _execute(runtime, current)
    assert calls == {"actor": 1, "contract": 2}
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_failed_draft_review_can_use_one_remaining_author_candidate_review(tmp_path, monkeypatch, approved):
    runtime, current = await _case(tmp_path)
    calls = _install(monkeypatch, runtime)
    before = runtime.store._path(current.session.session_id).read_bytes()

    async def review(self, **kwargs):
        calls["contract"] += 1
        if calls["contract"] == 1:
            raise NumericV2EvaluatorError("numeric_v2_contract_check_timeout")
        assert kwargs["actor_performance"]["segments"][0]["performance"] == ""
        assert calls["contract"] == 2
        if not approved:
            finding = _fixed_finding(kwargs["actor_performance"])
            kwargs["diagnostics"]["findings"] = [finding]
            return (finding["rule"],)
        return ()

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", review)
    if approved:
        result = await _execute(runtime, current)
        assert result.stored.session.current_node_id == "work"
        assert result.diagnostics["authored_transition_recoveries"] == 1
    else:
        with pytest.raises(wf.NumericV2ActorOutputError, match="contract_violation"):
            await _execute(runtime, current)
        assert runtime.store._path(current.session.session_id).read_bytes() == before
    assert calls == {"actor": 1, "contract": 2}


@pytest.mark.asyncio
async def test_recovery_review_failure_cannot_recursively_recover_or_commit(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path)
    calls = _install(monkeypatch, runtime)
    before = runtime.store._path(current.session.session_id).read_bytes()

    async def timeout(self, **kwargs):
        calls["contract"] += 1
        raise NumericV2EvaluatorError("numeric_v2_contract_check_timeout")

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", timeout)
    with pytest.raises(wf.NumericV2ActorOutputError, match="contract_check_failed"):
        await _execute(runtime, current)
    assert calls == {"actor": 1, "contract": 2}
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
async def test_same_rejected_rewrite_uses_cache_but_author_candidate_needs_its_own_review(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path)
    calls = _install(monkeypatch, runtime)
    reviewed = []

    async def review(self, **kwargs):
        calls["contract"] += 1
        candidate = deepcopy(kwargs["actor_performance"])
        reviewed.append(candidate)
        return ("动态来源内容不相容。",) if candidate["segments"][0]["performance"] else ()

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", review)
    result = await _execute(runtime, current)
    assert result.stored.session.current_node_id == "work"
    assert calls == {"actor": 2, "contract": 2}
    assert reviewed[0]["segments"][0]["performance"]
    assert reviewed[1]["segments"][0]["performance"] == ""


@pytest.mark.asyncio
async def test_fixed_cause_with_editable_current_predecessor_still_allows_actor_repair(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path)
    calls = _install(monkeypatch, runtime)
    generate = wf.NumericV2Actor.generate_turn

    async def repair(self, **kwargs):
        result = await generate(self, **kwargs)
        if calls["actor"] == 2:
            result["segments"][0]["performance"] = "准备条件已经核对完毕。"
        return result

    async def review(self, **kwargs):
        calls["contract"] += 1
        if calls["contract"] == 1:
            finding = _fixed_finding(kwargs["actor_performance"], rule=_FIXED_ORIGINAL_CAUSE_RULE,
                prior={"id": "c0", "text": "尚未完成准备。"})
            kwargs["diagnostics"]["findings"] = [finding]
            return (finding["rule"],)
        return ()

    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", repair)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", review)
    result = await _execute(runtime, current)
    assert result.stored.session.current_node_id == "work"
    assert result.performance["segments"][0]["performance"] == "准备条件已经核对完毕。"
    assert calls == {"actor": 2, "contract": 2}


@pytest.mark.asyncio
async def test_author_recovery_also_obeys_enabled_full_review(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path)
    prepared = runtime.prepare_turn(current, TurnRequestV2("choice", 0, INPUT), (),
        transition_intent="reject",
        fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},))
    current = await runtime.commit_turn(prepared, {"performance": "设备去向已确定。", "suggested_inputs": []})
    calls = _install(monkeypatch, runtime, generation_failure="numeric_v2_actor_timeout")
    reviews = []

    async def module_options():
        return {**wf.numeric_v2_options.default_options(), "evaluator": True, "review": True,
                "review_contract": True, "dispute": False}

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), True, transition_intent="initiate")

    async def review(self, **kwargs):
        reviews.append(deepcopy(kwargs["actor_performance"]))
        raise NumericV2EvaluatorError("numeric_v2_transition_judge_timeout")

    monkeypatch.setattr(wf, "aload_theater_module_options", module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    before = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(wf.NumericV2ActorOutputError, match="transition_review_failed"):
        await _execute(runtime, current)
    assert len(reviews) == 1 and calls == {"actor": 1, "contract": 0}
    assert runtime.store._path(current.session.session_id).read_bytes() == before
