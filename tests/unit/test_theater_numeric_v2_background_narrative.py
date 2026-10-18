"""Reviewed delivery must not wait for ledger models or accept stale background writes."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_narrative import project_narrative_state
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_reply_latency import _setup
from tests.unit.test_theater_numeric_v2_narrative_degradation import _parsed_review
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_narrative_proposer import _client, _session, _event


@pytest.mark.asyncio
@pytest.mark.parametrize("indexes,complete", [([], False), ([0], True), ([True], True), ([1], True)])
async def test_independent_ledger_review_rejects_invalid_indexes(monkeypatch, indexes, complete):
    worker, seen = _client(monkeypatch, {
        "approved_narrative_event_indexes": indexes, "narrative_review_complete": complete,
    })
    text = "我拿起木匣。"
    candidate = {"performance": text, "narrative_events": [_event(text,
        [{"entity": "item:木匣", "attribute": "holder", "value": "character"}],
        phase="turn", subject="character", source="performance")]}
    if any(type(i) is not int or i != 0 for i in indexes):
        with pytest.raises(workflow.NumericV2ActorOutputError):
            await worker.review(performance=candidate, player_input="先等。", session=_session())
    else:
        result = await worker.review(performance=candidate, player_input="先等。", session=_session())
        assert result.approved_narrative_event_indexes == tuple(indexes)
        assert result.narrative_review_complete is complete
    assert seen["closed"] == 1


@pytest.mark.asyncio
async def test_reviewed_reply_returns_before_ledger_then_cold_restore_matches(tmp_path, monkeypatch):
    release = asyncio.Event()

    async def propose(self, **kwargs):
        await release.wait()
        return []

    async def ledger_review(self, **kwargs):
        return _parsed_review(kwargs["performance"], kwargs["player_input"])

    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, "review", ledger_review, raising=False)
    runtime, current, execute = await _setup(tmp_path, monkeypatch, propose=propose)
    result = await asyncio.wait_for(execute(background_narrative=True), .3)
    assert result.performance["performance"] == "我会在这里等你。"
    assert result.stored.ledger_events[-1].get("narrative_pending")
    assert project_narrative_state(result.stored.session)["current"] == {}
    release.set()
    from services.theater.numeric_v2_background_narrative import settle_before_input
    settled = await settle_before_input(runtime, result.stored)
    assert "narrative_pending" not in settled.ledger_events[-1]
    assert settled.ledger_events[-1]["narrative_record"]["coverage"] == "confirmed_no_change"
    assert settled.session.performance_history[-1]["performance"] == result.performance["performance"]
    assert await runtime.restore_session(current.session.session_id) == settled


@pytest.mark.asyncio
async def test_fast_next_input_cancels_slow_ledger_and_preserves_history(tmp_path, monkeypatch):
    cancelled = asyncio.Event()

    async def propose(self, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    runtime, _, execute = await _setup(tmp_path, monkeypatch, propose=propose)
    result = await execute(background_narrative=True)
    from services.theater import numeric_v2_background_narrative as background
    monkeypatch.setattr(background, "NEXT_INPUT_WAIT_SECONDS", .01)
    await asyncio.sleep(0)
    settled = await asyncio.wait_for(background.settle_before_input(runtime, result.stored), .3)
    assert cancelled.is_set()
    assert "narrative_pending" not in settled.ledger_events[-1]
    assert settled.ledger_events[-1]["narrative_record"]["coverage"] == "unrecorded"
    assert project_narrative_state(settled.session)["current"] == {}
    assert settled.session.performance_history[-1]["performance"] == "我会在这里等你。"
    assert await runtime.restore_session(settled.session.session_id) == settled


@pytest.mark.asyncio
async def test_failed_body_review_never_schedules_ledger_or_commits(tmp_path, monkeypatch):
    async def propose(self, **kwargs):
        raise AssertionError("Rejected body must never reach background work")

    async def review(self, **kwargs):
        raise workflow.NumericV2EvaluatorError("numeric_v2_transition_judge_timeout")

    runtime, current, execute = await _setup(tmp_path, monkeypatch, propose=propose, review=review)
    with pytest.raises(workflow.NumericV2ActorOutputError):
        await execute(background_narrative=True)
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["end", "forget", "delete"])
async def test_stale_background_result_cannot_cross_lifecycle_or_forget(tmp_path, monkeypatch, mutation):
    release = asyncio.Event()

    async def propose(self, **kwargs):
        await release.wait()
        return []

    runtime, _, execute = await _setup(tmp_path, monkeypatch, propose=propose)
    result = await execute(background_narrative=True)
    from services.theater import numeric_v2_background_narrative as background
    task = background._TASKS[background._key(runtime, result.stored)]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    session = result.stored.session
    if mutation == "end":
        changed = await runtime.end_session(session.session_id, base_revision=1,
            base_lifecycle_revision=session.lifecycle_revision, reason="user_exit")
    elif mutation == "forget":
        changed = await runtime.store.forget_history_through_current_revision(session.session_id,
            through_revision=1)
    else:
        runtime.store._path(session.session_id).unlink()
        changed = None
    record = {"schema": "numeric_v2_narrative_record_v2", "coverage": "confirmed_no_change",
              "events": [], "proposal_count": 0}
    assert await runtime.store.settle_narrative(result.stored, record) is False
    assert await runtime.restore_session(session.session_id) == changed


@pytest.mark.asyncio
async def test_restart_seals_pending_gap_without_models_or_duplicate_settlement(tmp_path, monkeypatch):
    async def propose(self, **kwargs):
        await asyncio.sleep(10)

    runtime, _, execute = await _setup(tmp_path, monkeypatch, propose=propose)
    result = await execute(background_narrative=True)
    from services.theater import numeric_v2_background_narrative as background
    task = background._TASKS[background._key(runtime, result.stored)]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    restarted = type(runtime)(runtime.engine, tmp_path)
    restored = await restarted.restore_session(result.stored.session.session_id)
    sealed = await background.settle_before_input(restarted, restored)
    assert "narrative_pending" not in sealed.ledger_events[-1]
    assert sealed.ledger_events[-1]["narrative_record"]["coverage"] == "unrecorded"
    assert await restarted.store.settle_narrative(restored, None) is False
    assert await restarted.restore_session(restored.session.session_id) == sealed


@pytest.mark.asyncio
async def test_pending_marker_must_match_tail_body(tmp_path, monkeypatch):
    async def propose(self, **kwargs):
        await asyncio.sleep(10)

    runtime, _, execute = await _setup(tmp_path, monkeypatch, propose=propose)
    result = await execute(background_narrative=True)
    from services.theater import numeric_v2_background_narrative as background
    task = background._TASKS[background._key(runtime, result.stored)]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    path = runtime.store._path(result.stored.session.session_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["ledger_events"][-1]["narrative_pending"]["body"] = "wrong-body"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="numeric_ledger_replay_invalid"):
        await runtime.restore_session(result.stored.session.session_id)


@pytest.mark.asyncio
async def test_next_turn_reads_settled_ledger_and_raw_delivered_history(tmp_path, monkeypatch):
    async def propose(self, **kwargs):
        return [_event("我会在这里等你。", [
            {"entity": "character", "attribute": "commitment", "value": "在这里等你"}],
            phase="turn", source="performance", subject="character")]

    async def review(self, **kwargs):
        return _parsed_review(kwargs["performance"], kwargs["player_input"])

    runtime, _, execute = await _setup(tmp_path, monkeypatch, propose=propose)
    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, "review", review)
    first = await execute(background_narrative=True)

    async def second_generate(self, **kwargs):
        session = kwargs["session"]
        assert session.narrative_state["entities"]["character"]["commitment"]["value"] == "在这里等你"
        assert session.narrative_state["entities"]["character"]["commitment"]["current"] is True
        assert session.performance_history[-1]["performance"] == "我会在这里等你。"
        return {"performance": "好，我听着。", "suggested_inputs": []}

    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", second_generate)
    second = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime,
        current=first.stored, turn=TurnRequestV2("second", 1, "我有话说。"),
        ensure_current_binding=lambda _: _binding(), background_narrative=True)
    assert second.stored.session.revision == 2
    from services.theater.numeric_v2_background_narrative import settle_before_input
    settled = await settle_before_input(runtime, second.stored)
    assert await runtime.restore_session(settled.session.session_id) == settled


@pytest.mark.asyncio
async def test_background_failure_closes_gap_without_waiting_for_another_input(tmp_path, monkeypatch):
    async def propose(self, **kwargs):
        raise workflow.NumericV2ActorError("numeric_v2_actor_narrative_model_call_failed")

    runtime, _, execute = await _setup(tmp_path, monkeypatch, propose=propose)
    result = await execute(background_narrative=True)
    from services.theater import numeric_v2_background_narrative as background
    task = background._TASKS[background._key(runtime, result.stored)]
    await task
    restored = await runtime.restore_session(result.stored.session.session_id)
    assert "narrative_pending" not in restored.ledger_events[-1]
    assert restored.ledger_events[-1]["narrative_record"]["coverage"] == "unrecorded"
    assert restored.session.performance_history[-1]["performance"] == result.performance["performance"]


@pytest.mark.asyncio
async def test_same_revision_and_body_cannot_accept_another_job_result(tmp_path, monkeypatch):
    async def propose(self, **kwargs):
        await asyncio.sleep(10)

    runtime, _, execute = await _setup(tmp_path, monkeypatch, propose=propose)
    result = await execute(background_narrative=True)
    from services.theater import numeric_v2_background_narrative as background
    task = background._TASKS[background._key(runtime, result.stored)]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    events = deepcopy(result.stored.ledger_events)
    assert events[-1]["narrative_pending"].get("job_id")
    events[-1]["narrative_pending"]["job_id"] = "a" * 32
    recreated = replace(result.stored, ledger_events=events)
    runtime.store._write(runtime.store._path(recreated.session.session_id), recreated)
    assert await runtime.store.settle_narrative(result.stored, None) is False
    assert await runtime.restore_session(recreated.session.session_id) == recreated
