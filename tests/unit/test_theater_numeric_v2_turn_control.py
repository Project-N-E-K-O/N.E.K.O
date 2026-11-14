"""Bound provider work without granting story progress or committing rejected prose."""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_workflow as wf
from services.theater.numeric_v2_turn_control import (
    NumericV2ModelCallLimitError, bounded_model_calls, invitation_checkpoint_due, safe_pause_performance,
)
from services.theater.numeric_v2_usage import invoke_with_usage, numeric_v2_usage_scope
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_authored_transition_recovery import _case, _install, KEY
from tests.unit.test_theater_numeric_v2_runtime import _binding
from services.theater.numeric_v2_performance import valid_mixed_performance_policy


@pytest.fixture(autouse=True)
def _language(monkeypatch):
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-CN")


@pytest.mark.asyncio
async def test_limit_counts_failed_calls_and_never_sends_or_charges_a_fifth():
    client = SimpleNamespace(calls=0)

    async def invoke(messages):
        client.calls += 1
        raise RuntimeError("provider_failed")

    client.ainvoke = invoke
    with numeric_v2_usage_scope() as usage, bounded_model_calls() as calls:
        for _ in range(4):
            with pytest.raises(RuntimeError, match="provider_failed"):
                await invoke_with_usage(client, [], stage="actor")
        with pytest.raises(NumericV2ModelCallLimitError):
            await invoke_with_usage(client, [], stage="contract")
    assert client.calls == len(usage) == len(calls.stages) == 4
    # The next request owns a fresh allowance.
    with bounded_model_calls() as next_calls:
        with pytest.raises(RuntimeError, match="provider_failed"):
            await invoke_with_usage(client, [], stage="actor")
    assert next_calls.stages == ["actor"]


@pytest.mark.asyncio
async def test_concurrent_requests_have_independent_allowances():
    async def run(count):
        async def invoke(messages):
            await asyncio.sleep(0)
            return SimpleNamespace(content="ok")
        with bounded_model_calls() as calls:
            for _ in range(count):
                await invoke_with_usage(SimpleNamespace(ainvoke=invoke), [], stage="actor")
            return len(calls.stages)
    assert await asyncio.gather(run(4), run(2)) == [4, 2]


@pytest.mark.asyncio
async def test_public_turn_stops_at_four_provider_calls_and_delivers_safe_current_state(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path)
    _install(monkeypatch, runtime)
    sent = []

    async def invoke(messages):
        sent.append(messages)
        return SimpleNamespace(content="uncommitted")

    async def expensive(**kwargs):
        for _ in range(5):
            await invoke_with_usage(SimpleNamespace(ainvoke=invoke), [], stage="contract")
        raise AssertionError("fifth call must not run")

    monkeypatch.setattr(wf, "_execute_numeric_v2_turn", expensive)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("limited", 0, "先看看。"), ensure_current_binding=lambda _: _binding())
    assert len(sent) == result.diagnostics["model_calls"] == 4
    assert result.diagnostics["safe_delivery_fallback"] is True
    assert result.stored.session.current_node_id == current.session.current_node_id
    assert result.stored.session.story_state["facts"] == current.session.story_state["facts"]
    assert result.stored.ledger_events[-1]["delivery_hold"] is True
    assert await runtime.restore_session(current.session.session_id) == result.stored
    fork = await runtime.fork_session_for_test(current.session.session_id, session_id="held-fork", through_revision=1)
    assert fork.session.current_node_id == current.session.current_node_id


@pytest.mark.asyncio
async def test_safe_reply_cannot_swallow_an_identity_change(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path)
    _install(monkeypatch, runtime)
    before = runtime.store._path(current.session.session_id).read_bytes()

    async def rejected(**kwargs):
        raise wf.NumericV2ActorOutputError("rejected")

    checks = []
    def identity(_):
        checks.append(True)
        if len(checks) == 2:
            raise ValueError("character_changed")
        return _binding()

    monkeypatch.setattr(wf, "_execute_numeric_v2_turn", rejected)
    with pytest.raises(ValueError, match="character_changed"):
        await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("identity", 0, "先看看。"), ensure_current_binding=identity)
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.parametrize("language", ["en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"])
@pytest.mark.parametrize("policy", ["required", "optional", "forbidden"])
def test_localized_safe_reply_respects_dialogue_policy(monkeypatch, language, policy):
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: language)
    assert valid_mixed_performance_policy(safe_pause_performance(policy), policy)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["numeric_v2_contract_check_timeout", "fixed_original"])
