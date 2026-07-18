"""验证轻量 Story、Graph 和 Rules 的确定性边界。"""  # noqa: DOCSTRING_CJK

from copy import deepcopy
from pathlib import Path

import pytest

from services.theater import projector, rules, runtime, story_graph, story_loader
from tests.utils.theater_story_fixture import (
    THEATER_TEST_EXCHANGE_NODE_ID,
    THEATER_TEST_START_NODE_ID,
    THEATER_TEST_STORY_ID,
    THEATER_TEST_STORY_PATH,
)
from utils.file_utils import atomic_write_json_async, read_json_async


def _minimal_strict_story() -> dict:
    """构造一份字段完整的最小 Story，供单项 Loader 边界测试修改。"""  # noqa: DOCSTRING_CJK
    return {
        "id": "strict_story",
        "title": "严格剧本",
        "background": "旧教室的灯刚刚亮起。",
        "initial_scene_id": "scene_setup",
        "scenes": [
            {
                "id": "scene_setup",
                "phase": "setup",
                "title": "开场",
                "text": "两人站在开关旁。",
            },
            {
                "id": "scene_ending",
                "phase": "ending",
                "title": "落幕",
                "text": "灯光稳定下来。",
            },
        ],
        "narrative_nodes": [
            {
                "node_id": "node_seed",
                "belong_phase": "setup",
                "node_type": "seed",
            },
            {
                "node_id": "node_ending",
                "belong_phase": "ending",
                "node_type": "ending",
                "ending_id": "ending_light",
                "scripted_dialogue": "灯光已经稳定了，我们可以关掉开关了。",
                "suggestions": [
                    {
                        "choice_id": "choice_finish",
                        "choice_mode": "action",
                        "label": "关掉闪烁的开关",
                        "callback": "你关掉开关，灯光不再闪烁。",
                    }
                ],
            },
        ],
        "edges": [{"from_node": "node_seed", "to_node": "node_ending"}],
    }


def test_public_performance_renders_current_catgirl_name_placeholder():
    """Scene、作者旁白和对白都应使用当前 Session 的真实猫娘名。"""  # noqa: DOCSTRING_CJK
    story = _minimal_strict_story()
    story["scenes"][0]["text"] = "{{lanlan_name}}站在你身边。"
    state = rules.initial_state(story, initial_node_id="node_seed")
    rules.apply_node(story, state, story["narrative_nodes"][0])
    response = projector.public_response(
        session={
            "session_id": "session_role_projection",
            "story_id": story["id"],
            "lanlan_name": "糖糖",
            "story_state": state,
        },
        story=story,
        scene=story["scenes"][0],
        narration="{{lanlan_name}}注意到你的目光。",
        dialogue="我是{{lanlan_name}}。",
        trace=None,
        ending={},
        can_resume=True,
    )

    assert response["scene"]["text"] == "糖糖站在你身边。"
    assert response["narration"]["text"] == "糖糖注意到你的目光。"
    assert response["dialogue"]["text"] == "我是糖糖。"


