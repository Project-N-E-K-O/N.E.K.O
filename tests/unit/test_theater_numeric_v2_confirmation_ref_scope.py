"""Every supplied original remains selectable; program checks enforce order and phase."""

import json
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import pytest

from services.theater import numeric_v2_evaluator as ev
from tests.unit.test_theater_numeric_v2_review_protocol import _session
from tests.utils.theater_prompt import contract_audit_payload, prompt_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


def _install(monkeypatch, respond):
    calls = []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content)
            calls.append(data)
            answer = respond(messages, data, kwargs)
            return SimpleNamespace(content=json.dumps(answer, ensure_ascii=False))

    async def factory(*args, **kwargs):
        assert kwargs["max_completion_tokens"] <= 720
        return Client()

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("clearance", ["c1", "c2"])
@pytest.mark.parametrize("source", ["我同意之后展示。", "我已经启动展示。"])
async def test_source_finding_keeps_own_wording_but_excludes_later_target(monkeypatch, clearance, source):
    body = {"segments": [
        {"phase": "source_response", "performance": "（点头）" + source},
        {"phase": "target_opening", "scene_narration": "展板上出现完整图像。", "performance": "看到了吗？"},
    ]}

    def respond(messages, data, kwargs):
        if "sources" in data:
            return contract_audit_payload({"violations": [{"rule_index": 0, "candidate_ref": "c1",
                "quote": source, "prior_ref": ""}]}, messages)
        refs = kwargs["response_format"]["json_schema"]["schema"]["properties"]["clearance"]["items"]["enum"]
        assert "eligible_clearance_refs" not in data
        assert {"c0", "c1", "c2", "c3"} <= set(refs)
        assert [row["id"] for row in data["visible_order"]] == ["c0", "c1", "c2", "c3"]
        assert "clearance可选择visible_order或clearance_sources中的任一原文id" in messages[0].content
        answer = {"verdict": "clear", "reason": "该同意句尚未实际执行。", "clearance": [clearance]}
        errors = list(Draft202012Validator(kwargs["response_format"]["json_schema"]["schema"]).iter_errors(answer))
        assert errors == []
        if source == "我已经启动展示。" and clearance == "c1":
            return {"verdict": "violation", "reason": "来源段原文已宣告实际执行。", "clearance": []}
        return answer

    calls = _install(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["来源幕不得启动展示。"]}},
        target_node={"story_beat": {}}, session=_session(), actor_performance=body,
        player_input="先商量。", include_all_segments=True)
    assert bool(result) is (clearance == "c2" or source == "我已经启动展示。")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_later_same_phase_context_is_not_removed_from_single_finding(monkeypatch):
    body = {"performance": "（点头）我来启动。只是明天的分工，今天不启动。"}

    def respond(messages, data, kwargs):
        if "sources" in data:
            return contract_audit_payload({"violations": [{"rule_index": 0, "candidate_ref": "c0",
                "quote": "点头", "prior_ref": ""}]}, messages)
        refs = kwargs["response_format"]["json_schema"]["schema"]["properties"]["clearance"]["items"]["enum"]
        assert "c1" in refs and data["visible_order"][1]["phase"] == data["finding"]["candidate"]["phase"]
        return {"verdict": "clear", "reason": "后句明确是未来分工。", "clearance": ["c1"]}

    _install(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["本幕不得启动。"]}}, session=_session(), actor_performance=body,
        player_input="只说安排。") == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("clearance", ["c0", "c1", "c2"])
async def test_fixed_cause_enum_excludes_effect_and_all_later_blocks(monkeypatch, clearance):
    body = {"scene_narration": "小岚按下播放键。", "fixed_narrations": [
        {"position": "before", "text": "录音传来老人的声音。"}], "performance": "听到了。"}

    def respond(messages, data, kwargs):
        if "sources" in data:
            return contract_audit_payload({"violations": [{
                "rule_index": next(row["rule_index"] for row in data["author_boundaries"]
                                   if row["text"] == ev._FIXED_ORIGINAL_CAUSE_RULE),
                "candidate_ref": "c1", "quote": "录音传来老人的声音。", "prior_ref": ""}]}, messages)
        refs = kwargs["response_format"]["json_schema"]["schema"]["properties"]["clearance"]["items"]["enum"]
        assert {"c0", "c1", "c2"} <= set(refs)
        assert len(data["visible_order"]) == 3
        return {"verdict": "clear", "reason": "已按原文顺序核对直接触发。", "clearance": [clearance]}

    _install(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(), actor_performance=body, player_input="请继续。")
    assert bool(result) is (clearance != "c0")


@pytest.mark.asyncio
async def test_multiple_findings_keep_the_existing_union_not_an_intersection(monkeypatch):
    body = {"segments": [
        {"phase": "source_response", "performance": "（点头）我同意稍后展示。"},
        {"phase": "target_opening", "scene_narration": "展板亮了。", "performance": "已经展示。"},
    ]}

    def respond(messages, data, kwargs):
        if "sources" in data:
            return contract_audit_payload({"violations": [
                {"rule_index": 0, "candidate_ref": "c1", "quote": "我同意稍后展示。", "prior_ref": ""},
                {"rule_index": 1, "candidate_ref": "c3", "quote": "已经展示。", "prior_ref": ""},
            ]}, messages)
        assert "eligible_clearance_refs" not in data
        schema = kwargs["response_format"]["json_schema"]["schema"]
        refs = schema["properties"]["verdicts"]["items"]["properties"]["clearance"]["items"]["enum"]
        assert {"c0", "c1", "c2", "c3"} <= set(refs)
        return {"verdicts": [
            {"verdict": "clear", "reason": "原句仅为未来同意。", "clearance": ["c1"]},
            {"verdict": "violation", "reason": "确实已经交付展示。", "clearance": []},
        ]}

    calls = _install(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["不得提前执行展示。", "不得宣告展示完成。"]}},
        target_node={"story_beat": {}}, session=_session(), actor_performance=body, player_input="稍后再说。",
        include_all_segments=True)
    assert len(calls) == 2
