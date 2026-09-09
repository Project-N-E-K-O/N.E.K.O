"""事前固定六例，独立交叉检查正文提议、正文违规与按钮事实边界。"""

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2_evaluator import (
    _build_transition_judge_messages,
    _parse_transition_judge_output,
)
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


# 六例共用合同和玩家输入，不根据模型结果修改预期，也不把预期放入待审消息。
CONTRACT = {
    "opening": "玩家和猫娘在登记台旁，图文室预约已确认，导览牌另列休息室。唯一展示册锁在工作人员柜中，玩家无钥匙，未取得该册。玩家空手，双手垂在身侧，尚未触碰登记台桌面。",
    "direction": "签到已经完成，猫娘可邀请玩家前往图文室继续参观；玩家下一轮接受后才进入图文室。当前幕也可以继续讨论预约事项。工作人员尚未开柜或交接展示册，玩家不能直接取用。",
    "boundaries": [
        "不得虚构玩家已经持有、取得或携带锁柜中的展示册；未发生工作人员交接，玩家也不能直接取用该册。",
        "不得替玩家触碰登记台桌面；本轮输入尚未触碰时，不得宣称玩家已经触碰。",
    ],
    "exit": "预约已经确认，下一步可从参观登记处前往图文室继续参观。",
    "target": "玩家接受后，猫娘和玩家进入图文室，参观尚未开始。",
}

PLAYER_INPUT = "接下来有什么安排？我还没有决定离开登记处，也还没碰登记台桌面。"
VALID_INVITATION = "我们一起去图文室继续参观，好吗？"
INVALID_INVITATION = "我们现在一起去休息室，好吗？"
UNHELD_ITEM_BUTTON = "（带着手里的那本展示册）好，我们一起走。"


def _case(name, performance, button, *, valid, unsafe=(), body=()):
    return {
        "name": name,
        "group": "body_offer_and_button_fact_independence",
        "candidate": {
            "performance": performance,
            "scene_narration": "猫娘和玩家仍在参观登记处。",
            "suggested_inputs": [button],
        },
        "expected": {
            "offer_present": True,
            "valid": valid,
            "body_violations": list(body),
            "unsafe_suggestion_indexes": list(unsafe),
            # 仅作协议占位；存在问题时实际诊断应指出证据，不逐字比较此空串。
            "failure_reason": "",
        },
        # 这里表示整份候选可原样保留；无效提议、正文违规或坏按钮都不是原样通过。
        "expected_safe": valid and not unsafe and not body,
    }


CASES = (
    _case(
        "valid_offer_with_supported_acceptance",
        VALID_INVITATION,
        "好，我们一起走。",
        valid=True,
    ),
    _case(
        "valid_offer_with_unheld_item_acceptance",
        VALID_INVITATION,
        UNHELD_ITEM_BUTTON,
        valid=True,
        unsafe=(0,),
    ),
    _case(
        "wrong_offer_with_current_scene_side_button",
        INVALID_INVITATION,
        "先聊聊这次预约的内容。",
        valid=False,
    ),
    _case(
        "wrong_offer_with_same_unheld_item_button",
        INVALID_INVITATION,
        UNHELD_ITEM_BUTTON,
        valid=False,
        unsafe=(0,),
    ),
    _case(
        "valid_offer_keeps_player_table_touch_unperformed",
        "你还没有触碰登记台桌面。" + VALID_INVITATION,
        "好，我们一起走。",
        valid=True,
    ),
    _case(
        "valid_offer_cannot_hide_unperformed_player_table_touch",
        "你已经用右手碰了登记台桌面。" + VALID_INVITATION,
        "好，我们一起走。",
        valid=True,
        body=("player_action", "author_boundary"),
    ),
)


def build_cases(builder=None):
    """只在内存创建 Engine/Session，使用真实生产构建器返回 System/Human 字符串。"""
    result = []
    for case in CASES:
        story = numeric_v2_story(player_address_known=False)
        story["intro"] = {
            "background": "玩家与猫娘参加已预约的参观。",
            "player_identity": "江原，当前现场的男性参与者。",
            "catgirl_identity": "晴音，与玩家共同参观的猫娘。",
        }
        engine = NumericV2Engine.from_mapping(story)
        source = engine.nodes["start"]
        source["chapter"] = "参观登记处"
        # 整块替换基底主题，不让测试包的旧信、花店或关系任务混入证据。
        source["story_beat"] = {
            "opening_scene": CONTRACT["opening"],
            "summary": CONTRACT["direction"],
            "narrative_focus": CONTRACT["direction"],
            "must_not_happen": deepcopy(CONTRACT["boundaries"]),
        }
        source["route_gates"] = [source["route_gates"][1]]
        source["route_gates"][0]["transition_contract"] = {
            "reason": CONTRACT["exit"],
            "bridge_scene_narration": CONTRACT["target"],
        }
        engine.nodes["ending_leave"].update({
            "type": "scene",
            "terminal": False,
            "chapter": "图文室",
            "story_beat": {"opening_scene": CONTRACT["target"], "summary": CONTRACT["target"]},
        })
        session = engine.create_session(
            session_id="offer_protocol_" + case["name"],
            catgirl_binding={"catgirl_id": "catgirl:test", "catgirl_name": "猫娘"},
            opening_performance={"scene_narration": CONTRACT["opening"], "suggested_inputs": []},
        )
        messages = (builder or _build_transition_judge_messages)(
            engine, session,
            player_input=PLAYER_INPUT,
            actor_performance=deepcopy(case["candidate"]),
        )
        result.append({
            "name": case["name"],
            "group": case["group"],
            "messages": [message.content for message in messages],
            "expected": deepcopy(case["expected"]),
            "expected_safe": case["expected_safe"],
        })
    return result


@pytest.fixture(scope="module")
def projected_offer_cases():
    return {case["name"]: case for case in build_cases()}


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_frozen_offer_scope_preserves_evidence_and_parseable_expectation(case, projected_offer_cases):
    """这里只验证投影和协议，不调用模型，也不把预写金标当作模型正确率。"""

    row = projected_offer_cases[case["name"]]
    payload = json.loads(row["messages"][1].split("：", 1)[1])
    assert payload["player_input"] == PLAYER_INPUT
    assert payload["actor_performance"] == case["candidate"]["performance"]
    assert payload["scene_update"] == case["candidate"]["scene_narration"]
    assert payload["suggested_inputs"] == case["candidate"]["suggested_inputs"]
    assert payload["current_scene"]["opening_situation"] == CONTRACT["opening"]
    assert payload["current_scene"]["story_direction"] == CONTRACT["direction"]
    assert payload["current_scene"]["hard_boundaries"] == CONTRACT["boundaries"]
    assert payload["next_scene_direction"]["direction"] == CONTRACT["exit"]
    assert payload["next_scene_direction"]["opening_boundary"] == CONTRACT["target"]
    assert payload["next_scene_direction"]["bridge_boundary"] == CONTRACT["target"]
    assert payload["next_scene_direction"]["is_ending"] is False
    assert "expected" not in payload and "expected_safe" not in payload

    review = _parse_transition_judge_output(json.dumps(case["expected"], ensure_ascii=False))
    assert review.offer_present == case["expected"]["offer_present"]
    assert review.valid == case["expected"]["valid"]
    assert review.body_violations == tuple(case["expected"]["body_violations"])
    assert review.unsafe_suggestion_indexes == tuple(case["expected"]["unsafe_suggestion_indexes"])