async def test_rejected_draft_becomes_a_safe_hold_without_its_facts_or_transition(tmp_path, monkeypatch, failure):
    runtime, current = await _case(tmp_path, flag=False)
    _install(monkeypatch, runtime, failure=failure)
    if failure != "fixed_original":
        async def rejected(self, **kwargs):
            raise wf.NumericV2ActorOutputError(failure)
        monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", rejected)
    config = SimpleNamespace(project_root=Path(__file__).resolve().parents[2])
    result = await wf.execute_numeric_v2_turn(config_manager=config, runtime=runtime, current=current,
        turn=TurnRequestV2("hold", 0, "我先看看。"), ensure_current_binding=lambda _: _binding())
    assert result.stored.session.revision == 1
    assert result.stored.session.current_node_id == current.session.current_node_id
    assert result.stored.session.story_state["facts"] == current.session.story_state["facts"]
    assert result.performance["performance"] == "接下来，你想怎么继续？"
    assert "作者原文" not in str(result.performance)
    assert result.diagnostics["safe_delivery_fallback"] is True
    assert result.stored.ledger_events[-1]["delivery_fallback"]
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("message", ["我看完了。", "现在不去，等一下。"])
async def test_budget_invitation_needs_real_completion_and_respects_deferral(tmp_path, monkeypatch, ready, message):
    runtime, current = await _case(tmp_path)
    _install(monkeypatch, runtime)
    source = runtime.engine.nodes[current.session.current_node_id]
    source["recommended_turns"] = 3
    source["route_gates"][0]["transition_contract"].pop("advance_on_completion")
    source["route_gates"][0]["transition_contract"].update(
        fallback_offer="要一起到工作台另一侧继续吗？", accept_input="好，一起到工作台另一侧。")
    session = replace(current.session, node_turn_count=3)
    if ready:
        state = {**session.story_state, "facts": {**session.story_state["facts"], KEY: {
            "value": True, "visibility": "public", "updated_revision": 0,
            "source_revision": 0, "client_turn_id": "fixture"}}}
        session = replace(session, story_state=state)
    current = replace(current, session=session)
    candidate = wf._checkpoint_invitation(runtime, current, TurnRequestV2("checkpoint", 0, message))
    assert bool(candidate) is (ready and message == "我看完了。")
    if candidate:
        outcome, performance = candidate
        assert outcome.session.current_node_id == session.current_node_id
        assert outcome.ledger_event["program_invitation"]["accept_input"] == performance["suggested_inputs"][0]
        assert outcome.session.story_state["facts"] == session.story_state["facts"]


@pytest.mark.asyncio
async def test_three_consecutive_fallbacks_request_the_same_checkpoint_and_reset_after_success(tmp_path):
    runtime, current = await _case(tmp_path)
    source = current.session.current_node_id
    failed = {"from_node_id": source, "to_node_id": source, "delivery_fallback": "review_failed"}
    assert invitation_checkpoint_due(runtime.engine, replace(current, ledger_events=(failed,) * 3)) is True
    assert invitation_checkpoint_due(runtime.engine, replace(current, ledger_events=(failed,) * 2)) is False
    success = {"from_node_id": source, "to_node_id": source}
    assert invitation_checkpoint_due(runtime.engine, replace(current, ledger_events=(*(failed,) * 3, success))) is False


@pytest.mark.asyncio
async def test_program_checkpoint_does_not_disable_full_review_conditional_delivery_gates(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path)
    monkeypatch.setattr(wf.numeric_v2_options, "MODEL_REVIEWS_ENABLED", True)
    def no_unreviewed_prepare(*args, **kwargs):
        raise AssertionError("full review keeps its conditional narration path")
    monkeypatch.setattr(runtime, "prepare_turn", no_unreviewed_prepare)
    assert wf._checkpoint_invitation(runtime, current, TurnRequestV2("full-review", 0, "继续。")) is None


@pytest.mark.asyncio
async def test_program_checkpoint_offer_restores_and_exact_acceptance_needs_no_model_intent_vote(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path)
    calls = _install(monkeypatch, runtime)
    source = runtime.engine.nodes[current.session.current_node_id]
    source["recommended_turns"] = 1
    source["route_gates"][0]["transition_contract"].pop("advance_on_completion")
    source["route_gates"][0]["transition_contract"].update(
        fallback_offer="要一起到工作台另一侧继续吗？", accept_input="好，一起到工作台另一侧。")
    ready = runtime.prepare_turn(current, TurnRequestV2("ready", 0, "我决定把设备留在这里。"), (),
        fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},))
    current = await runtime.commit_turn(ready, {"performance": "好，就按这个安排。", "suggested_inputs": []})

    async def no_intent_vote(self, **kwargs):
        raise AssertionError("an exact program choice is not another intent vote")

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", no_intent_vote)
    offered = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("offer", 1, "我看完了。"), ensure_current_binding=lambda _: _binding())
    assert calls == {"actor": 0, "contract": 0}
    restored = await runtime.restore_session(current.session.session_id)
    accept = offered.performance["suggested_inputs"][0]
    accepted = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=restored,
        turn=TurnRequestV2("accept", 2, accept, "suggestion"), ensure_current_binding=lambda _: _binding())
    assert accepted.stored.session.current_node_id == "work"
    assert accepted.diagnostics["evaluator_skip_reason"] == "program_progress_acceptance"
