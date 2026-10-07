"""Reuse an approved ordinary draft only after a speculative initiation is rejected."""

from copy import deepcopy
from dataclasses import replace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_actor import NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import (
    apply_fact_ops,
    MetricChangeV2, NumericV2Engine, NumericV2Runtime, NumericV2RuntimeError, TurnRequestV2,
)
from services.theater.numeric_v2_store import NumericV2StoredSession
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "safe", "unsafe_button", "phantom_offer", "body_violation", "new_offer",
    "invalid_offer", "review_fact", "actor_fact", "fixed_narration",
    "formal_timeout", "formal_allowed", "formal_extra_action", "different_outcome",
    "completed_scene",
    "valid_review_fact", "mixed_review_facts",
])
async def test_rejected_recovery_reuses_only_the_same_approved_ordinary_draft(
    tmp_path, monkeypatch, scenario,
):
    case = initiation_case(message="我们能去阅览室吗？")
    engine = case["engine"]
    if scenario in {"valid_review_fact", "mixed_review_facts"}:
        engine.fact_contract["scene:start:light_on"] = {
            "value_type": "bool", "visibility": "public", "description": "桌边的灯光明亮。",
        }
        engine.nodes["start"]["completion_contract"] = {
            "all": [{"key": "scene:start:light_on", "equals": True}],
        }
    if scenario == "completed_scene":
        engine.nodes["start"]["route_gates"][1]["transition_contract"]["fallback_offer"] = "要一起去阅览室吗？"
        monkeypatch.setattr(engine, "completion_contract_satisfied", lambda session: True)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="recovery_reuse", catgirl_binding=_binding(),
        opening_performance=case["session"].opening_performance,
    )
    actors, reviews = [], []
    evaluation_calls = []
    fact = {"key": "unknown_fact", "value": True, "evidence_quote": "我会配合你。"}
    valid_fact = {"key": "scene:start:light_on", "value": True,
                  "evidence_quote": "桌边的灯光依然明亮。"}
    review_facts = (
        (fact,) if scenario == "review_fact" else
        (valid_fact,) if scenario == "valid_review_fact" else
        (fact, valid_fact) if scenario == "mixed_review_facts" else ()
    )
    first_review = evaluator.NumericV2TransitionOfferReview(
        offer_present=scenario in {"new_offer", "invalid_offer"},
        valid=scenario == "new_offer",
        body_violations=("author_boundary",) if scenario == "body_violation" else (),
        unsafe_suggestion_indexes=(0,) if scenario == "unsafe_button" else (),
        failure_reason="作者禁令冲突。" if scenario == "body_violation" else "",
        missed_initiation=True,
        public_destination_quote="左侧走廊通往阅览室，通道已经开放。",
        fact_candidates=review_facts,
        fixed_narration_triggers=({"id": "undelivered", "evidence": "我会配合你。"},)
        if scenario == "fixed_narration" else (),
    )

    async def options():
        return {"evaluator": True, "review": True}

    async def evaluate(self, **kwargs):
        evaluation_calls.append(kwargs)
        return evaluator.NumericV2EvaluationResult(
            (MetricChangeV2("trust", 2, "玩家兑现承诺", case["message"]),), False,
        )

    async def generate(self, **kwargs):
        outcome = kwargs["outcome"]
        actors.append(outcome.session.current_node_id)
        assert outcome.session.metrics["trust"] == current.session.metrics["trust"] + 2
        if outcome.session.current_node_id != "start":
            return engine.finalize_transition_performance(
                outcome, _candidate(), target_opening="阅览室入口。",
            )
        if len(actors) > 1:
            if scenario == "different_outcome":
                raise NumericV2ActorOutputError("test_changed_outcome_requires_new_draft")
            return {"performance": "重新生成的留幕稿。", "suggested_inputs": [],
                    "transition_offered": False}
        return {
            "performance": "我会配合你。",
            "scene_narration": "桌边的灯光依然明亮。",
            "suggested_inputs": ["（拿起桌上的笔）", "（安静等待）"],
            "transition_offered": scenario == "phantom_offer",
            "fact_candidates": [fact] if scenario == "actor_fact" else [],
        }

    async def review(self, **kwargs):
        reviews.append(kwargs)
        if kwargs.get("check_missed_initiation"):
            return first_review
        if kwargs["route_changed"]:
            if scenario == "formal_timeout":
                raise evaluator.NumericV2EvaluatorError("numeric_v2_transition_judge_timeout")
            if scenario == "formal_allowed":
                return evaluator.NumericV2TransitionOfferReview(False, False, (), (),
                                                               initiation_authorized=True)
            if scenario == "formal_extra_action":
                return evaluator.NumericV2TransitionOfferReview(
                    False, False, ("player_action",), (),
                    "候选新增了其他玩家操作。", initiation_authorized=True,
                )
            return evaluator.NumericV2TransitionOfferReview(
                False, False, ("player_action",), (),
                "玩家只是询问，未授权进入阅览室。", initiation_authorized=False,
            )
        return evaluator.NumericV2TransitionOfferReview(False, False, (), ())

    prepare = runtime.prepare_turn
    prepare_calls = []
    preflight = workflow._has_new_review_facts
    preflight_calls = []

    def record_preflight(*args):
        preflight_calls.append(args)
        return preflight(*args)

    def prepare_with_changed_projection(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        prepare_calls.append(prepared)
        if scenario == "different_outcome" and len(prepare_calls) == 3:
            return replace(prepared, ledger_event={**prepared.ledger_event,
                "player_action_projection": {"completed_actions": ["不同的动作投影"]}})
        return prepared

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    monkeypatch.setattr(runtime, "prepare_turn", prepare_with_changed_projection)
    monkeypatch.setattr(workflow, "_has_new_review_facts", record_preflight)
    diagnostics = {}
    call = workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("question", current.session.revision, case["message"]),
        ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics,
    )
    if scenario == "formal_timeout":
        with pytest.raises(NumericV2ActorOutputError, match="numeric_v2_transition_review_failed"):
            await call
        assert await runtime.restore_session(current.session.session_id) == current
        assert actors == ["start", "ending_leave"]
        assert len(preflight_calls) == 1
        return
    if scenario == "different_outcome":
        with pytest.raises(NumericV2ActorOutputError, match="test_changed_outcome_requires_new_draft"):
            await call
        assert actors == ["start", "ending_leave", "start"]
        assert diagnostics["recovered_ordinary_drafts_reused"] == 0
        assert len(preflight_calls) == 1
        assert await runtime.restore_session(current.session.session_id) == current
        return

    result = await call
    assert len(evaluation_calls) == 1
    assert len(result.stored.ledger_events) == 1
    assert result.stored.session.revision == 1
    assert result.stored.session.metrics["trust"] == current.session.metrics["trust"] + 2
    assert await NumericV2Runtime(engine, tmp_path).restore_session(current.session.session_id) == result.stored
    reused = scenario in {
        "safe", "unsafe_button", "phantom_offer", "completed_scene", "actor_fact", "review_fact",
    }
    if reused:
        assert actors == ["start", "ending_leave"]
        assert len(reviews) == 2
        assert diagnostics["actor_generation_attempts"] == 2
        assert diagnostics["transition_judge_calls"] == 2
        assert diagnostics["semantic_rewrite_attempts"] == 0
        assert diagnostics["transition_cancellations"] == 1
        assert result.performance["performance"] == "我会配合你。"
        assert result.performance["scene_narration"] == "桌边的灯光依然明亮。"
        assert not result.performance["transition_offered"]
        assert not result.stored.session.transition_offered
        assert result.outcome.ledger_event == prepare_calls[0].ledger_event
        assert result.stored.session.current_node_id == "start"
        if scenario == "unsafe_button":
            assert result.performance["suggested_inputs"] == []
            assert diagnostics["unsafe_suggestions_removed"] == 2
        if scenario == "phantom_offer":
            assert diagnostics["phantom_transition_flags_cleared"] == 1
        if scenario == "completed_scene":
            assert diagnostics["completion_fallback_offer_applied"] == 0
    elif scenario in {"formal_allowed", "formal_extra_action"}:
        assert result.stored.session.current_node_id == "ending_leave"
        assert "我会配合你。" not in str(result.stored.session.performance_history)
    else:
        assert actors == ["start", "ending_leave", "start"]
        assert len(reviews) == 3
        assert result.performance["performance"] == "重新生成的留幕稿。"
        assert diagnostics["semantic_rewrite_attempts"] == 1
    assert diagnostics["recovered_ordinary_drafts_reused"] == int(reused)
    assert len(preflight_calls) == (0 if scenario in {
        "body_violation", "new_offer", "invalid_offer", "fixed_narration",
    } else 1)
    assert "unknown_fact" not in result.stored.session.story_state["facts"]
    assert "fact_candidates" not in result.performance


