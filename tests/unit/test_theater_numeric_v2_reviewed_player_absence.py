"""Same-turn exact source absence receipts never waive tail or whole-body rules."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_performance import performance_content_blocks
from services.theater.numeric_v2_structured_output import contract_output_schema
from tests.unit.test_theater_numeric_v2_review_protocol import _session
from tests.utils.theater_prompt import contract_audit_payload, prompt_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


def _install(monkeypatch, respond):
    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            return SimpleNamespace(content=json.dumps(respond(messages,
                prompt_payload(messages[1].content), kwargs), ensure_ascii=False))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)


def _body():
    return {"segments": [
        {"phase": "source_response", "performance": "（点头）我同意一起拆。"},
        {"phase": "transition_bridge", "scene_narration": "两人来到桌边。"},
        {"phase": "target_opening", "performance": "（拿起自己的笔）先记下收到的内容。"},
    ]}


def test_authority_schema_excludes_only_issued_player_absence_refs():
    schema = contract_output_schema(grounded=True, check_player_authority=True,
        candidate_refs=["c0", "c1", "c2", "s0"], player_authority_refs=["c2"], author_rule_count=1)
    assert list(schema["properties"]["player_authority"]["properties"]) == ["c2"]
    assert schema["properties"]["author_checks"]["properties"]["0"]["anyOf"][1]["properties"]["candidate_ref"]["enum"] == ["c0", "c1", "c2"]
    original = contract_output_schema(grounded=True, check_player_authority=True, candidate_refs=["c0", "c1"])
    assert list(original["properties"]["player_authority"]["properties"]) == ["c0", "c1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source_change", ["none", "text", "order", "speaker", "missing_block", "phase"])
async def test_only_exact_complete_source_reuses_player_absence(monkeypatch, source_change):
    body = _body()
    receipt = performance_content_blocks(body["segments"][0])
    if source_change == "text": receipt[0]["text"] += "微笑"
    elif source_change == "order": receipt.reverse()
    elif source_change == "speaker": receipt[1]["speaker_id"] = "someone_else"
    elif source_change == "missing_block": receipt.pop()
    elif source_change == "phase": body["segments"][0]["phase"] = "target_opening"
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        refs = list(kwargs["response_format"]["json_schema"]["schema"]["properties"]["player_authority"]["properties"])
        assert refs == (["c2", "c3", "c4"] if source_change == "none" else ["c0", "c1", "c2", "c3", "c4"])
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"] = {ref: "none" for ref in refs}
        if source_change == "none":
            assert data["reviewed_player_absence_refs"] == ["c0", "c1"]
            assert len([row for row in data["sources"] if row["id"].startswith("c")]) == 5
            # A repeated ownership accusation cannot resample the exact source.
            payload["player_authority"]["c0"] = [{"quote": "点头", "authorization_ref": ""}]
            player_rule = next(row["rule_index"] for row in data["author_boundaries"] if row["text"] == ev._PLAYER_AUTHORITY_RULE)
            payload["violations"].append({"rule_index": player_rule, "candidate_ref": "c0", "quote": "点头", "prior_ref": ""})
        else:
            assert "reviewed_player_absence_refs" not in data
        return payload

    _install(monkeypatch, respond)
    before = deepcopy((body, receipt))
    diagnostics = {}
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, target_node={"story_beat": {}}, session=_session(), player_input="好。",
        actor_performance=body, include_all_segments=True, reviewed_player_absence=receipt, diagnostics=diagnostics)
    assert problems == () and len(calls) == 1
    assert diagnostics.get("player_authority_reused_refs", []) == (["c0", "c1"] if source_change == "none" else [])
    assert (body, receipt) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["missing", "uncertain", "empty", "unknown_ref"])
async def test_source_receipt_cannot_fill_incomplete_tail_authority(monkeypatch, bad):
    body = _body()

    def respond(messages, data, kwargs):
        authority = {"c2": "none", "c3": "none", "c4": "none"}
        if bad == "missing": authority.pop("c3")
        elif bad == "uncertain": authority["c3"] = "uncertain"
        elif bad == "empty": authority["c3"] = []
        else: authority["c99"] = "none"
        if "player_authority_repair" in data:
            return {"player_authority": authority}
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"] = authority
        return payload

    _install(monkeypatch, respond)
    kwargs = dict(node={"story_beat": {}}, target_node={"story_beat": {}}, session=_session(), player_input="好。",
        actor_performance=body, include_all_segments=True,
        reviewed_player_absence=performance_content_blocks(body["segments"][0]))
    reviewer = ev.NumericV2MetricEvaluator(object())
    if bad == "uncertain":
        assert await reviewer.verify_contract_boundaries(**kwargs) == ()
    else:
        with pytest.raises(ev.NumericV2EvaluatorOutputError):
            await reviewer.verify_contract_boundaries(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["transition_bridge", "target_opening"])
async def test_source_receipt_does_not_waive_new_player_actions_in_tail(monkeypatch, phase):
    body = _body()
    if phase == "transition_bridge":
        body["segments"][1]["scene_narration"] = "林舟检查了接口。"
        ref, quote = "c2", "林舟检查了接口。"
    else:
        body["segments"][2]["performance"] = "（林舟检查了接口）开始吧。"
        ref, quote = "c3", "林舟检查了接口"
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        if "sources" not in data:
            assert data["finding"]["candidate"]["phase"] == phase
            return {"verdict": "violation", "reason": "玩家原话没有授权检查接口。", "clearance": []}
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"] = {"c2": "none", "c3": "none", "c4": "none"}
        payload["player_authority"][ref] = [{"quote": quote, "authorization_ref": ""}]
        return payload

    _install(monkeypatch, respond)
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, target_node={"story_beat": {}}, session=_session(), player_input="好。",
        actor_performance=body, include_all_segments=True,
        cast_scope={"player": ["林舟"], "active_catgirl": ["小岚"]},
        reviewed_player_absence=performance_content_blocks(body["segments"][0]))
    assert ev._PLAYER_AUTHORITY_RULE in problems and len(calls) == 2


@pytest.mark.asyncio
async def test_same_source_still_undergoes_author_prohibition_check(monkeypatch):
    body = _body()
    body["segments"][0]["performance"] = "（打开设备柜）我知道了。"
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        if "sources" not in data:
            return {"verdict": "violation", "reason": "来源幕明确禁止开柜，原文实际打开。", "clearance": []}
        assert data["author_checks_required"][0]["origin_phase"] == "source_response"
        assert data["author_checks_required"][0]["check_phases"] == ["source_response", "transition_bridge", "target_opening"]
        payload = contract_audit_payload({"violations": [{"rule_index": 0, "candidate_ref": "c0",
            "quote": "打开设备柜", "prior_ref": ""}]}, messages)
        payload["player_authority"] = {"c2": "none", "c3": "none", "c4": "none"}
        return payload

    _install(monkeypatch, respond)
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["本幕不得打开设备柜。"]}}, target_node={"story_beat": {}},
        session=_session(), player_input="好。", actor_performance=body, include_all_segments=True,
        reviewed_player_absence=performance_content_blocks(body["segments"][0]))
    assert problems and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", ["candidate_and_rule", "candidate_only", "no_evidence", "unknown_ref"])
async def test_full_consent_dialogue_can_clear_physical_action_report_with_its_rule(monkeypatch, proof):
    calls = []
    body = {"performance": "我同意一起拆。"}

    def respond(messages, data, kwargs):
        calls.append(data)
        if "sources" in data:
            return contract_audit_payload({"violations": [{"rule_index": 0, "candidate_ref": "c0",
                "quote": "我同意一起拆。", "prior_ref": ""}]}, messages)
        assert "完整对白只表达同意或未来提议" in messages[0].content
        assert "规则禁止发言或承诺本身" in messages[0].content
        return {"verdict": "clear", "reason": "该禁令只禁实际拆封，整句只是同意。",
                "clearance": {"candidate_and_rule": ["c0", "r0"], "candidate_only": ["c0"],
                              "no_evidence": [], "unknown_ref": ["c0", "missing"]}[proof]}

    _install(monkeypatch, respond)
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["本幕不得实际拆封。"]}}, session=_session(), player_input="先讨论。",
        actor_performance=body)
    assert bool(problems) is (proof in {"no_evidence", "unknown_ref"}) and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["action", "narration", "cause", "player", "repeat", "duplicate_author_repeat"])
async def test_whole_dialogue_clearance_does_not_extend_to_other_evidence_domains(monkeypatch, domain):
    text = "我重新播放录音。"
    body = {"performance": f"（{text}）" if domain == "action" else text}
    if domain == "narration":
        body = {"scene_narration": text}
    node = {"story_beat": {"must_not_happen": [
        ev._REPEAT_COMPLETION_RULE if domain == "duplicate_author_repeat" else "本幕不得播放录音。"
    ] if domain in {"action", "narration", "duplicate_author_repeat"} else []}}

    def respond(messages, data, kwargs):
        if "sources" not in data:
            return {"verdict": "clear", "reason": "重引原句不足以提供该域需要的其他证据。", "clearance": ["c0"]}
        payload = contract_audit_payload({"violations": []}, messages)
        if domain == "player":
            payload["player_authority"] = {"c0": [{"quote": text, "authorization_ref": ""}]}
        else:
            index = (0 if domain in {"action", "narration"} else next(
                row["rule_index"] for row in data["author_boundaries"]
                if row["rule_index"] >= len(node["story_beat"]["must_not_happen"])
                and (row["text"] == ev._REPEAT_COMPLETION_RULE if "repeat" in domain
                     else "首次宣告不可逆结果" in row["text"])))
            history = next(row["id"] for row in data["sources"] if row["id"].startswith("h"))
            payload = contract_audit_payload({"violations": [{"rule_index": index,
                "candidate_ref": "c0", "quote": text, "prior_ref": history if "repeat" in domain else ""}]}, messages)
        return payload

    _install(monkeypatch, respond)
    diagnostics = {}
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=node, session=_session(), player_input="先讨论，不操作。", actor_performance=body, diagnostics=diagnostics)
    assert problems
    assert diagnostics["confirmation_verdicts"][0]["verdict"] == "uncertain"
