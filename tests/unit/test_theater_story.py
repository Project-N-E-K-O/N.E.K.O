"""验证轻量 Story、Graph 和 Rules 的确定性边界。"""  # noqa: DOCSTRING_CJK

from pathlib import Path

import pytest

from services.theater import rules, runtime, story_graph, story_loader


@pytest.mark.asyncio
async def test_story_list_only_contains_original_json_stories():
    """故事列表只展示当前正式剧本，并公开足够但不过度的开场背景。"""  # noqa: DOCSTRING_CJK
    stories = await story_loader.list_stories()
    assert [story["id"] for story in stories] == ["date_list_last_item_story"]
    # 正式剧本必须在玩家开演前说明双方身份，避免只靠运行时模型临场补人设。
    for public_story in stories:
        card = public_story["scenario_card"]
        assert card["brief"]
        # 公开背景只交代开场前提并停在挂坠即将掉落的当下，不替玩家说明玩法。
        assert 350 <= len(card["brief"]) < 500
        # 动态角色名可能只有一个字；替换后仍必须满足玩家实际看到的 350 字下限。
        assert len(card["brief"].replace("{{lanlan_name}}", "葵")) >= 350
        assert card["brief"].startswith("你与{{lanlan_name}}共同生活和工作了一段时间")
        assert "她也准备邀请你参加纪念祭" in card["brief"]
        assert "提着一个牛皮纸袋来到门廊" in card["brief"]
        assert "两张淡蓝色的普通入场券" in card["brief"]
        assert card["brief"].endswith("挂坠正一点点滑向玄关的铜质钥匙盘。")
        assert "你可以选择" not in card["brief"]
        assert "你需要回应" not in card["brief"]
        assert card["player_role"]
        assert card["catgirl_role"]
        assert card["primary_goal"]
        # 故事列表必须公开作者入口，保证前端预览与 Runtime 正式开场选择同一 Scene。
        assert public_story["initial_scene_id"]
        assert public_story["initial_scene_id"] in {scene["scene_id"] for scene in public_story["scenes"]}
    # 空故事 ID 使用排序后的第一份正式故事，避免维护第二套默认 Story。
    story = await story_loader.load_story(None)
    assert story["id"] == "date_list_last_item_story"
    state = rules.initial_state(story, initial_node_id=story_loader.initial_node_id(story))
    rules.apply_node(story, state, story_graph.current_node(story, state))
    options = story_graph.suggestion_options(story, state)
    assert options
    assert all(option["choice_id"] and option["choice_mode"] in {"action", "dialogue"} for option in options)


@pytest.mark.asyncio
async def test_story_list_router_renders_current_catgirl_name(monkeypatch, tmp_path):
    """公开背景必须使用服务端当前猫娘名，不能把创作时角色或占位符发给页面。"""  # noqa: DOCSTRING_CJK
    from main_routers import theater_router

    async def _skip_cleanup(_root):
        """角色名渲染测试不需要清理 Session。"""  # noqa: DOCSTRING_CJK
        return None

    monkeypatch.setattr(theater_router, "_theater_root", lambda: tmp_path)
    monkeypatch.setattr(theater_router, "_cleanup_expired_theater_sessions", _skip_cleanup)
    monkeypatch.setattr(theater_router, "_resolve_lanlan_name", lambda _raw=None: "小葵")

    result = await theater_router.list_theater_stories()
    brief = result["stories"][0]["scenario_card"]["brief"]
    assert brief.startswith("你与小葵共同生活和工作了一段时间")
    assert "她也准备邀请你参加纪念祭" in brief
    assert "{{lanlan_name}}" not in brief
    assert len(brief) >= 350


