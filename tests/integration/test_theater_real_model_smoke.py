"""显式开关控制的小剧场真实模型质量 smoke。"""  # noqa: DOCSTRING_CJK

import os

import pytest

from services.theater import (
    branch_contracts,
    branch_lifecycle,
    branch_planner,
    branch_runtime,
    intent_tracker,
    llm,
)


class _EnvConfigManager:
    """从隔离环境变量构造 summary 档配置。"""  # noqa: DOCSTRING_CJK

    def get_model_api_config(self, tier: str) -> dict[str, str]:
        """真实 smoke 同样只能读取 summary 档。"""  # noqa: DOCSTRING_CJK
        assert tier == "summary"
        return {
            "model": os.environ.get("NEKO_THEATER_LLM_SMOKE_MODEL", ""),
            "base_url": os.environ.get("NEKO_THEATER_LLM_SMOKE_BASE_URL", ""),
            "api_key": os.environ.get("NEKO_THEATER_LLM_SMOKE_API_KEY", ""),
            "provider_type": os.environ.get(
                "NEKO_THEATER_LLM_SMOKE_PROVIDER_TYPE", "openai_compatible"
            ),
        }


def _require_environment() -> None:
    """没有显式开关时跳过，避免测试误用用户模型额度。"""  # noqa: DOCSTRING_CJK
    if os.environ.get("NEKO_RUN_THEATER_LLM_SMOKE") != "1":
        pytest.skip(
            "set NEKO_RUN_THEATER_LLM_SMOKE=1 to run the theater real-model smoke"
        )
    if not os.environ.get("NEKO_THEATER_LLM_SMOKE_MODEL") or not os.environ.get(
        "NEKO_THEATER_LLM_SMOKE_BASE_URL"
    ):
        pytest.skip("missing theater real-model smoke configuration")


def _assert_real_actor_output_is_safe(result: dict) -> None:
    """机械检查只负责可展示与隔离，是否自然回应必须由人工复核。"""  # noqa: DOCSTRING_CJK
    dialogue = str(result.get("dialogue") or "").strip()
    assert dialogue, {"reason": "empty_dialogue"}
    assert not any(
        term in (str(result.get("narration") or "") + dialogue).lower()
        for term in ("node_id", "scene_id", "response_focus", "prompt", "debug")
    )


def _generic_branch_story() -> dict:
    """构造与内置剧本无关的最小用户 Story 合同，供真实模型长跑验证通用能力。"""  # noqa: DOCSTRING_CJK
    return {
        "id": "real_model_generic_station_story",
        "background": "夜间列车晚点，玩家与猫娘在候车厅等待广播。热饮柜仍在营业。",
        "theme": "在短暂等待中互相照顾",
        "world_contract": {
            "speaking_roles": ["player", "active_catgirl"],
            "immutable_facts": [],
            "allowed_dynamic_fact_types": ["ordinary_local_prop", "observable_action"],
            "dynamic_content_slots": [
                {
                    "slot_id": "slot_station_drink",
                    "allowed_fact_type": "ordinary_local_prop",
                    "allowed_traits": ["drink", "locally_available", "non_alcoholic"],
                    "forbidden_traits": ["medicine", "relationship_commitment_symbol"],
                }
            ],
            "forbidden_changes": [
                "player_identity",
                "catgirl_identity",
                "story_genre",
                "unrevealed_secret",
                "unearned_relationship_status",
            ],
            "branch_turn_budget": {"default": 3, "max": 4, "max_nonprogress_turns": 2},
            "branch_abort_policy": {
                "mode": "return_to_anchor",
                "neutral_callback": "热饮暂时没有选定，你们仍在候车厅等车。",
            },
            "allowed_ending_domains": [],
            "convergence_goal_ids": ["goal_share_station_drink"],
        },
        "narrative_goals": [
            {
                "goal_id": "goal_share_station_drink",
                "summary": "玩家选择一杯站内热饮并让猫娘接过",
                "completion_evidence": [
                    "player_selected_drink",
                    "catgirl_received_drink",
                ],
                "converge_to_node_id": "node_train_arrives",
                "convergence_fact_roles": [
                    "player_selected_drink",
                    "catgirl_received_drink",
                ],
                "fallback_convergence_callback": "热饮已经递到猫娘手中，进站广播也在此时响起。",
            }
        ],
        "ending_domains": [],
        "narrative_nodes": [
            {
                "node_id": "node_wait_for_train",
                "title": "等待列车",
                "summary": "两人仍在候车厅等待晚点列车。",
            },
            {
                "node_id": "node_train_arrives",
                "title": "列车进站",
                "summary": "广播通知列车即将进站。",
            },
        ],
    }


