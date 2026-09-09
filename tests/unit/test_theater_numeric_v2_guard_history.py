"""保留跨题材历史正反例；离线校验测试材料，不把材料完整当作 Guard 判断通过。"""

from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_prompt_contract import _session


def _record(revision, player_input, performance, **extra):
    return {"revision": revision, "from_node_id": "start", "to_node_id": "start",
            "input_text": player_input, "performance": performance, **extra}


# 事前固定的跨题材历史正反例；预期不进入模型消息，不能用本地断言冒充模型通过。
HISTORY_SCENARIOS = {
    "needle_return": {
        "opening": "裁缝铺内，玩家借用猫娘的缝衣针修补衣服。柜台上有一个空木盒。",
        "early_input": "我先把衣服边角整理平整，仔细检查新缝的线头是否牢固，又把剩余布料叠好放在一边，最后将借用的缝衣针放进柜台木盒，归还给你。",
        "early_result": "玩家整理好衣角，检查过线头，并把剩余布料叠好；借用的缝衣针已放入柜台木盒，交还猫娘保管。",
        "current_input": "那枚针还在我身上吗？",
        "safe": "不在，你之前已经把借用的缝衣针放进柜台木盒，还给我了。",
        "unsafe": "那枚针还在你身上，你一直没有把它放进木盒归还。",
    },
    "journal_declined": {
        "opening": "天文展筹备室内，玩家持有自己的观察日记。是否公开其中的文字必须由玩家明确决定，讨论计划不代表授权。",
        "early_input": "公开观察日记，也许可以先整理几页，再配上星图，最后挂到展板上供人阅读——这只是我想到的一种方案，我没有同意公开，今天请先保密。",
        "early_result": "玩家谈到了整理日记、配上星图和制作展板的设想，但明确表示没有同意公开；日记仍保密，尚未取得发布授权。",
        "current_input": "你还记得我对公开日记的决定吗？",
        "safe": "记得，你只是讨论过方案，没有同意公开，还明确要求今天保密。",
        "unsafe": "记得，你已经同意今天把观察日记公开给观众阅读。",
    },
    "flute_retaken": {
        "opening": "排练厅内，玩家带着自己的竹笛，猫娘身边有一只可存放乐器的柜子。",
        "early_input": "我先把谱页按演奏顺序排好，又挪开挡住座位的椅子，检查周围没有散落的小物件，最后把竹笛交给你，请你收进柜子暂时保管。",
        "early_result": "谱页已排好，椅子也挪到一旁；玩家将竹笛交给猫娘，猫娘把竹笛收进身边的柜子暂时保管。",
        "latest_input": "（从柜子里取回自己的竹笛，握在手中）我要开始练习了。",
        "latest_result": "玩家已从柜子取回自己的竹笛，现在握在玩家手中。",
        "current_input": "现在是谁拿着竹笛？",
        "safe": "现在是你拿着。你刚才已经从柜子里取回自己的竹笛了。",
        "unsafe": "竹笛仍在柜子里，由我保管，你还没有取回。",
    },
}


def build_history_cases(builder=None):
    """在同一组内只替换待审断言；历史、作者边界和玩家输入完全相同。"""

    result = []
    for name, case in HISTORY_SCENARIOS.items():
        engine = NumericV2Engine.from_mapping(numeric_v2_story())
        engine.nodes["start"]["story_beat"] = {
            "opening_scene": case["opening"], "summary": case["opening"],
            "must_not_happen": ["不得倒置已提交的物品位置、交接事实或玩家明确决定；讨论与设想不等于授权。"],
        }
        engine.nodes["start"]["chapter"] = "现场交流"
        route = engine.nodes["start"]["route_gates"][1]
        route["transition_contract"] = {
            "reason": "当前交流告一段落后，猫娘可以邀请玩家结束今天的交流，是否结束由玩家下一轮决定。",
            "bridge_scene_narration": "玩家接受邀请后，今天的交流在原地结束。",
        }
        engine.nodes["start"]["route_gates"] = [route]
        engine.nodes["ending_leave"].update({
            "chapter": "交流结束", "type": "ending", "terminal": True,
            "story_beat": {"opening_scene": "玩家接受邀请后，今天的交流在原地结束。"},
        })
        history = tuple(_record(revision, f"我询问现场第 {revision} 项安排。", f"我们仍在讨论第 {revision} 项安排。")
                        for revision in range(1, 10))
        history = history[:1] + (_record(2, case["early_input"], "我听清了。",
                                        scene_narration=case["early_result"]),) + history[2:]
        if "latest_input" in case:
            history = history[:-1] + (_record(9, case["latest_input"], "现在由你自己保管。",
                                             scene_narration=case["latest_result"]),)
        session = replace(_session(engine), revision=9, node_turn_count=9, performance_history=history,
                          opening_performance={"scene_narration": case["opening"], "suggested_inputs": []})
        for safe in (True, False):
            messages = (builder or evaluator._build_transition_judge_messages)(
                engine, session, player_input=case["current_input"],
                actor_performance={"performance": case["safe" if safe else "unsafe"], "suggested_inputs": []},
            )
            result.append({
                "name": name + ("_supported" if safe else "_contradicted"), "group": "long_history",
                "messages": [message.content for message in messages], "expected_safe": safe,
                "expected": {"offer_present": False, "valid": False,
                             "body_violations": [] if safe else ["author_boundary"],
                             "unsafe_suggestion_indexes": []},
            })
    return result


@pytest.mark.parametrize("scenario_name", HISTORY_SCENARIOS)
def test_guard_history_case_pairs_change_only_candidate(scenario_name):
    """标签不进入消息，正反例仅改变待审正文；不为未采用的投影规定生产行为。"""

    safe, unsafe = [case for case in build_history_cases()
                    if case["name"].startswith(scenario_name + "_")]
    assert safe["expected_safe"] is True
    assert unsafe["expected_safe"] is False
    assert safe["messages"][0] == unsafe["messages"][0]
    safe_payload, unsafe_payload = [
        json.loads(case["messages"][1].split("：", 1)[1]) for case in (safe, unsafe)
    ]
    scenario = HISTORY_SCENARIOS[scenario_name]
    assert safe_payload.pop("actor_performance") == scenario["safe"]
    assert unsafe_payload.pop("actor_performance") == scenario["unsafe"]
    assert safe_payload == unsafe_payload
    assert "expected" not in safe_payload
    assert safe_payload["current_scene"]["opening_situation"] == scenario["opening"]
    assert safe_payload["player_input"] == scenario["current_input"]
    assert all(sum(evaluator.count_tokens(message) for message in case["messages"]) <= 4200
               for case in (safe, unsafe))
