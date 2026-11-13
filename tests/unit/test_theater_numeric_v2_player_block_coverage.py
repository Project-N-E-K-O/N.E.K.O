"""Every candidate block declares new player acts against issued authorization originals."""

from dataclasses import replace
import json
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
        session_id="player-block-coverage", catgirl_binding=_binding(), opening_performance=_opening())


def _client(monkeypatch, report, *, confirmation=None):
    calls = []

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content)
            calls.append(data)
            if "sources" not in data:
                return SimpleNamespace(content=json.dumps(confirmation or {
                    "verdict": "violation", "reason": "新增玩家操作缺少对应原始授权。", "clearance": []}))
            if "player_authority_repair" in data:
                return SimpleNamespace(content=json.dumps({"player_authority": report(data) if callable(report) else report},
                                                          ensure_ascii=False))
            payload = contract_audit_payload({"violations": []}, messages)
            payload["player_authority"] = report(data) if callable(report) else report
            return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "test"}

    async def factory(*args, **kwargs):
        assert kwargs["max_completion_tokens"] <= 720
        return Client()

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    return calls


def test_player_schema_requires_every_candidate_block_without_an_aggregate_clear_escape():
    schema = contract_output_schema(grounded=True, check_player_authority=True,
        candidate_refs=["c0", "c1", "s0"], authorization_refs=["p0", "p1", "t0"])
    authority = schema["properties"]["player_authority"]
    assert authority["required"] == ["c0", "c1"]
    assert authority["additionalProperties"] is False
    for value in authority["properties"].values():
        scalar, actions = value["anyOf"]
        assert scalar["enum"] == ["none", "uncertain"]
        assert actions["minItems"] == 1
        assert actions["items"]["properties"]["authorization_ref"]["enum"] == ["", "p0", "p1", "t0"]


@pytest.mark.asyncio
@pytest.mark.parametrize("report", [
    {}, {"c0": "none"}, {"c0": "none", "c1": "uncertain"},
    {"c0": "none", "c1": []}, {"c0": "none", "c1": "none", "c2": "none"},
    {"status": "clear", "checked_refs": ["c0", "c1"], "actions": []},
])
async def test_missing_or_legacy_empty_reports_cannot_prove_full_player_coverage(monkeypatch, report):
    calls = _client(monkeypatch, report, confirmation={
        "verdict": "uncertain", "reason": "该完整块的玩家权限仍未判明。", "clearance": []})
    diagnostics = {}
    kwargs = dict(node={"story_beat": {}}, session=_session(), actor_performance={
        "scene_narration": "周砚检查接口。", "performance": "先听内容。"},
        player_input="我只拆封材料。", diagnostics=diagnostics)
    reviewer = ev.NumericV2MetricEvaluator(object())
    if report == {"c0": "none", "c1": "uncertain"}:
        assert await reviewer.verify_contract_boundaries(**kwargs) == (ev._PLAYER_AUTHORITY_RULE,)
    else:
        with pytest.raises(ev.NumericV2EvaluatorOutputError):
            await reviewer.verify_contract_boundaries(**kwargs)
    assert diagnostics["player_authority_complete"] is False
    assert len(calls) == (2 if report in ({}, {"c0": "none"}, {"c0": "none", "c1": []},
                                        {"c0": "none", "c1": "uncertain"}) else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "玩家站在桌旁，衣袖被风吹动。", "刚才已经签收的文件仍在桌上。", "之后可以考虑由你检查设备。",
    "叶澄拿起录音带，NPC回答了刚才的问题。", "你递来的文件被收件人接过，交接已经完成。",
    "角色打开画册，把完整图画展示在台面上。",
])
async def test_no_new_player_act_needs_no_artificial_permission(monkeypatch, text):
    calls = _client(monkeypatch, {"c0": "none"})
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(), actor_performance={"scene_narration": text},
        player_input="我把文件递给收件人。")
    assert result == () and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["周砚检查接口。", "你签下了新的保证书。", "检查接口后，他确认线路正常。",
                                  "你终于理解了画面的全部含义，觉得非常满意。"])