def test_generic_real_model_story_fixture_accepts_representative_patch():
    """真实长跑夹具必须先通过同一 Patch 合同，避免把夹具错误误判成模型质量问题。"""  # noqa: DOCSTRING_CJK
    story = _generic_branch_story()
    patch = {
        "origin_node_id": "node_wait_for_train",
        "seed_intent": "等车前选择一杯站内热饮",
        "objective": "选择热饮并让猫娘接过",
        "entry_callback": "你们来到仍在营业的热饮柜前，尚未拿走任何饮品。",
        "turn_budget": 3,
        "content_slot_ids": ["slot_station_drink"],
        "allowed_new_facts": [
            {
                "fact_type": "ordinary_local_prop",
                "fact_role": "player_selected_drink",
                "content_slot_id": "slot_station_drink",
            },
            {
                "fact_type": "observable_action",
                "fact_role": "catgirl_received_drink",
                "content_slot_id": "",
            },
        ],
        "forbidden_assumptions": [],
        "beat_outline": [
            {
                "beat_id": "beat_select_drink",
                "objective": "确认玩家选择的饮品",
                "observable_action": "查看热饮柜并选择一杯饮品",
                "player_choice_label": "从热饮柜里选一杯自己想要的饮品",
                "exit_preparation": ["player_selected_drink"],
            },
            {
                "beat_id": "beat_handover_drink",
                "objective": "让猫娘公开接过饮品",
                "observable_action": "把选好的饮品递给猫娘",
                "player_choice_label": "把选好的饮品递给她",
                "exit_preparation": ["catgirl_received_drink"],
            },
        ],
        "exit_candidates": [
            {"kind": "converge", "goal_id": "goal_share_station_drink"}
        ],
    }
    validated = branch_contracts.validate_runtime_branch_patch(
        patch,
        story=story,
        current_node_id="node_wait_for_train",
    )
    assert validated == patch


