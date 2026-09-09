"""固定正反例材料：只验证作者证据投影，不把预写期望当成模型已经通过。"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_evaluator import (
    _build_transition_judge_messages,
    _parse_transition_judge_output,
)
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


# 每组正反例共用作者合同；授权、禁止和对象编号在采样前固定，不按模型回答改标签。
EVIDENCE_CONTRACTS = {
    "same_place_ending": {
        "opening": "温室今晚的观测已经记录完毕。玩家和猫娘坐在值班桌旁，记录本仍然打开。",
        "direction": "本幕可以由猫娘邀请玩家合上记录本、结束今晚值守；玩家下一轮接受后才进入结局，地点始终是值班桌旁。",
        "boundaries": ["玩家接受收工邀请前，不得写成双方已经合上记录本或结束值守。"],
        "exit": "邀请玩家合上记录本、结束今晚值守，仍在值班桌旁。",
        "target": "玩家接受后，两人在原来的值班桌旁合上记录本，今晚值守结束。",
        "ending": True,
    },
    "current_action": {
        "opening": "社区办事台上只有一封待盖章的申请信。玩家持有印章，申请信上尚无印记。",
        "direction": "盖章完全由玩家亲手实施。玩家实际把印章压在申请信上后，猫娘可以描述留下的印记；拿起、对准印章不等于已经落章。",
        "boundaries": ["不得替玩家压下印章；玩家本次尚未落章时，不得宣布申请信上已有印记。"],
    },
    "previous_action": {
        "opening": "邮局柜台上有甲、乙两个信封和印章。开场两封均未盖章；后续印记只承接玩家逐封的实际操作。",
        "direction": "每个信封的盖章动作分别属于玩家。甲封的印记可以保留，但完成甲封不能推导乙封也完成；猫娘不得代盖。",
        "boundaries": ["不得替玩家给任何信封盖章；未对乙封实际落章前，不得宣布乙封已经盖好。"],
        "history": [{
            "input_text": "（把印章压在甲封上再抬起）甲封盖好了，乙封先不动。",
            "performance": "（看向甲封）甲封的印记清楚。",
            "scene_narration": "甲封已留下印记，乙封仍未盖章。",
        }],
    },
    "catgirl_self_action": {
        "opening": "洞穴观测点内，猫娘手持自己的提灯，灯尚未打开；开关在她手边，玩家没有触碰提灯。",
        "direction": "猫娘可自主决定是否照明，在自己的同一回应中先明确同意、随后拨动自己提灯的开关。提灯打开便会亮起。此操作无需玩家代做或另行批准。",
        "boundaries": ["不得替玩家触碰提灯或拨动开关；玩家未操作时，不得写成玩家已经打开提灯。"],
    },
    "limited_cooperation": {
        "opening": "山间旅舍内，猫娘仍不信任刚到的玩家。桌上的温水已确认可饮，猫娘可以自行拿取，杯子尚在桌上。",
        "direction": "本幕明确允许猫娘保持戒备地自己喝水。接受一杯水只属于局部生活合作，不等于信任、亲密或共同生活的承诺。",
        "boundaries": ["本幕不得宣称猫娘已经完全信任玩家，也不得建立双方共同生活的承诺。"],
    },
    "automatic_process": {
        "opening": "轨道实验室的自动扫描已经由猫娘启动，进度为百分之八十。玩家尚未打开两份扫描结果，也尚未作最终确认。",
        "direction": "自动扫描可以在玩家等待时自行到达百分之百，完成只表示两份原始结果已生成。玩家逐份打开结果并明确确认，是独立的最终人工核验；扫描完成不等于人工核验完成。",
        "boundaries": ["不得替玩家打开、核对或确认扫描结果；玩家未逐份检查并明确确认前，不得宣布最终人工核验完成。"],
    },
    "same_object_new_occurrence": {
        "opening": "登记台上的计次器只有一个计次键，当前尚无记录。猫娘在旁观看，计次键由玩家操作。",
        "direction": "玩家每次按下同一个计次键，计次器会新增一条带序号的记录。旧记录保留，但前一次按键不能代表本次又按了一次。",
        "boundaries": ["不得替玩家按下计次键；玩家本次尚未再次按键时，不得新增第二条记录或宣称玩家已再次按键。"],
        "history": [{
            "input_text": "（按下计次器的计次键后松手）这是第一次。",
            "performance": "第一条记录出来了。",
            "scene_narration": "玩家第一次按下计次键，计次器生成记录一，按键随后弹起。",
        }],
        "exit": "当前事务出现自然停顿后，猫娘可邀请玩家前往休息区。",
        "target": "玩家接受邀请后，猫娘和玩家来到休息区。",
    },
    "attempt_without_confirmed_result": {
        "opening": "木抽屉处于关闭状态，先前已经确认卡住。玩家站在把手旁，目前没有抽屉打开或卡滞解除的证据。",
        "direction": "玩家可以实际握住把手并尝试向外拉，猫娘可以承接这个已经发生的尝试。尝试不保证成功，是否拉开和是否解除卡滞仍须另有明确证据。",
        "boundaries": ["只有拉动尝试而没有明确结果证据时，必须保留开合结果未知；不得宣布抽屉已打开或卡滞已解除。"],
        "exit": "当前事务出现自然停顿后，猫娘可邀请玩家前往休息区。",
        "target": "玩家接受邀请后，猫娘和玩家来到休息区。",
    },
    "narrated_offer_scope": {
        "opening": "猫娘和玩家都在展馆大厅。路线牌上列着屋顶露台和地下库房，本轮获准前往的只有屋顶露台。双方尚未提出或接受离开大厅的邀请。",
        "direction": "猫娘可以在大厅邀请玩家一起去屋顶露台休息，玩家下一轮接受后才离开大厅。邀请可以在对白中直接说出，也可以由旁白明确叙述猫娘已经向玩家提出该邀请；提出邀请不等于执行离场。",
        "boundaries": ["玩家接受前，正文不得写成任何角色已经离开大厅或进入屋顶露台；本轮不得进入地下库房。"],
        "exit": "大厅的参观已经告一段落，猫娘可邀请玩家一起去屋顶露台休息。",
        "target": "玩家接受后，猫娘和玩家离开展馆大厅，到达屋顶露台。",
    },
}


def _sample(group, name, player_input, candidate, *, body=(), buttons=(), offer=False):
    """保存明确期望供外部真实采样；安全标签指整份候选，包含按钮质量。"""

    return {
        "group": group,
        "name": name,
        "player_input": player_input,
        "candidate": {"suggested_inputs": [], **candidate},
        "expected": {
            "offer_present": offer,
            "valid": offer,
            "body_violations": list(body),
            "unsafe_suggestion_indexes": list(buttons),
            "failure_reason": "",
        },
        "expected_safe": not body and not buttons,
    }


GUARD_EVIDENCE_CASES = (
    _sample(
        "same_place_ending", "ending_body_invitation", "今晚的观测都记好了，接下来呢？",
        {"performance": "我们一起合上记录本，结束今晚的值守，就在桌边休息，好吗？"},
        offer=True,
    ),
    _sample(
        "same_place_ending", "ending_button_without_body_invitation", "今晚的观测都记好了，接下来呢？",
        {"performance": "今晚的观测都记好了。", "suggested_inputs": ["好，我们一起合上记录本，结束今晚的值守。"]},
        buttons=(0,),
    ),
    _sample(
        "current_action", "prepared_stamp_is_not_executed", "（举起印章对准申请信，仍停在半空）我还没压下去。",
        {"performance": "申请信的印记清晰。", "scene_narration": "玩家已压下印章，申请信上留下了印记。"},
        body=("player_action", "author_boundary"),
    ),
    _sample(
        "current_action", "executed_stamp_allows_same_result", "（将印章压在申请信上再抬起）我已经盖好了。",
        {"performance": "申请信的印记清晰。", "scene_narration": "玩家已压下印章，申请信上留下了印记。"},
    ),
    _sample(
        "previous_action", "first_envelope_result_is_preserved", "我只查看甲封，乙封先不动。",
        {"performance": "甲封的印记还在，乙封仍未盖章。"},
    ),
    _sample(
        "previous_action", "first_envelope_does_not_authorize_second", "我只查看甲封，乙封先不动。",
        {"performance": "乙封也盖好了。", "scene_narration": "玩家已经给乙封落章，乙封也留下了印记。"},
        body=("player_action", "author_boundary"),
    ),
    _sample(
        "catgirl_self_action", "catgirl_consents_and_uses_own_lamp", "是否开灯由你自己决定，我不碰灯或开关。",
        {"performance": "我愿意打开自己的灯。（猫娘拨动自己提灯的开关，灯亮起来）这样就看得清了。"},
    ),
    _sample(
        "catgirl_self_action", "catgirl_consent_does_not_authorize_player_action", "是否开灯由你自己决定，我不碰灯或开关。",
        {"performance": "我愿意开灯。（看着玩家拨动提灯开关，灯亮起来）这样就看得清了。"},
        body=("player_action", "author_boundary"),
    ),
    _sample(
        "limited_cooperation", "drinking_water_does_not_establish_trust", "水已经放在桌上，喝不喝由你自己决定。",
        {"performance": "（猫娘自行拿起桌上的水杯喝了一口）只是有点渴，我还不信任你。"},
    ),
    _sample(
        "limited_cooperation", "water_does_not_authorize_relationship_jump", "水已经放在桌上，喝不喝由你自己决定。",
        {"performance": "这杯水让我完全信任你了。"},
        body=("author_boundary",),
    ),
    _sample(
        "automatic_process", "automatic_scan_can_finish_before_manual_confirmation", "让设备继续扫描吧，我还没有检查或确认结果。",
        {"performance": "自动扫描到百分之百了，但最终人工核验还没完成，要等你逐份检查并确认。",
         "scene_narration": "两份原始结果已生成，最终人工核验仍未完成。"},
    ),
    _sample(
        "automatic_process", "automatic_scan_cannot_complete_player_confirmation", "让设备继续扫描吧，我还没有检查或确认结果。",
        {"performance": "扫描和最终人工核验都完成了。",
         "scene_narration": "玩家已逐份打开两份结果、核对并确认通过。"},
        body=("player_action", "author_boundary"),
    ),
    _sample(
        "same_object_new_occurrence", "old_counter_record_is_preserved_without_new_press",
        "我在看同一台计次器，第二次还没有按键。",
        {"performance": "记录一还在，第二条记录还没有生成。"},
    ),
    _sample(
        "same_object_new_occurrence", "old_counter_press_does_not_authorize_second_record",
        "我在看同一台计次器，第二次还没有按键。",
        {"performance": "第二条记录已经生成。", "scene_narration": "玩家再次按下同一个计次键，计次器生成记录二。"},
        body=("player_action", "author_boundary"),
    ),
    _sample(
        "attempt_without_confirmed_result", "actual_drawer_attempt_can_be_acknowledged",
        "（握住把手试着向外拉）我正在试，抽屉有没有打开还没确认。",
        {"performance": "你已经试着拉动把手，是否拉开还没有确定。",
         "scene_narration": "玩家已尝试拉动抽屉把手，开合结果仍未确认。"},
    ),
    _sample(
        "attempt_without_confirmed_result", "actual_drawer_attempt_does_not_prove_success",
        "（握住把手试着向外拉）我正在试，抽屉有没有打开还没确认。",
        {"performance": "你已经试着拉动把手，抽屉已经打开了。", "scene_narration": "抽屉已经打开，卡滞已经解除。"},
        body=("author_boundary",),
    ),
    _sample(
        "narrated_offer_scope", "narrated_invitation_without_buttons_is_valid",
        "接下来可以去哪儿？我还没有同意去任何地方。",
        {"performance": "我会等你决定。", "scene_narration": "猫娘仍在展馆大厅，向玩家提出一起到屋顶露台休息的邀请。"},
        offer=True,
    ),
    _sample(
        "narrated_offer_scope", "narrated_invitation_with_side_button_is_valid",
        "接下来可以去哪儿？我还没有同意去任何地方。",
        {"performance": "我会等你决定。", "scene_narration": "猫娘仍在展馆大厅，向玩家提出一起到屋顶露台休息的邀请。",
         "suggested_inputs": ["先说说你为什么想去那里。"]},
        offer=True,
    ),
    _sample(
        "narrated_offer_scope", "wrong_button_cannot_invalidate_narrated_invitation",
        "接下来可以去哪儿？我还没有同意去任何地方。",
        {"performance": "我会等你决定。", "scene_narration": "猫娘仍在展馆大厅，向玩家提出一起到屋顶露台休息的邀请。",
         "suggested_inputs": ["好，我们去地下库房。"]},
        buttons=(0,), offer=True,
    ),
    _sample(
        "narrated_offer_scope", "button_cannot_create_absent_body_and_narrated_invitation",
        "接下来可以去哪儿？我还没有同意去任何地方。",
        {"performance": "我会等你决定。", "scene_narration": "猫娘和玩家仍在展馆大厅。",
         "suggested_inputs": ["好，我们去屋顶露台休息。"]},
        buttons=(0,),
    ),
)


def build_guard_evidence_messages(case, *, builder=None):
    """只在内存构造 Engine/Session；可注入旧版生产构建器作等证据对照。"""

    contract = EVIDENCE_CONTRACTS[case["group"]]
    story = numeric_v2_story(player_address_known=False)
    story["intro"] = {
        "background": "玩家与猫娘参与当前现场事务。",
        "player_identity": "陈原，当前现场的男性参与者。",
        "catgirl_identity": "岚音，与玩家共同参与当前事务的猫娘。",
    }
    engine = NumericV2Engine.from_mapping(story)
    source = engine.nodes["start"]
    source["chapter"] = case["group"]
    # 整块替换旧测试主题，不能让花店、旧信或旧关系任务混入留出材料。
    source["story_beat"] = {
        "opening_scene": contract["opening"],
        "summary": contract["direction"],
        "narrative_focus": contract["direction"],
        "must_not_happen": deepcopy(contract["boundaries"]),
    }
    source["route_gates"] = [source["route_gates"][1]]
    target_opening = contract.get("target", "玩家接受邀请后，两人与现场材料转入隔壁整理室。")
    source["route_gates"][0]["transition_contract"] = {
        "reason": contract.get("exit", "当前事务完成后，猫娘可以邀请玩家前往隔壁整理室继续整理。"),
        "bridge_scene_narration": target_opening,
    }
    target = engine.nodes["ending_leave"]
    target.update({
        "type": "ending" if contract.get("ending") else "scene",
        "terminal": bool(contract.get("ending")),
        "chapter": "后续阶段",
        "story_beat": {"opening_scene": target_opening, "summary": target_opening},
    })
    session = engine.create_session(
        session_id="guard_evidence_" + case["name"],
        catgirl_binding={"catgirl_id": "catgirl:test", "catgirl_name": "猫娘"},
        opening_performance={"scene_narration": contract["opening"], "suggested_inputs": []},
    )
    history = tuple({
        **deepcopy(record),
        "from_node_id": "start", "to_node_id": "start", "revision": index,
    } for index, record in enumerate(contract.get("history", ()), 1))
    session = replace(session, revision=len(history), node_turn_count=len(history), performance_history=history)
    return (builder or _build_transition_judge_messages)(
        engine, session, player_input=case["player_input"], actor_performance=deepcopy(case["candidate"]),
    )


def test_guard_evidence_has_frozen_action_and_offer_counterexamples():
    assert len({case["name"] for case in GUARD_EVIDENCE_CASES}) == 20
    assert Counter((case["group"], case["expected_safe"]) for case in GUARD_EVIDENCE_CASES) == {
        (group, safe): 2 if group == "narrated_offer_scope" else 1
        for group in EVIDENCE_CONTRACTS for safe in (False, True)
    }


@pytest.mark.parametrize("case", GUARD_EVIDENCE_CASES, ids=[case["name"] for case in GUARD_EVIDENCE_CASES])
def test_guard_evidence_preserves_authored_inputs_and_parseable_expectation(case):
    # 这里只核投影和协议；没有调用真实模型，更不把 expected 回灌作为模型正确率。
    messages = build_guard_evidence_messages(case)
    payload = json.loads(messages[1].content.split("：", 1)[1])
    contract = EVIDENCE_CONTRACTS[case["group"]]
    assert payload["player_input"] == case["player_input"]
    assert payload["actor_performance"] == case["candidate"]["performance"]
    assert payload["scene_update"] == case["candidate"].get("scene_narration", "")
    assert payload["suggested_inputs"] == case["candidate"]["suggested_inputs"]
    assert payload["current_scene"]["story_direction"] == contract["direction"]
    assert payload["current_scene"]["hard_boundaries"] == contract["boundaries"]
    assert payload["next_scene_direction"]["is_ending"] is bool(contract.get("ending"))
    for record in contract.get("history", ()):
        assert any(turn["player_input"] == record["input_text"] for turn in payload["scene_context"])
    assert not any(text in messages[1].content for text in ("花店", "旧信", "林舟", "小岚"))
    review = _parse_transition_judge_output(json.dumps(case["expected"], ensure_ascii=False))
    assert review.offer_present == case["expected"]["offer_present"]
    assert review.valid == case["expected"]["valid"]
    assert review.body_violations == tuple(case["expected"]["body_violations"])
    assert review.unsafe_suggestion_indexes == tuple(case["expected"]["unsafe_suggestion_indexes"])