@pytest.fixture(params=[
    ("反应堆已经启动。", "反应堆完成启动。", "启动控制台。"),
    ("古树的刻印已经亮起。", "古树刻印已经发光。", "森林祭坛。"),
], ids=["science_fiction", "forest"])
def review_fact_context(request):
    text, description, opening = request.param
    story = numeric_v2_story()
    key = "scene:start:result_confirmed"
    story["fact_contract"] = {"facts": {
        key: {"value_type": "bool", "visibility": "public", "description": description},
        "scene:ending_leave:other": {
            "value_type": "bool", "visibility": "public", "description": description,
        },
        "prop:result": {"value_type": "bool", "visibility": "public", "description": description},
    }}
    story["nodes"][0]["completion_contract"] = {"all": [
        {"key": key, "equals": True}, {"key": "prop:result", "equals": True},
    ]}
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="review_fact_preflight", catgirl_binding=_binding(),
        opening_performance={"performance": opening},
    )
    current = NumericV2StoredSession(session, ())
    outcome = engine.resolve_turn(session, TurnRequestV2("observe", 0, "看看结果。"), ())
    performance = {"performance": text, "suggested_inputs": ["（静候回应）"]}
    candidate = {"key": key, "value": True, "evidence_quote": text}
    return engine, current, outcome, performance, candidate


