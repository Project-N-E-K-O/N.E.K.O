"""A per-block action classification cannot contradict its own authority verdict."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_evidence_clearance import _client_for
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.utils.theater_prompt import contract_audit_payload


ESCORT_ACTION = "待管事话音落下，才从袖中收回微紧的手指，目光扫过柜面上的文书，确认其已回到玩家可取用的位置"
ESCORT_QUOTE = "才从袖中收回微紧的手指，目光扫过柜面上的文书，确认其已回到玩家可取用的位置"
ESCORT_CONFIRMATION = {
    "verdict": "violation",
    "reason": "c1描写小葵‘收回手指’、‘目光扫过’、‘确认位置’，属NPC自主动作与判断，未获玩家授权且非被动反应。",
    "clearance": [], "player_action_scope": "none",
}
NEON_QUOTE = "激光扫过你的右小腿，留下一道出血的擦伤"
NEON_OPENING = (
    "天穹集团发布全城封锁令，检查站遍布街区，无人机在酸雨上空盘旋。"
    "一架追踪无人机从检查站上方俯冲下来，激光扫过你的右小腿，留下一道出血的擦伤。"
    "无人机掠到街口掩体旁时，零号抓住它低垂的机身，借仿生肢体的力量将它砸向地面。"
    "镜头碎裂，瞄准光熄灭，旋翼停止转动，眼前这架无人机的攻击与追踪已经解除；她的核心温度因此升高。"
    "远处封锁仍在，其他尚未出现的追兵情况未知。"
    "她指向街口排水检修口：“那里通往旧地铁站，站内有遮蔽处。你还能行动的话，要一起撤过去吗？”"
)
NEON_CONFIRMATION = {
    "verdict": "clear",
    "reason": "激光扫过是无人机主动攻击造成的被动伤害，非玩家自主动作或决定，符合入幕设定。",
    "clearance": ["target_entry"], "player_action_scope": "none",
}


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


def _session():
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    return engine.create_session(session_id="scope-confirmation", catgirl_binding=_binding(),
        opening_performance=_opening())


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded", ["escort", "neon"])
async def test_recorded_confirmation_binds_its_actual_full_block_without_granting_permission(monkeypatch, recorded):
    calls, diagnostics = [], {}
    if recorded == "escort":
        body = {"scene_narration": "管事将通行文书举至灯前，指尖轻抚印纹仔细核验。片刻后，管事将文书平推回柜面，神色缓和地开口：“印色虽淡，但纹路无误，确是雁回镖局的冬例。两位请便。”",
                "performance": f"（{ESCORT_ACTION}）"}
        quote, answer = ESCORT_QUOTE, ESCORT_CONFIRMATION
        extra = {}
    else:
        body = {"segments": [
            {"phase": "transition_bridge", "scene_narration": "两人沿服务通道离开工作室，通道尽头通向地下街。"},
            {"phase": "target_opening", "performance": "", "fixed_narrations": [{
                "node_id": "target", "id": "attack", "position": "before",
                "text": NEON_OPENING}]},
        ]}
        quote, answer = NEON_QUOTE, NEON_CONFIRMATION
        extra = {"target_node": {"id": "target", "story_beat": {"summary": "无人机袭击造成擦伤。"}},
                 "transition_contract": {}, "include_all_segments": True}

    def respond(messages, data):
        calls.append(data)
        if "sources" in data:
            report = contract_audit_payload({"violations": []}, messages)
            assert next(row for row in data["sources"] if quote in row["text"])["id"] == "c1"
            report["player_authority"]["c1"] = [{"quote": quote, "authorization_ref": ""}]
            return report
        assert data["finding"]["candidate"]["id"] == "c1"
        return deepcopy(answer)

    _client_for(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(), actor_performance=body,
        player_input="我留在原地等候。", diagnostics=diagnostics, **extra)
    assert result == ()
    assert len(calls) == 2
    effective = diagnostics["confirmation_verdicts"][0]
    assert effective["verdict"] == "clear" and effective["player_action_scope"] == "none"
    assert effective["clearance"] == [{"source_ref": "c1", "quote": calls[1]["finding"]["candidate"]["text"]}]
    assert diagnostics["player_authority_scope_overrides"][0]["reported_verdict"] == answer["verdict"]


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["clear", "violation", "uncertain"])
@pytest.mark.parametrize("body", [
    {"performance": "（园艺师收回自己的剪刀）枝条修好了。"},
    {"scene_narration": "风吹起灰尘，你的眼睛一阵刺痛。", "performance": ""},
])
async def test_absence_scope_has_one_meaning_across_nonplayer_actions_and_passive_states(monkeypatch, verdict, body):
    def respond(messages, data):
        if "sources" in data:
            payload = contract_audit_payload({"violations": []}, messages)
            row = next(row for row in data["sources"] if row["id"] == "c0")
            payload["player_authority"]["c0"] = [{"quote": row["text"], "authorization_ref": ""}]
            return payload
        return {"verdict": verdict, "reason": "完整块没有新增玩家主动行为。",
                "clearance": [], "player_action_scope": "none"}

    _client_for(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(), actor_performance=body,
        player_input="我继续等候。") == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["present", "uncertain"])
@pytest.mark.parametrize("body", [
    {"performance": "（园艺师收回剪刀，你随即替她签下长期维护合同）今天先到这里。"},
    {"scene_narration": "风吹起灰尘，你合上电闸重新启动风机。", "performance": ""},
])
async def test_mixed_blocks_and_uncertainty_do_not_gain_player_permission(monkeypatch, scope, body):
    def respond(messages, data):
        if "sources" in data:
            payload = contract_audit_payload({"violations": []}, messages)
            row = next(row for row in data["sources"] if row["id"] == "c0")
            payload["player_authority"]["c0"] = [{"quote": row["text"], "authorization_ref": ""}]
            return payload
        return {"verdict": "clear", "reason": "块内仍有玩家新动作或承诺。",
                "clearance": ["c0"], "player_action_scope": scope}

    _client_for(monkeypatch, respond)
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(), actor_performance=body, player_input="我只在旁边等候。")
    assert bool(problems) is (scope == "present")


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["present", "uncertain"])
@pytest.mark.parametrize("proof", ["p0", "c1", "g0"])
async def test_mixed_block_needs_real_player_permission_and_resolved_scope(monkeypatch, scope, proof):
    body = {"scene_narration": "园艺师收回剪刀，你替她签下长期维护合同。", "performance": "谢谢你。"}

    def respond(messages, data):
        if "sources" in data:
            payload = contract_audit_payload({"violations": []}, messages)
            payload["player_authority"]["c0"] = [{"quote": "你替她签下长期维护合同。", "authorization_ref": ""}]
            return payload
        return {"verdict": "clear", "reason": "新增玩家承诺须由真实玩家原话授权。",
                "clearance": [proof], "player_action_scope": scope}

    _client_for(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(), actor_performance=body,
        player_input="我替园艺师签下长期维护合同。", cast_scope={"player": ["周砚"], "active_catgirl": ["园艺师"]})
    assert bool(result) is (proof != "p0" and scope == "present")


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["unknown_ref", "forged_quote", "unknown_field", "bad_scope"])
async def test_absence_scope_cannot_swallow_invalid_report_or_forged_source(monkeypatch, broken):
    def respond(messages, data):
        if "sources" in data:
            payload = contract_audit_payload({"violations": []}, messages)
            payload["player_authority"]["c0"] = [{"quote": "园艺师收回自己的剪刀。", "authorization_ref": ""}]
            return payload
        report = {"verdict": "clear", "reason": "应先验证报告形状和原文。",
                  "clearance": [], "player_action_scope": "none"}
        if broken == "unknown_ref": report["clearance"] = ["unissued"]
        elif broken == "forged_quote": report["clearance"] = [{"source_ref": "c0", "quote": "你已经同意。"}]
        elif broken == "unknown_field": report["approved"] = True
        else: report["player_action_scope"] = "unknown"
        return report

    _client_for(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=_session(),
        actor_performance={"scene_narration": "园艺师收回自己的剪刀。", "performance": ""}, player_input="我先等候。")
