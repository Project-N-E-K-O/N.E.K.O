"""Continuing actors distinguish initial premises/themes from current facts without losing either."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


@pytest.fixture(params=[
    ("面包房归店主独立经营。你们是多年旧识。今晚，你像往常一样推开店门。", "雨夜的错题本"),
], ids=["bakery"])
def context(request):
    background, chapter = request.param
    story = numeric_v2_story()
    story["intro"]["background"] = background
    story["nodes"][2]["chapter"] = chapter
    story["nodes"][2]["story_beat"]["opening_scene"] = "翌日清晨，两人在约定的大厅里见面。"
    route = story["nodes"][0]["route_gates"][1]["transition_contract"]
    route["reason"] = "双方先道别，翌日清晨再到大厅见面。"
    route["bridge_scene_narration"] = "翌日清晨，两人按约到达大厅。"
    route["must_deliver"] = [route["bridge_scene_narration"]]
    return story, background, chapter


def _messages(story, *, formal=False, message=None):
    engine = NumericV2Engine.from_mapping(story)
    base = engine.create_session(session_id="initial_context", catgirl_binding=_binding(),
        opening_performance={"performance": "我们先聊聊。", "suggested_inputs": []})
    session = replace(base, revision=1, story_state={**base.story_state, "revision": 1},
        node_turn_count=2, performance_history=({"revision": 1, "from_node_id": "start", "to_node_id": "start",
            "input_text": "我在这里稍坐一会儿。", "scene_narration": "周末的午后，阳光落在桌上。",
            "performance": "（合上记录本）我听着呢。"},))
    if message is None:
        message = "先看看眼前的材料。" if not formal else "好，我们按约定见面。"
    outcome = engine.resolve_turn(session, TurnRequestV2("continue", 1, message), (),
        scene_complete=formal, natural_ending_ready=formal)
    assert (outcome.session.current_node_id != session.current_node_id) is formal
    return actor._turn_messages(engine, session, outcome, message, "温和但保留独立判断。", "测试猫娘", "哥哥")


def _data(messages):
    return prompt_payload(messages[1].content)


@pytest.mark.parametrize("formal", [False, True])
def test_continuing_initial_premise_is_relabelled_in_full_without_identity_or_history_loss(context, formal):
    story, background, chapter = context
    original = deepcopy(story)
    messages = _messages(story, formal=formal)
    data = _data(messages)
    system = messages[0].content
    if formal:
        assert data["story_context"]["initial_background"] == background
        assert "background" not in data["story_context"]
        assert data["story_context"]["player_identity"] == "哥哥，回乡整理旧屋的年轻男性。"
        assert data["acting_context"]["story_identity"] == "测试猫娘，经营花店、保留旧信的年轻女性。"
        history = json.dumps(data["recent_context"], ensure_ascii=False)
        assert data["transition"]["target_scene"]["chapter_title"] == chapter
    else:
        assert f"剧本初始前情：{background}" in data["current_scene"]
        assert "共同背景：" not in data["role"]
        assert "玩家身份：哥哥，回乡整理旧屋的年轻男性。" in data["role"]
        assert "剧本身份：测试猫娘，经营花店、保留旧信的年轻女性。" in data["role"]
        assert "与玩家的当前关系：" in data["role"]
        history = data["story_so_far"]
        assert chapter in data["next_scene"]
    assert "周末的午后，阳光落在桌上。" in history
    assert "剧本初始前情" in system and "不作为当前时地或重演动作" in system
    assert "章节标题只标示主题" in system
    assert "稳定的世界设定、身份和关系约束仍有效" in system
    assert story == original


def test_formal_explicit_future_time_contract_is_preserved_and_still_authoritative(context):
    story, _, _ = context
    messages = _messages(story, formal=True)
    transition = _data(messages)["transition"]
    assert transition["bridge_scene_narration"] == "翌日清晨，两人按约到达大厅。"
    assert transition["target_scene"]["opening_situation"] == "翌日清晨，两人在约定的大厅里见面。"
    # Existing projection keeps the identical full text in bridge_scene_narration only.
    assert transition["must_deliver"] == []
    assert "本次获准的具体开场或转场合同" in messages[0].content


@pytest.mark.parametrize("formal", [False, True])
def test_current_player_turn_stays_last_without_rewriting_quoted_speech(context, formal):
    """Focus the real packed request without changing quotes into new dialogue or facts."""
    story, _, _ = context
    message = "你刚才说‘我听着呢’，我想再问一句。（把记录本放在桌上）"
    messages = _messages(story, formal=formal, message=message)
    data = _data(messages)
    assert list(data)[-1] == "player_input"
    assert data["player_input"] == message
    prefix = messages[1].content.split("\n", 1)[0]
    assert "玩家刚结束的发言" in prefix
    assert "角色设定中的你" in prefix
    assert "suggested_inputs 是玩家看到" in prefix
    if formal:
        assert "source_performance" in prefix and "target_performance" in prefix
        assert data["transition"]["target_scene"]["opening_situation"] == story["nodes"][2]["story_beat"]["opening_scene"]
    else:
        # Entry is a past starting point; the actually committed indoor scene remains visible.
        assert "本幕首次入场时的开场设定" in data["current_scene"]
        assert "周末的午后，阳光落在桌上。" in data["story_so_far"]


@pytest.mark.parametrize("formal,restricted_node", [(False, "source"), (True, "source"), (True, "target")])
def test_cognition_gate_never_regains_hidden_background_or_identity(context, formal, restricted_node):
    story, background, _ = context
    node = story["nodes"][0 if restricted_node == "source" else 2]
    node["story_beat"]["acting_contract"] = {
        "cognition_state": "limited", "memory_state": "partial", "self_reference_mode": "system_neutral",
        "persona_scope": "style_only", "dialogue_policy": "required", "assertable_self_facts": ["我只记得眼前的事情"],
        "allowed_behaviors": [], "forbidden_behaviors": [],
    }
    messages = _messages(story, formal=formal)
    data = _data(messages)
    full = json.dumps(data, ensure_ascii=False)
    assert background not in full
    assert "回乡整理旧屋的年轻男性" not in full
    if formal:
        assert "initial_background" not in data["story_context"]
        assert "background" not in data["story_context"]
        assert "knowledge_scope" in data["story_context"]
    else:
        assert "剧本初始前情：" not in data["current_scene"]
        assert "认知范围：" in data["role"]
