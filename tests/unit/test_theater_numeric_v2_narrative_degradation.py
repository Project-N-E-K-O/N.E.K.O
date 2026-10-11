"""Auxiliary proposal failures preserve reviewed delivery and explicit ledger gaps."""

import asyncio
from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_evaluator as evaluator, numeric_v2_workflow as workflow
from services.theater.numeric_v2_narrative import (
    RECORD_SCHEMA, narrative_body_fingerprint, project_narrative_state,
)
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_narrative_proposer import _client, _event
from tests.unit.test_theater_numeric_v2_natural_ending import _engine as _ending_engine
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


# The orchestration fixture replaces this method at test setup; these cases
# deliberately retain its real parsing and evidence checks with a fake SDK.
_NATIVE_PROPOSE = workflow.NumericV2NarrativeProposer.propose


@pytest.fixture(autouse=True)
def _offline_token_counter(monkeypatch):
    monkeypatch.setattr("utils.tokenize._get_encoder", lambda encoding: None)


async def _start_with_known_item(tmp_path, session_id):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    event = _event(
        "我先保管木匣。", [{"entity": "item:木匣", "attribute": "holder", "value": "character"}],
        phase="opening", source="performance", subject="character",
    )
    current = await runtime.start_session(
        session_id=session_id, catgirl_binding=_binding(),
        opening_performance={
            "performance": "我先保管木匣。", "suggested_inputs": [],
            "narrative_record": {"schema": RECORD_SCHEMA, "coverage": "confirmed_changes",
                                 "events": [event], "proposal_count": 1},
        },
    )
    return runtime, current


def _parsed_review(candidate, message, *, formal=False):
    enabled, events = evaluator._narrative_review_events(candidate, message)
    payload = {"offer_present": False, "valid": False, "body_violations": [],
               "unsafe_suggestion_indexes": [], "failure_reason": ""}
    if enabled:
        payload.update(approved_narrative_event_indexes=list(range(len(events))),
                       narrative_review_complete=True)
    if formal:
        payload["delivery_matches_route"] = True
    return evaluator._parse_transition_judge_output(
        json.dumps(payload), narrative_review=enabled, narrative_events=events,
        narrative_body=narrative_body_fingerprint(candidate) if enabled else "",
        transition_delivery_review=formal,
    )


def _install_modules(monkeypatch, instance, generate, review):
    async def options():
        return {"evaluator": True, "review": True}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    monkeypatch.setattr(instance, "propose", _NATIVE_PROPOSE.__get__(instance))
    monkeypatch.setattr(workflow, "NumericV2NarrativeProposer", lambda config_manager: instance)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_proposal", ["previous_body_quote", "invalid_character_reference"])
async def test_real_bad_proposal_delivers_gap_and_next_turn_resumes_with_history(
    tmp_path, monkeypatch, bad_proposal,
):
    runtime, current = await _start_with_known_item(tmp_path, bad_proposal)
    first_body, second_body = "我会在这里等你。", "好，你拿稳木匣。"
    if bad_proposal == "previous_body_quote":
        event = _event(
            current.session.opening_performance["performance"],
            [{"entity": "item:木匣", "attribute": "holder", "value": "character"}],
            phase="turn", source="performance", subject="character",
        )
    else:
        event = _event(
            first_body, [{"entity": "character:猫耳少女", "attribute": "commitment", "value": "会在这里等你"}],
            phase="turn", source="performance", subject="character",
        )
    payload = {"narrative_events": [event]}
    instance, seen = _client(monkeypatch, payload)
    actors, reviews = [], []

    async def generate(self, **kwargs):
        actors.append(deepcopy(kwargs["session"]))
        return {"performance": first_body if len(actors) == 1 else second_body,
                "suggested_inputs": ["先等一下。"]}

    async def review(self, **kwargs):
        candidate = kwargs["actor_performance"]
        reviews.append(deepcopy(candidate))
        return _parsed_review(candidate, kwargs["message"])

    _install_modules(monkeypatch, instance, generate, review)
    first = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("wait", 0, "先等一下。"), ensure_current_binding=lambda _: _binding(),
    )
    assert len(seen["requests"]) == len(reviews) == len(actors) == 1
    assert reviews[0]["performance"] == first_body
    assert reviews[0]["suggested_inputs"] == ["先等一下。"]
    assert "narrative_events" not in reviews[0]
    assert first.performance["performance"] == first_body
    assert first.stored.session.revision == 1
    assert first.stored.session.status == "active"
    assert first.diagnostics["narrative_proposal_failures"] == ["numeric_v2_actor_narrative_output_invalid"]
    record = first.stored.ledger_events[-1]["narrative_record"]
    assert record == first.stored.session.performance_history[-1]["narrative_record"]
    assert record == {"schema": RECORD_SCHEMA, "coverage": "unrecorded", "events": [], "proposal_count": 0}
    projection = project_narrative_state(first.stored.session)
    assert projection["current"] == {}
    assert projection["last_known"]["item:木匣"]["holder"]["value"] == "character"
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session(bad_proposal)
    assert restored == first.stored

    message = "（接过木匣）我来拿。"
    payload["narrative_events"] = [_event(
        "接过木匣", [{"entity": "item:木匣", "attribute": "holder", "value": "player"}],
    )]
    second = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=restored,
        turn=TurnRequestV2("take", 1, message), ensure_current_binding=lambda _: _binding(),
    )
    assert len(seen["requests"]) == len(reviews) == len(actors) == 2
    assert actors[1].performance_history[-1]["performance"] == first_body
    assert actors[1].performance_history[-1]["input_text"] == "先等一下。"
    assert project_narrative_state(actors[1]) == projection
    assert second.stored.session.revision == 2 and second.stored.session.status == "active"
    assert second.diagnostics["narrative_proposal_failures"] == []
    assert second.stored.ledger_events[-1]["narrative_record"]["coverage"] == "confirmed_changes"
    assert project_narrative_state(second.stored.session)["current"]["item:木匣"]["holder"]["value"] == "player"
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(bad_proposal) == second.stored