@pytest.mark.parametrize("case,expected,validation_calls", [
    ("valid", True, 1),
    ("fake_quote", False, 1),
    ("wrong_type", False, 1),
    ("wrong_scene", False, 1),
    ("global", True, 1),
    ("invalid_then_valid", True, 2),
    ("valid_then_invalid", True, 1),
    ("committed_same", False, 0),
    ("current_turn_same", False, 0),
    ("committed_different", True, 1),
    ("unknown_transaction_error", True, 1),
])
def test_review_fact_preflight_preserves_scope_and_is_pure(
    review_fact_context, monkeypatch, case, expected, validation_calls,
):
    engine, current, outcome, performance, candidate = review_fact_context
    operation = {"op": "set", "key": candidate["key"], "value": True, "visibility": "public"}
    if case in {"committed_same", "committed_different"}:
        state = apply_fact_ops(
            current.session.story_state, fact_contract={"facts": engine.fact_contract}, revision=1, client_turn_id="earlier",
            ops=[{**operation, "value": case == "committed_same"}],
        )
        current = replace(current, session=replace(current.session, revision=1, story_state=state))
        outcome = engine.resolve_turn(current.session, TurnRequestV2("observe", 1, "看看结果。"), ())
    elif case == "current_turn_same":
        outcome = engine.finalize_fact_operations(current.session, outcome, operations=(operation,))
    if case == "fake_quote":
        candidate = {**candidate, "evidence_quote": "只在旧历史出现的文字。"}
    elif case == "wrong_type":
        candidate = {**candidate, "value": "true"}
    elif case == "wrong_scene":
        candidate = {**candidate, "key": "scene:ending_leave:other"}
    elif case == "global":
        candidate = {**candidate, "key": "prop:result"}
    candidates = (candidate,)
    invalid = {**candidate, "evidence_quote": "这段引文并未出现在本轮正文。"}
    if case == "invalid_then_valid":
        candidates = (invalid, candidate)
    elif case == "valid_then_invalid":
        candidates = (candidate, invalid)
    review = evaluator.NumericV2TransitionOfferReview(False, False, (), (), fact_candidates=candidates)
    before = deepcopy((engine.story, engine.fact_contract, current, outcome, performance, review))
    calls = []
    validate = engine.finalize_actor_fact_candidates

    def record_validation(*args, **kwargs):
        calls.append(kwargs)
        if case == "unknown_transaction_error":
            raise NumericV2RuntimeError("actor_fact_outcome_mismatch")
        return validate(*args, **kwargs)

    monkeypatch.setattr(engine, "finalize_actor_fact_candidates", record_validation)
    assert workflow._has_new_review_facts(engine, current, outcome, performance, review) is expected
    assert len(calls) == validation_calls
    assert (engine.story, engine.fact_contract, current, outcome, performance, review) == before
    assert all(call["evidence_sources"] == {
        "actor_performance": workflow._actor_fact_evidence_text(performance),
    } for call in calls)