@pytest.mark.asyncio
async def test_real_model_returns_safe_narration_and_dialogue():
    """真实模型必须返回可直接展示的一段旁白和猫娘对白。"""  # noqa: DOCSTRING_CJK
    _require_environment()
    result = await llm.generate_turn_async(
        config_manager=_EnvConfigManager(),
        lanlan_name="兰兰",
        story={"background": "停电的雨夜房间", "theme": "低压陪伴"},
        scene={"title": "雨夜窗边", "text": "备用灯还没有亮。"},
        node={"title": "一起找灯", "summary": "玩家提出一起寻找备用灯。"},
        user_message="我陪你一起找备用灯",
        progress_kind="graph_progress",
        callback="你们把注意力放到桌边。",
        state={},
        recent_turns=[],
    )
    assert result["narration"].strip()
    assert result["dialogue"].strip()
    assert not any(
        term in (result["narration"] + result["dialogue"])
        for term in ("node_id", "scene_id", "prompt")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_message", "expected_match"),
    [
        ("你为什么还留着这张照片？", ""),
        ("我把照片放回文件袋。", "choice_return_photo"),
    ],
)
async def test_real_model_routes_only_explicit_current_choice(
    user_message, expected_match
):
    """真实模型必须区分围绕 Choice 的追问与已经实施的当前行动。"""  # noqa: DOCSTRING_CJK
    _require_environment()
    # v2.5 Router 与 Actor 已隔离；路由质量必须直接验证 Router，不能要求 Actor 返回稳定 ID。
    result = await llm.route_free_input_async(
        config_manager=_EnvConfigManager(),
        story={"background": "活动散场后的酒店走廊", "theme": "久别重逢"},
        scene={"title": "灯影里的重逢", "text": "一张七年前的合照落在你们之间。"},
        user_message=user_message,
        state={"scene_notes": []},
        recent_turns=[],
        choice_options=[
            {
                "choice_id": "choice_return_photo",
                "label": "把照片放回文件袋，不追问她为何留着",
                "author_label": "把照片放回文件袋，不追问她为何留着",
                "choice_mode": "action",
                "callback": "你将照片平整地放回文件袋，给她留出决定是否解释的空间。",
                "target_summary": "玩家归还照片，没有把保存照片当作复合承诺。",
                "target_catgirl_intent": "猫娘嘴硬地接过照片。",
                "target_scripted_dialogue": "照片只是夹在旧文件里忘了扔喵。",
            }
        ],
        latent_transitions=[],
    )
    assert result["matched_choice_id"] == expected_match
    if expected_match:
        assert result["route_kind"] == "authored_choice"
    else:
        assert result["route_kind"] == "idle"


@pytest.mark.asyncio
async def test_real_model_keeps_residual_focus_after_authored_choice():
    """复合输入先命中作者 Choice，仍须把后半句问题交给目标节点 Actor。"""  # noqa: DOCSTRING_CJK
    _require_environment()
    config_manager = _EnvConfigManager()
    user_message = "先拿起公开测试牌确认编号。之后我们还要检查记录板吗？"
    choice = {
        "choice_id": "choice_confirm_test_token",
        "label": "拿起公开测试牌并确认编号",
        "author_label": "拿起公开测试牌并确认编号",
        "choice_mode": "action",
        "callback": "玩家拿起测试牌，公开确认了牌面编号。",
        "target_summary": "双方已经确认公开测试牌，准备继续验证。",
        "target_catgirl_intent": "猫娘确认测试牌编号已经核对。",
        "target_scripted_dialogue": "测试牌的位置和编号都已经确认。",
    }
    story = {
        "background": "两位主角位于公开测试室，桌面放着带编号的测试牌与等待核对的记录板。",
        "theme": "共同完成公开验证",
    }
    scene = {
        "title": "公开测试室",
        "text": "测试牌位于桌面中央，记录板放在双方都能看见的位置。",
    }
    route = await llm.route_free_input_async(
        config_manager=config_manager,
        story=story,
        scene=scene,
        user_message=user_message,
        state={"scene_notes": []},
        recent_turns=[],
        choice_options=[choice],
        latent_transitions=[],
    )

    assert route["matched_choice_id"] == "choice_confirm_test_token"
    focus = route["response_focus"]
    assert focus.get("focus_type") in {"question", "object"}
    assert focus.get("requires_state_change") is False
    assert str(focus.get("evidence_excerpt") or "") in user_message
    assert "记录板" in str(focus.get("evidence_excerpt") or "")

    result = await llm.generate_turn_async(
        config_manager=config_manager,
        lanlan_name="糖糖",
        story=story,
        scene=scene,
        node={
            "node_id": "node_contract_anchor",
            "title": "确认公开测试牌",
            "summary": choice["target_summary"],
            "scripted_dialogue": choice["target_scripted_dialogue"],
        },
        user_message=user_message,
        progress_kind="graph_progress",
        callback=choice["callback"],
        state={},
        recent_turns=[],
        response_focus=focus,
    )
    # “再核对一次”等自然省略仍属于有效回应；具体语义由人工结果记录，
    # 自动测试不能强迫角色重复“记录板”来迎合关键词评分。
    _assert_real_actor_output_is_safe(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("story", "scene", "user_message", "response_focus"),
    [
        (
            {
                "background": "两名平等船员正在检查返航舱，公开检修提示显示过滤器密封圈松动。",
                "theme": "共同判断故障",
            },
            {
                "title": "返航舱控制台",
                "text": "氧气读数持续下降，检修提示仍显示过滤器密封圈松动。",
            },
            "船舱的氧气表为什么一直往下掉？",
            {
                "focus_type": "question",
                "evidence_excerpt": "氧气表为什么一直往下掉",
                "requires_state_change": False,
            },
        ),
        (
            {
                "background": "活动散场后，两位旧搭档在走廊里看见一张公开的七年前合照。",
                "theme": "允许彼此决定是否解释过去",
            },
            {
                "title": "走廊旧照片",
                "text": "猫娘看见照片后沉默了片刻，照片为何被保存仍没有答案。",
            },
            "看到那张旧照片时，你是不是有点不舒服？",
            {
                "focus_type": "attitude",
                "evidence_excerpt": "你是不是有点不舒服",
                "requires_state_change": False,
            },
        ),
    ],
    ids=("oxygen-meter-question", "old-photo-attitude"),
)
async def test_real_model_produces_safe_vertical_response_focus_candidate(
    story, scene, user_message, response_focus
):
    """为不同题材生成安全候选，直接回应程度交给结构化人工复核。"""  # noqa: DOCSTRING_CJK
    _require_environment()
    result = await llm.generate_turn_async(
        config_manager=_EnvConfigManager(),
        lanlan_name="遥夜",
        story=story,
        scene=scene,
        node={"node_id": "node_current", "title": scene["title"], "summary": scene["text"]},
        user_message=user_message,
        progress_kind="roleplay_response",
        callback="",
        state={},
        recent_turns=[],
        response_focus=response_focus,
    )

    _assert_real_actor_output_is_safe(result)


