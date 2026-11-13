"""Actual program originals require a causal verdict even without authored prohibitions."""

import json

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_structured_output import contract_output_schema
from tests.unit.test_theater_numeric_v2_fixed_original_causality import _piece
from tests.unit.test_theater_numeric_v2_review_protocol import _client, _session
from tests.utils.theater_prompt import contract_audit_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


def _body():
    return {"scene_narration": "机器还没有启动。", "performance": "（按下播放键）请听。",
            "fixed_narrations": [_piece("录音里传来老人的请求。", "before")]}


@pytest.mark.parametrize("refs", [[], ["c1"], ["c1", "c4"]])
def test_schema_requires_only_the_actual_fixed_original_blocks(refs):
    schema = contract_output_schema(grounded=True, check_player_authority=True,
        candidate_refs=["c0", "c1", "c2", "c3", "c4"], fixed_original_refs=refs)
    if not refs:
        assert "fixed_original_cause" not in schema["properties"]
        return
    coverage = schema["properties"]["fixed_original_cause"]
    assert coverage["required"] == refs
    assert set(coverage["properties"]) == set(refs)
    assert coverage["additionalProperties"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("authored_rules", [[], ["不得改写录音内容。"]])
@pytest.mark.parametrize("reported", [None, {}, {"c1": "uncertain"}, {"c0": "clear"},
                                     {"c1": "clear", "c99": "clear"}, {"c1": {"quote": "不存在的原文", "prior_ref": ""}}])
async def test_empty_violations_does_not_replace_original_cause_coverage(monkeypatch, authored_rules, reported):
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        assert "fixed_original_cause" in kwargs["response_format"]["json_schema"]["schema"]["required"]
        payload = contract_audit_payload({"violations": []}, messages)
        payload.pop("fixed_original_cause", None)
        if reported is not None:
            payload["fixed_original_cause"] = reported
        return payload

    _client(monkeypatch, respond)
    diagnostics = {}
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": authored_rules}}, session=_session(),
        actor_performance=_body(), player_input="请你展示。", diagnostics=diagnostics)
    assert result == (() if reported == {"c1": "uncertain"} else (ev._FIXED_ORIGINAL_CAUSE_RULE,))
    assert diagnostics["fixed_original_cause_complete"] is False
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("clear", [False, True])
async def test_fixed_block_verdict_reuses_existing_confirmation_and_issued_rule(monkeypatch, clear):
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        if "sources" in data:
            payload = contract_audit_payload({"violations": []}, messages)
            payload["fixed_original_cause"] = {"c1": "clear" if clear else {
                "quote": "录音里传来老人的请求。", "prior_ref": ""}}
            return payload
        assert data["finding"]["candidate"]["id"] == "c1"
        assert data["finding"]["rule"] == ev._FIXED_ORIGINAL_CAUSE_RULE
        return {"verdict": "violation", "reason": "启动动作在原文之后，不能回补前因。", "clearance": []}

    _client(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(), actor_performance=_body(), player_input="请你展示。")
    assert bool(result) is not clear
    assert len(calls) == (1 if clear else 2)


@pytest.mark.asyncio
async def test_clearing_one_old_finding_does_not_fill_missing_fixed_cause_coverage(monkeypatch):
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        if "sources" in data:
            rule = next(row for row in data["author_boundaries"] if row["text"] == ev._FIXED_ORIGINAL_CAUSE_RULE)
            payload = contract_audit_payload({"violations": [{"rule_index": rule["rule_index"],
                "candidate_ref": "c1", "quote": "录音里传来老人的请求。", "prior_ref": ""}]}, messages)
            payload.pop("fixed_original_cause", None)
            return payload
        return {"verdict": "clear", "reason": "早期原文支持同一次启动。", "clearance": ["c0"]}

    _client(monkeypatch, respond)
    diagnostics = {}
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(), actor_performance=_body(),
        player_input="请你展示。", diagnostics=diagnostics)
    assert result == (ev._FIXED_ORIGINAL_CAUSE_RULE,) and len(calls) == 2
    assert diagnostics["fixed_original_cause_complete"] is False


def test_causal_coverage_does_not_raise_existing_output_budget():
    payload = {"author_checks": {str(i): "clear" for i in range(40)}, "violations": [],
               "player_authority": {f"c{i}": "none" for i in range(16)},
               "fixed_original_cause": {f"c{i}": "clear" for i in range(8)}}
    assert ev.count_tokens(json.dumps(payload, ensure_ascii=False)) < 720


@pytest.mark.asyncio
async def test_original_that_delivers_its_own_authorized_cause_does_not_require_an_extra_trigger(monkeypatch):
    original = "角色按下播放键，随后录音里传来老人的请求。"
    body = {"performance": "听完再说说你的想法吧。", "fixed_narrations": [_piece(original, "before")]}
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        assert "原文本身已按先后交付合法前因时无需另补" in messages[0].content
        assert "原文只给结果时不得补造未描述前因" in messages[0].content
        payload = contract_audit_payload({"violations": []}, messages)
        payload["fixed_original_cause"] = {"c0": "clear"}
        return payload

    _client(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(), actor_performance=body, player_input="请你展示。")
    assert result == () and len(calls) == 1


