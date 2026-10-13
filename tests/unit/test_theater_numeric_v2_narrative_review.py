"""Narrative events share Review and stay bound to its request and final body."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_narrative import RECORD_SCHEMA, project_narrative_state
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_structured_output import review_output_schema
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


def _event(*, phase="turn", source="performance", quote="我接过木匣。"):
    return {
        "phase": phase, "subject": "character", "kind": "action",
        "changes": [{"entity": "item:木匣", "attribute": "holder", "value": "character"}],
        "source": source, "quote": quote, "known_to": ["player", "character"],
        "authorization_quote": "我把木匣递给你。",
    }


def _payload(**fields):
    return {
        "offer_present": False, "valid": False, "body_violations": [],
        "unsafe_suggestion_indexes": [], "failure_reason": "", **fields,
    }


def _context():
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(
        session_id="narrative_review", catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "出口在前面。"},
    )
    return engine, session


@pytest.mark.parametrize("indexes", [None, "0", [True], [False], [-1], [1], [0, 0], [0.0], ["0"]])
def test_invalid_narrative_indexes_revoke_group_without_losing_body_verdict(indexes):
    review = evaluator._parse_transition_judge_output(
        json.dumps(_payload(
            body_violations=["author_boundary"], approved_narrative_event_indexes=indexes,
            narrative_review_complete=True,
        )), narrative_review=True, narrative_events=(_event(),),
    )
    assert review.approved_narrative_event_indexes == ()
    assert review.narrative_review_complete is False
    assert review.body_violations == ("author_boundary",)


@pytest.mark.parametrize("fields", [
    {}, {"approved_narrative_event_indexes": [0]}, {"narrative_review_complete": True},
    {"approved_narrative_event_indexes": [0], "narrative_review_complete": "true"},
    {"approved_narrative_event_indexes": [0], "narrative_review_complete": 1},
])
def test_missing_or_malformed_confirmation_remains_a_gap(fields):
    review = evaluator._parse_transition_judge_output(
        json.dumps(_payload(**fields)), narrative_review=True, narrative_events=(_event(),),
    )
    assert review.approved_narrative_event_indexes == ()
    assert review.narrative_review_complete is False


def test_partial_confirmation_keeps_only_original_approved_events_and_frozen_body():
    events = (_event(),)
    original = deepcopy(events)
    review = evaluator._parse_transition_judge_output(
        json.dumps(_payload(approved_narrative_event_indexes=[0], narrative_review_complete=False)),
        narrative_review=True, narrative_events=events, narrative_body="reviewed-body",
    )
    events[0]["changes"][0]["value"] = "player"
    assert review.approved_narrative_event_indexes == (0,)
    assert review.narrative_review_complete is False
    assert review.reviewed_narrative_events == original
    assert review.reviewed_narrative_body == "reviewed-body"


@pytest.mark.parametrize("enabled", [False, True])
def test_empty_table_is_confirmed_only_by_explicit_enabled_review(enabled):
    review = evaluator._parse_transition_judge_output(
        json.dumps(_payload(approved_narrative_event_indexes=[], narrative_review_complete=True)),
        narrative_review=enabled,
    )
    assert review.narrative_review_complete is enabled
    assert review.approved_narrative_event_indexes == ()


@pytest.mark.parametrize("enabled", [False, True])
def test_schema_requests_narrative_confirmation_only_for_enabled_drafts(enabled):
    schema = review_output_schema(narrative_events=enabled)
    for field in ("approved_narrative_event_indexes", "narrative_review_complete"):
        assert (field in schema["properties"]) is enabled
        assert (field in schema["required"]) is enabled


@pytest.mark.parametrize("phase", ["ordinary", "formal", "opening"])
@pytest.mark.asyncio
async def test_narrative_table_uses_one_existing_review_in_all_body_phases(monkeypatch, phase):
    engine, session = _context()
    event_phase = {"ordinary": "turn", "formal": "source_response", "opening": "opening"}[phase]
    event = _event(phase=event_phase)
    supplied = {"performance": event["quote"], "scene_narration": "", "suggested_inputs": [],
                "narrative_events": [event]}
    if phase == "formal":
        supplied["segments"] = [
            {"phase": "source_response", "performance": event["quote"]},
            {"phase": "transition_bridge", "scene_narration": "两人来到门口。"},
            {"phase": "target_opening", "performance": "我看着门外。", "scene_narration": "夜色安静。"},
        ]
    original = deepcopy(supplied)
    calls, requests = [], []
    projected = {"coverage": {"complete": True}, "attributes": []}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages, **kwargs):
            requests.append((messages, kwargs))
            return SimpleNamespace(content=json.dumps(_payload(
                approved_narrative_event_indexes=[0], narrative_review_complete=True,
                **({"delivery_matches_route": True} if phase == "formal" else {}),
            )))

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}

    async def factory(*args, **kwargs):
        calls.append(kwargs)
        return Client()

    def unexpected_thinking(_):
        raise AssertionError("narrative approval must not enable thinking")

    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    monkeypatch.setattr(evaluator, "focus_extra_body", unexpected_thinking)
    monkeypatch.setattr(evaluator, "project_narrative_state", lambda _, **kwargs: deepcopy(projected))
    kwargs = {}
    if phase == "formal":
        kwargs["transition_outcome"] = engine.resolve_turn(
            session, TurnRequestV2("formal_narrative", 0, "带路吧。"), (), transition_intent="initiate",
        )
    review = await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(
        engine=engine, session=session, message="我把木匣递给你。", actor_performance=supplied,
        route_changed=phase != "ordinary", **kwargs,
    )
    assert supplied == original
    assert len(calls) == len(requests) == 1
    assert calls[0]["max_completion_tokens"] == (620 if phase == "formal" else 280)
    assert calls[0]["max_retries"] == 0
    assert calls[0]["timeout"] == evaluator.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS
    assert "extra_body" not in calls[0]
    schema = requests[0][1]["response_format"]["json_schema"]["schema"]
    assert "approved_narrative_event_indexes" in schema["required"]
    messages = requests[0][0]
    data = json.loads(messages[1].content[messages[1].content.index("{"):])
    assert data["narrative_state"] == projected
    assert data["narrative_events"][0]["quote"] == event["quote"]
    assert data["narrative_events"][0]["index"] == 0
    assert "approved_narrative_event_indexes" in messages[0].content
    assert "narrative_review_complete" in messages[0].content
    assert "逐项核验 known_to" in messages[0].content
    assert "reason 和 time 只采原文明示依据" in messages[0].content
    if phase == "formal":
        assert data["candidate_segments"][0]["performance"] == event["quote"]
    assert review.approved_narrative_event_indexes == (0,)
    assert review.narrative_review_complete is True
    assert review.reviewed_narrative_events[0]["quote"] == event["quote"]
    assert review.reviewed_narrative_body == evaluator.narrative_body_fingerprint(supplied)


@pytest.mark.parametrize("supplied", [
    {"performance": "我看着你。", "suggested_inputs": []},
    {"performance": "我看着你。", "suggested_inputs": [], "narrative_events": [_event()]},
])
def test_absent_or_wrong_quote_table_does_not_offer_confirmation_permission(supplied):
    engine, session = _context()
    original = deepcopy(supplied)
    messages, _ = evaluator._build_transition_judge_messages(
        engine, session, actor_performance=supplied, player_input="我把木匣递给你。",
    )
    data = json.loads(messages[1].content[messages[1].content.index("{"):])
    assert "narrative_events" not in data
    assert "approved_narrative_event_indexes" not in messages[0].content
    assert supplied == original


def test_valid_empty_event_table_still_requests_no_change_confirmation():
    engine, session = _context()
    messages, _ = evaluator._build_transition_judge_messages(
        engine, session, actor_performance={"performance": "我看着你。", "narrative_events": []},
        player_input="继续说。",
    )
    data = json.loads(messages[1].content[messages[1].content.index("{"):])
    assert data["narrative_events"] == []
    assert "narrative_review_complete" in messages[0].content


@pytest.mark.asyncio
async def test_evaluator_actual_request_keeps_current_state_and_original_pending_offer(monkeypatch, tmp_path):
    engine, _ = _context()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="narrative_evaluator_input", catgirl_binding=_binding(),
        opening_performance={"performance": "出口在前面。", "suggested_inputs": []},
    )
    message = "你保管木匣。"
    outcome = runtime.prepare_turn(current, TurnRequestV2("offer", 0, message), ())
    body = "我抱着木匣。我们现在离开，好吗？"
    performance = {"performance": body, "suggested_inputs": ["好，我们现在离开。"]}
    outcome, performance = engine.finalize_transition_offer_state(outcome, performance, new_offer=True)
    event = _event(quote="我抱着木匣。")
    event["authorization_quote"] = message
    outcome = replace(outcome, ledger_event={**outcome.ledger_event, "narrative_record": {
        "schema": RECORD_SCHEMA, "coverage": "confirmed_changes", "events": [event], "proposal_count": 1,
    }})
    current = await runtime.commit_turn(outcome, performance)
    calls, requests = [], []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages, **kwargs):
            requests.append(messages)
            return SimpleNamespace(content=json.dumps({"scene_complete": False, "metric_changes": {}}))

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}

    async def factory(*args, **kwargs):
        calls.append(kwargs)
        return Client()

    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    await evaluator.NumericV2MetricEvaluator(object()).evaluate(
        engine=engine, session=current.session, message="先等等。", recent_ledger_events=current.ledger_events,
    )
    assert len(calls) == len(requests) == 1
    assert calls[0]["max_completion_tokens"] == evaluator.NUMERIC_V2_EVALUATOR_MAX_OUTPUT_TOKENS
    assert calls[0]["timeout"] == evaluator.NUMERIC_V2_EVALUATOR_TIMEOUT_SECONDS
    assert calls[0]["max_retries"] == 0
    assert "extra_body" not in calls[0]
    messages = requests[0]
    data = json.loads(messages[1].content[messages[1].content.index("{"):])
    assert data["narrative_state"] == project_narrative_state(current.session)
    assert data["narrative_state"]["current"]["item:木匣"]["holder"]["value"] == "character"
    assert data["pending_transition"]["visible_performance"] == body
    assert data["pending_transition"]["suggested_inputs"] == performance["suggested_inputs"]
    assert data["pending_transition"]["origin_revision"] == 1
    assert "stage_result 和 choice" in messages[0].content
    assert "唯一待确认换幕邀请合同" in messages[0].content