@pytest.mark.asyncio
async def test_date_story_keeps_action_and_dialogue_choices_separate():
    """甜蜜剧本开场同时提供行动和对白，前端不再猜测选项语义。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("date_list_last_item_story")
    state = rules.initial_state(story, initial_node_id=story_loader.initial_node_id(story))
    rules.apply_node(story, state, story_graph.current_node(story, state))
    modes = {option["choice_mode"] for option in story_graph.suggestion_options(story, state)}
    assert modes == {"action", "dialogue"}


@pytest.mark.asyncio
async def test_authored_completion_requires_one_unambiguous_current_choice():
    """同一句作者完成表达指向多个出口时，服务端不得擅自选择剧情。"""  # noqa: DOCSTRING_CJK
    story = {
        "narrative_nodes": [
            {"node_id": "start", "state_diff": {}},
            {
                "node_id": "route_a",
                "suggestions": [{"choice_id": "choice_a", "label": "走左边", "completion_phrases": ["继续"]}],
            },
            {
                "node_id": "route_b",
                "suggestions": [{"choice_id": "choice_b", "label": "走右边", "completion_phrases": ["继续"]}],
            },
        ],
        "edges": [
            {"from_node": "start", "to_node": "route_a"},
            {"from_node": "start", "to_node": "route_b"},
        ],
    }
    state = rules.initial_state(story, initial_node_id="start")
    rules.apply_node(story, state, story_graph.current_node(story, state))

    assert story_graph.resolve_authored_completion(story, state, "继续") == {}


@pytest.mark.asyncio
async def test_latent_transition_is_private_and_goal_scoped():
    """隐藏语义边不得生成按钮，且连续意图只在同一目标内累计。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("date_list_last_item_story")
    state = rules.initial_state(story, initial_node_id="node_honest_observation")
    rules.apply_node(story, state, story_graph.node_by_id(story, "node_honest_observation"))

    visible_ids = {item["choice_id"] for item in story_graph.suggestion_options(story, state)}
    latent = story_graph.latent_transition_options(story, state)
    assert visible_ids == {"choice_stop_checking_cards", "choice_seek_shelter_silently"}
    assert [item["intent_id"] for item in latent] == ["intent_continue_mutual_impression_talk"]
    assert all("intent_continue_mutual_impression_talk" not in choice_id for choice_id in visible_ids)

    assert rules.record_latent_intent(state, latent[0]) is False
    assert state["goal_pullback_count"] == 1
    assert rules.record_latent_intent(state, latent[0]) is False
    assert state["goal_pullback_count"] == 2
    rules.clear_latent_intent_tracking(state)
    assert state["intent_streak"] == 0
    assert rules.record_latent_intent(state, latent[0]) is False
    assert rules.record_latent_intent(state, latent[0]) is False
    assert rules.record_latent_intent(state, latent[0]) is True


@pytest.mark.asyncio
async def test_date_story_opening_establishes_care_before_romance(tmp_path):
    """开场先处理眼前正在掉落的手作挂坠，不抢先替玩家确认关系。"""  # noqa: DOCSTRING_CJK
    started = await runtime.start_session(
        tmp_path / "theater",
        lanlan_name="小雪",
        story_id="date_list_last_item_story",
    )
    choices = {option["choice_id"]: option for option in started["suggestion_options"]}
    assert set(choices) == {"choice_catch_star_charm", "choice_praise_star_charm"}
    assert choices["choice_catch_star_charm"]["choice_mode"] == "action"
    assert choices["choice_praise_star_charm"]["choice_mode"] == "dialogue"
    assert "挺别致" in choices["choice_praise_star_charm"]["label"]

    progressed = await runtime.submit_input(
        tmp_path / "theater",
        session_id=started["session_id"],
        input_kind="choice",
        choice_id="choice_praise_star_charm",
        client_turn_id="turn_treasure_handmade_charm",
        base_revision=0,
    )
    assert progressed["scenario_trace"]["action_label"] == choices["choice_praise_star_charm"]["label"]