# Verbatim first qwen3.8-max receipt from model-comparison/A07-original-cause-after.
MAX_A07_RECEIPT = '{"player_authority":{"c0":"none","c1":"none","c2":"none","c3":"none"},"fixed_original_cause":{"c1":{"quote":"录音里传来老人完整的请求，最后是一声叹息。","prior_ref":"c2"}},"violations":[]}'


@pytest.mark.asyncio
@pytest.mark.parametrize("clear_later", [False, True])
async def test_recorded_later_trigger_is_negative_evidence_and_cannot_clear_the_original_cause(
    monkeypatch, clear_later,
):
    receipt = json.loads(MAX_A07_RECEIPT)
    body = {"scene_narration": "录音机尚未启动，小岚的手停在播放键上方。",
            "fixed_narrations": [_piece("录音里传来老人完整的请求，最后是一声叹息。", "before")],
            "performance": "（按下播放键）听到了。"}
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        if "sources" in data:
            # No helper modifies the original model verdict or source references.
            return receipt
        assert data["finding"]["candidate"]["id"] == "c1"
        assert data["finding"]["prior"]["id"] == "c2"
        assert data["finding"]["rule"] == ev._FIXED_ORIGINAL_CAUSE_RULE
        return {"verdict": "clear" if clear_later else "violation", "reason": "核对启动动作相对程序原文的先后。",
                "clearance": ["c2"] if clear_later else []}

    _client(monkeypatch, respond)
    diagnostics = {}
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"summary": "小岚可自行启动录音机，声音由程序原文展示。"}}, session=_session(),
        actor_performance=body, player_input="请你播放，我在桌边听。", diagnostics=diagnostics)
    assert result == (ev._FIXED_ORIGINAL_CAUSE_RULE,) and len(calls) == 2
    assert diagnostics["grounded_findings"] == 1
    if clear_later:
        assert diagnostics["confirmation_verdicts"][0]["verdict"] == "uncertain"


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", ["c1", "a0"])
async def test_dropped_fixed_cause_report_remains_unresolved(monkeypatch, prior):
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        payload = contract_audit_payload({"violations": []}, messages)
        payload["fixed_original_cause"] = {"c1": {"quote": "录音里传来老人的请求。", "prior_ref": prior}}
        return payload

    _client(monkeypatch, respond)
    diagnostics = {}
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"summary": "角色可自行启动机器。"}}, session=_session(),
        actor_performance=_body(), player_input="请你播放。", diagnostics=diagnostics)
    assert result == (ev._FIXED_ORIGINAL_CAUSE_RULE,) and len(calls) == 1
    assert diagnostics["fixed_original_cause_complete"] is False


@pytest.mark.asyncio
async def test_later_predecessor_for_other_generic_rules_remains_excluded(monkeypatch):
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        index = next(row["rule_index"] for row in data["author_boundaries"] if row["text"] == ev._REPEAT_COMPLETION_RULE)
        payload = contract_audit_payload({"violations": [{"rule_index": index, "candidate_ref": "c1",
            "quote": "录音里传来老人的请求。", "prior_ref": "c2"}]}, messages)
        payload["fixed_original_cause"] = {"c1": "clear"}
        return payload

    _client(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(), actor_performance=_body(), player_input="请你播放。")
    assert result == () and len(calls) == 1


@pytest.mark.asyncio
async def test_same_fixed_block_literal_prefix_is_retained_but_whole_effect_is_not_clearance(monkeypatch):
    cause, effect = "角色按下播放键，", "录音里传来老人的请求。"
    body = {"performance": "听完再说吧。", "fixed_narrations": [_piece(cause + effect, "before")]}
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        if "sources" in data:
            payload = contract_audit_payload({"violations": []}, messages)
            payload["fixed_original_cause"] = {"c0": {"quote": effect, "prior_ref": "c0"}}
            return payload
        assert data["finding"]["prior"]["text"] == cause
        assert data["finding"]["quote"] == effect
        return {"verdict": "clear", "reason": "原文同块的效果之前已描述按下播放键。", "clearance": ["c0"]}

    _client(monkeypatch, respond)
    diagnostics = {}
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(), actor_performance=body,
        player_input="请你播放。", diagnostics=diagnostics)
    # The current ID-only confirmation cannot select the retained prefix alone.
    # The whole original still cannot clear a finding about its own cause.
    assert result == (ev._FIXED_ORIGINAL_CAUSE_RULE,) and len(calls) == 2
    assert diagnostics["confirmation_verdicts"][0]["verdict"] == "uncertain"
