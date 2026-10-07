"""Keep explicit rejection codes when a model appends an explanation to them."""

import json

import pytest

from services.theater import numeric_v2_evaluator as evaluator


def _payload(violations, reason=""):
    return json.dumps({
        "offer_present": False, "valid": False, "body_violations": violations,
        "unsafe_suggestion_indexes": [], "failure_reason": reason,
    })


@pytest.mark.parametrize("explanation", [
    "player_action: 正文让玩家割开手腕，但玩家只解释了饮料的用途。",
    "player_action：正文让玩家递出钥匙，但玩家只询问同伴是否愿意帮忙。",
])
def test_explicit_code_with_explanation_remains_a_body_rejection(explanation):
    review = evaluator._parse_transition_judge_output(_payload([explanation]))
    assert review.body_violations == ("player_action",)
    assert explanation.split("：" if "：" in explanation else ":", 1)[1].strip() in review.failure_reason
    assert not review.approved_evaluator_fact_indexes


def test_existing_reason_and_multiple_distinct_codes_are_preserved():
    review = evaluator._parse_transition_judge_output(_payload([
        "player_action: 替玩家做出了新行动。", "author_boundary：与作者禁令冲突。",
    ], reason="本轮输入没有授权该行动。"))
    assert review.body_violations == ("player_action", "author_boundary")
    assert review.failure_reason == "本轮输入没有授权该行动。"


@pytest.mark.parametrize("label", [
    "not_player_action: 未发现冲突", "没有 player_action：只是请求", "player_actionish: 错误",
    "player_action", "unknown: player_action", "player_action:",
])
def test_recovery_never_searches_explanation_for_a_code(label):
    if label == "player_action":
        assert evaluator._parse_transition_judge_output(_payload([label])).body_violations == (label,)
    else:
        with pytest.raises(evaluator.NumericV2EvaluatorOutputError):
            evaluator._parse_transition_judge_output(_payload([label]))


def test_unknown_code_is_not_silently_discarded_alongside_a_known_code():
    with pytest.raises(evaluator.NumericV2EvaluatorOutputError):
        evaluator._parse_transition_judge_output(_payload(["player_action: 未授权行动", "unknown: 其他冲突"]))


@pytest.mark.parametrize("code", ["player_action", "author_boundary", "scene_boundary"])
@pytest.mark.parametrize("metadata", [
    {"description": "与已经给出的边界冲突。"},
    {"detail": "与已经给出的边界冲突。"},
    {"reason": "与已经给出的边界冲突。", "evidence_quote": "这不是事实证据。"},
])
def test_explicit_object_code_preserves_rejection_without_authorizing_facts(code, metadata):
    review = evaluator._parse_transition_judge_output(_payload([{"type": code, **metadata}]))
    assert review.body_violations == (code,)
    assert review.failure_reason == "与已经给出的边界冲突。"
    assert not review.fact_candidates
    assert not review.approved_evaluator_fact_indexes
    assert not review.body_issues
    assert not review.scene_update_removal_safe


@pytest.mark.parametrize("item", [
    {"type": "unknown", "reason": "player_action"},
    {"reason": "player_action"},
    {"type": "player_action: 未授权"},
    {"type": "player_action", "valid": True},
    {"type": "player_action", "reason": []},
    {"type": ["player_action"]},
])
def test_object_code_does_not_guess_or_discard_invalid_structure(item):
    with pytest.raises(evaluator.NumericV2EvaluatorOutputError):
        evaluator._parse_transition_judge_output(_payload(["author_boundary", item]))


def test_multiple_findings_of_one_known_violation_preserve_rejection():
    review = evaluator._parse_transition_judge_output(_payload([
        "author_boundary", {"type": "author_boundary", "detail": "正文替玩家接受了结果。"},
        {"type": "author_boundary", "detail": "正文另编了原文字句。"},
    ]))
    assert review.body_violations == ("author_boundary",)
    assert not review.author_boundaries_preserved
    assert not review.body_issues
    assert not review.approved_evaluator_fact_indexes


@pytest.mark.asyncio
@pytest.mark.parametrize("code_shape", ["inline", "object", "duplicate_detail"])
@pytest.mark.parametrize("message,unsafe,safe", [
    ("这只是提神用的。", "你割开手腕，把血涂在控制台上。", "（放下杯子）原来如此。"),
    ("我只是问问这条路。", "你交出了家门钥匙，并承诺明天搬走。", "（指向路牌）可以先看看地图。"),
])
async def test_inline_rejection_cannot_commit_the_rejected_draft(tmp_path, monkeypatch, message, unsafe, safe, code_shape):
    from services.theater import numeric_v2_workflow as workflow
    from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
    from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
    from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(session_id="inline_rejection", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    calls = {"actor": 0, "review": 0}

    async def options():
        return {"evaluator": True, "review": True, "dispute": False}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        calls["actor"] += 1
        return {"performance": unsafe if calls["actor"] == 1 else safe,
                "transition_offered": False, "suggested_inputs": []}

    async def review(self, **kwargs):
        calls["review"] += 1
        code = ("player_action: 正文新增了玩家未授权的行动。" if code_shape == "inline" else
                {"type": "player_action", "reason": "正文新增了玩家未授权的行动。"})
        violations = [code] if calls["review"] == 1 else []
        if violations and code_shape == "duplicate_detail":
            violations = [{"type": "player_action", "detail": "正文新增了玩家未授权的行动。"}] * 2
        return evaluator._parse_transition_judge_output(_payload(violations))

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(evaluator.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(evaluator.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("inline_rejection_turn", 0, message), ensure_current_binding=lambda _: _binding(),
    )
    assert calls == {"actor": 2, "review": 2}
    assert result.performance["performance"] == safe
    assert len(result.stored.ledger_events) == len(current.ledger_events) + 1
    assert unsafe not in json.dumps(result.stored.session.performance_history, ensure_ascii=False)
    assert await runtime.restore_session("inline_rejection") == result.stored
    fork = await runtime.fork_session_for_test("inline_rejection", session_id="inline_rejection_fork",
                                               through_revision=result.stored.session.revision)
    assert fork.session.story_state == result.stored.session.story_state
