"""Entry originals keep a legal slot for their authorized nonverbal cause."""

from copy import deepcopy

import pytest

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_fixed_narration import add_entry
from services.theater.numeric_v2_performance import performance_content_blocks
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for


CASES = [
    ("录音室", "小岚按下播放键，录音开始。", "录音里传来老人的请求：请在关停前回应这封信。", "小岚按下录音机的播放键。", "我听到了，我们商量怎么回应吧。"),
    ("档案室", "小岚打开档案卷，完整记录展开。", "档案原文：借阅登记于上周结束，目前的保管位置是东侧库房。", "小岚打开台上的档案卷。", "这份记录写明了保管位置，我们接着核对吧。"),
]


def _case(topic, *, fixed=True, fixed_entry_only=False, tail=False):
    destination, opening, original, cause, reaction = topic
    story = story_for(destination)
    source = story["nodes"][0]
    route = source["route_gates"][1]
    target = next(node for node in story["nodes"] if node["id"] == route["target_node_id"])
    target["story_beat"].update(opening_scene=opening, summary=opening)
    if fixed:
        target["story_beat"]["fixed_narrations"] = [{
            "id": "entry_original", "text": original, "trigger": {"type": "entry"},
            "after": [], "required_before_exit": True,
        }]
    if fixed_entry_only:
        route["transition_contract"]["fixed_entry_only"] = True
        target["story_beat"]["acting_contract"] = {
            "dialogue_policy": "optional", "cognition_state": "normal", "memory_state": "available",
            "self_reference_mode": "persona_allowed", "persona_scope": "full",
            "allowed_behaviors": [], "forbidden_behaviors": [],
        }
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="entry-trigger-order", catgirl_binding=_binding(), opening_performance=_opening())
    message = f"好，我们去{destination}。"
    outcome = engine.resolve_turn(session, TurnRequestV2("enter", 0, message), (), transition_intent="initiate")
    assert outcome.route is not None and outcome.session.status == "active"
    kwargs = {"transition_source": {"performance": "（点头）那就过去。"}} if tail else {}
    messages = _turn_messages(engine, session, outcome, message, "沉着。", "小岚", "林舟",
                              fixed_entry_only=fixed_entry_only, **kwargs)
    return engine, session, outcome, messages


@pytest.mark.parametrize("topic", CASES)
@pytest.mark.parametrize("tail", [False, True])
def test_native_formal_prompt_allows_only_authorized_nonverbal_entry_cause(topic, tail):
    _, _, _, messages = _case(topic, tail=tail)
    system = messages[0].content
    assert system.count("入幕前因规则：") == 1
    assert "原文尚缺且已获准、前因明确的角色或在场NPC非语言必要触发动作" in system
    assert "用明确主体在target_entry_trigger交付一次" in system
    assert "不替玩家行动、发言或补新授权" in system
    assert "角色对白仍在target_performance" in system
    assert "除入幕前因规则允许的非语言必要触发动作外，你自身动作" in system
    assert "目标旁白仅建立程序原文之前的环境" not in system
    assert "场景旁白只建立原文之前的地点与氛围" not in system
    assert "环境/NPC与自身表演分别写目标旁白和target_performance" not in system


@pytest.mark.parametrize("topic", CASES)
def test_existing_renderer_delivers_cause_then_exact_original_then_reaction(topic):
    engine, session, outcome, _ = _case(topic)
    _, _, original, cause, reaction = topic
    draft = {"source_performance": "", "bridge_scene_narration": "", "target_scene_narration": cause,
             "target_performance": reaction, "suggested_inputs": []}
    before = deepcopy(draft)
    candidate = engine.finalize_transition_performance(outcome, draft, target_opening=cause,
                                                     source_dialogue_policy="optional")
    target = engine.nodes[outcome.session.current_node_id]
    candidate["segments"][2] = add_entry(target, candidate["segments"][2], _binding(), True, session=session)
    blocks = performance_content_blocks(candidate)
    assert [block["text"] for block in blocks] == [cause, original, reaction]
    assert blocks[0]["type"] == blocks[1]["type"] == "narration"
    assert blocks[2]["type"] == "dialogue"
    assert candidate["segments"][2]["fixed_narrations"][0]["text"] == original
    assert original not in draft["target_scene_narration"] + draft["target_performance"]
    assert candidate["segments"][2]["fixed_narrations"][0]["position"] == "before"
    assert draft == before


@pytest.mark.parametrize("topic", CASES)
@pytest.mark.parametrize("kind", ["without_fixed", "fixed_entry_only"])
def test_non_applicable_formal_paths_keep_the_original_ownership_rule(topic, kind):
    _, _, _, messages = _case(topic, fixed=kind != "without_fixed", fixed_entry_only=kind == "fixed_entry_only")
    system = messages[0].content
    assert "除入幕前因规则允许的非语言必要触发动作外" not in system
    assert "原文尚缺且已获准、前因明确" not in system
    assert "你自身动作、身体状态变化及其确定的直接可见结果，只在 performance 中按发生顺序交付" in system