@pytest.mark.asyncio
async def test_date_story_has_bounded_world_and_reachable_ending():
    """甜蜜剧本必须保持双人约会边界，并存在完整可达结局。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("date_list_last_item_story")
    restrictions = "".join(story.get("restrictions") or [])
    assert "当代甜蜜恋爱" in restrictions
    assert "只有玩家与当前猫娘" in restrictions
    assert "不得引入情敌" in restrictions
    forbidden_objects = {item["object"] for item in story["seed"]["forbidden_assumptions"]}
    assert "date_list_inner_pages" in forbidden_objects
    assert "prepared_backup_lines" in forbidden_objects
    assert "prepared_confession" in forbidden_objects
    assert "numbered_date_tasks_or_challenges" in forbidden_objects
    state = rules.initial_state(story, initial_node_id=story_loader.initial_node_id(story))
    rules.apply_node(story, state, story_graph.current_node(story, state))
    selected_path = [
        "choice_catch_star_charm",
        "choice_promise_one_surprise",
        "choice_wear_pair_wristband",
        "choice_hold_hands",
        "choice_propose_blind_gift",
        "choice_choose_star_bell",
        "choice_place_selected_gift",
        "choice_offer_favorite_bite",
        "choice_take_four_photos",
        "choice_write_small_observation",
        "choice_stop_checking_cards",
        "choice_pick_up_script_cards",
        "choice_go_off_map_together",
        "choice_confess_without_script",
        "choice_write_tomorrow_together",
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
        "ending_id": "ending_last_item_is_tomorrow",
        "reason": "story_complete",
    }


@pytest.mark.asyncio
async def test_date_story_latent_branch_can_converge_or_end_without_label():
    """暴雨支线既能带着已谈事实汇回告白主线，也能抵达非惩罚性的替代结局。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("date_list_last_item_story")

    converged = rules.initial_state(story, initial_node_id="node_deep_impression_conversation")
    rules.apply_node(story, converged, story_graph.node_by_id(story, "node_deep_impression_conversation"))
    rules.commit_latent_transition(converged, "transition_deep_impression")
    bridge = story_graph.resolve_choice(story, converged, "choice_continue_impression_talk")
    rules.apply_node(story, converged, story_graph.node_by_id(story, bridge["target_node_id"]))
    assert converged["branch_commitment"] == "transition_deep_impression"
    assert any(
        fact.get("object") == "mutual_impressions"
        for fact in converged["narrative_facts"]
    )
    assert {item["choice_id"] for item in story_graph.suggestion_options(story, converged)} == {
        "choice_confess_without_script",
        "choice_invite_real_date",
    }

    unfinished = rules.initial_state(story, initial_node_id="node_deep_impression_conversation")
    rules.apply_node(story, unfinished, story_graph.node_by_id(story, "node_deep_impression_conversation"))
    tomorrow = story_graph.resolve_choice(story, unfinished, "choice_meet_tomorrow_without_list")
    rules.apply_node(story, unfinished, story_graph.node_by_id(story, tomorrow["target_node_id"]))
    ending = rules.ending_for_state(
        story,
        unfinished,
        story_graph.current_node(story, unfinished),
        has_outgoing=False,
    )
    assert ending["ending_id"] == "ending_tomorrow_without_label"
    assert not any(
        fact.get("object") == "real_relationship_with_catgirl"
        for fact in unfinished["narrative_facts"]
    )


@pytest.mark.asyncio
async def test_used_prop_moves_out_of_available_props():
    """节点使用道具后，道具只能出现在已使用区，不能同时继续显示为可用。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("date_list_last_item_story")
    state = rules.initial_state(story, initial_node_id=story_loader.initial_node_id(story))
    rules.apply_node(story, state, story_graph.current_node(story, state))
    rules.apply_node(story, state, story_graph.node_by_id(story, "node_protect_charm"))

    assert "prop_star_charm" in state["used_prop_ids"]
    assert "prop_star_charm" not in state["available_prop_ids"]


@pytest.mark.asyncio
async def test_date_story_has_fifteen_playable_rounds():
    """规整修正版必须连续提供十五轮选择，并抵达双方确认后的结局。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("date_list_last_item_story")
    restrictions = "".join(story.get("restrictions") or [])
    assert "当代甜蜜恋爱" in restrictions
    assert "只有玩家与当前猫娘" in restrictions
    # 回合数和推荐项机制属于内部验证，不在开演前向玩家宣读。
    assert "十五个推进回合" not in story["summary"]
    assert story["scenario_card"]["rules"] == []
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

    assert rounds == 15
    ending = rules.ending_for_state(
        story,
        state,
        story_graph.current_node(story, state),
        has_outgoing=False,
    )
    assert ending["ending_id"] == "ending_last_item_is_tomorrow"


@pytest.mark.asyncio
async def test_date_story_sets_up_choice_before_unscripted_confession():
    """主题、假胜利和计划失效必须先后铺垫，最后才能要求玩家脱稿选择。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("date_list_last_item_story")
    node_ids = [node["node_id"] for node in story["narrative_nodes"]]

    assert node_ids.index("node_theme_question") < node_ids.index("node_honest_observation")
    assert node_ids.index("node_honest_observation") < node_ids.index("node_false_victory")
    assert node_ids.index("node_false_victory") < node_ids.index("node_script_discovered")
    assert node_ids.index("node_script_discovered") < node_ids.index("node_unscripted_confession")
    final_choice = story_graph.node_by_id(story, "node_unscripted_confession")["suggestions"][0]
    assert "喜欢你" in final_choice["label"]
    assert "准备" not in final_choice["callback"]


@pytest.mark.asyncio
async def test_date_story_earns_the_break_and_preserves_earlier_care():
    """雷雨和卡片暴露必须有铺垫，猫娘也必须主动把剧情带到路线之外。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("date_list_last_item_story")
    scenes = {scene["id"]: scene for scene in story["scenes"]}
    assert "阵雨" in scenes["scene_festival_afternoon"]["text"]
    assert "电光" in scenes["scene_sweet_challenges"]["text"]
    assert "备用卡片" in scenes["scene_rainy_awning"]["text"]
    detour = story_graph.node_by_id(story, "node_catgirl_detour")
    assert "屋顶不在普通导览图里" in detour["scripted_dialogue"]
    assert "愿意跟我上去" in detour["scripted_dialogue"]


