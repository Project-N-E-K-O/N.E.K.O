"""Narrow completion checks return consumed decisions without verbose diagnostics crowding approvals."""

from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_structured_output import review_output_schema
from tests.unit.test_theater_numeric_v2_completion_fact_review import (
    KEY, RESULT, _capture, _response,
)


@pytest.mark.asyncio
async def test_completion_only_omits_diagnostic_but_keeps_native_approval_and_limits(monkeypatch):
    payload = _response()
    payload.pop("failure_reason")
    result, _, schema, messages, options = await _capture(monkeypatch, mode=True, response=payload)

    assert "failure_reason" not in schema["properties"]
    assert "failure_reason" not in schema["required"]
    assert "failure_reason" not in messages[0].content
    # Request-local diagnostics extend this mode; every existing safety/approval property stays identical.
    original = review_output_schema(completion_facts=True, actor_facts=True, completion_fact_review_only=True)
    assert {key: schema["properties"][key] for key in original["properties"]} == original["properties"]
    assert set(schema["properties"]) == set(original["properties"]) | {"body_issues"}
    assert schema["required"] == [
        "offer_present", "offer_quote", "offer_kind", "valid", "body_violations",
        "unsafe_suggestion_indexes", "player_action_kind", "fact_candidates", "approved_actor_fact_indexes",
        "body_issues",
    ]
    assert schema["properties"]["body_issues"]["maxItems"] == 2
    assert schema["properties"]["body_issues"]["items"]["properties"]["quote"]["maxLength"] == 40
    assert schema["properties"]["body_issues"]["items"]["properties"]["code"]["enum"] == ["other"]
    assert "body_issues" in messages[0].content
    assert "scene_update_removal_safe" not in schema["properties"]
    assert options["max_completion_tokens"] == 350
    assert options["timeout"] == 8
    assert options["max_retries"] == 0
    assert result.failure_reason == ""
    assert result.fact_candidates == ({"key": KEY, "value": True, "evidence_quote": RESULT},)


@pytest.mark.parametrize("options", [
    pytest.param({}, id="options0"),
    pytest.param({"formal": True, "transition_intent": "accept", "display_suggestions": True}, id="options3"),
])
def test_all_other_schema_modes_keep_existing_diagnostic(options):
    original = review_output_schema(**options)
    assert original == review_output_schema(**options, completion_fact_review_only=False)
    assert original["properties"]["failure_reason"] == {"type": "string"}
    assert "failure_reason" in original["required"]
    compact = review_output_schema(**options, completion_fact_review_only=True)
    # 仅省略诊断；审批、邀请、正文、按钮字段及各自约束逐值保留。
    expected = {**original, "properties": {key: value for key, value in original["properties"].items()
                                           if key != "failure_reason"},
                "required": [key for key in original["required"] if key != "failure_reason"]}
    assert compact == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"body_violations": ["author_boundary"], "approved_actor_fact_indexes": []},
    {"body_violations": ["player_action"], "player_action_kind": "unauthorized"},
    {"offer_present": True, "offer_quote": "我接过木匣。", "offer_kind": "invitation", "valid": False},
    {"approved_actor_fact_indexes": [99]},
])
async def test_missing_diagnostic_never_changes_rejection_or_candidate_authority(monkeypatch, extra):
    payload = _response(**extra, failure_reason="这段说明只供诊断，不能改变已填写的判定。")
    legacy = (await _capture(monkeypatch, mode=True, response=payload))[0]
    payload.pop("failure_reason")
    compact = (await _capture(monkeypatch, mode=True, response=payload))[0]
    assert compact == replace(legacy, failure_reason="")


def test_compact_response_keeps_button_verdicts_and_rejects_truncated_json():
    payload = _response()
    payload.pop("failure_reason")
    payload.pop("unsafe_suggestion_indexes")
    payload["suggestion_checks"] = [
        {"index": 0, "decision": "allow", "requires": []},
        {"index": 1, "decision": "reject", "requires": []},
    ]
    result = ev._parse_transition_judge_output(json.dumps(payload, ensure_ascii=False),
                                               completion_fact_review=True,
                                               display_suggestions=("先等消息。", "我已经替你报名了。"))
    assert result.unsafe_suggestion_indexes == (1,)
    assert result.approved_suggestion_texts == ("先等消息。",)
    # 不从被截断的诊断中抢救前缀判定，仍要求完整 JSON。
    with pytest.raises(ev.NumericV2EvaluatorOutputError, match="numeric_v2_transition_judge_invalid_json"):
        ev._parse_transition_judge_output('{"offer_present":false,"body_violations":["author_boundary"],"failure_reason":"未完')


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["source_only", "pure_invitation"])
async def test_non_actor_completion_modes_do_not_gain_issue_or_removal_authority(monkeypatch, mode):
    kwargs = {"candidate_override": {"performance": "", "suggested_inputs": [], "transition_offered": False}}
    response = _response(approved_actor_fact_indexes=[], scene_update_removal_safe=True)
    if mode == "source_only":
        kwargs["evaluator_fact_claims"] = ({
            "op": "set", "key": KEY, "value": True, "description": "玩家已经请求核验。",
            "evidence": [{"source": "player_input", "quote": "请管事核验文书。"}],
        },)
        response["approved_evaluator_fact_indexes"] = [0]
    else:
        kwargs["candidate_override"]["performance"] = "要现在一起去山径的避风棚吗？"
        kwargs["candidate_override"]["transition_offered"] = True
        kwargs["require_offer_acceptance"] = True
        response["offer_acceptance_valid"] = False
        response.pop("fact_candidates")
    result, data, schema, messages, options = await _capture(
        monkeypatch, mode=True, response=response, **kwargs)
    for field in ("body_issues", "scene_update_removal_safe"):
        assert field not in schema["properties"]
        assert field not in messages[0].content
    assert not data.get("actor_fact_candidates")
    assert result.body_issues == ()
    assert result.scene_update_removal_safe is False
    assert options["timeout"] == 8 and options["max_retries"] == 0
    assert options["max_completion_tokens"] <= 350


@pytest.mark.asyncio
@pytest.mark.parametrize("issue", [
    {"code": "other", "field": "scene_update", "quote": RESULT, "violations": ["author_boundary"]},
    {"code": "other", "field": "actor_performance", "quote": RESULT, "violations": ["author_boundary"]},
    {"code": "other", "field": "scene_update", "quote": "上一稿的别句原文。", "violations": ["author_boundary"]},
])
async def test_completion_locators_bind_exact_current_fields_without_granting_removal(monkeypatch, issue):
    result, _, _, _, _ = await _capture(monkeypatch, mode=True, response=_response(
        body_violations=["author_boundary"], body_issues=[issue],
        scene_update_removal_safe=True, approved_actor_fact_indexes=[]))
    assert result.body_violations == ("author_boundary",)
    assert result.body_issues == ((issue,) if issue["field"] == "scene_update" and issue["quote"] == RESULT else ())
    assert result.scene_update_removal_safe is False
