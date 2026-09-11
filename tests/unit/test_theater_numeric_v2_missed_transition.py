"""Recover only the current uncommitted candidate, still requiring source verification, formal review and one atomic commit."""

import json

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow
from services.theater.numeric_v2_actor import NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import MetricChangeV2, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate

QUOTE = "左侧走廊通往阅览室，通道已经开放。"


@pytest.mark.parametrize("quote,allowed", [(QUOTE, True), ("作者安排去阅览室。", False), ("", False)])
def test_recovery_requires_actual_public_quote(quote, allowed):
    case = initiation_case()
    payload = dict(offer_present=False, valid=False, body_violations=["scene_boundary"],
                   unsafe_suggestion_indexes=[], missed_initiation=True, public_destination_index=0)
    review = ev._parse_transition_judge_output(json.dumps(payload), recovery_session=case["session"], recovery_evidence=(quote,))
    assert review.missed_initiation is allowed
    assert review.public_destination_quote == (QUOTE if allowed else "")
    assert review.body_violations == ("scene_boundary",)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_recovery_boolean_is_not_coerced(value):
    payload = dict(offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[],
                   missed_initiation=value, public_destination_index=0)
    with pytest.raises(ev.NumericV2EvaluatorOutputError):
        ev._parse_transition_judge_output(json.dumps(payload), recovery_session=initiation_case()["session"])


def test_legacy_review_does_not_invent_recovery():
    payload = dict(offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[])
    result = ev._parse_transition_judge_output(json.dumps(payload), recovery_session=initiation_case()["session"])
    assert not result.missed_initiation and not result.public_destination_quote


@pytest.mark.parametrize("index", [-1, 1, True, "0", None])
def test_invalid_evidence_index_does_not_authorize(index):
    # 布尔值也是Python整数的子类，不能被当作编号；越界或缺失同样不恢复。
    payload = dict(offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[],
                   missed_initiation=True, public_destination_index=index)
    result = ev._parse_transition_judge_output(json.dumps(payload), recovery_session=initiation_case()["session"],
                                               recovery_evidence=(QUOTE,))
    assert not result.missed_initiation and not result.public_destination_quote


def test_numbered_evidence_excludes_player_and_candidate():
    case = initiation_case()
    messages = ev._build_transition_judge_messages(
        case["engine"], case["session"], player_input="带我去新秘密房间。",
        actor_performance={"performance": "新秘密房间已经开放。", "suggested_inputs": []},
        check_missed_initiation=True)
    data = json.loads(messages[1].content.split("：", 1)[1])
    assert data["public_destination_evidence"]
    assert all(text in case["session"].opening_performance["performance"]
               for text in data["public_destination_evidence"])
    assert "新秘密房间" not in str(data["public_destination_evidence"])


@pytest.mark.asyncio
@pytest.mark.parametrize("formal_failure", [False, "timeout", "body"])
@pytest.mark.parametrize("recover_after_rewrite", [False, True])
async def test_recovery_restarts_from_original_state_and_commits_only_formal(tmp_path, monkeypatch, formal_failure, recover_after_rewrite):
    # 补查误判仍会被正式复核指出；持续语义否定按用户决策兜底，技术故障仍回滚。
    case = initiation_case(message="我们能去阅览室吗？" if formal_failure == "body" else "带路吧。")
    engine = case["engine"]; runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="recover", catgirl_binding=_binding(),
                                          opening_performance=case["session"].opening_performance)
    actor_nodes = []; reviews = []; evaluations = []
    async def evaluate(self, **kwargs):
        evaluations.append(kwargs)
        return ev.NumericV2EvaluationResult((MetricChangeV2("trust", 2, "玩家兑现承诺", case["message"]),), False)
    async def generate(self, **kwargs):
        outcome = kwargs["outcome"]; actor_nodes.append(outcome.session.current_node_id)
        assert outcome.session.metrics["trust"] == current.session.metrics["trust"] + 2
        if outcome.session.current_node_id == "start":
            return {"performance": "这份普通候选必须丢弃。", "suggested_inputs": [], "transition_offered": False}
        return engine.finalize_transition_performance(outcome, _candidate(), target_opening="阅览室入口。")
    async def review(self, **kwargs):
        reviews.append(kwargs)
        if kwargs.get("check_missed_initiation"):
            # 普通稿先耗尽改稿额度再补查换幕时，正式稿不能再次改写或再次争议复查。
            if recover_after_rewrite and len(actor_nodes) == 1:
                return ev.NumericV2TransitionOfferReview(False, False, ("author_boundary",), (), "普通稿存在冲突。")
            return ev.NumericV2TransitionOfferReview(False, False, (), (), missed_initiation=True,
                                                     public_destination_quote=QUOTE)
        assert kwargs["public_destination_quote"] == QUOTE
        assert kwargs["session"] == current.session
        if formal_failure == "timeout":
            raise ev.NumericV2EvaluatorError("test_formal_timeout")
        if formal_failure == "body":
            return ev.NumericV2TransitionOfferReview(False, False, ("player_action",), (), "玩家只询问可行性。")
        return ev.NumericV2TransitionOfferReview(False, False, (), ())
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
                  turn=TurnRequestV2("go", 0, case["message"]), ensure_current_binding=lambda _: _binding())
    if formal_failure == "timeout":
        with pytest.raises(NumericV2ActorOutputError, match="numeric_v2_transition_(review_failed|fact_boundary)"):
            await workflow.execute_numeric_v2_turn(**kwargs)
        assert await runtime.restore_session("recover") == current
    else:
        result = await workflow.execute_numeric_v2_turn(**kwargs)
        assert result.stored.session.revision == 1 and len(result.stored.ledger_events) == 1
        assert result.stored.session.current_node_id == "ending_leave"
        assert result.diagnostics["missed_initiation_recoveries"] == 1
        assert result.diagnostics["semantic_review_fallback"] is (formal_failure == "body")
        assert result.stored.session.metrics["trust"] == current.session.metrics["trust"] + 2
        assert "普通候选必须丢弃" not in str(result.stored)
        assert "public_destination_quote" not in result.stored.session.to_dict()
        assert await NumericV2Runtime(engine, tmp_path).restore_session("recover") == result.stored
    assert len(evaluations) == 1
    assert actor_nodes == (["start", "start", "ending_leave"] if recover_after_rewrite
                           else ["start", "ending_leave", "ending_leave"] if formal_failure == "body"
                           else ["start", "ending_leave"])
    assert len(reviews) == (4 if formal_failure == "body" or recover_after_rewrite else 2)
