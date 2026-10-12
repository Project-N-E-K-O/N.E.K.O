"""Review input overflow can defer ledger proposals without bypassing prose review."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as evaluator, numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
from services.theater.numeric_v2_narrative import narrative_body_fingerprint, narrative_event_table
from tests.unit.test_theater_numeric_v2_narrative_degradation import (
    _install_modules, _parsed_review, _start_with_known_item,
)
from tests.unit.test_theater_numeric_v2_narrative_proposer import _client, _event
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_natural_ending import _engine as _ending_engine
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


_NATIVE_REVIEW = evaluator.NumericV2MetricEvaluator.validate_transition_offer
_BUDGET_ERROR = "numeric_v2_transition_review_budget_exceeded"


@pytest.fixture(autouse=True)
def _offline_token_counter(monkeypatch):
    monkeypatch.setattr("utils.tokenize._get_encoder", lambda encoding: None)


@pytest.mark.asyncio
@pytest.mark.parametrize("proposals_nonempty", [True, False], ids=["events", "successful_empty"])
async def test_budget_overflow_defers_events_and_commits_only_after_full_review(tmp_path, monkeypatch, proposals_nonempty):
    runtime, current = await _start_with_known_item(tmp_path, "budget_gap")
    body = "我会在这里等你。"
    event = _event(body, [{"entity": "character", "attribute": "commitment", "value": "会在这里等你"}],
                   phase="turn", source="performance", subject="character")
    instance, seen = _client(monkeypatch, {"narrative_events": [event] if proposals_nonempty else []})
    candidates = []

    async def generate(self, **kwargs):
        return {"performance": body, "scene_narration": "灯还亮着。", "suggested_inputs": ["先等一下。"]}

    async def review(self, **kwargs):
        candidate = kwargs["actor_performance"]
        candidates.append(deepcopy(candidate))
        assert await runtime.restore_session("budget_gap") == current
        if "narrative_events" in candidate:
            raise evaluator.NumericV2EvaluatorError("numeric_v2_transition_review_budget_exceeded")
        return _parsed_review(candidate, kwargs["message"])

    _install_modules(monkeypatch, instance, generate, review)
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("wait", 0, "先等一下。"), ensure_current_binding=lambda _: _binding(),
    )
    assert len(seen["requests"]) == 1 and len(candidates) == 2
    assert "narrative_events" in candidates[0] and "narrative_events" not in candidates[1]
    assert {key: value for key, value in candidates[0].items() if key != "narrative_events"} == candidates[1]
    assert result.performance["performance"] == body
    assert result.performance["scene_narration"] == "灯还亮着。"
    assert result.performance["suggested_inputs"] == ["先等一下。"]
    assert result.stored.session.revision == 1
    assert result.stored.ledger_events[-1]["narrative_record"]["coverage"] == "unrecorded"
    assert result.diagnostics["narrative_proposal_failures"] == []
    assert result.diagnostics["narrative_review_budget_degrades"] == 1
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session("budget_gap") == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("formal", [False, True])
async def test_native_budget_check_sends_only_complete_degraded_draft(tmp_path, monkeypatch, formal):
    runtime, current = await _start_with_known_item(tmp_path, "native_budget")
    if formal:
        runtime = NumericV2Runtime(_ending_engine(), tmp_path)
        current = await runtime.start_session(
            session_id="native_formal", catgirl_binding=_binding(),
            opening_performance={"performance": "我先保管木匣。", "suggested_inputs": []},
        )
    message = "给你毛巾。" if formal else "先等一下。"
    body = _candidate()["source_performance"] if formal else "我会在这里等你。"
    event = _event(body, [{"entity": "character", "attribute": "condition", "value": "等候"}],
                   phase="source_response" if formal else "turn", source="performance", subject="character")
    instance, seen = _client(monkeypatch, {"narrative_events": [event]})
    attempts, requests, factories = [], [], []

    async def generate(self, **kwargs):
        if formal:
            return runtime.engine.finalize_transition_performance(kwargs["outcome"], _candidate(), target_opening="旧开场。")
        return {"performance": body, "scene_narration": "灯还亮着。", "suggested_inputs": ["先等一下。"]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages, **kwargs):
            requests.append((messages, kwargs))
            data = json.loads(messages[1].content[messages[1].content.index("{"):])
            assert "narrative_events" not in data
            payload = {"offer_present": False, "valid": False, "body_violations": [],
                       "unsafe_suggestion_indexes": [], "failure_reason": ""}
            if formal:
                payload["delivery_matches_route"] = True
            return SimpleNamespace(content=json.dumps(payload))

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "http://test.invalid"}

    async def factory(*args, **kwargs):
        factories.append(kwargs)
        return Client()

    async def review(self, **kwargs):
        candidate = kwargs["actor_performance"]
        attempts.append(deepcopy(candidate))
        if len(attempts) == 1:
            plain = deepcopy(candidate)
            plain.pop("narrative_events")
            build = {key: value for key, value in kwargs.items() if key in {
                "engine", "session", "scene_complete", "route_changed", "transition_outcome",
                "player_action_projection", "public_destination_quote", "check_missed_initiation",
            }}
            messages, _ = evaluator._build_transition_judge_messages(
                **build, actor_performance=plain, player_input=kwargs["message"],
            )
            # Separate the complete-body and auxiliary-table sizes under the
            # offline conservative counter. Fixed 8500 coverage lives in review_capacity.
            limit = sum(evaluator.count_tokens(item.content) for item in messages) + 8
            profile = NUMERIC_V2_ACTOR_BUDGET_PROFILES[kwargs["session"].actor_budget_profile]
            monkeypatch.setitem(profile, "judge_input_max_tokens", limit)
            monkeypatch.setitem(profile, "formal_judge_input_max_tokens", limit)
            try:
                return await _NATIVE_REVIEW(self, **kwargs)
            except evaluator.NumericV2EvaluatorError as exc:
                assert str(exc) == _BUDGET_ERROR and requests == factories == []
                raise
        return await _NATIVE_REVIEW(self, **kwargs)

    _install_modules(monkeypatch, instance, generate, review)
    if formal:
        async def evaluate(self, **kwargs):
            return evaluator.NumericV2EvaluationResult((), True, natural_ending_ready=True)
        monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("native", 0, message), ensure_current_binding=lambda _: _binding(),
    )
    assert len(seen["requests"]) == 1 and len(attempts) == 2
    assert len(requests) == len(factories) == 1
    assert factories[0]["max_retries"] == 0
    assert {key: value for key, value in attempts[0].items() if key != "narrative_events"} == attempts[1]
    data = json.loads(requests[0][0][1].content[requests[0][0][1].content.index("{"):])
    assert data["player_input"] == message
    if formal:
        assert [{key: value for key, value in segment.items() if key != "performer"}
                for segment in data["candidate_segments"]] == result.performance["segments"]
        assert result.stored.session.status == "ended"
    else:
        assert data["actor_performance"] == body
        assert result.performance["scene_narration"] == "灯还亮着。"
        assert result.performance["suggested_inputs"] == ["先等一下。"]
    assert result.stored.ledger_events[-1]["narrative_record"]["coverage"] == "unrecorded"


@pytest.mark.asyncio
async def test_reviewed_opening_budget_gap_retains_the_original_opening(monkeypatch):
    from services.theater.numeric_v2_runtime import NumericV2Engine
    from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story

    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["opening_only_boundaries"] = ["不得提前离开。"]
    engine = NumericV2Engine.from_mapping(story)
    original = {"performance": "我会在这里等你。", "scene_narration": "灯还亮着。", "suggested_inputs": []}
    order = []

    async def options():
        return {"review": True}

    async def opening(self, **kwargs):
        order.append("actor")
        return deepcopy(original)

    async def propose(self, **kwargs):
        order.append("narrative")
        return [_event(original["performance"], [{"entity": "character", "attribute": "condition", "value": "等候"}],
                       phase="opening", source="performance", subject="character")]

    async def review(self, **kwargs):
        order.append("review")
        candidate = kwargs["actor_performance"]
        if "narrative_events" in candidate:
            raise evaluator.NumericV2EvaluatorError(_BUDGET_ERROR)
        assert candidate == original
        return evaluator.NumericV2TransitionOfferReview(False, False, (), ())

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_opening", opening)
    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, "propose", propose)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    result = await workflow.generate_validated_opening(
        engine=engine, config_manager=object(), session_id="opening_budget",
        catgirl_binding=_binding(), actor_budget_profile="balanced",
    )
    assert order == ["actor", "narrative", "review", "review"]
    assert {key: result[key] for key in original} == original
    assert result["narrative_record"]["coverage"] == "unrecorded"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["second_budget", "timeout", "budget_without_events"])
async def test_budget_degradation_never_bypasses_an_unfinished_review(tmp_path, monkeypatch, failure):
    runtime, current = await _start_with_known_item(tmp_path, failure)
    body = "我会在这里等你。"
    event = _event(body, [{"entity": "character", "attribute": "condition", "value": "等候"}],
                   phase="turn", source="performance", subject="character")
    instance, seen = _client(monkeypatch, {"narrative_events": [event]})
    reviews = []

    async def generate(self, **kwargs):
        return {"performance": body, "suggested_inputs": []}

    async def review(self, **kwargs):
        reviews.append(deepcopy(kwargs["actor_performance"]))
        raise evaluator.NumericV2EvaluatorError(
            "numeric_v2_transition_judge_timeout" if failure == "timeout" else _BUDGET_ERROR,
        )

    _install_modules(monkeypatch, instance, generate, review)
    if failure == "budget_without_events":
        async def propose(self, **kwargs):
            raise workflow.NumericV2ActorError("numeric_v2_actor_narrative_timeout")
        monkeypatch.setattr(instance, "propose", propose.__get__(instance))
    with pytest.raises(workflow.NumericV2ActorOutputError, match="transition_review_failed"):
        await workflow.execute_numeric_v2_turn(
            config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("wait", 0, "先等一下。"), ensure_current_binding=lambda _: _binding(),
        )
    assert len(reviews) == (2 if failure == "second_budget" else 1)
    if failure == "second_budget":
        assert "narrative_events" in reviews[0] and "narrative_events" not in reviews[1]
    assert len(seen["requests"]) == (0 if failure == "budget_without_events" else 1)
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(failure) == current


@pytest.mark.asyncio
async def test_successful_empty_proposal_keeps_explicit_no_change_review(tmp_path, monkeypatch):
    runtime, current = await _start_with_known_item(tmp_path, "no_change")
    instance, seen = _client(monkeypatch, {"narrative_events": []})
    reviews = []

    async def generate(self, **kwargs):
        return {"performance": "我等你。", "suggested_inputs": []}

    async def review(self, **kwargs):
        candidate = kwargs["actor_performance"]
        reviews.append(deepcopy(candidate))
        assert candidate["narrative_events"] == []
        return _parsed_review(candidate, kwargs["message"])

    _install_modules(monkeypatch, instance, generate, review)
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("wait", 0, "先等一下。"), ensure_current_binding=lambda _: _binding(),
    )
    assert len(seen["requests"]) == len(reviews) == 1
    assert result.diagnostics["narrative_review_budget_degrades"] == 0
    assert result.stored.ledger_events[-1]["narrative_record"]["coverage"] == "confirmed_no_change"


@pytest.mark.asyncio
async def test_same_body_does_not_restore_deferred_events_or_repeat_proposer(tmp_path, monkeypatch):
    runtime, current = await _start_with_known_item(tmp_path, "same_body")
    body = "我会在这里等你。"
    event = _event(body, [{"entity": "character", "attribute": "condition", "value": "等候"}],
                   phase="turn", source="performance", subject="character")
    instance, seen = _client(monkeypatch, {"narrative_events": [event]})
    reviews, actors = [], []

    async def generate(self, **kwargs):
        actors.append(True)
        return {"performance": body, "suggested_inputs": []}

    async def review(self, **kwargs):
        candidate = kwargs["actor_performance"]
        reviews.append(deepcopy(candidate))
        if len(reviews) == 1:
            raise evaluator.NumericV2EvaluatorError(_BUDGET_ERROR)
        assert "narrative_events" not in candidate
        parsed = _parsed_review(candidate, kwargs["message"])
        return replace(parsed, body_violations=("author_boundary",), failure_reason="改写正文。") if len(reviews) == 2 else parsed

    _install_modules(monkeypatch, instance, generate, review)
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("wait", 0, "先等一下。"), ensure_current_binding=lambda _: _binding(),
    )
    assert len(seen["requests"]) == 1 and len(actors) == 2 and len(reviews) == 3
    assert result.diagnostics["narrative_review_budget_degrades"] == 1
    assert result.stored.ledger_events[-1]["narrative_record"]["coverage"] == "unrecorded"


@pytest.mark.asyncio
async def test_rewrite_budget_gap_preserves_only_previously_approved_player_events(tmp_path, monkeypatch):
    runtime, current = await _start_with_known_item(tmp_path, "preserved_player")
    message = "（接过木匣）我来拿。"
    player = _event("接过木匣", [{"entity": "item:木匣", "attribute": "holder", "value": "player"}])
    body_event = _event("木匣封好了", [{"entity": "item:木匣", "attribute": "condition", "value": "封好"}],
                        phase="turn", source="performance", subject="character")
    actors, proposals, reviews = [], [], []

    async def options():
        return {"evaluator": True, "review": True}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        actors.append(True)
        return {"performance": "木匣封好了，你已经走到门外。" if len(actors) == 1 else "木匣封好了。", "suggested_inputs": []}

    async def propose(self, **kwargs):
        proposals.append(True)
        return deepcopy([player, body_event])

    async def review(self, **kwargs):
        candidate = kwargs["actor_performance"]
        reviews.append(deepcopy(candidate))
        if len(reviews) == 2:
            raise evaluator.NumericV2EvaluatorError(_BUDGET_ERROR)
        if len(reviews) == 1:
            return evaluator.NumericV2TransitionOfferReview(
                False, False, ("player_action",), (), failure_reason="不得替玩家走出门。",
                approved_narrative_event_indexes=(0, 1), narrative_review_complete=True,
                reviewed_narrative_events=narrative_event_table(candidate, message),
                reviewed_narrative_body=narrative_body_fingerprint(candidate),
            )
        assert "narrative_events" not in candidate
        return _parsed_review(candidate, message)

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, "propose", propose)
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("take", 0, message), ensure_current_binding=lambda _: _binding(),
    )
    record = result.stored.ledger_events[-1]["narrative_record"]
    assert len(actors) == len(proposals) == 2 and len(reviews) == 3
    assert result.diagnostics["narrative_review_budget_degrades"] == 1
    assert result.performance["performance"] == "木匣封好了。"
    assert record["coverage"] == "partial" and record["events"] == [player]
    assert result.stored.session.narrative_state["entities"]["item:木匣"]["holder"]["value"] == "player"
    assert "condition" not in result.stored.session.narrative_state["entities"]["item:木匣"]
