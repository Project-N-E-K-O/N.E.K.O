"""Resolve reading choices against delivered pieces, without another model call."""

from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_fixed_content_repair import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


CHOICES = ("（阅读刚显示的文字）", "（暂时沉默）", "（询问这份材料的来源）")


def _payload():
    return {
        "offer_present": False, "valid": False, "body_violations": [],
        "unsafe_suggestion_indexes": [], "failure_reason": "",
        "fixed_narration_triggers": [{"id": "0", "evidence": "指尖触碰物品"}],
        "suggestion_checks": [
            {"index": 0, "decision": "after_display", "requires": ["0"]},
            {"index": 1, "decision": "allow", "requires": []},
            {"index": 2, "decision": "allow", "requires": []},
        ],
    }


def _review(payload=None):
    return evaluator._parse_transition_judge_output(
        json.dumps(_payload() if payload is None else payload, ensure_ascii=False),
        fixed_narration_review=True, fixed_narration_ids=("record", "second"),
        display_suggestions=CHOICES,
    )


def test_dependency_resolves_request_references_and_binds_original_choice_text():
    review = _review()
    assert review.display_dependent_suggestions == (
        {"text": CHOICES[0], "requires": ("record",)},
    )
    assert review.unsafe_suggestion_indexes == ()


@pytest.mark.parametrize("change", [
    {"index": True}, {"index": 3}, {"index": 1}, {"requires": []},
    {"requires": ["unknown"]}, {"requires": ["0", "0"]}, {"safe": True},
    {"decision": "unknown"}, {"decision": "allow"}, {"requires": [{}]},
])
def test_malformed_dependency_only_removes_choices_and_keeps_trigger_and_body(change):
    payload = _payload()
    payload["suggestion_checks"][0].update(change)
    review = _review(payload)
    assert review.display_dependent_suggestions == ()
    assert review.unsafe_suggestion_indexes == (0, 1, 2)
    assert not review.body_violations
    assert review.fixed_narration_triggers[0]["id"] == "record"


@pytest.mark.parametrize("checks", [None, {}, [], [{"index": 0, "decision": "allow", "requires": []}]])
def test_missing_or_incomplete_checks_do_not_silently_allow_unchecked_choices(checks):
    payload = _payload()
    payload.pop("unsafe_suggestion_indexes")
    payload["suggestion_checks"] = checks
    assert _review(payload).unsafe_suggestion_indexes == (0, 1, 2)


def test_legacy_response_has_no_dependencies_and_unsolicited_field_is_rejected():
    payload = _payload()
    payload.pop("suggestion_checks")
    assert _review(payload).display_dependent_suggestions == ()
    with pytest.raises(evaluator.NumericV2EvaluatorOutputError):
        evaluator._parse_transition_judge_output(json.dumps(_payload()), fixed_narration_review=True,
                                                 fixed_narration_ids=("record", "second"))


@pytest.mark.parametrize("delivered,expected", [(False, [CHOICES[1], CHOICES[2]]), (True, list(CHOICES))])
def test_final_filter_uses_actual_delivery_not_review_trigger_claim(delivered, expected):
    candidate = {"performance": "文字可见。", "suggested_inputs": list(CHOICES)}
    if delivered:
        candidate["fixed_narrations"] = [{"node_id": "start", "id": "record"}]
    result, removed = workflow._drop_undelivered_display_suggestions(candidate, _review(), node_id="start")
    assert result["suggested_inputs"] == expected
    assert removed == int(not delivered)
    assert candidate["suggested_inputs"] == list(CHOICES)


def test_dependencies_cannot_override_hard_rejection_or_drift_after_choice_insertion():
    payload = _payload()
    payload["unsafe_suggestion_indexes"] = [0]
    review = _review(payload)
    candidate, _ = workflow._drop_reported_unsafe_suggestions(
        {"suggested_inputs": list(CHOICES)}, review.unsafe_suggestion_indexes,
    )
    candidate["fixed_narrations"] = [{"node_id": "start", "id": "record"}]
    assert workflow._drop_undelivered_display_suggestions(candidate, review, node_id="start")[0] == candidate
    shifted = {"suggested_inputs": ["接受邀请。", *CHOICES]}
    result, _ = workflow._drop_undelivered_display_suggestions(shifted, review, node_id="start")
    assert result["suggested_inputs"] == ["接受邀请。", CHOICES[1], CHOICES[2]]


def test_reject_decision_derives_existing_unsafe_index_without_display_dependency():
    payload = _payload()
    payload.pop("unsafe_suggestion_indexes")
    payload["suggestion_checks"][0] = {"index": 0, "decision": "reject", "requires": []}
    review = _review(payload)
    assert review.unsafe_suggestion_indexes == (0,)
    assert not review.display_dependent_suggestions


