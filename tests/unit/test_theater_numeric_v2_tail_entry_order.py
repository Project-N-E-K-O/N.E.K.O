"""Reviewed-source continuations retain the normal fixed-entry playback order."""

from copy import deepcopy
from dataclasses import replace

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_source_delivery_routing import _engine, KEY
from tests.utils.theater_prompt import prompt_payload


ENTRY = "设备回传了完整记录，检验过程已经结束。"
PRIVATE = "条件触发后才揭示的私密原文。"
ORDER = "入幕原文在场景建立后、你开口前展示，可承接反应"
AFTER = "角色正文从原文结束后的状态回应，不再次宣布或执行已经交付的事件"


@pytest.mark.parametrize("mode", ["entry", "source_and_entry", "none", "condition", "displayed", "entry_only"])
def test_tail_uses_pending_entry_order_without_leaking_conditional_text_or_source_generation(mode):
    engine = _engine()
    target = engine.nodes["work"]
    if mode == "source_and_entry":
        engine.nodes["start"]["story_beat"]["fixed_narrations"] = [{
            "id": "source-condition", "text": PRIVATE, "trigger": {"type": "condition", "condition": "另一次查验结束"},
            "after": [], "required_before_exit": False,
        }]
    if mode != "none":
        target["story_beat"]["fixed_narrations"] = [{
            "id": "entry", "text": PRIVATE if mode == "condition" else ENTRY,
            "trigger": {"type": "condition", "condition": "检验完成"} if mode == "condition" else {"type": "entry"},
            "after": [], "required_before_exit": False,
        }]
    session = engine.create_session(session_id="tail-entry", catgirl_binding=_binding(),
        opening_performance={"performance": "先商量方案。", "suggested_inputs": []})
    session = replace(session, node_turn_count=10)
    if mode == "displayed":
        session = replace(session, opening_performance={**session.opening_performance, "fixed_narrations": [{
            "node_id": "work", "id": "entry", "text": ENTRY, "position": "before", "bindings": {},
        }]})
    outcome = engine.resolve_turn(session, TurnRequestV2("tail", session.revision, "按方案继续。"), (),
        fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},))
    assert outcome.session.current_node_id == "work"
    original = deepcopy((target, session.to_dict()))
    messages = actor._turn_messages(engine, session, outcome, "按方案继续。", "温和。", "小岚", "你",
        fixed_entry_only=mode == "entry_only", transition_source={"performance": "方案已经确认。"})
    system = messages[0].content
    wire = "\n".join(m.content for m in messages)
    if mode in {"entry", "source_and_entry", "condition"}:
        if mode == "condition":
            assert system.count(ORDER) == 1 and system.count(AFTER) == 1
            assert "场景旁白只建立原文之前的地点与氛围" in system
        else:
            assert "按桥段、目标旁白、target_entry_trigger、程序入幕原文、目标表演的顺序播放" in system
            assert "原文尚缺且已获准、前因明确的角色或在场NPC非语言必要触发动作" in system
            assert "不替玩家行动、发言或补新授权" in system
        if mode != "condition":
            assert ENTRY not in system
            delivery = prompt_payload(messages[1].content)["transition"]["program_entry_delivery"]
            assert delivery["status"] == "scheduled_this_draft"
            assert [piece["text"] for piece in delivery["fixed_narrations"]] == [ENTRY]
    else:
        assert ORDER not in system and AFTER not in system
    assert PRIVATE not in wire
    assert "source_scene_narration:string" not in system
    assert "source_performance只回应玩家尚未得到回应" not in system
    assert "不重新生成、改写或重复其中的对白和动作" in system
    if mode == "entry_only":
        assert ENTRY not in wire
        assert "两个target字段必须为空字符串" in system
    assert (target, session.to_dict()) == original


def test_full_fixed_entry_transition_keeps_its_complete_contract_without_tail_rules():
    system = actor._system_prompt(catgirl_name="小岚", phase="transition_compact", entry_narration=True)
    assert "换场按来源回应、桥段、目标旁白、程序入幕原文、目标表演的顺序播放" in system
    assert "原文已经交付的动作、结果、问题和邀请不再由模型复述或换词重演" in system
    assert ORDER not in system and AFTER not in system
    assert "source_scene_narration只交付本轮在场NPC" in system
    assert "来源回应已审核，保持原文" not in system