def test_framework_runtime_does_not_special_case_test_story():
    """通用服务与 Prompt 不得嵌入测试 Story 身份或专属物件。"""  # noqa: DOCSTRING_CJK
    runtime_sources = list(Path("services/theater").glob("*.py")) + [
        Path("config/prompts/prompts_theater.py"),
        Path("main_routers/theater_router.py"),
        Path("templates/theater.html"),
        Path("static/js/theater.js"),
        Path("static/css/theater.css"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in runtime_sources)
    test_story_markers = (
        THEATER_TEST_STORY_ID,
        "node_contract_anchor",
        "choice_confirm_test_token",
        "公开测试牌",
    )
    assert all(marker not in combined for marker in test_story_markers)


@pytest.mark.asyncio
async def test_story_list_only_contains_isolated_test_story():
    """测试故事列表只公开中性夹具的背景、初始 Scene 和结构化角色卡。"""  # noqa: DOCSTRING_CJK
    stories = await story_loader.list_stories()
    assert [story["id"] for story in stories] == [THEATER_TEST_STORY_ID]
    # 选剧接口不得携带完整概要或未来 Scene；作者背景和开场动作各自只有一份真源。
    for public_story in stories:
        card = public_story["scenario_card"]
        authored = await story_loader.load_story_exact(public_story["id"])
        assert set(public_story) == {
            "id",
            "title",
            "background",
            "initial_scene",
            "scenario_card",
        }
        assert set(card) == {"player_role", "catgirl_role", "primary_goal"}
        assert public_story["background"] == authored["background"]
        assert public_story["initial_scene"] == story_loader.public_scene(
            story_loader.scene_by_id(authored, authored["initial_scene_id"])
        )
        assert "summary" not in public_story
        assert "scenes" not in public_story
        assert "initial_scene_id" not in public_story
        assert "brief" not in card
        assert "rules" not in card
        assert card["player_role"]
        assert card["catgirl_role"]
        assert card["primary_goal"]
    # 空故事 ID 使用测试目录排序后的第一份 Story，不维护第二套默认夹具。
    story = await story_loader.load_story(None)
    assert story["id"] == THEATER_TEST_STORY_ID
    state = rules.initial_state(
        story, initial_node_id=story_loader.initial_node_id(story)
    )
    rules.apply_node(story, state, story_graph.current_node(story, state))
    options = story_graph.suggestion_options(story, state)
    assert options
    assert all(
        option["choice_id"] and option["choice_mode"] in {"action", "dialogue"}
        for option in options
    )


@pytest.mark.asyncio
async def test_contract_story_fallback_path_remains_playable_without_model(tmp_path):
    """关闭模型时逐轮回放中性夹具，静态路径仍须确定性闭合。"""  # noqa: DOCSTRING_CJK
    result = await runtime.start_session(
        tmp_path / "theater",
        lanlan_name="测试猫娘",
        story_id=THEATER_TEST_STORY_ID,
    )
    session_id = result["session_id"]
    revision = 0
    snapshots: list[dict] = []
    while result["can_resume"]:
        # 快照保存玩家点击前真正可见的场景、演出与选项，防止测试只验证最终 Ending ID。
        snapshots.append(
            {
                "scene": dict(result["scene"]),
                "narration": str(result["narration"]["text"]),
                "dialogue": str(result["dialogue"]["text"]),
                "options": [dict(item) for item in result["suggestion_options"]],
            }
        )
        assert result["scene"]["scene_id"]
        assert result["dialogue"]["text"].strip()
        assert result["suggestion_options"]
        result = await runtime.submit_input(
            tmp_path / "theater",
            session_id=session_id,
            input_kind="choice",
            choice_id=result["suggestion_options"][0]["choice_id"],
            client_turn_id=f"contract_fallback_{revision}",
            base_revision=revision,
        )
        revision += 1
        assert result["ok"] is True
    assert revision == len(snapshots) == 3
    assert result["ending"]["ending_id"] == "ending_contract_complete"


@pytest.mark.asyncio
async def test_exact_story_loader_rejects_removed_user_story():
    """Session 恢复不得把缺失的用户 Story 静默替换成目录第一份剧本。"""  # noqa: DOCSTRING_CJK
    with pytest.raises(FileNotFoundError):
        await story_loader.load_story_exact("removed_user_story")


@pytest.mark.asyncio
async def test_story_list_router_is_read_only_and_keeps_authored_background():
    """读取故事列表只投影作者背景，不得在 Router 中另造背景文案。"""  # noqa: DOCSTRING_CJK
    from main_routers import theater_router

    result = await theater_router.list_theater_stories()
    authored = await story_loader.load_story_exact(result["stories"][0]["id"])
    assert result["stories"][0]["background"] == authored["background"]
    assert "brief" not in result["stories"][0]["scenario_card"]


@pytest.mark.asyncio
async def test_contract_story_keeps_action_and_dialogue_choices_separate():
    """中性测试 Story 开场同时提供行动和对白，前端不猜测选项语义。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    state = rules.initial_state(
        story, initial_node_id=story_loader.initial_node_id(story)
    )
    rules.apply_node(story, state, story_graph.current_node(story, state))
    modes = {
        option["choice_mode"] for option in story_graph.suggestion_options(story, state)
    }
    assert modes == {"action", "dialogue"}


@pytest.mark.asyncio
async def test_loader_rejects_v25_contract_with_unknown_goal(tmp_path: Path):
    """World Contract 只能引用当前 Story 声明的稳定 Narrative Goal。"""  # noqa: DOCSTRING_CJK
    payload = await read_json_async(THEATER_TEST_STORY_PATH)
    payload["world_contract"]["convergence_goal_ids"] = ["goal_missing"]
    # 临时夹具只改变待验证引用，不修改正式 Story 或复用当前工作区状态。
    await atomic_write_json_async(tmp_path / "unknown-goal.json", payload)
    with pytest.raises(ValueError, match="unknown convergence goal"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_static_node_with_unknown_completed_goal(tmp_path: Path):
    """静态节点只能完成当前 Story Package 声明的 Narrative Goal。"""  # noqa: DOCSTRING_CJK
    payload = await read_json_async(THEATER_TEST_STORY_PATH)
    target = next(
        node
        for node in payload["narrative_nodes"]
        if node["node_id"] == THEATER_TEST_EXCHANGE_NODE_ID
    )
    target["completes_goal_ids"] = ["goal_missing"]
    # 作者引用错误必须在创建玩家 Session 前失败，不能运行时静默写入未知 Goal。
    await atomic_write_json_async(tmp_path / "unknown-static-goal.json", payload)
    with pytest.raises(ValueError, match="unknown completed goal"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_recommended_edge_with_unknown_goal_binding(
    tmp_path: Path,
):
    """推荐边若声明 Goal 归属，就必须引用现有 Narrative Goal。"""  # noqa: DOCSTRING_CJK
    payload = await read_json_async(THEATER_TEST_STORY_PATH)
    recommended = next(
        edge
        for edge in payload["edges"]
        if str(edge.get("visibility") or "recommended") == "recommended"
    )
    recommended["goal_id"] = "goal_missing"
    # 未知绑定不能留给 Story Graph 猜测或当作永不完成的普通字符串。
    await atomic_write_json_async(tmp_path / "unknown-edge-goal.json", payload)
    with pytest.raises(ValueError, match="recommended edge references unknown goal"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_v25_contract_without_safe_abort_callback(tmp_path: Path):
    """允许动态支线时必须有作者中性退出文案，不能让模型临场补收束。"""  # noqa: DOCSTRING_CJK
    payload = await read_json_async(THEATER_TEST_STORY_PATH)
    payload["world_contract"]["branch_abort_policy"]["neutral_callback"] = ""
    # 空 callback 必须在 Loader 阶段失败，不能等到预算耗尽后才暴露死路。
    await atomic_write_json_async(tmp_path / "missing-abort.json", payload)
    with pytest.raises(ValueError, match="abort callback"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_v25_contract_with_invalid_branch_budget(tmp_path: Path):
    """默认支线预算不得超过作者上限，避免运行时永远无法满足合同。"""  # noqa: DOCSTRING_CJK
    payload = await read_json_async(THEATER_TEST_STORY_PATH)
    payload["world_contract"]["branch_turn_budget"]["default"] = 7
    # 预算错误属于作者配置错误，Loader 必须在任何玩家 Session 创建前阻断。
    await atomic_write_json_async(tmp_path / "invalid-budget.json", payload)
    with pytest.raises(ValueError, match="invalid branch budget"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_v25_slot_with_unapproved_fact_type(tmp_path: Path):
    """动态内容槽位不得绕过 World Contract 引入未批准的事实类型。"""  # noqa: DOCSTRING_CJK
    payload = await read_json_async(THEATER_TEST_STORY_PATH)
    payload["world_contract"]["dynamic_content_slots"][0]["allowed_fact_type"] = (
        "relationship_status"
    )
    # 槽位只能收窄顶层白名单，不能自行扩大 Planner 的创作权限。
    await atomic_write_json_async(tmp_path / "invalid-slot.json", payload)
    with pytest.raises(ValueError, match="invalid dynamic content slot"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_accepts_v25_ending_domain_with_public_evidence(tmp_path: Path):
    """Ending Domain 可引用作者结局，但必须声明可确定判断的 Goal 或事实证据。"""  # noqa: DOCSTRING_CJK
    payload = await read_json_async(THEATER_TEST_STORY_PATH)
    payload["ending_domains"] = [
        {
            "ending_domain_id": "ending_domain_gentle_pause",
            "required_goal_ids": [],
            "required_fact_types": ["observable_action"],
            "required_fact_roles": ["mutual_pause_confirmed"],
            "forbidden_fact_roles": ["unilateral_relationship_commitment"],
            "ending_id": "ending_contract_complete",
        }
    ]
    payload["world_contract"]["allowed_ending_domains"] = ["ending_domain_gentle_pause"]
    # 此夹具只验证稳定引用和证据结构，不改变正式故事允许抵达的结局域。
    await atomic_write_json_async(tmp_path / "valid-ending-domain.json", payload)
    loaded = await story_loader.list_stories(story_dir=tmp_path)
    assert [item["id"] for item in loaded] == [THEATER_TEST_STORY_ID]


@pytest.mark.asyncio
async def test_loader_rejects_v25_ending_domain_with_unknown_fact_type(tmp_path: Path):
    """Ending Domain 的完成证据必须来自 World Contract 允许的事实类型。"""  # noqa: DOCSTRING_CJK
    payload = await read_json_async(THEATER_TEST_STORY_PATH)
    payload["ending_domains"] = [
        {
            "ending_domain_id": "ending_domain_bad_evidence",
            "required_goal_ids": [],
            "required_fact_types": ["model_declared_feeling"],
            "required_fact_roles": ["mutual_pause_confirmed"],
            "forbidden_fact_roles": [],
            "ending_id": "ending_contract_complete",
        }
    ]
    payload["world_contract"]["allowed_ending_domains"] = ["ending_domain_bad_evidence"]
    # 模型解释不能通过自造 fact_type 晋升为正式结局证据。
    await atomic_write_json_async(tmp_path / "invalid-ending-domain.json", payload)
    with pytest.raises(ValueError, match="unknown evidence reference"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_authored_completion_requires_one_unambiguous_current_choice():
    """同一句作者完成表达指向多个出口时，服务端不得擅自选择剧情。"""  # noqa: DOCSTRING_CJK
    story = {
        "narrative_nodes": [
            {"node_id": "start", "state_diff": {}},
            {
                "node_id": "route_a",
                "suggestions": [
                    {
                        "choice_id": "choice_a",
                        "label": "走左边",
                        "completion_phrases": ["继续"],
                    }
                ],
            },
            {
                "node_id": "route_b",
                "suggestions": [
                    {
                        "choice_id": "choice_b",
                        "label": "走右边",
                        "completion_phrases": ["继续"],
                    }
                ],
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


def test_static_goal_completion_filters_all_author_bound_stale_entries():
    """静态完成 Goal 后，重复完成节点、推荐入口、隐藏入口和旧提交 ID 必须同时失效。"""  # noqa: DOCSTRING_CJK
    goal_id = "goal_shared_task"
    story = {
        "narrative_nodes": [
            {
                "node_id": "node_completed",
                "completes_goal_ids": [goal_id],
                "suggestions": [],
            },
            {
                "node_id": "node_repeat_completion",
                "completes_goal_ids": [goal_id],
                "suggestions": [
                    {"choice_id": "choice_repeat", "label": "重复完成公开约定"}
                ],
            },
            {
                "node_id": "node_stale_entry",
                "suggestions": [
                    {"choice_id": "choice_stale_entry", "label": "重新进入已完成目标"}
                ],
            },
            {
                "node_id": "node_latent_entry",
                "suggestions": [],
            },
            {
                "node_id": "node_fresh",
                "suggestions": [
                    {"choice_id": "choice_fresh", "label": "继续新的公开行动"}
                ],
            },
        ],
        "edges": [
            {"from_node": "node_completed", "to_node": "node_repeat_completion"},
            {
                "from_node": "node_completed",
                "to_node": "node_stale_entry",
                "goal_id": goal_id,
            },
            {
                "from_node": "node_completed",
                "to_node": "node_latent_entry",
                "visibility": "latent",
                "goal_id": goal_id,
                "transition_id": "transition_stale",
                "intent_id": "intent_stale",
                "intent_summary": "重新进入已经完成的目标",
                "intent_examples": ["再做一次"],
                "pullbacks_before_transition": 1,
            },
            {"from_node": "node_completed", "to_node": "node_fresh"},
        ],
    }
    state = rules.initial_state(story, initial_node_id="node_completed")
    rules.apply_node(story, state, story_graph.current_node(story, state))

    assert state["completed_goal_ids"] == [goal_id]
    assert [
        item["choice_id"] for item in story_graph.suggestion_options(story, state)
    ] == ["choice_fresh"]
    assert story_graph.latent_transition_options(story, state) == []
    assert story_graph.resolve_choice(story, state, "choice_repeat") == {}
    assert story_graph.resolve_choice(story, state, "choice_stale_entry") == {}
    assert (
        story_graph.resolve_choice(story, state, "choice_fresh")["target_node_id"]
        == "node_fresh"
    )


@pytest.mark.asyncio
async def test_latent_transition_is_private_and_goal_scoped():
    """隐藏语义边不得生成按钮，且连续意图只在同一目标内累计。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    story = deepcopy(story)
    story["edges"].append(
        {
            "from_node": THEATER_TEST_START_NODE_ID,
            "to_node": THEATER_TEST_EXCHANGE_NODE_ID,
            "visibility": "latent",
            "transition_id": "transition_contract_review",
            "goal_id": "goal_complete_public_exchange",
            "intent_id": "intent_review_public_exchange",
            "intent_summary": "玩家希望先复核公开交换步骤",
            "intent_examples": ["先复核一下交换步骤"],
            "pullbacks_before_transition": 2,
            "callback": "双方暂不推进静态路线，先复核公开交换步骤。",
        }
    )
    state = rules.initial_state(story, initial_node_id=THEATER_TEST_START_NODE_ID)
    rules.apply_node(story, state, story_graph.current_node(story, state))

    visible_ids = {
        item["choice_id"] for item in story_graph.suggestion_options(story, state)
    }
    latent = story_graph.latent_transition_options(story, state)
    assert visible_ids == {"choice_confirm_test_token", "choice_confirm_test_plan"}
    assert [item["intent_id"] for item in latent] == [
        "intent_review_public_exchange"
    ]
    assert all(
        "intent_review_public_exchange" not in choice_id
        for choice_id in visible_ids
    )

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
async def test_used_prop_moves_out_of_available_props():
    """节点使用道具后，道具只能出现在已使用区，不能同时继续显示为可用。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    state = rules.initial_state(
        story, initial_node_id=story_loader.initial_node_id(story)
    )
    rules.apply_node(story, state, story_graph.current_node(story, state))
    rules.apply_node(
        story,
        state,
        story_graph.node_by_id(story, THEATER_TEST_EXCHANGE_NODE_ID),
    )

    assert "prop_public_test_token" in state["used_prop_ids"]
    assert "prop_public_test_token" not in state["available_prop_ids"]


@pytest.mark.asyncio
async def test_loader_accepts_a_future_light_story(tmp_path: Path):
    """旧剧本删除后，作者仍可按轻量协议新增独立 Story JSON。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "future.json"
    story_path.write_text(
        '{"id":"future","title":"新故事","background":"一间刚刚亮灯的旧教室。","initial_scene_id":"setup",'
        '"scenes":[{"id":"setup","phase":"setup","title":"开场","text":"灯亮了"},'
        '{"id":"ending","phase":"ending","title":"落幕","text":"灯光稳定下来。"}],'
        '"narrative_nodes":[{"node_id":"start","belong_phase":"setup","node_type":"seed"},'
        '{"node_id":"end","belong_phase":"ending","node_type":"ending","ending_id":"ending_light",'
        '"scripted_dialogue":"灯光已经稳定了。",'
        '"suggestions":[{"choice_id":"choice_finish","choice_mode":"action","label":"关掉开关",'
        '"callback":"你关掉开关，确认灯光不再闪烁。"}]}],'
        '"edges":[{"from_node":"start","to_node":"end"}]}',
        encoding="utf-8",
    )
    stories = await story_loader.list_stories(story_dir=tmp_path)
    assert [story["id"] for story in stories] == ["future"]


@pytest.mark.asyncio
async def test_loader_rejects_duplicate_public_background_source(tmp_path: Path):
    """scenario_card 不得重新声明 brief 或规则，背景与私有约束必须各有唯一真源。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    payload["scenario_card"] = {
        "brief": "第二份背景",
        "player_role": "玩家",
        "catgirl_role": "猫娘",
        "primary_goal": "一起关灯",
    }
    await atomic_write_json_async(tmp_path / "duplicate-background.json", payload)

    with pytest.raises(ValueError, match="duplicates public or private content"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_field", ["choice_id", "choice_mode", "label", "callback"]
)
async def test_loader_rejects_incomplete_author_choice(
    tmp_path: Path, missing_field: str
):
    """Choice 的稳定身份、类型和 callback 缺一不可，运行时不能替作者补写。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    del payload["narrative_nodes"][1]["suggestions"][0][missing_field]
    await atomic_write_json_async(tmp_path / "incomplete-choice.json", payload)

    with pytest.raises(ValueError, match="incomplete choice"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_missing_author_dialogue(tmp_path: Path):
    """可推进节点必须提供作者对白，运行时不能让模型补写静态正文。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    del payload["narrative_nodes"][1]["scripted_dialogue"]
    await atomic_write_json_async(tmp_path / "missing-dialogue.json", payload)

    with pytest.raises(ValueError, match="missing scripted dialogue"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_unreachable_static_subgraph(tmp_path: Path):
    """全部作者节点都必须从 setup 可达，不允许孤立子图或其中的隐藏环。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    payload["narrative_nodes"].extend(
        [
            {
                "node_id": "node_orphan_a",
                "belong_phase": "setup",
                "node_type": "core",
                "scripted_dialogue": "这段孤立内容不应被加载。",
                "suggestions": [
                    {
                        "choice_id": "choice_orphan_a",
                        "choice_mode": "action",
                        "label": "进入孤立节点 A",
                        "callback": "你进入孤立节点 A。",
                    }
                ],
            },
            {
                "node_id": "node_orphan_b",
                "belong_phase": "setup",
                "node_type": "core",
                "scripted_dialogue": "孤立节点之间也不能形成环。",
                "suggestions": [
                    {
                        "choice_id": "choice_orphan_b",
                        "choice_mode": "action",
                        "label": "进入孤立节点 B",
                        "callback": "你进入孤立节点 B。",
                    }
                ],
            },
        ]
    )
    payload["edges"].extend(
        [
            {"from_node": "node_orphan_a", "to_node": "node_orphan_b"},
            {"from_node": "node_orphan_b", "to_node": "node_orphan_a"},
        ]
    )
    await atomic_write_json_async(tmp_path / "unreachable-cycle.json", payload)

    with pytest.raises(ValueError, match="unreachable static node"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_duplicate_scene_phase(tmp_path: Path):
    """每个阶段只能有一个作者 Scene，运行时不能在重复阶段中自行选第一项。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    duplicate = deepcopy(payload["scenes"][1])
    duplicate["id"] = "scene_ending_duplicate"
    payload["scenes"].append(duplicate)
    await atomic_write_json_async(tmp_path / "duplicate-phase.json", payload)

    with pytest.raises(ValueError, match="duplicate scene phase"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_duplicate_node_id(tmp_path: Path):
    """节点 ID 必须全局唯一，避免边和恢复状态指向不确定作者结果。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    payload["narrative_nodes"].append(deepcopy(payload["narrative_nodes"][1]))
    await atomic_write_json_async(tmp_path / "duplicate-node.json", payload)

    with pytest.raises(ValueError, match="invalid or duplicate node ids"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_multiple_setup_seeds(tmp_path: Path):
    """setup seed 只能有一个，框架不能替作者猜测真正入口。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    payload["narrative_nodes"].append(
        {
            "node_id": "node_second_seed",
            "belong_phase": "setup",
            "node_type": "seed",
        }
    )
    await atomic_write_json_async(tmp_path / "multiple-seeds.json", payload)

    with pytest.raises(ValueError, match="exactly one setup seed"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_duplicate_choice_id(tmp_path: Path):
    """Choice ID 必须全局唯一，不能让同一公开身份对应多个作者动作。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    suggestions = payload["narrative_nodes"][1]["suggestions"]
    suggestions.append(deepcopy(suggestions[0]))
    await atomic_write_json_async(tmp_path / "duplicate-choice.json", payload)

    with pytest.raises(ValueError, match="duplicate choice id"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_recommended_edge_without_matching_choice(tmp_path: Path):
    """推荐边必须命中目标节点的作者 Choice，框架不能拿任意按钮代替。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    payload["edges"][0]["behavior"] = "continue"
    payload["narrative_nodes"][1]["suggestions"][0]["behavior_hint"] = "stop"
    await atomic_write_json_async(tmp_path / "unmatched-edge.json", payload)

    with pytest.raises(ValueError, match="recommended edge has no matching choice"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_duplicate_visible_choice_route(tmp_path: Path):
    """同一来源的多条边不能重复投影同一个作者 Choice。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    payload["edges"].append(deepcopy(payload["edges"][0]))
    await atomic_write_json_async(tmp_path / "duplicate-route.json", payload)

    with pytest.raises(ValueError, match="duplicate one visible choice"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_reachable_static_cycle(tmp_path: Path):
    """从 setup 可达的静态图不得回访旧节点，循环语义必须由作者重新建模。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    payload["edges"].append(
        {
            "from_node": "node_ending",
            "to_node": "node_seed",
            "visibility": "latent",
            "transition_id": "transition_restart",
            "goal_id": "goal_restart",
            "intent_id": "intent_restart",
            "intent_summary": "返回开场",
            "intent_examples": ["回到最开始"],
            "pullbacks_before_transition": 0,
        }
    )
    await atomic_write_json_async(tmp_path / "static-cycle.json", payload)

    with pytest.raises(ValueError, match="static graph contains a cycle"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_node_without_matching_scene_phase(tmp_path: Path):
    """节点阶段缺 Scene 时必须在加载期失败，不能回退到 scenes 第一项。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    payload["narrative_nodes"][1]["belong_phase"] = "missing_phase"
    await atomic_write_json_async(tmp_path / "missing-phase.json", payload)

    with pytest.raises(ValueError, match="unknown scene phase"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_non_ending_dead_end(tmp_path: Path):
    """普通节点没有出口是坏图状态，不能等运行时把它伪装成正式结局。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    payload["narrative_nodes"].insert(
        1,
        {
            "node_id": "node_dead_end",
            "belong_phase": "setup",
            "node_type": "core",
            "scripted_dialogue": "前面没有出口，我们不能把这里当成结局。",
            "suggestions": [
                {
                    "choice_id": "choice_enter_dead_end",
                    "choice_mode": "action",
                    "label": "走到没有出口的门前",
                    "callback": "你走到门前，发现前方没有通路。",
                }
            ],
        },
    )
    payload["edges"] = [{"from_node": "node_seed", "to_node": "node_dead_end"}]
    await atomic_write_json_async(tmp_path / "dead-end.json", payload)

    with pytest.raises(ValueError, match="non-ending node without outgoing edge"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_loader_rejects_ending_without_author_id(tmp_path: Path):
    """正式结局必须声明 ending_id，Node ID 不能再充当框架生成的结局。"""  # noqa: DOCSTRING_CJK
    payload = _minimal_strict_story()
    del payload["narrative_nodes"][1]["ending_id"]
    await atomic_write_json_async(tmp_path / "missing-ending-id.json", payload)

    with pytest.raises(ValueError, match="ending node is missing ending_id"):
        await story_loader.list_stories(story_dir=tmp_path)


@pytest.mark.asyncio
async def test_runtime_rejects_unknown_story_instead_of_starting_default(
    tmp_path: Path,
):
    """显式错误 Story ID 必须返回稳定原因，不能开启目录第一份其他剧本。"""  # noqa: DOCSTRING_CJK
    result = await runtime.start_session(
        tmp_path / "theater",
        lanlan_name="测试猫娘",
        story_id="story_does_not_exist",
    )
    assert result == {"ok": False, "reason": "story_not_found"}


def test_runtime_graph_does_not_synthesize_missing_choice_fields():
    """绕过 Loader 的坏对象也不能在 StoryGraph 中获得框架生成按钮。"""  # noqa: DOCSTRING_CJK
    story = {
        "narrative_nodes": [
            {"node_id": "node_seed"},
            {
                "node_id": "node_target",
                "title": "框架不得拿标题生成按钮",
                "suggestions": [],
            },
        ],
        "edges": [{"from_node": "node_seed", "to_node": "node_target"}],
    }
    state = {"current_node_id": "node_seed", "completed_node_ids": []}
    assert story_graph.suggestion_options(story, state) == []


def test_non_ending_node_without_outgoing_is_not_story_ending():
    """无出口只代表坏图状态，Rules 不得生成 story_ending 或拿 Node ID 代替。"""  # noqa: DOCSTRING_CJK
    ending = rules.ending_for_state(
        {},
        {"narrative_facts": [], "clue_ids": []},
        {"node_id": "node_dead_end", "node_type": "core"},
        has_outgoing=False,
    )
    assert ending == {
        "should_offer_ending": False,
        "should_end_session": False,
        "ending_id": "",
    }


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
        '{"id":"broken","title":"坏故事","background":"测试背景。","initial_scene_id":"setup",'
        '"scenes":[{"id":"setup","phase":"setup","title":"开场","text":"灯亮了。"}],'
        '"narrative_nodes":[{"node_id":"start","belong_phase":"setup","node_type":"seed"}],'
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
        '{"id":"broken-scene","title":"坏场景","background":"测试背景。","initial_scene_id":"missing",'
        '"scenes":[{"id":"setup","phase":"setup","title":"开场","text":"灯亮了。"}],'
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
        '{"id":"broken-latent","title":"坏隐藏边","background":"测试背景。","initial_scene_id":"setup",'
        '"scenes":[{"id":"setup","phase":"setup","title":"开场","text":"灯亮了。"},'
        '{"id":"ending","phase":"ending","title":"落幕","text":"灯灭了。"}],'
        '"narrative_nodes":[{"node_id":"start","belong_phase":"setup","node_type":"seed"},'
        '{"node_id":"end","belong_phase":"ending","node_type":"ending","ending_id":"ending_test",'
        '"scripted_dialogue":"灯已经熄灭了。"}],'
        '"edges":[{"from_node":"start","to_node":"end","visibility":"latent",'
        '"transition_id":"route","goal_id":"goal","intent_id":"intent"}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing routing metadata"):
        await story_loader.list_stories(story_dir=tmp_path)
