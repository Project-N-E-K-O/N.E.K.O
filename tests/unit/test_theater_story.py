"""验证轻量 Story、Graph 和 Rules 的确定性边界。"""  # noqa: DOCSTRING_CJK

from pathlib import Path

import pytest

from services.theater import rules, story_graph, story_loader


@pytest.mark.asyncio
async def test_story_list_only_contains_original_json_stories():
    """故事列表只展示两份正式 JSON，不再混入代码内测试故事。"""  # noqa: DOCSTRING_CJK
    stories = await story_loader.list_stories()
    assert [story["id"] for story in stories] == [
        "always_like_you_story",
        "tape_for_tomorrow_story",
    ]
    # 正式剧本必须在玩家开演前说明双方身份，避免只靠运行时模型临场补人设。
    for public_story in stories:
        card = public_story["scenario_card"]
        assert card["brief"]
        assert card["player_role"]
        assert card["catgirl_role"]
        assert card["primary_goal"]
    # 空故事 ID 使用排序后的第一份正式故事，避免维护第二套默认 Story。
    story = await story_loader.load_story(None)
    assert story["id"] == "always_like_you_story"
    state = rules.initial_state(story, initial_node_id=story_loader.initial_node_id(story))
    rules.apply_node(story, state, story_graph.current_node(story, state))
    options = story_graph.suggestion_options(story, state)
    assert options
    assert all(option["choice_id"] and option["choice_mode"] in {"action", "dialogue"} for option in options)


@pytest.mark.asyncio
async def test_tape_story_keeps_action_and_dialogue_choices_separate():
    """正式短篇开场同时提供行动和对白，前端不再猜测选项语义。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("tape_for_tomorrow_story")
    state = rules.initial_state(story, initial_node_id=story_loader.initial_node_id(story))
    rules.apply_node(story, state, story_graph.current_node(story, state))
    modes = {option["choice_mode"] for option in story_graph.suggestion_options(story, state)}
    assert modes == {"action", "dialogue"}


@pytest.mark.asyncio
async def test_tape_story_has_bounded_world_and_reachable_ending():
    """新剧本必须保持校园记忆边界，并存在完整可达结局。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("tape_for_tomorrow_story")
    restrictions = "".join(story.get("restrictions") or [])
    assert "2013年校园记忆" in restrictions
    assert "不得引入未来科技" in restrictions
    state = rules.initial_state(story, initial_node_id=story_loader.initial_node_id(story))
    rules.apply_node(story, state, story_graph.current_node(story, state))
    selected_path = [
        "choice_ask_permission",
        "choice_enter_memory_dialogue",
        "choice_ask_why_push_away",
        "choice_go_broadcast_room",
        "choice_rewind_blank_tape",
        "choice_finish_broadcast",
        "choice_return_present",
    ]
    for choice_id in selected_path:
        option = story_graph.resolve_choice(story, state, choice_id)
        assert option, choice_id
        rules.apply_node(story, state, story_graph.node_by_id(story, option["target_node_id"]))
    ending_node = story_graph.current_node(story, state)
    ending = rules.ending_for_state(story, state, ending_node, has_outgoing=False)
    assert ending == {
        "should_offer_ending": True,
        "should_end_session": True,
        "ending_id": "ending_tape_for_tomorrow",
        "reason": "story_complete",
    }


@pytest.mark.asyncio
async def test_long_romance_story_has_twenty_plus_playable_rounds():
    """都市爱情剧本必须连续提供二十轮以上选项，并抵达作者正式结局。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("always_like_you_story")
    restrictions = "".join(story.get("restrictions") or [])
    assert "当代都市爱情" in restrictions
    assert "只有玩家与当前猫娘" in restrictions
    state = rules.initial_state(story, initial_node_id=story_loader.initial_node_id(story))
    rules.apply_node(story, state, story_graph.current_node(story, state))

    # 逐轮选择当前第一个作者选项，验证长主线不存在中途缺按钮或断边。
    rounds = 0
    while story_graph.current_node(story, state).get("node_type") != "ending":
        options = story_graph.suggestion_options(story, state)
        assert options, state["current_node_id"]
        assert all(option["choice_mode"] in {"action", "dialogue"} for option in options)
        target = story_graph.node_by_id(story, options[0]["target_node_id"])
        rules.apply_node(story, state, target)
        rounds += 1

    assert rounds == 28
    ending = rules.ending_for_state(
        story,
        state,
        story_graph.current_node(story, state),
        has_outgoing=False,
    )
    assert ending["ending_id"] == "ending_meet_again_before_evening_wind"


@pytest.mark.asyncio
async def test_loader_accepts_a_future_light_story(tmp_path: Path):
    """旧剧本删除后，作者仍可按轻量协议新增独立 Story JSON。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "future.json"
    story_path.write_text(
        '{"id":"future","title":"新故事","initial_scene_id":"setup",'
        '"scenes":[{"id":"setup","phase":"setup","title":"开场","text":"灯亮了"}],'
        '"narrative_nodes":[{"node_id":"start","belong_phase":"setup","node_type":"seed"},'
        '{"node_id":"end","belong_phase":"ending","node_type":"ending","suggestions":[{"label":"结束"}]}],'
        '"edges":[{"from_node":"start","to_node":"end"}]}',
        encoding="utf-8",
    )
    stories = await story_loader.list_stories(story_dir=tmp_path)
    assert [story["id"] for story in stories] == ["future"]


def test_scene_notes_never_become_authoritative_facts():
    """自由互动笔记只帮助对话承接，不得污染权威事实。"""  # noqa: DOCSTRING_CJK
    state = {"scene_notes": [], "narrative_facts": []}
    rules.append_scene_note(state, "我从口袋里拿出一根猫草")
    assert state["scene_notes"] == ["我从口袋里拿出一根猫草"]
    assert state["narrative_facts"] == []


@pytest.mark.asyncio
async def test_loader_rejects_unknown_edge_reference(tmp_path: Path):
    """轻量编译检查必须阻止引用不存在节点的边。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "broken.json"
    story_path.write_text(
        '{"id":"broken","title":"坏故事","initial_scene_id":"setup","scenes":[{"id":"setup","phase":"setup"}],'
        '"narrative_nodes":[{"node_id":"start","belong_phase":"setup"}],'
        '"edges":[{"from_node":"start","to_node":"missing"}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown node"):
        await story_loader.list_stories(story_dir=tmp_path)