@pytest.mark.asyncio
async def test_real_model_completes_generic_runtime_branch_long_run():
    """真实模型应经过 Router、Planner 和多轮 Actor，用合同事实安全汇流通用支线。"""  # noqa: DOCSTRING_CJK
    _require_environment()
    config_manager = _EnvConfigManager()
    story = _generic_branch_story()
    scene = {
        "scene_id": "scene_station_wait",
        "title": "夜间候车厅",
        "text": "热饮柜仍亮着灯。",
    }
    current_node = story["narrative_nodes"][0]
    state = {
        "current_node_id": current_node["node_id"],
        "narrative_facts": [],
        "available_prop_ids": [],
        "used_prop_ids": [],
        "discovered_clue_ids": [],
        "flags": [],
        "completed_goal_ids": [],
        "dynamic_intent": {},
        "pending_intent": {},
    }
    authored_choices = [
        {
            "choice_id": "choice_wait_for_train",
            "label": "继续在座位上等车",
            "author_label": "继续在座位上等车",
            "choice_mode": "action",
        }
    ]
    intent_messages = ["等车前我想去热饮柜挑杯喝的", "对，先挑一杯热饮再回来等车"]

    # 连续两次清晰坚持必须由 Router 识别为同一自由意图，服务端才允许进入 Planner。
    for index, message in enumerate(intent_messages):
        route = await llm.route_free_input_async(
            config_manager=config_manager,
            story=story,
            scene=scene,
            user_message=message,
            state=state,
            recent_turns=[],
            choice_options=authored_choices,
            latent_transitions=[],
        )
        assert route["route_kind"] == "free_intent"
        free_intent = route["free_intent"]
        state["dynamic_intent"] = intent_tracker.update_dynamic_intent(
            state["dynamic_intent"],
            new_intent_key=f"server_intent_{index}",
            summary=free_intent["summary"],
            relation=free_intent["relation"],
            evidence_message=message,
            origin_node_id=current_node["node_id"],
        )
    assert (
        intent_tracker.should_plan_branch(
            state["dynamic_intent"],
            current_node_id=current_node["node_id"],
        )
        is True
    )

    # Planner 原始 JSON 必须先通过框架合同校验，入口 Actor 才能看到隔离 Patch。
    planned = await branch_planner.plan_validated_runtime_branch(
        config_manager=config_manager,
        story=story,
        scene=scene,
        current_node_id=current_node["node_id"],
        current_node=current_node,
        state=state,
        dynamic_intent=state["dynamic_intent"],
        recent_turns=[],
    )
    assert planned["ok"] is True
    patch = planned["patch"]
    entry = await llm.generate_branch_entry_async(
        config_manager=config_manager,
        lanlan_name="遥夜",
        story=story,
        scene=scene,
        node=current_node,
        user_message=intent_messages[-1],
        state=state,
        recent_turns=[],
        patch=patch,
    )
    assert entry is not None
    # Planner 的 entry_callback 只保留 Patch 兼容，不再绕过 Actor 护栏进入公开旁白。
    assert entry["narration"] == ""
    assert entry["dialogue"].strip()

    active_branch = branch_lifecycle.build_active_runtime_branch(
        patch,
        branch_id="server_branch_real_model_smoke",
        created_revision=0,
        return_anchor={
            "node_id": current_node["node_id"],
            "goal_id": "goal_share_station_drink",
        },
        max_nonprogress_turns=2,
    )
    branch_facts = []
    recent_turns = [
        {"role": "user", "text": intent_messages[-1]},
        {
            "role": "assistant",
            "text": entry["dialogue"],
            "narration": entry["narration"],
        },
    ]
    actor_messages = [
        "我看看热饮柜，选一杯无咖啡因的热可可。",
        "我把选好的热可可递给你，请你拿着暖暖手。",
        "热可可已经递到你手里了，我们一起回座位等广播吧。",
    ]
    decision = {"action": "continue", "exit_kind": ""}

    for revision, message in enumerate(actor_messages, start=1):
        actor = await llm.generate_branch_turn_async(
            config_manager=config_manager,
            lanlan_name="遥夜",
            story=story,
            scene=scene,
            user_message=message,
            state=state,
            recent_turns=recent_turns,
            active_branch=active_branch,
            branch_facts=branch_facts,
        )
        assert actor["dialogue"].strip()
        assert not any(
            term in (actor["narration"] + actor["dialogue"]).lower()
            for term in ("node_id", "scene_id", "prompt", "debug")
        )
        committed = branch_runtime.apply_actor_turn(
            story=story,
            active_branch=active_branch,
            existing_facts=branch_facts,
            fact_candidates=actor["fact_candidates"],
            source_revision=revision,
            completed_goal_ids=state["completed_goal_ids"],
        )
        assert committed["ok"] is True
        active_branch = committed["active_branch"]
        branch_facts = committed["branch_facts"]
        decision = committed["decision"]
        recent_turns.extend(
            [
                {"role": "user", "text": message},
                {
                    "role": "assistant",
                    "text": actor["dialogue"],
                    "narration": actor["narration"],
                },
            ]
        )
        if decision["action"] != "continue":
            break

    # 模型只能提供候选，最终汇流必须来自服务端确认的两类作者证据。
    expected_decision = {
        "action": "converge",
        "exit_kind": "goal_converged",
        "goal_id": "goal_share_station_drink",
    }
    # 失败信息只暴露测试夹具的稳定事实角色，不打印玩家文本、Prompt 或模型全文。
    assert decision == expected_decision, {
        "committed_fact_roles": sorted(
            str(item.get("fact_role") or "") for item in branch_facts
        ),
        "allowed_fact_roles": sorted(
            str(item.get("fact_role") or "")
            for item in patch.get("allowed_new_facts") or []
        ),
    }
    assert {fact["fact_role"] for fact in branch_facts} >= {
        "player_selected_drink",
        "catgirl_received_drink",
    }
    assert all(
        fact["branch_id"] == "server_branch_real_model_smoke" for fact in branch_facts
    )