def test_every_dependency_and_correct_node_must_be_delivered():
    review = replace(_review(), display_dependent_suggestions=({"text": CHOICES[0], "requires": ("record", "second")},))
    candidate = {"suggested_inputs": list(CHOICES), "fixed_narrations": [
        {"node_id": "start", "id": "record"}, {"node_id": "elsewhere", "id": "second"},
    ]}
    result, _ = workflow._drop_undelivered_display_suggestions(candidate, review, node_id="start")
    assert CHOICES[0] not in result["suggested_inputs"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["家书原文：愿你平安。", "航行记录：所有人员生还。"])
@pytest.mark.parametrize("trigger_valid", [True, False])
@pytest.mark.parametrize("confirmed", [True, False])
async def test_workflow_delivers_then_filters_once_and_restores(tmp_path, monkeypatch, text, trigger_valid, confirmed):
    runtime = NumericV2Runtime(_engine(text), tmp_path)
    current = await runtime.start_session(session_id="display-choice", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    calls = {"actor": 0, "review": 0}

    async def enabled():
        return {"evaluator": True, "review": True, "dispute": False, "suggestion_fill": True}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        calls["actor"] += 1
        return {"performance": "（指尖触碰物品）文字清楚了。", "suggested_inputs": list(CHOICES),
                "transition_offered": False}

    async def review(self, **kwargs):
        calls["review"] += 1
        result = _review()
        if not confirmed:
            result = replace(result, display_dependent_suggestions=())
        if not trigger_valid:
            result = replace(result, fixed_narration_triggers=({"id": "record", "evidence": "并不存在的动作"},))
        return result

    monkeypatch.setattr(workflow, "aload_theater_module_options", enabled)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("new", 0, "请你触碰，我仍保管物品。"), ensure_current_binding=lambda _: _binding(),
    )
    assert calls == {"actor": 1, "review": 1}
    expected = [] if trigger_valid else (list(CHOICES[1:]) if confirmed else list(CHOICES))
    assert result.performance["suggested_inputs"] == expected
    assert bool(result.performance.get("fixed_narrations")) is trigger_valid
    assert "display_dependent_suggestions" not in json.dumps(result.stored.session.to_dict())
    cold = await NumericV2Runtime(runtime.engine, tmp_path).restore_session("display-choice")
    assert cold == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("text,stale_choice", [
    ("家书原文：愿你平安。", "（静静看着小葵辨认）你慢慢认，我不急。"),
    ("航行记录：所有人员生还。", "（注视屏幕）文字出现了吗？"),
])
async def test_misclassified_choice_is_hidden_only_on_actual_display_turn(
    tmp_path, monkeypatch, text, stale_choice,
):
    runtime = NumericV2Runtime(_engine(text), tmp_path)
    current = await runtime.start_session(session_id="display-followup", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    calls = {"actor": 0, "review": 0}
    next_choices = ["（安静地看着眼前的文字）", "（点头）谢谢你帮忙。"]

    async def enabled():
        return {"evaluator": True, "review": True, "dispute": False, "suggestion_fill": True}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        calls["actor"] += 1
        if calls["actor"] == 1:
            return {"performance": "（指尖触碰物品）文字清楚了。",
                    "suggested_inputs": [stale_choice], "transition_offered": False}
        return {"performance": "（收回手）没写在上面的事情，我也不知道。",
                "suggested_inputs": next_choices, "transition_offered": False}

    async def review(self, **kwargs):
        calls["review"] += 1
        if calls["review"] == 1:
            # Real Flash failures tagged these waiting choices as valid after display.
            return replace(_review(), display_dependent_suggestions=(
                {"text": stale_choice, "requires": ("record",)},
            ))
        return evaluator.NumericV2TransitionOfferReview(False, False, (), ())

    monkeypatch.setattr(workflow, "aload_theater_module_options", enabled)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    first = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("display", 0, "请触碰表面，我仍保管物品。"),
        ensure_current_binding=lambda _: _binding(),
    )
    assert first.performance["suggested_inputs"] == []
    assert [piece["text"] for piece in first.performance["fixed_narrations"]] == [text]
    assert calls == {"actor": 1, "review": 1}
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session("display-followup")
    assert restored == first.stored
    second = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=restored,
        turn=TurnRequestV2("followup", 1, "（看完文字）没写在上面的事，你也不知道，对吗？"),
        ensure_current_binding=lambda _: _binding(),
    )
    assert second.performance["suggested_inputs"] == next_choices
    assert not second.performance.get("fixed_narrations")
    assert second.stored.session.revision == 2
    assert calls == {"actor": 2, "review": 2}
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session("display-followup") == second.stored