async def test_new_player_act_without_authorization_remains_rejected(monkeypatch, action):
    calls = _client(monkeypatch, {"c0": [{"quote": action, "authorization_ref": ""}]})
    diagnostics = {}
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"summary": action}}, session=_session(),
        actor_performance={"scene_narration": action}, player_input="先看看材料。", diagnostics=diagnostics,
        cast_scope={"player": ["周砚"], "active_catgirl": ["叶澄"]})
    assert result == (ev._PLAYER_AUTHORITY_RULE,) and len(calls) == 2
    assert calls[1]["finding"]["quote"] == action
    assert "主观判断或自主反馈" in calls[1]["finding"]["rule"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["p0", "p1", "t0", "h0", "a0", "missing"])
async def test_actions_bind_only_issued_player_authorization_originals(monkeypatch, ref):
    session = replace(_session(), performance_history=({"revision": 1,
        "input_text": "我检查同一接口。", "performance": "你已经答应检查接口。"},))
    calls = _client(monkeypatch, {"c0": [{"quote": "周砚检查接口。", "authorization_ref": ref}]})
    diagnostics = {}
    kwargs = dict(node={"story_beat": {"summary": "周砚检查接口。"}}, session=session,
        actor_performance={"scene_narration": "周砚检查接口。"}, player_input="我检查同一接口。",
        accepted_arrangement="你检查接口，我看资料。", diagnostics=diagnostics)
    reviewer = ev.NumericV2MetricEvaluator(object())
    if ref in {"p0", "p1", "t0"}:
        assert await reviewer.verify_contract_boundaries(**kwargs) == () and len(calls) == 1
        original = next(row for row in calls[0]["player_authorization_sources"] if row["id"] == ref)
        assert diagnostics["player_authority"]["actions"][0]["authorization_quote"] == original["text"]
    else:
        with pytest.raises(ev.NumericV2EvaluatorOutputError):
            await reviewer.verify_contract_boundaries(**kwargs)
        assert len(calls) == 2


@pytest.mark.asyncio
async def test_one_authorized_act_does_not_cover_another_ungranted_act_in_the_same_block(monkeypatch):
    allowed, ungranted = "周砚拆开纸袋", "周砚检查播放接口"
    calls = _client(monkeypatch, {"c0": [
        {"quote": allowed, "authorization_ref": "p0"}, {"quote": ungranted, "authorization_ref": ""}]})
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(),
        actor_performance={"scene_narration": f"{allowed}，随后{ungranted}。"}, player_input="我拆开纸袋。")
    assert result == (ev._PLAYER_AUTHORITY_RULE,) and len(calls) == 2
    assert calls[1]["finding"]["quote"] == ungranted


@pytest.mark.parametrize("with_action", [False, True])
def test_full_block_coverage_keeps_existing_720_token_output_budget(with_action):
    report = {"author_checks": {str(i): "clear" for i in range(40)}, "violations": [],
              "player_authority": {f"c{i}": "none" for i in range(16)}}
    if with_action:
        report["player_authority"]["c0"] = [{"quote": "周砚检查接口。", "authorization_ref": "p0"}]
    assert ev.count_tokens(json.dumps(report, ensure_ascii=False)) <= 720


@pytest.mark.asyncio
@pytest.mark.parametrize("body,player", [
    ({"performance": "（拉开遮板）现在能看清了。"}, "请你展示画面。"),
    ({"scene_narration": "录音里，老人说：‘我打开了窗，等你回家。’"}, "先听录音。"),
    ({"scene_narration": "信件写着：‘我已把卷宗交给你。’"}, "先看信件。"),
    ({"performance": "我来开门，你在门外等。"}, "你先开门，我等着。"),
])
async def test_pronouns_are_scoped_to_the_actual_input_or_candidate_speaker(monkeypatch, body, player):
    calls = []

    def report(data):
        calls.append(data)
        return {row["id"]: "none" for row in data["sources"] if row["id"].startswith("c")}

    _client(monkeypatch, report)
    real_build = ev._build_contract_check_messages

    def build(**kwargs):
        messages = real_build(**kwargs)
        system = messages[0].content
        assert 'p玩家原话中的“我”指玩家、“你”指受话角色' in system
        assert 'c角色对白中的“我”指角色、“你”指玩家' in system
        assert "录音、信件等引文的人称按其内部说话人" in system
        assert "明确主语优先" in system
        return messages

    monkeypatch.setattr(ev, "_build_contract_check_messages", build)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(), actor_performance=body, player_input=player)
    assert result == () and len(calls) == 1