def test_body_fact_preflight_cannot_treat_unapproved_evaluator_claim_as_committed(review_fact_context):
    engine, current, outcome, performance, candidate = review_fact_context
    # This approval index has no applied operation yet. The helper must stay conservative;
    # it cannot infer a current-turn duplicate from an unrelated approval number.
    review = evaluator.NumericV2TransitionOfferReview(
        False, False, (), (), fact_candidates=(candidate,), approved_evaluator_fact_indexes=(0,),
    )
    assert workflow._has_new_review_facts(engine, current, outcome, performance, review)


@pytest.mark.parametrize("field", ["performance", "scene_narration"])
def test_fact_preflight_uses_current_exact_body_fields_only(review_fact_context, field):
    engine, current, outcome, performance, candidate = review_fact_context
    original = performance["performance"]
    performance = {"performance": "（静静观察）", field: original, "suggested_inputs": []}
    review = evaluator.NumericV2TransitionOfferReview(False, False, (), (), fact_candidates=(candidate,))
    assert workflow._has_new_review_facts(engine, current, outcome, performance, review)
    # A suggestion remains a future option and cannot make the same quotation valid.
    performance = {"performance": "（静静观察）", "suggested_inputs": [original]}
    assert not workflow._has_new_review_facts(engine, current, outcome, performance, review)


@pytest.mark.asyncio
async def test_review_disabled_keeps_actor_facts_without_using_recovery_preflight(
    review_fact_context, tmp_path, monkeypatch,
):
    engine, source, _outcome, performance, candidate = review_fact_context
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="review_disabled", catgirl_binding=_binding(),
        opening_performance=source.session.opening_performance,
    )
    calls = []

    async def options():
        return {"evaluator": True, "review": False}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        calls.append(kwargs)
        return {**deepcopy(performance), "fact_candidates": [candidate]}

    async def unexpected_review(*args, **kwargs):
        raise AssertionError("Review is disabled")

    def unexpected_preflight(*args, **kwargs):
        raise AssertionError("Recovery preflight belongs only to enabled Review")

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow, "_has_new_review_facts", unexpected_preflight)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", unexpected_review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("observe", 0, "看看结果。"), ensure_current_binding=lambda _: _binding(),
    )
    assert len(calls) == 1
    assert result.stored.session.story_state["facts"][candidate["key"]]["value"] is True
    assert "fact_candidates" not in result.performance
    assert await runtime.restore_session(current.session.session_id) == result.stored