@pytest.mark.asyncio
async def test_formal_transition_reviews_and_commits_all_segments_after_auxiliary_failure(tmp_path, monkeypatch):
    engine = _ending_engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="formal_gap", catgirl_binding=_binding(),
        opening_performance={"performance": "我先保管木匣。", "suggested_inputs": []},
    )
    event = _event(
        "我先保管木匣。", [{"entity": "item:木匣", "attribute": "holder", "value": "character"}],
        phase="source_response", source="performance", subject="character",
    )
    instance, seen = _client(monkeypatch, {"narrative_events": [event]})
    candidates, reviews = [], []

    async def generate(self, **kwargs):
        candidate = engine.finalize_transition_performance(kwargs["outcome"], _candidate(), target_opening="旧开场。")
        candidates.append(deepcopy(candidate))
        return candidate

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), True, natural_ending_ready=True)

    async def review(self, **kwargs):
        assert kwargs["route_changed"] is True
        assert kwargs["session"] == current.session
        reviews.append(deepcopy(kwargs["actor_performance"]))
        return _parsed_review(kwargs["actor_performance"], kwargs["message"], formal=True)

    _install_modules(monkeypatch, instance, generate, review)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("finish", 0, "给你毛巾。"), ensure_current_binding=lambda _: _binding(),
    )
    assert len(seen["requests"]) == len(candidates) == len(reviews) == 1
    assert "narrative_events" not in reviews[0]
    assert [segment["phase"] for segment in reviews[0]["segments"]] == [
        "source_response", "transition_bridge", "target_opening",
    ]
    assert reviews[0]["segments"] == candidates[0]["segments"] == result.performance["segments"]
    assert result.stored.session.performance_history[-1]["segments"] == candidates[0]["segments"]
    assert result.stored.session.status == "ended"
    assert result.stored.session.current_node_id == "ending_leave"
    assert result.stored.session.revision == 1
    record = result.stored.ledger_events[-1]["narrative_record"]
    assert record["coverage"] == "unrecorded" and record["events"] == []
    assert record == result.stored.session.performance_history[-1]["narrative_record"]
    assert await NumericV2Runtime(engine, tmp_path).restore_session("formal_gap") == result.stored


@pytest.mark.asyncio
async def test_auxiliary_failure_does_not_bypass_failed_review(tmp_path, monkeypatch):
    runtime, current = await _start_with_known_item(tmp_path, "review_failed")
    event = _event(
        "我先保管木匣。", [{"entity": "item:木匣", "attribute": "holder", "value": "character"}],
        phase="turn", source="performance", subject="character",
    )
    instance, seen = _client(monkeypatch, {"narrative_events": [event]})
    reviews = []

    async def generate(self, **kwargs):
        return {"performance": "我会在这里等你。", "suggested_inputs": []}

    async def review(self, **kwargs):
        reviews.append(deepcopy(kwargs["actor_performance"]))
        raise evaluator.NumericV2EvaluatorError("numeric_v2_transition_judge_timeout")

    _install_modules(monkeypatch, instance, generate, review)
    diagnostics = {}
    with pytest.raises(workflow.NumericV2ActorOutputError, match="transition_review_failed"):
        await workflow.execute_numeric_v2_turn(
            config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("wait", 0, "先等一下。"), ensure_current_binding=lambda _: _binding(),
            diagnostics_sink=diagnostics,
        )
    assert len(seen["requests"]) == len(reviews) == 1
    assert "narrative_events" not in reviews[0]
    assert diagnostics["narrative_proposal_failures"] == ["numeric_v2_actor_narrative_output_invalid"]
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session("review_failed") == current


@pytest.mark.asyncio
async def test_cancelled_proposal_still_cancels_turn_without_commit(tmp_path, monkeypatch):
    runtime, current = await _start_with_known_item(tmp_path, "cancelled")
    instance, seen = _client(monkeypatch, {"narrative_events": []}, slow="request")

    async def generate(self, **kwargs):
        return {"performance": "我会在这里等你。", "suggested_inputs": []}

    async def review(self, **kwargs):
        pytest.fail("Cancellation must not reach Review")

    _install_modules(monkeypatch, instance, generate, review)
    diagnostics = {}
    task = asyncio.create_task(workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("wait", 0, "先等一下。"), ensure_current_binding=lambda _: _binding(),
        diagnostics_sink=diagnostics,
    ))
    try:
        for _ in range(100):
            if seen["requests"]:
                break
            await asyncio.sleep(.001)
        assert len(seen["requests"]) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert seen["closed"] == 1
    assert diagnostics["narrative_proposal_failures"] == []
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session("cancelled") == current