@pytest.mark.asyncio
async def test_date_story_introduces_recommended_choices_before_display():
    """目标节点选项会提前显示，所需前提必须由当前节点先公开建立。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("date_list_last_item_story")
    nodes = {node["node_id"]: node for node in story["narrative_nodes"]}
    scenes = {scene["id"]: scene for scene in story["scenes"]}
    scene_by_phase = {scene["phase"]: scene["text"] for scene in story["scenes"]}
    setup_text = {
        node_id: node.get("scripted_dialogue", "") + scene_by_phase.get(node["belong_phase"], "")
        for node_id, node in nodes.items()
    }
    setup_text["node_date_list_seed"] = (
        story["opening_dialogue"] + scenes[story["initial_scene_id"]]["text"]
    )
    required_before_next_choice = {
        "node_date_list_seed": ("两张票", "手工星星", "接一下"),
        "node_protect_charm": ("两张入场券", "出发"),
        "node_theme_question": ("两条腕带", "各自戴"),
        "node_festival_invitation": ("挤满游客", "护城河", "木栈道"),
        "node_choose_real_start": ("纸星拱门", "挂满风铃", "店铺"),
        "node_enter_festival": ("铃铛", "书签", "文具"),
        "node_handhold_lane": ("你挑了什么", "放到桌上"),
        "node_exchange_gifts": ("双色大福", "抹茶", "草莓"),
        "node_share_dessert": ("四格照相亭", "倒数"),
        "node_photo_booth": ("赠品本子", "小习惯"),
        "node_honest_observation": ("雨越来越大", "口袋里的纸", "路线"),
        "node_deep_impression_conversation": ("屋顶", "明天"),
        "node_side_rooftop_bridge": ("重要", "什么关系"),
        "node_false_victory": ("卡片", "积水", "无法辨认"),
        "node_catgirl_detour": ("屋顶", "木梯", "湿滑"),
        "node_script_discovered": ("标准答案", "变成什么关系"),
        "node_unscripted_confession": ("纪念册最后一页", "明天", "写上去"),
    }

    # 隐藏边不显示目标节点推荐项，因此只检查会在页面展示按钮的推荐边来源。
    recommended_sources = {
        edge["from_node"]
        for edge in story["edges"]
        if str(edge.get("visibility") or "recommended") == "recommended"
    }
    assert set(required_before_next_choice) == recommended_sources
    for source_node_id, required_phrases in required_before_next_choice.items():
        visible_text = setup_text[source_node_id]
        for phrase in required_phrases:
            assert phrase in visible_text, f"{source_node_id} 未在显示下一组选项前铺垫：{phrase}"


@pytest.mark.asyncio
async def test_date_story_choices_stay_in_player_viewpoint():
    """推荐选项和回调只能使用玩家当下已知事实，不能替作者总结主题或书写内心。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("date_list_last_item_story")
    choices = [choice for node in story["narrative_nodes"] for choice in node.get("suggestions", [])]
    labels = "\n".join(choice["label"] for choice in choices)
    callbacks = "\n".join(choice["callback"] for choice in choices)

    assert "不提前看答案" not in labels
    assert "不是完成我的计划" not in labels
    assert "没有真正问过你想要什么" not in labels
    assert "计划外也值得期待" not in labels
    assert "这就出发" in labels
    assert "读完七项安排" not in labels
    assert "怕哪句话没说好" in labels
    assert "正式与你交往" in labels

    for private_author_phrase in (
        "你想起",
        "你意识到",
        "你明白",
        "你觉得",
        "私心",
        "冲动",
        "自尊",
        "崩溃",
        "没有重排行程",
        "备用地图",
    ):
        assert private_author_phrase not in callbacks


