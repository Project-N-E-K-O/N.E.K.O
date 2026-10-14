"""Bound auxiliary waiting and retries without delivering unreviewed drafts."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_narrative import project_narrative_state
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_narrative_degradation import (
    _parsed_review, _start_with_known_item,
)
from tests.unit.test_theater_numeric_v2_runtime import _binding


async def _setup(tmp_path, monkeypatch, *, propose, generate=None, review=None):
    runtime, current = await _start_with_known_item(tmp_path, "latency")

    async def options():
        return {"evaluator": True, "review": True, "actor_retry": True}

    async def evaluate(self, **kwargs):
        return workflow.NumericV2EvaluationResult((), False)

    async def default_generate(self, **kwargs):
        return {"performance": "我会在这里等你。", "suggested_inputs": []}

    async def default_review(self, **kwargs):
        return _parsed_review(kwargs["actor_performance"], kwargs["message"])

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review or default_review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate or default_generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, "propose", propose)

    async def execute(**kwargs):
        return await workflow.execute_numeric_v2_turn(
            config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("wait", 0, "先等一下。"),
            ensure_current_binding=lambda _: _binding(), **kwargs,
        )

    return runtime, current, execute


@pytest.mark.asyncio
async def test_slow_auxiliary_is_cancelled_then_reviewed_and_committed_with_gap(tmp_path, monkeypatch):
    cancelled = asyncio.Event()

    async def propose(self, **kwargs):
        try:
            await asyncio.sleep(.2)
            return []
        finally:
            cancelled.set()

    monkeypatch.setattr(workflow, "NUMERIC_V2_NARRATIVE_BUDGET_SECONDS", .01, raising=False)
    runtime, current, execute = await _setup(tmp_path, monkeypatch, propose=propose)
    result = await execute()
    assert cancelled.is_set()
    assert result.stored.session.revision == 1
    assert result.stored.ledger_events[-1]["narrative_record"]["coverage"] == "unrecorded"
    assert project_narrative_state(result.stored.session)["current"] == {}
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
async def test_narrative_budget_is_shared_by_rewritten_candidates(tmp_path, monkeypatch):
    bodies = iter(["这一稿要改。", "这一稿已修好。"])

    async def generate(self, **kwargs):
        return {"performance": next(bodies), "suggested_inputs": []}

    async def propose(self, **kwargs):
        await asyncio.sleep(.04)
        return []

    async def review(self, **kwargs):
        result = _parsed_review(kwargs["actor_performance"], kwargs["message"])
        if kwargs["actor_performance"]["performance"] == "这一稿要改。":
            result = replace(result, body_violations=("author_boundary",), failure_reason="改稿。")
        return result

    monkeypatch.setattr(workflow, "NUMERIC_V2_NARRATIVE_BUDGET_SECONDS", .06, raising=False)
    _, _, execute = await _setup(tmp_path, monkeypatch, propose=propose, generate=generate, review=review)
    result = await execute()
    assert result.performance["performance"] == "这一稿已修好。"
    assert result.stored.ledger_events[-1]["narrative_record"]["coverage"] == "unrecorded"
    assert result.diagnostics["transition_judge_calls"] == 2


@pytest.mark.asyncio
async def test_total_generation_deadline_cancels_slow_actor_and_preserves_session(tmp_path, monkeypatch):
    cancelled = asyncio.Event()

    async def generate(self, **kwargs):
        try:
            await asyncio.sleep(.2)
            return {"performance": "迟到的正文。", "suggested_inputs": []}
        finally:
            cancelled.set()

    async def propose(self, **kwargs):
        return []

    monkeypatch.setattr(workflow, "NUMERIC_V2_TURN_MODEL_BUDGET_SECONDS", .04, raising=False)
    monkeypatch.setattr(workflow, "NUMERIC_V2_TURN_REVIEW_RESERVE_SECONDS", .01, raising=False)
    runtime, current, execute = await _setup(tmp_path, monkeypatch, propose=propose, generate=generate)
    with pytest.raises(workflow.NumericV2ActorError, match="actor_timeout"):
        await execute()
    assert cancelled.is_set()
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_total_budget_never_approves_an_unreviewed_candidate(tmp_path, monkeypatch):
    async def propose(self, **kwargs):
        return []

    async def review(self, **kwargs):
        raise workflow.NumericV2EvaluatorError("numeric_v2_transition_judge_timeout")

    runtime, current, execute = await _setup(tmp_path, monkeypatch, propose=propose, review=review)
    with pytest.raises(workflow.NumericV2ActorOutputError, match="transition_review_failed"):
        await execute()
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_slow_generation_reserves_final_review_and_skips_auxiliary(tmp_path, monkeypatch):
    offset = 0
    real_clock = time.monotonic
    monkeypatch.setattr(workflow, "time", SimpleNamespace(monotonic=lambda: real_clock() + offset))

    async def generate(self, **kwargs):
        nonlocal offset
        offset = 52
        return {"performance": "我会在这里等你。", "suggested_inputs": []}

    async def propose(self, **kwargs):
        raise AssertionError("复核预留时间不能被辅助整理占用")

    async def review(self, **kwargs):
        assert 0 < kwargs["timeout_seconds"] < 8
        assert "narrative_events" not in kwargs["actor_performance"]
        return _parsed_review(kwargs["actor_performance"], kwargs["message"])

    runtime, _, execute = await _setup(tmp_path, monkeypatch, propose=propose, generate=generate, review=review)
    result = await execute()
    assert result.diagnostics["narrative_model_attempts"] == 0
    assert result.stored.ledger_events[-1]["narrative_record"]["coverage"] == "unrecorded"
    assert await runtime.restore_session("latency") == result.stored


@pytest.mark.asyncio
async def test_caller_cancellation_is_not_swallowed_as_auxiliary_timeout(tmp_path, monkeypatch):
    entered = asyncio.Event()

    async def propose(self, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    runtime, current, execute = await _setup(tmp_path, monkeypatch, propose=propose)
    task = asyncio.create_task(execute())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await runtime.restore_session("latency") == current


@pytest.mark.asyncio
async def test_expired_turn_does_not_start_optional_terminal_model_check(tmp_path, monkeypatch):
    from services.theater.numeric_v2_runtime import NumericV2Runtime
    from tests.unit.test_theater_numeric_v2_natural_ending import _engine
    from tests.unit.test_theater_numeric_v2_runtime import _opening
    from tests.unit.test_theater_numeric_v2_transition_history import _candidate

    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id="terminal_deadline", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    offset = 0
    real_clock = time.monotonic
    monkeypatch.setattr(workflow, "time", SimpleNamespace(monotonic=lambda: real_clock() + offset))

    async def options():
        return {"evaluator": True, "review": True}

    async def evaluate(self, **kwargs):
        return workflow.NumericV2EvaluationResult((), True, natural_ending_ready=True)

    async def generate(self, **kwargs):
        nonlocal offset
        offset = 59.6
        return {**_candidate(), "target_performance": "要不要再等一下？"}

    async def forbidden(self, **kwargs):
        raise AssertionError("总等待额度用完后不能再开始窄模型核对")

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "verify_contract_boundaries", forbidden)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    with pytest.raises(workflow.NumericV2ActorOutputError, match="transition_review_failed"):
        await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("finish", 0, "给你毛巾。"), ensure_current_binding=lambda _: _binding())
    assert await runtime.restore_session("terminal_deadline") == current
