"""Contract reports bind rules and counterevidence without resampling source text."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_runtime import NumericV2Engine
from services.theater.numeric_v2_structured_output import contract_output_schema
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.utils.theater_prompt import contract_audit_payload, prompt_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


def _session():
    return NumericV2Engine.from_mapping(numeric_v2_story()).create_session(
        session_id="bound-review", catgirl_binding=_binding(), opening_performance=_opening())


def _client(monkeypatch, respond):
    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content)
            return SimpleNamespace(content=json.dumps(respond(messages, data, kwargs), ensure_ascii=False))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)


def test_native_contract_schema_assigns_rule_keys_and_counterevidence_references():
    schema = contract_output_schema(grounded=True, author_rule_count=3, candidate_refs=["c0"])
    author = schema["properties"]["author_checks"]
    assert author["required"] == ["0", "1", "2"]
    assert all("rule_index" not in row["anyOf"][1]["properties"] for row in author["properties"].values())
    confirmation = contract_output_schema(grounded=True, confirmation=True, clearance_refs=["c0", "p0", "r0"])
    assert confirmation["properties"]["clearance"]["items"] == {"type": "string", "enum": ["c0", "p0", "r0"]}


@pytest.mark.parametrize("count", [13, 40])
def test_complete_clear_report_fits_existing_first_audit_output_limit(count):
    report = {"author_checks": {str(i): "clear" for i in range(count)}, "violations": [],
              "player_authority": {"status": "clear", "checked_refs": ["c0", "c1", "c2"], "actions": []}}
    assert ev.count_tokens(json.dumps(report, ensure_ascii=False)) < 720


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["clear", "missing", "uncertain", "wrong_field"])
async def test_one_unresolved_rule_does_not_erase_other_completed_rule_checks(monkeypatch, bad):
    rules = ["本幕不得开柜。", "本幕不得签署合同。", "本幕不得启用广播。"]
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        checks = {str(i): "clear" for i in range(3)}
        if bad == "missing": checks.pop("1")
        elif bad == "uncertain": checks["1"] = "uncertain"
        elif bad == "wrong_field": checks["1"] = {"rule_index": 2}
        payload = contract_audit_payload({"violations": []}, messages)
        payload["author_checks"] = checks
        return payload

    _client(monkeypatch, respond)
    diagnostics = {}
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": rules}}, session=_session(),
        actor_performance={"performance": "先讨论方案。"}, player_input="先等等。", diagnostics=diagnostics)
    assert result == (() if bad == "clear" else (rules[1],))
    assert diagnostics["unresolved_author_rules"] == ([] if bad == "clear" else [rules[1]])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_repeated_findings_for_one_author_rule_are_checked_without_losing_coverage(monkeypatch):
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        if "sources" in data:
            payload = contract_audit_payload({"violations": []}, messages)
            payload["author_checks"] = {"clear": [0, 2], "uncertain": [], "violations": [
                {"rule_index": 1, "candidate_ref": "c0", "quote": quote, "prior_ref": ""}
                for quote in ["之后再开柜", "是否申请许可"]]}
            return payload
        assert len(data["findings"]) == 2
        return {"verdicts": [{"verdict": "clear", "reason": "完整原句只是未来提议，未执行。", "clearance": ["c0"]}] * 2}

    _client(monkeypatch, respond)
    diagnostics = {}
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["不得签署合同。", "本幕不得开柜。", "不得启用广播。"]}},
        session=_session(), actor_performance={"performance": "之后再开柜，可以先考虑是否申请许可。"},
        player_input="先讨论。", diagnostics=diagnostics)
    assert result == () and diagnostics["author_coverage_complete"] is True
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", ["p0", "unknown", "actor_history", "ellipsized_quote"])
async def test_bound_clearance_uses_original_player_input_and_keeps_invalid_evidence_blocked(monkeypatch, proof):
    session = replace(_session(), performance_history=({"revision": 1, "input_text": "我先等等。",
        "performance": "玩家已经同意检查接口。", "suggested_inputs": []},))
    player = "（查找公开电话）先打给管理处，请他们代为转达。"
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        if "sources" in data:
            payload = contract_audit_payload({"violations": []}, messages)
            payload["player_authority"] = {"status": "violation", "checked_refs": ["c0"], "actions": [{
                "candidate_ref": "c0", "quote": "周砚拨通管理处电话。", "authorization_ref": "", "authorization_quote": ""}]}
            return payload
        schema = kwargs["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["clearance"]["items"]["type"] == "string"
        evidence = ([{"source_ref": "p0", "quote": "（查找公开电话）...先打给管理处"}]
                    if proof == "ellipsized_quote" else ["h0" if proof == "actor_history" else proof])
        return {"verdict": "clear", "reason": "玩家原话明确要求拨打同一电话。", "clearance": evidence}

    _client(monkeypatch, respond)
    diagnostics = {}
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session, actor_performance={"scene_narration": "周砚拨通管理处电话。"},
        player_input=player, diagnostics=diagnostics)
    assert bool(result) is (proof != "p0")
    assert len(calls) == 2
    if proof == "p0":
        assert diagnostics["confirmation_verdicts"][0]["clearance"] == [{"source_ref": "p0", "quote": player}]


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", ["广播", "展柜"])
@pytest.mark.parametrize("executed", [False, True])
async def test_plain_turn_audit_knows_program_has_not_authorized_the_next_stage(monkeypatch, topic, executed):
    rule = f"本幕只讨论，不得启用{topic}。"
    body = f"{topic}已经启用。" if executed else "先说明需要什么条件。"

    def respond(messages, data, kwargs):
        assert data["current_scene_authority"] == {"node_id": "start", "transition_authorized": False}
        assert "玩家请求或前轮越界均不解除当前阶段禁令" in messages[0].content
        if "finding" in data:
            return {"verdict": "violation", "reason": "本轮仍在讨论阶段，原文已执行受限操作。", "clearance": []}
        payload = contract_audit_payload({"violations": []}, messages)
        payload["author_checks"] = {"0": {"candidate_ref": "c0", "quote": body, "prior_ref": ""} if executed else "clear"}
        return payload

    _client(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": [rule]}}, session=_session(),
        actor_performance={"performance": body}, player_input="现在开始。")
    assert result == ((rule,) if executed else ())
