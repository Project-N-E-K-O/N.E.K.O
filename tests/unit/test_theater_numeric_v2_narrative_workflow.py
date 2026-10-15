"""Narrative approval uses the existing final Review and atomic turn commit."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_evaluator as evaluator, numeric_v2_workflow as workflow
from services.theater.numeric_v2_narrative import narrative_body_fingerprint, narrative_event_table
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_actor_output import (
    NumericV2ActorError, NumericV2ActorOutputError, NumericV2ActorUnavailableError,
)
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["approve", "veto", "partial", "rewrite", "fallback", "timeout", "review_off"])
async def test_independent_narrative_candidates_use_final_review_and_atomic_commit(tmp_path, monkeypatch, mode):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(session_id=mode, catgirl_binding=_binding(), opening_performance=_opening())
    message = "（接过木匣）我先拿着。"
    events = [{
        "phase": "player_input", "subject": "player", "kind": "action",
        "changes": [{"entity": "item:木匣", "attribute": "holder", "value": "player"}],
        "source": "player_input", "quote": "接过木匣", "authorization_quote": "接过木匣",
        "known_to": ["player", "character"],
    }, {
        "phase": "turn", "subject": "character", "kind": "action",
        "changes": [{"entity": "item:木匣", "attribute": "condition", "value": "扣紧"}],
        "source": "performance", "quote": "木匣的盖子扣紧", "known_to": ["player", "character"],
    }]
    calls = {"evaluator": 0, "actor": 0, "narrative": 0, "review": 0}

    async def options():
        return {"evaluator": True, "review": mode != "review_off"}

    async def evaluate(self, **kwargs):
        calls["evaluator"] += 1
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        calls["actor"] += 1
        return {"performance": "（把木匣的盖子扣紧）好，你先拿稳。" if calls["actor"] == 1
                else "（检查木匣的盖子扣紧）路上拿稳就好。",
                "suggested_inputs": [], "narrative_events": deepcopy(events)}

    async def review(self, **kwargs):
        calls["review"] += 1
        if mode == "timeout":
            raise evaluator.NumericV2EvaluatorError("numeric_v2_transition_judge_timeout")
        performance = kwargs["actor_performance"]
        approved = (0, 1) if mode in {"approve", "fallback", "rewrite"} else (0,) if mode == "partial" else ()
        if mode == "rewrite" and calls["review"] == 2:
            approved = ()
        bad_body = mode == "fallback" or (mode == "rewrite" and calls["review"] == 1)
        return evaluator.NumericV2TransitionOfferReview(
            False, False, ("author_boundary",) if bad_body else (), (),
            failure_reason="本稿违背作者边界。" if bad_body else "",
            approved_narrative_event_indexes=approved, narrative_review_complete=mode == "approve",
            reviewed_narrative_events=narrative_event_table(performance, message),
            reviewed_narrative_body=narrative_body_fingerprint(performance),
        )

    async def propose(self, **kwargs):
        calls["narrative"] += 1
        assert "narrative_events" not in kwargs["performance"]
        assert kwargs["player_input"] == message
        assert kwargs["session"] == current.session
        return deepcopy(events)

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, "propose", propose)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    execute = workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("take", 0, message), ensure_current_binding=lambda _: _binding(),
    )
    if mode == "timeout":
        with pytest.raises(workflow.NumericV2ActorOutputError, match="transition_review_failed"):
            await execute
        assert await runtime.restore_session(mode) == current
        return
    result = await execute
    record = result.stored.ledger_events[-1]["narrative_record"]
    assert "narrative_events" not in result.performance
    assert record == result.stored.session.performance_history[-1]["narrative_record"]
    expected = 2 if mode == "approve" else 1 if mode in {"partial", "fallback"} else 0
    assert len(record["events"]) == expected
    if expected:
        assert result.stored.session.narrative_state["entities"]["item:木匣"]["holder"]["value"] == "player"
    else:
        assert result.stored.session.narrative_state["entities"] == {}
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(mode) == result.stored
    assert calls == {"evaluator": 1, "actor": 2 if mode in {"rewrite", "fallback"} else 1,
                     "narrative": 0 if mode == "review_off" else 2 if mode in {"rewrite", "fallback"} else 1,
                     "review": 0 if mode == "review_off" else 2 if mode in {"rewrite", "fallback"} else 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("has_boundary,review_enabled", [(False, True), (True, False)])
async def test_opening_without_existing_review_does_not_call_proposer_or_self_confirm_events(
    monkeypatch, has_boundary, review_enabled,
):
    story = numeric_v2_story()
    if has_boundary:
        story["nodes"][0]["story_beat"]["opening_only_boundaries"] = ["不得提前离开。"]
    engine = NumericV2Engine.from_mapping(story)
    calls = []

    async def opening(self, **kwargs):
        calls.append("actor")
        return {"scene_narration": "柜台上放着木匣。", "performance": "你来了。",
                "suggested_inputs": [], "narrative_events": []}

    async def options():
        return {"review": review_enabled}

    async def propose(self, **kwargs):
        pytest.fail("Opening without an existing Review must not call the proposer")

    monkeypatch.setattr(workflow.NumericV2Actor, "generate_opening", opening)
    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, "propose", propose)
    result = await workflow.generate_validated_opening(engine=engine, config_manager=object(), session_id="opening",
        catgirl_binding=_binding(), actor_budget_profile="standard")
    assert calls == ["actor"]
    assert result["narrative_record"]["coverage"] == "unrecorded"
    assert "narrative_events" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["approve", "rewrite", "proposal_timeout"])
async def test_reviewed_opening_proposes_each_actual_draft_before_review(monkeypatch, mode):
    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["opening_only_boundaries"] = ["不得提前离开。"]
    engine = NumericV2Engine.from_mapping(story)
    order = []
    drafts = []

    async def options():
        return {"review": True}

    async def opening(self, **kwargs):
        order.append("actor")
        body = "（抱着木匣）你来了。" if not drafts else "（抱稳木匣）欢迎。"
        drafts.append(body)
        return {"performance": body, "scene_narration": "", "suggested_inputs": [],
                "narrative_events": [{"untrusted_actor_event": True}]}

    async def propose(self, **kwargs):
        order.append("narrative")
        assert kwargs["opening"] is True and kwargs["player_input"] == ""
        assert "narrative_events" not in kwargs["performance"]
        if mode == "proposal_timeout":
            raise NumericV2ActorError("numeric_v2_actor_narrative_timeout")
        return [{"phase": "opening", "subject": "character", "kind": "action",
                 "changes": [{"entity": "item:木匣", "attribute": "holder", "value": "character"}],
                 "source": "performance", "quote": drafts[-1], "known_to": ["player", "character"]}]

    async def review(self, **kwargs):
        order.append("review")
        candidate = kwargs["actor_performance"]
        events = narrative_event_table(candidate, "", opening=True) if "narrative_events" in candidate else ()
        return evaluator.NumericV2TransitionOfferReview(
            False, False, ("author_boundary",) if mode == "rewrite" and len(drafts) == 1 else (), (),
            approved_narrative_event_indexes=(0,) if events else (), narrative_review_complete=bool(events),
            reviewed_narrative_events=events,
            reviewed_narrative_body=narrative_body_fingerprint(candidate),
        )

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_opening", opening)
    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, "propose", propose)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    execute = workflow.generate_validated_opening(
        engine=engine, config_manager=object(), session_id="reviewed_opening",
        catgirl_binding=_binding(), actor_budget_profile="balanced",
    )
    result = await execute
    assert order == ["actor", "narrative", "review"] * (2 if mode == "rewrite" else 1)
    if mode == "proposal_timeout":
        assert result["performance"] == drafts[-1]
        assert result["narrative_record"]["coverage"] == "unrecorded"
        assert result["narrative_record"]["events"] == []
    else:
        assert result["narrative_record"]["coverage"] == "confirmed_changes"
        assert result["narrative_record"]["events"][0]["quote"] == drafts[-1]
    assert "narrative_events" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("last_table", ["missing", "invalid", "proposal_failure"])
async def test_rewrite_retains_approved_player_input_when_new_proposal_omits_it(
    tmp_path, monkeypatch, last_table,
):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(
        session_id=last_table, catgirl_binding=_binding(), opening_performance=_opening(),
    )
    message = "（抱紧面包盒，转身走向门口）明天见。"
    player = {
        "phase": "player_input", "subject": "player", "kind": "action",
        "changes": [
            {"entity": "item:面包盒", "attribute": "holder", "value": "player"},
            {"entity": "player", "attribute": "position", "value": "门口"},
        ],
        "source": "player_input", "quote": message, "known_to": ["player", "character"],
    }
    discarded_body = {
        "phase": "turn", "subject": "character", "kind": "speech",
        "changes": [{"entity": "item:面包盒", "attribute": "condition", "value": "封好"}],
        "source": "performance", "quote": "面包盒已经封好", "known_to": ["player", "character"],
    }
    calls = {"evaluator": 0, "actor": 0, "review": 0}

    async def options():
        return {"evaluator": True, "review": True}

    async def evaluate(self, **kwargs):
        calls["evaluator"] += 1
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        calls["actor"] += 1
        if calls["actor"] == 1:
            return {"performance": "（看着你推门离开）明天见，面包盒已经封好。",
                    "suggested_inputs": [], "narrative_events": [player, discarded_body]}
        result = {"performance": "嗯，明天见。面包盒已经封好。", "suggested_inputs": []}
        if last_table == "invalid":
            bad = deepcopy(player)
            bad["changes"][0]["value"] = "抱紧面包盒"
            result["narrative_events"] = [bad]
        return result

    async def review(self, **kwargs):
        calls["review"] += 1
        performance = kwargs["actor_performance"]
        first = calls["review"] == 1
        return evaluator.NumericV2TransitionOfferReview(
            False, False, ("player_action",) if first else (), (),
            failure_reason="正文替玩家推门离开，玩家只走向门口。" if first else "",
            approved_narrative_event_indexes=(0, 1) if first else (),
            narrative_review_complete=first,
            reviewed_narrative_events=narrative_event_table(performance, message) if first else (),
            reviewed_narrative_body=narrative_body_fingerprint(performance) if first else "",
        )

    async def propose(self, **kwargs):
        if calls["actor"] != 1 and last_table == "proposal_failure":
            raise NumericV2ActorError("numeric_v2_actor_narrative_timeout")
        return deepcopy([player, discarded_body]) if calls["actor"] == 1 else []

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, "propose", propose)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("goodbye", 0, message), ensure_current_binding=lambda _: _binding(),
    )
    record = result.stored.ledger_events[-1]["narrative_record"]
    assert result.performance["performance"] == "嗯，明天见。面包盒已经封好。"
    assert record["coverage"] == "partial"
    assert record["events"] == [player]
    entities = result.stored.session.narrative_state["entities"]
    assert entities["item:面包盒"]["holder"]["value"] == "player"
    assert entities["player"]["position"]["value"] == "门口"
    assert "condition" not in entities["item:面包盒"]
    assert record == result.stored.session.performance_history[-1]["narrative_record"]
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(last_table) == result.stored
    assert calls == {"evaluator": 1, "actor": 2, "review": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("last_approval", [False, True])
async def test_recovery_cancellation_does_not_resurrect_an_intermediate_player_veto(
    tmp_path, monkeypatch, last_approval,
):
    message = "（接过木匣）我们能去阅览室吗？"
    case = initiation_case(message=message)
    runtime = NumericV2Runtime(case["engine"], tmp_path)
    current = await runtime.start_session(
        session_id="intermediate_veto", catgirl_binding=_binding(),
        opening_performance=case["session"].opening_performance,
    )
    player = {
        "phase": "player_input", "subject": "player", "kind": "action",
        "changes": [{"entity": "item:木匣", "attribute": "holder", "value": "player"}],
        "source": "player_input", "quote": "接过木匣", "known_to": ["player", "character"],
    }
    actors, reviews, evaluations = [], [], []

    async def options():
        return {"evaluator": True, "review": True}

    async def evaluate(self, **kwargs):
        evaluations.append(kwargs)
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        outcome = kwargs["outcome"]
        actors.append(outcome.session.current_node_id)
        if outcome.session.current_node_id != "start":
            result = runtime.engine.finalize_transition_performance(
                outcome, _candidate(), target_opening="阅览室入口。",
            )
            result["narrative_events"] = [deepcopy(player)]
            return result
        if len(actors) == 1:
            return {"performance": "我会配合你。", "suggested_inputs": [],
                    "narrative_events": [deepcopy(player)]}
        result = {"performance": "重新生成的留幕稿。", "suggested_inputs": []}
        if last_approval:
            result["narrative_events"] = [deepcopy(player)]
        return result

    async def review(self, **kwargs):
        reviews.append(kwargs)
        performance = kwargs["actor_performance"]
        first, formal = len(reviews) == 1, kwargs["route_changed"]
        return evaluator.NumericV2TransitionOfferReview(
            False, False, ("author_boundary",) if first else ("player_action",) if formal else (), (),
            failure_reason="首稿违反作者边界。" if first else "未授权转场，该原话事件不批准。" if formal else "",
            missed_initiation=first,
            public_destination_quote="左侧走廊通往阅览室，通道已经开放。" if first else "",
            initiation_authorized=False if formal else None,
            approved_narrative_event_indexes=(0,) if first or (not formal and last_approval) else (),
            narrative_review_complete=first or (not formal and last_approval),
            reviewed_narrative_events=narrative_event_table(performance, message)
            if "narrative_events" in performance else (),
            reviewed_narrative_body=narrative_body_fingerprint(performance),
        )

    async def propose(self, **kwargs):
        return [deepcopy(player)] if len(actors) < 3 or last_approval else []

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, "propose", propose)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("question", 0, message), ensure_current_binding=lambda _: _binding(),
    )
    assert actors == ["start", "ending_leave", "start"]
    assert [review["route_changed"] for review in reviews] == [False, True, False]
    assert len(evaluations) == 1
    assert result.stored.session.current_node_id == "start"
    assert result.performance["performance"] == "重新生成的留幕稿。"
    record = result.stored.ledger_events[-1]["narrative_record"]
    assert record["events"] == ([player] if last_approval else [])
    assert record["coverage"] == ("confirmed_changes" if last_approval else "unconfirmed")
    assert bool(result.stored.session.narrative_state["entities"]) is last_approval
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session("intermediate_veto") == result.stored


@pytest.mark.asyncio
async def test_same_body_reuses_proposals_but_changed_body_cannot_reuse_them():
    calls = []

    class Proposer:
        async def propose(self, **kwargs):
            calls.append(deepcopy(kwargs["performance"]))
            return [{"draft": len(calls)}]

    cache = {}
    candidate = {"performance": "先放在这里。", "suggested_inputs": [],
                 "narrative_events": [{"untrusted_actor_event": True}]}
    kwargs = dict(session=None, message="我拿着。", cache=cache)
    assert await workflow._prepare_narrative_candidates(Proposer(), candidate, **kwargs)
    assert candidate["narrative_events"] == [{"draft": 1}]
    candidate["narrative_events"].clear()
    candidate["suggested_inputs"] = ["稍等一下。"]
    assert not await workflow._prepare_narrative_candidates(Proposer(), candidate, **kwargs)
    assert candidate["narrative_events"] == [{"draft": 1}]
    candidate["performance"] = "现在已经收好了。"
    assert await workflow._prepare_narrative_candidates(Proposer(), candidate, **kwargs)
    assert candidate["narrative_events"] == [{"draft": 2}]
    assert len(calls) == 2
    assert all("narrative_events" not in item for item in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    NumericV2ActorError("numeric_v2_actor_narrative_timeout"),
    NumericV2ActorOutputError("numeric_v2_actor_narrative_output_invalid"),
    NumericV2ActorError("numeric_v2_actor_narrative_input_over_budget"),
    NumericV2ActorError("numeric_v2_actor_narrative_model_call_failed"),
    NumericV2ActorUnavailableError("numeric_v2_actor_model_unavailable"),
])
async def test_proposal_failure_still_reviews_and_commits_a_recorded_gap(tmp_path, monkeypatch, error):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(session_id="degraded", catgirl_binding=_binding(), opening_performance=_opening())
    order = []

    async def options():
        return {"evaluator": True, "review": True}

    async def evaluate(self, **kwargs):
        order.append("evaluator")
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        order.append("actor")
        return {"performance": "你先拿稳。", "suggested_inputs": []}

    async def propose(self, **kwargs):
        order.append("narrative")
        assert kwargs["performance"]["performance"] == "你先拿稳。"
        raise error

    async def review(self, **kwargs):
        order.append("review")
        assert kwargs["actor_performance"]["performance"] == "你先拿稳。"
        assert "narrative_events" not in kwargs["actor_performance"]
        return evaluator.NumericV2TransitionOfferReview(False, False, (), ())

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2NarrativeProposer, "propose", propose)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    diagnostics = {}
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("take", 0, "我接过木匣。"), ensure_current_binding=lambda _: _binding(),
        diagnostics_sink=diagnostics,
    )
    assert order == ["evaluator", "actor", "narrative", "review"]
    assert diagnostics["narrative_model_attempts"] == 1
    assert diagnostics["narrative_proposal_failures"] == [str(error)]
    assert diagnostics["transition_judge_calls"] == 1
    assert result.performance["performance"] == "你先拿稳。"
    assert result.stored.session.revision == 1
    assert result.stored.session.status == "active"
    record = result.stored.ledger_events[-1]["narrative_record"]
    assert record["coverage"] == diagnostics["narrative_coverage"] == "unrecorded"
    assert record["events"] == []
    assert record == result.stored.session.performance_history[-1]["narrative_record"]
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session("degraded") == result.stored


@pytest.mark.asyncio
async def test_failed_proposal_is_cached_without_claiming_no_change():
    calls, failures, cache = [], [], {}

    class Proposer:
        async def propose(self, **kwargs):
            calls.append(kwargs["performance"]["performance"])
            if len(calls) == 1:
                raise NumericV2ActorError("numeric_v2_actor_narrative_timeout")
            return []

    candidate = {"performance": "先放在这里。", "suggested_inputs": [], "narrative_events": []}
    kwargs = dict(session=None, message="稍等一下。", cache=cache, failures=failures)
    assert await workflow._prepare_narrative_candidates(Proposer(), candidate, **kwargs)
    assert "narrative_events" not in candidate
    candidate["suggested_inputs"] = ["让我想想。"]
    assert not await workflow._prepare_narrative_candidates(Proposer(), candidate, **kwargs)
    assert "narrative_events" not in candidate
    assert failures == ["numeric_v2_actor_narrative_timeout"]
    candidate["performance"] = "我等你。"
    assert await workflow._prepare_narrative_candidates(Proposer(), candidate, **kwargs)
    assert candidate["narrative_events"] == []
    assert calls == ["先放在这里。", "我等你。"]


@pytest.mark.asyncio
async def test_narrative_preparation_does_not_swallow_unexpected_errors():
    class Proposer:
        async def propose(self, **kwargs):
            raise RuntimeError("unexpected programming error")

    cache = {}
    with pytest.raises(RuntimeError, match="unexpected programming error"):
        await workflow._prepare_narrative_candidates(
            Proposer(), {"performance": "我等你。"}, session=None, message="稍等。", cache=cache,
        )
    assert cache == {}