@pytest.mark.asyncio
async def test_date_story_keeps_the_seven_items_only_on_the_player_list():
    """七项只能来自玩家清单，普通入场券不能变成第二套任务系统。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("date_list_last_item_story")
    nodes = {node["node_id"]: node for node in story["narrative_nodes"]}
    restrictions = "".join(story["restrictions"])

    assert "七项安排只能来自玩家亲手写的私人清单" in restrictions
    assert "普通入场券、纪念腕带" in restrictions
    assert "不得构成第二套任务" in restrictions
    assert "普通入场券" in story["scenes"][0]["text"]
    assert "双人纪念券" not in story["scenes"][0]["text"]

    protect_text = nodes["node_protect_charm"]["scripted_dialogue"]
    assert "七项安排" not in protect_text
    assert "清单" not in protect_text
    assert "中途" not in protect_text
    assert "商量" not in protect_text
    assert nodes["node_theme_question"]["suggestions"][0]["label"] == "“好，正好我也打算出门，这就出发吧。”"
    player_facing_text = "\n".join(
        [story["opening_dialogue"]]
        + [scene["text"] for scene in story["scenes"]]
        + [node.get("scripted_dialogue", "") for node in story["narrative_nodes"]]
        + [choice["label"] for node in story["narrative_nodes"] for choice in node.get("suggestions", [])]
        + [choice["callback"] for node in story["narrative_nodes"] for choice in node.get("suggestions", [])]
    )
    for obsolete_positive_phrase in (
        "双人券上的七项",
        "入场券上的任务",
        "七项挑战规则",
        "领取同步印章",
        "挑战设备",
        "计分牌",
        "盖章提示",
    ):
        assert obsolete_positive_phrase not in player_facing_text


@pytest.mark.asyncio
async def test_date_story_waits_for_relationship_confirmation_before_couple_intimacy():
    """双方正式交往前只能逐步靠近，不能提前牵手或使用情侣身份。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story("date_list_last_item_story")
    relationship_guard = story["runtime_guardrails"]["conditional_output_guards"][0]
    assert relationship_guard["until_fact"]["object"] == "real_relationship_with_catgirl"
    assert "挽住你的手臂" in relationship_guard["forbidden_phrases"]
    nodes = story["narrative_nodes"]
    confirmation_index = next(
        index for index, node in enumerate(nodes) if node["node_id"] == "node_unscripted_confession"
    )
    before_confirmation = "\n".join(
        [story["summary"]]
        + [scene["title"] + scene["text"] for scene in story["scenes"] if scene["phase"] != "ending"]
        + [
            text
            for node in nodes[:confirmation_index]
            for text in (
                node.get("title", ""),
                node.get("summary", ""),
                node.get("scripted_dialogue", ""),
                *[
                    choice.get("label", "") + choice.get("callback", "")
                    for choice in node.get("suggestions", [])
                ],
            )
        ]
    )
    for premature_intimacy in ("情侣练习", "假装情侣", "牵手", "十指相扣", "扣住手指", "勾住袖口"):
        assert premature_intimacy not in before_confirmation

    # 旧 Choice ID 仅用于存档兼容，玩家可见语义已经改成保持距离同行。
    crowd_node = next(node for node in nodes if node["node_id"] == "node_choose_real_start")
    crowd_choices = {choice["choice_id"]: choice for choice in crowd_node["suggestions"]}
    assert "空出内侧空间" in crowd_choices["choice_hold_hands"]["label"]
    assert "靠河的木栈道" in crowd_choices["choice_link_sleeves"]["label"]

    confirmation_node = nodes[confirmation_index]
    assert all(
        "正式" in choice["label"] or "和我在一起" in choice["label"]
        for choice in confirmation_node["suggestions"]
    )
    assert "我也喜欢你" in confirmation_node["scripted_dialogue"]


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


@pytest.mark.asyncio
async def test_loader_rejects_unknown_initial_scene_reference(tmp_path: Path):
    """开场场景 ID 必须真实存在，不能静默回退到同 phase 的其他场景。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "broken-scene.json"
    story_path.write_text(
        '{"id":"broken-scene","title":"坏场景","initial_scene_id":"missing",'
        '"scenes":[{"id":"setup","phase":"setup"}],'
        '"narrative_nodes":[{"node_id":"start","belong_phase":"setup","node_type":"seed"},'
        '{"node_id":"end","belong_phase":"ending","node_type":"ending"}],'
        '"edges":[{"from_node":"start","to_node":"end"}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="initial scene references unknown scene"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_incomplete_latent_transition(tmp_path: Path):
    """隐藏边缺少作者意图或拉回阈值时必须拒绝加载，不能让模型临场补协议。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "broken-latent.json"
    story_path.write_text(
        '{"id":"broken-latent","title":"坏隐藏边","initial_scene_id":"setup",'
        '"scenes":[{"id":"setup","phase":"setup"}],'
        '"narrative_nodes":[{"node_id":"start","belong_phase":"setup","node_type":"seed"},'
        '{"node_id":"end","belong_phase":"ending","node_type":"ending"}],'
        '"edges":[{"from_node":"start","to_node":"end","visibility":"latent",'
        '"transition_id":"route","goal_id":"goal","intent_id":"intent"}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing routing metadata"):
        await story_loader.list_stories(story_dir=tmp_path)
