"""验证单次演绎模型的结构、上下文、世界边界和安全回退。"""  # noqa: DOCSTRING_CJK

import json
from pathlib import Path

import pytest

from config.prompts.prompts_theater import (
    THEATER_BRANCH_HANDOFF_SYSTEM_PROMPT,
    THEATER_BRANCH_PLANNER_SYSTEM_PROMPT,
    THEATER_BRANCH_TURN_SYSTEM_PROMPT,
    THEATER_ROUTE_SYSTEM_PROMPT,
    THEATER_TURN_SYSTEM_PROMPT,
    build_theater_branch_planner_prompts,
    build_theater_branch_turn_prompts,
    build_theater_route_prompts,
    build_theater_turn_prompts,
)
from services.theater import llm


def _prompt_sections(user_prompt: str) -> tuple[dict, dict]:
    """解析提示词分区，确保测试不会把内部规则误当成公开演绎上下文。"""  # noqa: DOCSTRING_CJK
    envelope = json.loads(user_prompt.split("\n", 1)[1])
    return envelope["公开演绎上下文"], envelope["内部规则（只执行，不复述）"]


class _CharacterConfig:
    """为人格文件边界测试提供最小角色配置。"""  # noqa: DOCSTRING_CJK

    def __init__(self, root: Path):
        self.app_docs_dir = root

    def load_characters(self):
        """只声明当前猫娘，其他目录都不属于可读取角色。"""  # noqa: DOCSTRING_CJK
        return {"当前猫娘": "安全猫娘", "猫娘": {"安全猫娘": {}}}


class _ModelConfig:
    """为结构化模型返回测试提供最小可用配置。"""  # noqa: DOCSTRING_CJK

    def get_model_api_config(self, _kind):
        """返回不会访问真实供应商的占位模型配置。"""  # noqa: DOCSTRING_CJK
        return {"model": "fake-model", "base_url": "https://example.invalid"}


def test_fallback_roleplay_responds_to_user_message():
    """离线角色互动必须自然留在当前事件且不复述越界原话。"""  # noqa: DOCSTRING_CJK
    result = llm.fallback_turn(
        lanlan_name="兰兰",
        scene={"text": "雨夜窗边"},
        node={},
        user_message="我有点担心你",
        progress_kind="roleplay_response",
        callback="",
    )
    assert result["narration"] == ""
    assert "我有点担心你" not in result["dialogue"]
    assert "我听见了" in result["dialogue"]
    assert "好好回应" in result["dialogue"]
    assert "放在心上" not in result["dialogue"]


def test_graph_fallback_never_exposes_generation_guide_as_dialogue():
    """内部演绎意图不是作者台词，缺少正式对白时必须保持为空。"""  # noqa: DOCSTRING_CJK
    result = llm.fallback_turn(
        lanlan_name="兰兰",
        scene={"text": "雨夜窗边"},
        node={
            "summary": "双方仍留在窗边。",
            "runtime_generation_guide": {
                "catgirl_raw_intent": "她先确认现场读数，不总结谁救了谁。"
            },
        },
        user_message="继续",
        progress_kind="graph_progress",
        callback="",
        has_scene_notes=True,
    )

    assert result["dialogue"] == ""


@pytest.mark.asyncio
async def test_offline_fallback_does_not_infer_story_semantics_from_choice_wording():
    """通用层不能因某种作者句式猜目的地；离线时只使用正式作者回退与通用回应。"""  # noqa: DOCSTRING_CJK
    expected = llm.fallback_turn(
        lanlan_name="测试猫娘",
        scene={"text": "候车室"},
        node={},
        user_message="接下来去哪里？",
        progress_kind="roleplay_response",
        callback="",
        choice_options=[
            {"choice_id": "choice_fixture", "label": "抵达东门后查看时刻表"}
        ],
    )
    result = await llm.generate_turn_async(
        config_manager=None,
        lanlan_name="测试猫娘",
        story={"background": "一段用户提供的旅途故事"},
        scene={"text": "候车室"},
        node={},
        user_message="接下来去哪里？",
        progress_kind="roleplay_response",
        callback="",
        state={"scene_notes": []},
        recent_turns=[],
        choice_options=[
            {"choice_id": "choice_fixture", "label": "抵达东门后查看时刻表"}
        ],
    )
    assert result == expected


def test_runtime_prompts_do_not_embed_official_story_examples():
    """正式剧本只能作为外部数据和测试夹具，通用提示词不得携带其专有文案。"""  # noqa: DOCSTRING_CJK
    current_story_fragments = (
        "黑色的",
        "还是那个",
        "照片为何保留",
        "不追问她为何留着",
        "第一站到了入口",
        "清单名称",
    )
    combined = (
        THEATER_ROUTE_SYSTEM_PROMPT
        + THEATER_TURN_SYSTEM_PROMPT
        + THEATER_BRANCH_PLANNER_SYSTEM_PROMPT
        + THEATER_BRANCH_TURN_SYSTEM_PROMPT
    )
    assert all(fragment not in combined for fragment in current_story_fragments)


def test_completed_branch_recall_is_bounded_and_prompt_safe():
    """普通 Actor 只接收有预算的事实语义，忽略调用方夹带的所有服务端身份。"""  # noqa: DOCSTRING_CJK
    long_value = "公开完成共同约定" * 80
    bounded = llm._bounded_completed_branch_recall(
        [
            {
                "completed_goal_summaries": [long_value] * 6,
                "facts": [
                    {
                        "subject": long_value,
                        "predicate": "completed_public_agreement",
                        "object": "shared_next_step",
                        "fact_id": "branch_fact_private",
                        "branch_id": "branch_private",
                        "source_revision": 9,
                        "public_entity": {
                            "kind": "prop",
                            "label": long_value,
                            "status": "used",
                            "entity_id": "branch_entity_private",
                        },
                    }
                    for _index in range(10)
                ],
            }
        ]
    )
    assert len(bounded[0]["completed_goal_summaries"]) == 4
    assert len(bounded[0]["facts"]) == 8
    assert bounded[0]["facts"][0]["subject"] != long_value
    serialized = json.dumps(bounded, ensure_ascii=False)
    assert "branch_fact_private" not in serialized
    assert "branch_private" not in serialized
    assert "branch_entity_private" not in serialized

    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="测试猫娘",
        story={"background": "用户提供的共享空间", "theme": "履行公开约定"},
        scene={"title": "共享空间", "text": "双方仍在公开场景中。"},
        node={},
        user_message="刚才完成的事情还记得吗？",
        progress_kind="roleplay_response",
        callback="",
        public_state={},
        recent_turns=[],
        character_profile="保持自然回应",
        choice_options=[],
        completed_branch_recall=bounded,
    )
    payload, _internal_rules = _prompt_sections(user_prompt)
    assert payload["历史支线已公开事实"] == bounded
    assert "不得否认、撤销或要求重复完成" in THEATER_TURN_SYSTEM_PROMPT


def test_branch_planner_prompt_exposes_contract_but_hides_intent_authority():
    """Planner 只获得作者合同与公开意图证据，不能读取服务端身份、次数或来源节点。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_branch_planner_prompts(
        story={
            "background": "夜间车站的候车厅",
            "theme": "共同选择下一步",
            "restrictions": ["只能由玩家与当前猫娘说话"],
            "world_contract": {
                "speaking_roles": ["player", "active_catgirl"],
                "dynamic_content_slots": [
                    {
                        "slot_id": "slot_station_item",
                        "allowed_fact_type": "ordinary_local_prop",
                    }
                ],
                "branch_turn_budget": {"default": 3, "max": 5},
                "convergence_goal_ids": ["goal_choose_item"],
            },
            "narrative_goals": [
                {
                    "goal_id": "goal_choose_item",
                    "summary": "共同选定一件站内物品",
                    "completion_evidence": ["player_selected_item"],
                }
            ],
            "ending_domains": [],
        },
        scene={"title": "候车厅", "text": "售货架仍在营业。"},
        current_node_id="node_waiting_room",
        current_node={"title": "等待出发", "summary": "两人仍在候车厅。"},
        public_state={"已确认事实": []},
        dynamic_intent={
            "intent_key": "private_intent_key",
            "intent_summary": "先从售货架挑一件物品",
            "origin_node_id": "private_origin_node",
            "streak": 2,
            "evidence_messages": ["先看看售货架", "挑一样再走"],
        },
        recent_turns=[{"role": "user", "text": "挑一样再走"}],
        completed_goal_ids=["goal_choose_item"],
    )
    payload = json.loads(user_prompt.split("\n", 1)[1])
    assert payload["当前作者节点"]["node_id"] == "node_waiting_room"
    assert (
        payload["作者世界合同"]["dynamic_content_slots"][0]["slot_id"]
        == "slot_station_item"
    )
    assert payload["当前自由意图"] == {
        "意图说明": "先从售货架挑一件物品",
        "最近玩家证据": ["先看看售货架", "挑一样再走"],
    }
    assert payload["已完成作者目标 ID"] == ["goal_choose_item"]
    assert "不得再次用作 converge 出口" in THEATER_BRANCH_PLANNER_SYSTEM_PROMPT
    # Planner 必须得到可机械执行的上下界规则，不能把 Beat 数量误当成回合预算。
    assert (
        "不得小于作者世界合同 branch_turn_budget.default"
        in THEATER_BRANCH_PLANNER_SYSTEM_PROMPT
    )
    assert "不能按 Beat 数量自行缩短预算" in THEATER_BRANCH_PLANNER_SYSTEM_PROMPT
    # 无内容槽的动作事实仍需保留精确 JSON 字段，避免模型按自然语义省略空值。
    assert (
        "也必须显式返回 content_slot_id 空字符串"
        in THEATER_BRANCH_PLANNER_SYSTEM_PROMPT
    )
    # Beat 出口准备是稳定角色列表，提示词需阻止模型退化为自然语言标量。
    assert "exit_preparation 必须是字符串数组" in THEATER_BRANCH_PLANNER_SYSTEM_PROMPT
    assert "不能返回单个字符串" in THEATER_BRANCH_PLANNER_SYSTEM_PROMPT
    # 玩家按钮和内部舞台编排必须分栏，避免把双方结果或猫娘反应公开成玩家指令。
    assert "observable_action 只供内部编排" in THEATER_BRANCH_PLANNER_SYSTEM_PROMPT
    assert "player_choice_label" in THEATER_BRANCH_PLANNER_SYSTEM_PROMPT
    assert (
        "不得规定猫娘的接受、拒绝、表情、对白或双方完成结果"
        in THEATER_BRANCH_PLANNER_SYSTEM_PROMPT
    )
    assert "private_intent_key" not in user_prompt
    assert "private_origin_node" not in user_prompt
    assert '"streak"' not in user_prompt


@pytest.mark.asyncio
async def test_branch_planner_uses_independent_call_label_and_budget(monkeypatch):
    """Planner 必须独立计量，并使用足以容纳完整 Patch 的受限输出预算。"""  # noqa: DOCSTRING_CJK
    observed: dict[str, object] = {"call_types": []}

    class _FakeClient:
        """返回结构正确但尚未进入合同校验的 Planner 候选。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return type(
                "Result", (), {"content": '{"origin_node_id":"node_waiting_room"}'}
            )()

    async def _create_fake_client(*_args, **kwargs):
        """记录 Planner 的超时与输出预算，避免测试访问真实供应商。"""  # noqa: DOCSTRING_CJK
        observed["client_kwargs"] = kwargs
        return _FakeClient()

    def _record_call_type(value):
        """记录本次模型调用分类，验证其不会混入普通演绎统计。"""  # noqa: DOCSTRING_CJK
        observed["call_types"].append(value)

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    monkeypatch.setattr(llm, "set_call_type", _record_call_type)
    result = await llm.plan_runtime_branch_async(
        config_manager=_ModelConfig(),
        story={"background": "夜间车站", "world_contract": {}, "narrative_goals": []},
        scene={"title": "候车厅", "text": "售货架仍在营业。"},
        current_node_id="node_waiting_room",
        current_node={"title": "等待出发", "summary": "两人仍在候车厅。"},
        state={"narrative_facts": []},
        dynamic_intent={
            "intent_summary": "先挑一件物品",
            "evidence_messages": ["先挑一样"],
        },
        recent_turns=[],
    )
    assert result == {"origin_node_id": "node_waiting_room"}
    assert observed["call_types"] == ["theater_planner"]
    assert observed["client_kwargs"] == {
        "provider_type": None,
        "timeout": llm.THEATER_PLANNER_TIMEOUT_SECONDS,
        "max_retries": 0,
        "max_completion_tokens": llm.THEATER_PLANNER_OUTPUT_MAX_TOKENS,
    }


def test_branch_entry_prompt_excludes_planner_callback_and_server_identity():
    """入口 Actor 不能读取 Planner 回调、服务端 ID、revision 或意图次数。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="星遥",
        story={"background": "夜间车站", "theme": "共同选择"},
        scene={"title": "候车厅", "text": "售货架仍在营业。"},
        node={"node_id": "node_waiting_room", "title": "等待出发"},
        user_message="先挑一件再走",
        progress_kind="branch_entry",
        callback="你已经完成 goal_private_callback，还剩两个回合预算。",
        public_state={"已确认事实": []},
        recent_turns=[],
        character_profile="自称：星遥",
        choice_options=[],
        response_focus={
            "focus_type": "action",
            "evidence_excerpt": "先挑一件",
            "requires_state_change": True,
        },
        runtime_branch_patch={
            "origin_node_id": "node_waiting_room",
            "seed_intent": "从售货架挑一件物品",
            "objective": "共同完成一次站内物品选择",
            "entry_callback": "你已经完成 goal_private_callback，还剩两个回合预算。",
            "beat_outline": [
                {
                    "beat_id": "beat_choose_item",
                    "objective": "确认要选择的物品",
                    "observable_action": "双方查看售货架",
                    "player_choice_label": "从售货架上挑一件自己想要的物品",
                    "exit_preparation": ["player_selected_item"],
                }
            ],
            "allowed_new_facts": [
                {
                    "fact_type": "ordinary_local_prop",
                    "fact_role": "player_selected_item",
                    "content_slot_id": "slot_station_item",
                }
            ],
            "forbidden_assumptions": [],
            "exit_candidates": [{"kind": "converge", "goal_id": "goal_choose_item"}],
        },
    )
    payload, internal_rules = _prompt_sections(user_prompt)
    assert payload["本轮类型"] == "branch_entry"
    assert payload["本轮回应焦点"] == {
        "focus_type": "action",
        "evidence_excerpt": "先挑一件",
        "requires_state_change": True,
    }
    assert "回应义务不等于事实已经发生" in internal_rules["本轮回应焦点边界"]
    assert "不得宣告后续事实" in internal_rules["本轮回应焦点边界"]
    assert "支线入口公开锚点" not in payload
    assert "entry_callback" not in internal_rules["已验证临时支线"]
    assert "goal_private_callback" not in user_prompt
    assert "回合预算" not in user_prompt
    assert internal_rules["已验证临时支线"]["objective"] == "共同完成一次站内物品选择"
    assert "branch_id" not in user_prompt
    assert "created_revision" not in user_prompt
    assert '"streak"' not in user_prompt


@pytest.mark.asyncio
async def test_branch_entry_actor_requires_valid_model_output_without_planner_narration(
    monkeypatch,
):
    """入口 Actor 成功时丢弃 Planner 与模型旁白，并继续归入 Actor 独立指标。"""  # noqa: DOCSTRING_CJK
    call_types: list[str] = []

    class _FakeClient:
        """返回可公开提交的入口对白，并故意给出会被服务端替换的模型旁白。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return type(
                "Result",
                (),
                {
                    "content": (
                        '{"narration":"模型不能覆盖这个锚点。",'
                        '"dialogue":"既然你还想挑，那星遥就陪你从眼前这些开始看。",'
                        '"choice_rewrites":[]}'
                    )
                },
            )()

    async def _create_fake_client(*_args, **_kwargs):
        """绕过真实网络并提供严格入口 Actor 输出。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    def _record_call_type(value):
        """记录入口演绎仍属于 Actor，而不是 Planner 或 Repair。"""  # noqa: DOCSTRING_CJK
        call_types.append(value)

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    monkeypatch.setattr(llm, "set_call_type", _record_call_type)
    result = await llm.generate_branch_entry_async(
        config_manager=_ModelConfig(),
        lanlan_name="星遥",
        story={"background": "夜间车站", "theme": "共同选择"},
        scene={"title": "候车厅", "text": "售货架仍在营业。"},
        node={"node_id": "node_waiting_room", "title": "等待出发"},
        user_message="先挑一件再走",
        state={"narrative_facts": []},
        recent_turns=[],
        patch={
            "origin_node_id": "node_waiting_room",
            "seed_intent": "从售货架挑一件物品",
            "objective": "共同完成一次站内物品选择",
            "entry_callback": "你已经完成 goal_choose_item，还剩两个回合预算。",
            "beat_outline": [],
            "allowed_new_facts": [],
            "forbidden_assumptions": [],
            "exit_candidates": [],
        },
    )
    assert result == {
        "narration": "",
        "dialogue": "既然你还想挑，那星遥就陪你从眼前这些开始看。",
        "choice_rewrites": [],
    }
    assert call_types == ["theater_actor"]
    assert "goal_choose_item" not in json.dumps(result, ensure_ascii=False)
    assert "回合预算" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_branch_entry_actor_repairs_uncommitted_invalid_format_once(monkeypatch):
    """入口坏格式可在原子提交前修复一次，且修复调用必须独立计量。"""  # noqa: DOCSTRING_CJK
    call_types: list[str] = []
    outputs = iter(
        [
            "这不是 JSON，因此不能公开提交。",
            '{"narration":"两人仍在售货架前。","dialogue":"星遥陪你从眼前这些开始挑。","choice_rewrites":[]}',
        ]
    )

    class _FakeClient:
        """依次返回坏格式首版和可提交的修复版。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            # 每次创建的新客户端仍共享测试迭代器，精确验证最多一次修复调用。
            return type("Result", (), {"content": next(outputs)})()

    async def _create_fake_client(*_args, **_kwargs):
        """绕过真实网络，让首版与 Repair 走相同客户端合同。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    def _record_call_type(value):
        """记录入口 Actor 与格式 Repair 的独立职责标签。"""  # noqa: DOCSTRING_CJK
        call_types.append(value)

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    monkeypatch.setattr(llm, "set_call_type", _record_call_type)
    result = await llm.generate_branch_entry_async(
        config_manager=_ModelConfig(),
        lanlan_name="星遥",
        story={"background": "夜间车站", "theme": "共同选择"},
        scene={"title": "候车厅", "text": "售货架仍在营业。"},
        node={"node_id": "node_waiting_room", "title": "等待出发"},
        user_message="先挑一件再走",
        state={"narrative_facts": []},
        recent_turns=[],
        patch={
            "origin_node_id": "node_waiting_room",
            "seed_intent": "从售货架挑一件物品",
            "objective": "共同完成一次站内物品选择",
            "entry_callback": "两人仍站在售货架前，尚未拿走任何物品。",
            "beat_outline": [],
            "allowed_new_facts": [],
            "forbidden_assumptions": [],
            "exit_candidates": [],
        },
    )
    assert result == {
        "narration": "",
        "dialogue": "星遥陪你从眼前这些开始挑。",
        "choice_rewrites": [],
    }
    assert call_types == ["theater_actor", "theater_repair"]


@pytest.mark.asyncio
async def test_branch_entry_actor_uses_safe_fallback_after_repair_rejected(monkeypatch):
    """入口首版和 Repair 都无效时仍以权威锚点启动合法支线，不公开坏模型文本。"""  # noqa: DOCSTRING_CJK
    call_types: list[str] = []

    class _FakeClient:
        """始终返回非 JSON，稳定复现生产记录中的两次 invalid_model_output。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            # 两次调用都返回不可提交内容，验证服务端不会把任何模型文本带入回退结果。
            return type("Result", (), {"content": "无法解析的入口演出"})()

    async def _create_fake_client(*_args, **_kwargs):
        """绕过真实网络并让 Actor 与 Repair 共享同一种失败。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    def _record_call_type(value):
        """记录一次 Actor 与一次 Repair，防止新增无界重试。"""  # noqa: DOCSTRING_CJK
        call_types.append(value)

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    monkeypatch.setattr(llm, "set_call_type", _record_call_type)
    result = await llm.generate_branch_entry_async(
        config_manager=_ModelConfig(),
        lanlan_name="星遥",
        story={"background": "夜间车站", "theme": "共同选择"},
        scene={"title": "候车厅", "text": "售货架仍在营业。"},
        node={"node_id": "node_waiting_room", "title": "等待出发"},
        user_message="继续刚才的选择",
        state={"narrative_facts": []},
        recent_turns=[],
        patch={
            "origin_node_id": "node_waiting_room",
            "seed_intent": "继续玩家坚持的本地行动",
            "objective": "完成一次不偏离世界设定的短支线",
            "entry_callback": "两人仍停留在当前场景，尚未完成玩家提出的行动。",
            "beat_outline": [],
            "allowed_new_facts": [],
            "forbidden_assumptions": [],
            "exit_candidates": [],
        },
    )
    assert result == {
        "narration": "",
        "dialogue": (
            "我们还在「候车厅」这里。你想做的“继续玩家坚持的本地行动”已经确认从这里开始；"
            "后面的事还没有发生，我们只从眼前这一步继续喵。"
        ),
        "choice_rewrites": [],
    }
    assert call_types == ["theater_actor", "theater_repair"]


def test_branch_turn_prompt_exposes_patch_semantics_without_runtime_authority():
    """支线 Actor 读取目标、Beat 和公开事实语义，但不能获得支线 ID、计数或事实 ID。"""  # noqa: DOCSTRING_CJK
    system_prompt, user_prompt = build_theater_branch_turn_prompts(
        lanlan_name="星遥",
        story={"background": "夜间车站", "theme": "共同选择", "restrictions": []},
        scene={"title": "候车厅", "text": "售货架仍在营业。"},
        user_message="为什么要选刚才看到的那件？",
        public_state={"已确认事实": []},
        recent_turns=[],
        character_profile="自称：星遥",
        patch={
            "seed_intent": "选择站内物品",
            "objective": "共同完成一次选择",
            "allowed_new_facts": [
                {
                    "fact_type": "ordinary_local_prop",
                    "fact_role": "player_selected_item",
                    "content_slot_id": "slot_station_item",
                }
            ],
            "forbidden_assumptions": [],
            "beat_outline": [
                {
                    "beat_id": "beat_select",
                    "objective": "确认玩家选择",
                    "observable_action": "玩家公开选定物品",
                    "player_choice_label": "拿起自己选定的物品",
                    "exit_preparation": ["player_selected_item"],
                }
            ],
            "exit_candidates": [{"kind": "converge", "goal_id": "goal_choose_item"}],
        },
        branch_facts=[
            {
                "fact_id": "private_fact_id",
                "branch_id": "private_branch_id",
                "source_revision": 4,
                "goal_id": "goal_choose_item",
                "fact_type": "spoken_preference",
                "fact_role": "item_preference_spoken",
                "subject": "player",
                "predicate": "prefers",
                "object": "station_item",
                "content_slot_id": "",
            }
        ],
        node={
            "runtime_generation_guide": {
                "narrator_intent": "只描述玩家实际拿起的物件。",
                "catgirl_raw_intent": "猫娘等待玩家确认，不替玩家挑选。",
                "forbidden_dialogue_phrases": ["我替你选好了"],
            }
        },
        response_focus={
            "focus_type": "question",
            "evidence_excerpt": "为什么要选刚才看到的那件",
            "requires_state_change": False,
        },
    )
    payload = json.loads(user_prompt.split("\n", 1)[1])
    assert payload["已验证支线"]["objective"] == "共同完成一次选择"
    assert payload["已提交支线事实"][0]["fact_role"] == "item_preference_spoken"
    assert payload["当前待推进Beat"]["pending_fact_roles"] == ["player_selected_item"]
    assert payload["尚未提交事实合同"][0]["fact_role"] == "player_selected_item"
    assert payload["故事边界"]["当前节点禁用对白"] == ["我替你选好了"]
    assert payload["故事边界"]["当前节点演绎意图"]["旁白意图"] == (
        "只描述玩家实际拿起的物件。"
    )
    assert payload["本轮回应焦点"] == {
        "focus_type": "question",
        "evidence_excerpt": "为什么要选刚才看到的那件",
        "requires_state_change": False,
    }
    assert "“本轮回应焦点”只约束本轮如何回应" in system_prompt
    assert "private_fact_id" not in user_prompt
    assert "private_branch_id" not in user_prompt
    assert "source_revision" not in user_prompt
    assert "turns_used" not in user_prompt
    # Actor 必须收到确定性 Board 状态枚举，避免用自由文本状态控制前端分组。
    assert "available、selected 或 used" in system_prompt
    assert "discovered" in system_prompt
    # 无槽动作事实不能复挂前一条道具实体，否则整组候选会被合同原子拒绝。
    assert "content_slot_id 为空字符串时必须完全省略 public_entity" in system_prompt
    assert "不能返回空对象" in system_prompt


def test_branch_turn_prompt_projects_personalized_convergence_semantics():
    """Actor 只读取当前出口的公开汇流语义，并用已提交动作形成有限回顾。"""  # noqa: DOCSTRING_CJK
    system_prompt, user_prompt = build_theater_branch_turn_prompts(
        lanlan_name="星遥",
        story={
            "background": "夜间车站",
            "theme": "共同选择",
            "restrictions": [],
            "narrative_goals": [
                {
                    "goal_id": "goal_choose_item",
                    "summary": "双方说清偏好并共同选定一件站内物品",
                    "converge_to_node_id": "node_leave_station",
                    "fallback_convergence_callback": (
                        "物品已经由双方共同选定，两人带着它离开候车厅。"
                    ),
                },
                {
                    "goal_id": "goal_unused",
                    "summary": "不属于当前支线的目标",
                    "converge_to_node_id": "node_unused",
                    "fallback_convergence_callback": "这段内容不能进入当前 Actor 上下文。",
                },
            ],
        },
        scene={"title": "候车厅", "text": "售货架仍在营业。"},
        user_message="就拿我刚才说的那件吧",
        public_state={},
        recent_turns=[],
        character_profile="说话直接，但会认真确认双方选择",
        patch={
            "seed_intent": "选择站内物品",
            "objective": "共同完成一次选择",
            "allowed_new_facts": [
                {
                    "fact_type": "spoken_preference",
                    "fact_role": "item_preference_spoken",
                    "content_slot_id": "",
                },
                {
                    "fact_type": "ordinary_local_prop",
                    "fact_role": "player_selected_item",
                    "content_slot_id": "slot_station_item",
                },
            ],
            "forbidden_assumptions": [],
            "beat_outline": [
                {
                    "beat_id": "beat_compare",
                    "objective": "说清双方选择依据",
                    "observable_action": "双方公开比较各自看重的细节",
                    "exit_preparation": ["item_preference_spoken"],
                },
                {
                    "beat_id": "beat_select",
                    "objective": "确认最终选择",
                    "observable_action": "玩家公开选定物品",
                    "exit_preparation": ["player_selected_item"],
                },
            ],
            "exit_candidates": [
                {"kind": "converge", "goal_id": "goal_choose_item"}
            ],
        },
        branch_facts=[
            {
                "fact_id": "private_fact_id",
                "branch_id": "private_branch_id",
                "source_revision": 4,
                "goal_id": "goal_choose_item",
                "fact_type": "spoken_preference",
                "fact_role": "item_preference_spoken",
                "subject": "player",
                "predicate": "explained",
                "object": "selection_reason",
                "content_slot_id": "",
            }
        ],
        response_focus={
            "focus_type": "action",
            "evidence_excerpt": "就拿我刚才说的那件吧",
            "requires_state_change": True,
        },
    )

    payload = json.loads(user_prompt.split("\n", 1)[1])
    assert payload["已验证做法回顾"] == [
        {
            "objective": "说清双方选择依据",
            "observable_action": "双方公开比较各自看重的细节",
        }
    ]
    assert payload["可能汇流语义"] == [
        {
            "完成条件摘要": "双方说清偏好并共同选定一件站内物品",
            "共同主线事件": "物品已经由双方共同选定，两人带着它离开候车厅。",
        }
    ]
    assert "goal_id" not in payload["可能汇流语义"][0]
    assert "这段内容不能进入当前 Actor 上下文" not in user_prompt
    assert "只有本轮事实候选覆盖全部“尚未提交事实合同”" in system_prompt
    assert "一至两个已经验证的具体做法" in system_prompt
    assert "不得宣读事实清单或支线总结" in system_prompt


def test_branch_turn_prompt_projects_only_patch_selected_catalog_content():
    """Actor 只读取 Patch 已绑定的作者目录成员，不能从完整目录中自行换物件。"""  # noqa: DOCSTRING_CJK
    system_prompt, user_prompt = build_theater_branch_turn_prompts(
        lanlan_name="星遥",
        story={
            "background": "夜间车站",
            "theme": "共同选择",
            "restrictions": [],
            "world_contract": {
                "dynamic_content_slots": [
                    {
                        "slot_id": "slot_station_drink",
                        "catalog_items": [
                            {
                                "content_id": "content_hot_cocoa",
                                "entity_kind": "prop",
                                "label": "热可可",
                                "fact_object": "hot_cocoa",
                                "traits": ["drink", "non_alcoholic"],
                            },
                            {
                                "content_id": "content_unused_tea",
                                "entity_kind": "prop",
                                "label": "未选择的茶",
                                "fact_object": "unused_tea",
                                "traits": ["drink", "non_alcoholic"],
                            },
                        ],
                    }
                ]
            },
        },
        scene={"title": "候车厅", "text": "售货架仍在营业。"},
        user_message="拿热可可",
        public_state={},
        recent_turns=[],
        character_profile="保持自然回应",
        patch={
            "seed_intent": "选择热饮",
            "objective": "共同选定一杯饮品",
            "allowed_new_facts": [
                {
                    "fact_type": "ordinary_local_prop",
                    "fact_role": "player_selected_item",
                    "content_slot_id": "slot_station_drink",
                    "content_id": "content_hot_cocoa",
                }
            ],
            "forbidden_assumptions": [],
            "beat_outline": [],
            "exit_candidates": [],
        },
        branch_facts=[],
    )

    payload = json.loads(user_prompt.split("\n", 1)[1])
    selected = payload["已验证支线"]["selected_catalog_items"]
    assert selected == [
        {
            "content_slot_id": "slot_station_drink",
            "content_id": "content_hot_cocoa",
            "fact_object": "hot_cocoa",
            "entity_kind": "prop",
            "label": "热可可",
        }
    ]
    assert "content_unused_tea" not in user_prompt
    assert "未选择的茶" not in user_prompt
    assert "不得用同义词改写标签" in system_prompt


def test_model_json_loader_accepts_one_wrapped_object_but_rejects_competing_objects():
    """供应商外围说明可被剥离，但多个竞争 JSON 对象必须整体拒绝。"""  # noqa: DOCSTRING_CJK
    wrapped = (
        '下面是结果：\n```json\n{"narration":"灯亮了。","dialogue":"继续吧。"}\n```'
    )
    assert llm._load_unique_model_json_object(wrapped) == {
        "narration": "灯亮了。",
        "dialogue": "继续吧。",
    }
    with pytest.raises(ValueError):
        llm._load_unique_model_json_object(
            '{"route_kind":"idle"}\n{"route_kind":"free_intent"}'
        )


@pytest.mark.asyncio
async def test_branch_turn_actor_returns_fact_candidates_with_actor_budget(monkeypatch):
    """活动支线 Actor 使用独立受限输出，并只返回演出与无权威事实候选。"""  # noqa: DOCSTRING_CJK
    observed: dict[str, object] = {"call_types": []}

    class _FakeClient:
        """返回一个与本轮公开动作一起生成的事实候选。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return type(
                "Result",
                (),
                {
                    "content": (
                        '{"narration":"玩家把选中的物品留在手中。",'
                        '"dialogue":"好，那就先确认这一件。",'
                        '"fact_candidates":[{"goal_id":"goal_choose_item",'
                        '"fact_type":"ordinary_local_prop","fact_role":"player_selected_item",'
                        '"subject":"player","predicate":"selected_item","object":"station_item",'
                        '"content_slot_id":"slot_station_item"}]}'
                    )
                },
            )()

    async def _create_fake_client(*_args, **kwargs):
        """记录支线 Actor 的输出预算并绕过真实网络。"""  # noqa: DOCSTRING_CJK
        observed["client_kwargs"] = kwargs
        return _FakeClient()

    def _record_call_type(value):
        """确认支线回合继续归入 Actor 指标且不会重新调用 Planner。"""  # noqa: DOCSTRING_CJK
        observed["call_types"].append(value)

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    monkeypatch.setattr(llm, "set_call_type", _record_call_type)
    result = await llm.generate_branch_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="星遥",
        story={"background": "夜间车站", "theme": "共同选择"},
        scene={"title": "候车厅", "text": "售货架仍在营业。"},
        user_message="就选这一件",
        state={"narrative_facts": []},
        recent_turns=[],
        active_branch={"patch": {"objective": "共同选择", "beat_outline": []}},
        branch_facts=[],
    )
    assert result["fact_candidates"][0]["fact_role"] == "player_selected_item"
    assert observed["call_types"] == ["theater_actor"]
    assert (
        observed["client_kwargs"]["max_completion_tokens"]
        == llm.THEATER_BRANCH_ACTOR_OUTPUT_MAX_TOKENS
    )


@pytest.mark.asyncio
async def test_branch_turn_actor_repairs_uncommitted_invalid_format_once(monkeypatch):
    """活动支线坏格式在事实提交和预算推进前只修复一次。"""  # noqa: DOCSTRING_CJK
    call_types: list[str] = []
    outputs = iter(
        [
            "这不是 JSON。",
            '{"narration":"玩家仍站在货架前。","dialogue":"先从眼前这些慢慢选吧。","fact_candidates":[]}',
        ]
    )

    class _FakeClient:
        """共享迭代器依次返回首版坏格式和修复版。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            # 两次调用都未进入公开提交边界，因此测试只验证一次 Repair 上限。
            return type("Result", (), {"content": next(outputs)})()

    async def _create_fake_client(*_args, **_kwargs):
        """绕过真实供应商，保留 Actor 与 Repair 的完整调用路径。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    def _record_call_type(value):
        """确认修复使用独立职责标签。"""  # noqa: DOCSTRING_CJK
        call_types.append(value)

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    monkeypatch.setattr(llm, "set_call_type", _record_call_type)
    result = await llm.generate_branch_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="星遥",
        story={"background": "夜间车站", "theme": "共同选择"},
        scene={"title": "候车厅", "text": "售货架仍在营业。"},
        user_message="我再看看眼前这些",
        state={"narrative_facts": []},
        recent_turns=[],
        active_branch={
            "patch": {
                "objective": "共同选择",
                "beat_outline": [],
                "allowed_new_facts": [],
                "forbidden_assumptions": [],
                "exit_candidates": [],
            }
        },
        branch_facts=[],
    )
    assert result == {
        "narration": "玩家仍站在货架前。",
        "dialogue": "先从眼前这些慢慢选吧。",
        "fact_candidates": [],
    }
    assert call_types == ["theater_actor", "theater_repair"]


def test_branch_turn_fallback_is_marked_as_internal_technical_degradation():
    """活动支线安全回退必须携带服务端标记，正常 Actor 合同中仍没有该公开字段。"""  # noqa: DOCSTRING_CJK
    result = llm.fallback_branch_turn(
        lanlan_name="星遥",
        scene={"title": "候车厅", "text": "售货架仍在营业。"},
        user_message="我再确认一下",
    )

    assert result["fact_candidates"] == []
    assert result["turn_delivery"] == "technical_degraded"


@pytest.mark.asyncio
async def test_branch_turn_actor_rechecks_missing_new_fact_role_once(monkeypatch):
    """首版无新事实时可复核当前 Beat，但仍由模型给候选、合同层做最终裁决。"""  # noqa: DOCSTRING_CJK
    call_types: list[str] = []
    outputs = iter(
        [
            '{"narration":"玩家看向货架。","dialogue":"可以再确认一下。","fact_candidates":[]}',
            '{"narration":"玩家明确选定眼前物品。","dialogue":"好，就先记住这一件。",'
            '"fact_candidates":[{"goal_id":"goal_choose_item","fact_type":"ordinary_local_prop",'
            '"fact_role":"player_selected_item","subject":"player","predicate":"selected_item",'
            '"object":"station_item","content_slot_id":"slot_station_item"}]}',
        ]
    )

    class _FakeClient:
        """依次返回无进度首版和带白名单角色的复核版。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return type("Result", (), {"content": next(outputs)})()

    async def _create_fake_client(*_args, **_kwargs):
        """绕过真实供应商并保留两次职责标签。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    def _record_call_type(value):
        """确认进度复核归入 Repair，而不是第二次 Actor。"""  # noqa: DOCSTRING_CJK
        call_types.append(value)

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    monkeypatch.setattr(llm, "set_call_type", _record_call_type)
    result = await llm.generate_branch_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="星遥",
        story={"background": "夜间车站", "theme": "共同选择"},
        scene={"title": "候车厅", "text": "售货架仍在营业。"},
        user_message="我选定眼前这一件",
        state={"narrative_facts": []},
        recent_turns=[],
        active_branch={
            "patch": {
                "objective": "共同选择",
                "allowed_new_facts": [
                    {
                        "fact_type": "ordinary_local_prop",
                        "fact_role": "player_selected_item",
                        "content_slot_id": "slot_station_item",
                    }
                ],
                "beat_outline": [],
                "forbidden_assumptions": [],
                "exit_candidates": [
                    {"kind": "converge", "goal_id": "goal_choose_item"}
                ],
            }
        },
        branch_facts=[],
        response_focus={
            "focus_type": "action",
            "evidence_excerpt": "我选定眼前这一件",
            "requires_state_change": True,
        },
    )
    assert result["fact_candidates"][0]["fact_role"] == "player_selected_item"
    assert call_types == ["theater_actor", "theater_repair"]


def test_model_output_requires_narration_for_story_progress():
    """剧情推进缺少旁白时必须拒绝模型结果并回退作者文本。"""  # noqa: DOCSTRING_CJK
    assert (
        llm._parse_output(
            '{"narration":"","dialogue":"继续吧喵"}', progress_kind="graph_progress"
        )
        is None
    )
    assert llm._parse_output(
        '{"narration":"灯亮了。","dialogue":"继续吧喵"}', progress_kind="graph_progress"
    ) == {
        "narration": "灯亮了。",
        "dialogue": "继续吧喵",
        "choice_rewrites": [],
    }


def test_branch_entry_allows_empty_non_authoritative_narration():
    """入口旁白由已验证 Patch 覆盖，Actor 只要给出合法对白就不应触发无意义 Repair。"""  # noqa: DOCSTRING_CJK
    assert llm._parse_output(
        '{"narration":"","dialogue":"那就从眼前能做的开始吧。","choice_rewrites":[]}',
        progress_kind="branch_entry",
    ) == {
        "narration": "",
        "dialogue": "那就从眼前能做的开始吧。",
        "choice_rewrites": [],
    }


def test_model_output_rejects_internal_terms():
    """模型不得把内部节点或提示词字段显示给玩家。"""  # noqa: DOCSTRING_CJK
    assert (
        llm._parse_output(
            '{"narration":"进入 node_id 下一幕","dialogue":"走吧喵"}',
            progress_kind="graph_progress",
        )
        is None
    )


def test_system_prompt_keeps_off_topic_input_inside_current_scene():
    """越界请求必须自然留在当前场景，不能照做或输出系统拉回话术。"""  # noqa: DOCSTRING_CJK
    assert "不得照做" in THEATER_TURN_SYSTEM_PROMPT
    assert "回到剧本选项" in THEATER_TURN_SYSTEM_PROMPT


def test_route_prompt_receives_public_context_choices_and_latent_candidates():
    """路由看到公开上下文、作者边和当前意图语义，但不能获得目标节点或服务端计数。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_route_prompts(
        story={"background": "公开测试室"},
        scene={"title": "验证区", "text": "桌面放着测试牌和记录板。"},
        user_message="确认测试牌，之后检查记录板。",
        public_state={"已确认事实": []},
        recent_turns=[{"role": "assistant", "text": "要不要确认测试牌？"}],
        choice_options=[
            {
                "choice_id": "choice_confirm",
                "choice_mode": "dialogue",
                "label": "“确认吧。”",
                "author_label": "“确认吧。”",
                "callback": "双方公开确认测试牌。",
                "target_summary": "两人进入验证区。",
                "completion_phrases": ["确认吧"],
            }
        ],
        latent_transitions=[
            {
                "intent_id": "intent_talk_more",
                "intent_summary": "继续谈彼此印象",
                "intent_examples": ["再说说你怎么看我"],
                "target_node_id": "private_target_must_not_leak",
            }
        ],
        current_dynamic_intent={
            "intent_key": "private_intent_key",
            "intent_summary": "整理桌上的旧明信片",
            "origin_node_id": "private_origin_node",
            "streak": 1,
            "evidence_messages": ["先看看桌上那张旧明信片"],
            "relation": "new",
        },
        current_pending_intent={
            "summary": "进入验证区后检查记录板",
            "evidence_excerpt": "然后检查记录板",
            "source_node_id": "private_pending_source",
            "target_node_id": "private_pending_target",
            "target_scene_id": "private_pending_scene",
            "created_revision": 4,
            "expires_revision": 5,
        },
    )
    payload = json.loads(user_prompt.split("\n", 1)[1])
    assert payload["玩家本轮原话"] == "确认测试牌，之后检查记录板。"
    assert payload["当前推荐选项"][0]["choice_id"] == "choice_confirm"
    assert payload["当前隐藏语义候选"][0]["intent_id"] == "intent_talk_more"
    assert payload["当前通用自由意图"] == {
        "意图说明": "整理桌上的旧明信片",
        "最近玩家证据": ["先看看桌上那张旧明信片"],
    }
    assert payload["待重验剩余意图"] == {
        "意图说明": "进入验证区后检查记录板",
        "原话摘录": "然后检查记录板",
    }
    assert "private_target_must_not_leak" not in user_prompt
    assert "private_intent_key" not in user_prompt
    assert "private_origin_node" not in user_prompt
    assert "private_pending_source" not in user_prompt
    assert "private_pending_target" not in user_prompt
    assert "private_pending_scene" not in user_prompt
    assert '"streak"' not in user_prompt


def test_roleplay_prompt_hides_scripted_dialogue_to_avoid_repetition():
    """自由互动不能再次注入刚完成的作者节点或固定台词。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="兰兰",
        story={"background": "旧教室", "theme": "告别"},
        scene={"title": "教室", "text": "窗外有蝉鸣。"},
        node={
            "title": "说出担心",
            "summary": "猫娘承认害怕。",
            "scripted_dialogue": "这一句不能重复。",
        },
        user_message="你愿意说说真实感受吗？",
        progress_kind="roleplay_response",
        callback="",
        public_state={},
        recent_turns=[],
        character_profile="",
        choice_options=[],
    )
    payload, internal_rules = _prompt_sections(user_prompt)
    assert "scripted_dialogue" not in payload["目标节点"]
    assert "title" not in payload["目标节点"]
    assert "summary" not in payload["目标节点"]
    assert payload["目标节点"] == {}
    assert "不得复述上一句台词" in internal_rules["本轮演绎指令"]
    assert "不得把其中的名称、数量或秘密当成默认话题" in internal_rules["本轮演绎指令"]
    assert "再次讨论已经完成的上一个 Choice" in internal_rules["本轮演绎指令"]
    assert "不是必须反复讨论的话题" in THEATER_TURN_SYSTEM_PROMPT
    # Choice 已收回作者层，普通互动只能理解尚未发生的选项，不能改写其显示文案。
    assert "choice_rewrites 必须始终返回空数组" in THEATER_TURN_SYSTEM_PROMPT
    assert "Choice 显示文案完全由作者" in THEATER_TURN_SYSTEM_PROMPT
    assert "不能增加完成条件" in THEATER_ROUTE_SYSTEM_PROMPT
    assert list(payload)[-1] == "本轮唯一回应目标"
    assert payload["本轮唯一回应目标"] == "你愿意说说真实感受吗？"
    assert "不得把同一个问题反问玩家" in internal_rules["本轮回应要求"]
    assert "不得只换一种说法把同一个问题反问玩家" in THEATER_TURN_SYSTEM_PROMPT
    assert "不可偏移的世界边界" not in payload
    assert internal_rules["使用方式"].startswith("只用于约束生成结果")
    assert "不得在 narration 或 dialogue 中引用" in THEATER_TURN_SYSTEM_PROMPT


def test_roleplay_prompt_includes_story_output_guardrails():
    """剧本输出硬边界必须进入模型上下文，且同时由代码在展示前校验。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="糖糖",
        story={
            "background": "公开测试室",
            "runtime_guardrails": {
                "conditional_output_guards": [
                    {
                        "until_fact": {
                            "subject": "pair",
                            "predicate": "is",
                            "object": "confirmed",
                        },
                        "forbidden_phrases": ["挽住手臂"],
                    }
                ]
            },
        },
        scene={"title": "入口", "text": "两人准备出发。"},
        node={"node_id": "node_depart"},
        user_message="我们先去哪里？",
        progress_kind="roleplay_response",
        callback="",
        public_state={},
        recent_turns=[],
        character_profile="",
        choice_options=[],
    )
    payload, internal_rules = _prompt_sections(user_prompt)
    assert "输出硬边界" not in payload
    assert internal_rules["输出硬边界"]["conditional_output_guards"][0][
        "forbidden_phrases"
    ] == ["挽住手臂"]


def test_graph_progress_prompt_preserves_author_dialogue_and_handoff():
    """剧情推进优先采用作者对白，并知道对白后会出现哪些推荐选项。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="霜瞳",
        story={"background": "约会前的家中", "theme": "共同决定"},
        scene={"title": "门口", "text": "歪星星挂在纸袋边。"},
        node={
            "title": "保留歪星星",
            "summary": "玩家接住挂坠。",
            "scripted_dialogue": "谢谢你留下它。今天如果想改路线，我们就一起商量。",
            "runtime_generation_guide": {
                "catgirl_raw_intent": "猫娘嘴硬地开心，并提出共同商量路线。"
            },
        },
        user_message="接住挂坠",
        progress_kind="graph_progress",
        callback="你把挂坠扣到包上。",
        public_state={},
        recent_turns=[],
        character_profile="自称本小姐；傲娇嘴硬",
        choice_options=[
            {
                "choice_id": "choice_agree",
                "label": "“好。有想改的地方就告诉我，我们一起决定。”",
                "choice_mode": "dialogue",
            }
        ],
    )
    payload, internal_rules = _prompt_sections(user_prompt)

    assert payload["猫娘人格摘要"] == "自称本小姐；傲娇嘴硬"
    assert payload["目标节点"]["author_dialogue"].startswith("谢谢你留下它")
    assert "narrator_intent" not in payload["目标节点"]
    assert "catgirl_intent" not in payload["目标节点"]
    assert internal_rules["作者演绎意图"]["猫娘意图"].startswith("猫娘嘴硬地开心")
    assert "performance_instruction" not in payload["目标节点"]
    assert "优先采用作者对白原文" in internal_rules["本轮演绎指令"]
    assert "不得增加口癖、自称" in internal_rules["本轮演绎指令"]
    assert "内部规则只通过不越界来执行" in internal_rules["本轮演绎指令"]
    assert "不得转述成对白" in internal_rules["本轮演绎指令"]
    assert payload["猫娘故事身份"] == "当前故事中的共同主角"
    assert payload["下一轮推荐选项"] == [
        {
            "显示文案": "“好。有想改的地方就告诉我，我们一起决定。”",
            "类型": "dialogue",
        }
    ]
    assert payload["当前可推进选项"] == []
    assert "完整保留下一轮推荐选项所需前提" in internal_rules["本轮回应要求"]
    assert "允许原样采用" in THEATER_TURN_SYSTEM_PROMPT


@pytest.mark.parametrize(
    ("user_message", "response_focus"),
    [
        (
            "船舱的氧气表为什么一直往下掉？",
            {
                "focus_type": "question",
                "evidence_excerpt": "氧气表为什么一直往下掉",
                "requires_state_change": False,
            },
        ),
        (
            "看到那张旧照片时，你是不是有点不舒服？",
            {
                "focus_type": "attitude",
                "evidence_excerpt": "你是不是有点不舒服",
                "requires_state_change": False,
            },
        ),
    ],
)
def test_graph_progress_prompt_keeps_bounded_response_focus_across_story_genres(
    user_message, response_focus
):
    """回应焦点只依赖有来源的本轮语义，不能绑定当前示例剧本的道具关键词。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="测试猫娘",
        story={"background": "用户提供的故事背景", "theme": "共同面对变化"},
        scene={"title": "当前场景", "text": "两位主角正在同一空间交谈。"},
        node={
            "title": "作者节点",
            "summary": "作者事件已经发生。",
            "scripted_dialogue": "我们先把眼前的路走完，再决定下一步。",
        },
        user_message=user_message,
        progress_kind="graph_progress",
        callback="作者声明的动作已经发生。",
        public_state={},
        recent_turns=[],
        character_profile="",
        choice_options=[],
        response_focus=response_focus,
    )
    payload, internal_rules = _prompt_sections(user_prompt)

    assert payload["本轮回应焦点"] == response_focus
    assert response_focus["evidence_excerpt"] in payload["本轮唯一回应目标"]
    assert "先回应焦点" in internal_rules["本轮演绎指令"]
    assert "逐字保留" in internal_rules["本轮演绎指令"]
    assert "不得为了体现口癖而强行改写作者原文" in THEATER_TURN_SYSTEM_PROMPT
    assert "不要让猫娘向玩家宣读" in THEATER_TURN_SYSTEM_PROMPT
    assert "故事身份、当前任务关系和已公开事实" in THEATER_TURN_SYSTEM_PROMPT
    assert "不能把平等队友写成主从" in THEATER_TURN_SYSTEM_PROMPT


def test_turn_prompt_exposes_catgirl_story_role_separately_from_personality():
    """剧本职业身份必须独立进入上下文，不能被角色卡中的日常称呼覆盖。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="糖糖",
        story={
            "background": "两名探空队员滞留半开发星球。",
            "scenario_card": {
                "player_role": "系统工程师",
                "catgirl_role": "负责风暴建模与通讯调制的平等队员",
            },
        },
        scene={"title": "受损舱室", "text": "电池再次冒烟。"},
        node={"title": "检查电池"},
        user_message="电池又冒烟了",
        progress_kind="roleplay_response",
        callback="",
        public_state={},
        recent_turns=[],
        character_profile="习惯把玩家叫作主人；活泼黏人",
        choice_options=[],
    )
    payload, _ = _prompt_sections(user_prompt)

    # 两种输入都保留，由系统规则明确当前故事身份拥有称呼和语域优先级。
    assert payload["猫娘人格摘要"].startswith("习惯把玩家叫作主人")
    assert payload["猫娘故事身份"] == "负责风暴建模与通讯调制的平等队员"


def test_punctuation_only_input_requests_new_explanation():
    """玩家只发问号时必须要求新解释，不能把上一回答原样播放。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="霜瞳",
        story={"background": "活动厅", "theme": "久别重逢"},
        scene={"title": "灯影里的重逢", "text": "旧合照落在两人之间。"},
        node={"title": "先叫出她现在的名字", "summary": "已经完成的旧动作"},
        user_message="？",
        progress_kind="roleplay_response",
        callback="",
        public_state={},
        recent_turns=[],
        character_profile="",
        choice_options=[],
    )
    payload, internal_rules = _prompt_sections(user_prompt)
    assert "补充新的解释" in internal_rules["本轮回应要求"]
    assert (
        payload["本轮唯一回应目标"]
        == "玩家没有理解你上一句话，正在等你换一种说法解释清楚。"
    )


def test_prompt_tolerates_invalid_optional_story_sections():
    """可选剧本段落类型异常时应使用安全默认值，不能阻断模型回退链路。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="兰兰",
        story={"background": "旧教室", "seed": None, "scenario_card": []},
        scene={"title": "教室", "text": "窗外有蝉鸣。"},
        node={"title": "重逢", "summary": "两人再次见面。"},
        user_message="你好。",
        progress_kind="roleplay_response",
        callback="",
        public_state={},
        recent_turns=[],
        character_profile="",
        choice_options=[],
    )
    payload, internal_rules = _prompt_sections(user_prompt)
    assert payload["玩家身份"] == "故事参与者"
    assert internal_rules["禁止假设"] == []
    assert internal_rules["主线目标"] == []


def test_recent_context_includes_assistant_narration_and_dialogue():
    """最近上下文必须独立保留对白，不能被较长旁白截断。"""  # noqa: DOCSTRING_CJK
    turns = llm._recent_public_turns(
        [
            {
                "role": "assistant",
                "narration": "她把合同推回桌面。",
                "text": "这一条需要修改喵。",
            }
        ]
    )
    assert turns == [
        {
            "role": "assistant",
            "dialogue": "这一条需要修改喵。",
            "narration": "她把合同推回桌面。",
        }
    ]


def test_assistant_echo_detection_rejects_player_choice_line():
    """猫娘近似照读玩家 Choice 时必须识别为角色反转。"""  # noqa: DOCSTRING_CJK
    assert (
        llm._assistant_echoes_user(
            "哼，既然数据无误……那就一起打开看看最后的真相吧，别手抖喵！",
            "既然数据无误，那就一起打开保险柜看看最后的真相吧",
        )
        is True
    )
    assert (
        llm._assistant_echoes_user(
            "我还需要一点时间想清楚喵。", "我会坐下来听你慢慢说。"
        )
        is False
    )


@pytest.mark.asyncio
async def test_graph_progress_echo_is_soft_and_keeps_model_dialogue(monkeypatch):
    """模型近似复述玩家时只影响文风，不能再触发 Repair 或作者兜底。"""  # noqa: DOCSTRING_CJK

    class _FakeClient:
        """返回可复现角色反转 JSON 的异步模型客户端。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return type(
                "Result",
                (),
                {
                    "content": '{"narration":"保险柜被打开。","dialogue":"既然数据无误，那就一起打开保险柜看看最后的真相吧。","choice_rewrites":[]}'
                },
            )()

    async def _create_fake_client(*_args, **_kwargs):
        """绕过真实网络并返回可控客户端。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="霜瞳",
        story={"background": "旧录音室"},
        scene={"text": "保险柜在墙角。"},
        node={
            "scripted_dialogue": "钥匙给你一半，我们一起打开它喵。",
            "summary": "共同打开保险柜。",
        },
        user_message="既然数据无误，那就一起打开保险柜看看最后的真相吧",
        progress_kind="graph_progress",
        callback="你们共同打开保险柜。",
        state={"scene_notes": ["刚才有过自由互动"]},
        recent_turns=[],
    )
    assert result["dialogue"] == "既然数据无误，那就一起打开保险柜看看最后的真相吧。"
    assert result["narration"] == "你们共同打开保险柜。"


@pytest.mark.asyncio
async def test_graph_progress_repairs_persona_coercion_once(monkeypatch):
    """傲娇人格把共同商量演成不可拒绝命令时，必须纠错一次并保留作者边界。"""  # noqa: DOCSTRING_CJK
    outputs = [
        '{"narration":"星星被扣好。","dialogue":"那今天都听本小姐的，不许有异议喵。","choice_rewrites":[]}',
        '{"narration":"星星被扣好。","dialogue":"本小姐可没打算一个人说了算。想停就停，想换地方也得我们两个都点头喵。","choice_rewrites":[]}',
    ]
    calls = 0
    call_types: list[str] = []

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            nonlocal calls
            content = outputs[calls]
            calls += 1
            return type("Result", (), {"content": content})()

    async def _create_fake_client(*_args, **_kwargs):
        return _FakeClient()

    def _record_call_type(value):
        """记录首版演绎与纠错的职责标签，防止两类指标重新混合。"""  # noqa: DOCSTRING_CJK
        call_types.append(value)

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    monkeypatch.setattr(llm, "set_call_type", _record_call_type)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="霜瞳",
        story={"background": "约会前的家中"},
        scene={"text": "星星挂坠被接住。"},
        node={
            "scripted_dialogue": "今天如果想停或改路线，我们就一起商量。",
            "summary": "两人约定共同商量路线。",
        },
        user_message="接住挂坠",
        progress_kind="graph_progress",
        callback="你把挂坠扣到包上。",
        state={"scene_notes": []},
        recent_turns=[],
    )

    assert calls == 2
    assert call_types == ["theater_actor", "theater_repair"]
    assert "不许有异议" not in result["dialogue"]
    assert "我们两个都点头" in result["dialogue"]
    assert result["narration"] == "你把挂坠扣到包上。"


@pytest.mark.asyncio
async def test_roleplay_drops_unchanged_choice_label_without_repair(monkeypatch):
    """自由对话按钮没有更新时直接恢复作者原文，不能为显示文案额外调用模型。"""  # noqa: DOCSTRING_CJK
    outputs = [
        '{"narration":"","dialogue":"那我们走吧喵。","choice_rewrites":['
        '{"choice_id":"choice_depart","label":"“好，那就一起出发吧。”"}]}',
        '{"narration":"","dialogue":"出发前先把票收好，到了入口再决定第一站喵。",'
        '"choice_rewrites":['
        '{"choice_id":"choice_depart","label":"“好，我收好票，到了入口再和你决定。”"}]}',
    ]
    calls = 0

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            nonlocal calls
            content = outputs[calls]
            calls += 1
            return type("Result", (), {"content": content})()

    async def _create_fake_client(*_args, **_kwargs):
        return _FakeClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="希尔",
        story={"background": "验证开始前的测试室"},
        scene={"text": "两枚测试牌放在透明盒里。"},
        node={"summary": "猫娘邀请玩家共同验证。"},
        user_message="先去哪里呢？",
        progress_kind="roleplay_response",
        callback="",
        state={"scene_notes": []},
        recent_turns=[{"role": "assistant", "text": "你愿意和我一起出发吗？"}],
        choice_options=[
            {
                "choice_id": "choice_depart",
                "label": "“好，那就一起出发吧。”",
                "author_label": "“好，那就一起出发吧。”",
                "choice_mode": "dialogue",
            }
        ],
    )

    assert calls == 1
    assert result["dialogue"] == "那我们走吧喵。"
    assert result["choice_rewrites"] == []


@pytest.mark.asyncio
async def test_roleplay_keeps_first_performance_when_choice_rewrite_is_invalid(
    monkeypatch,
):
    """按钮格式错误只清空显示改写，首版合格演出不得触发 Repair。"""  # noqa: DOCSTRING_CJK
    outputs = iter(
        [
            (
                '{"narration":"电池再次冒出白烟。","dialogue":"压力已经稳住，先处理电池。",'
                '"choice_rewrites":['
                '{"choice_id":"choice_save_power","label":"看着白烟，关闭重复呼叫，把电量留给生命保障"},'
                '{"choice_id":"choice_share_risk","label":"（检查读数）“从现在起，风险都对彼此公开。”"}]}'
            ),
            (
                '{"narration":"电池再次冒出白烟，警报重新亮起。","dialogue":"压力稳定。先断呼叫，别让电池替我们做决定。",'
                '"choice_rewrites":['
                '{"choice_id":"choice_save_power","label":"关闭重复呼叫，把宝贵的电量留给生命保障"},'
                '{"choice_id":"choice_share_risk","label":"“从现在起，风险都对彼此公开。”"}]}'
            ),
        ]
    )
    call_types: list[str] = []

    class _FakeClient:
        """先返回 Choice 类型漂移，再返回只剩核心连续性错误的合格演出。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return type("Result", (), {"content": next(outputs)})()

    async def _create_fake_client(*_args, **_kwargs):
        """隔离真实网络，只验证 Actor 与 Repair 的局部降级决策。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    def _record_call_type(value):
        """确认一次 Actor 失败后最多进行一次 Repair。"""  # noqa: DOCSTRING_CJK
        call_types.append(value)

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    monkeypatch.setattr(llm, "set_call_type", _record_call_type)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="星澜",
        story={"background": "两名探空队员困在受损舱室。"},
        scene={"text": "主电池再次冒出白烟。"},
        node={"node_id": "node_power_check"},
        user_message="电池又冒烟了",
        progress_kind="roleplay_response",
        callback="",
        state={"scene_notes": []},
        recent_turns=[],
        choice_options=[
            {
                "choice_id": "choice_save_power",
                "label": "关闭重复呼叫，把电量留给生命保障",
                "author_label": "关闭重复呼叫，把电量留给生命保障",
                "choice_mode": "action",
                "callback": "你关闭重复呼叫，把电量留给生命保障。",
            },
            {
                "choice_id": "choice_share_risk",
                "label": "“从现在起，风险都对彼此公开。”",
                "author_label": "“从现在起，风险都对彼此公开。”",
                "choice_mode": "dialogue",
                "callback": "你提出此后公开所有风险。",
            },
        ],
    )

    assert call_types == ["theater_actor"]
    assert result == {
        "narration": "电池再次冒出白烟。",
        "dialogue": "压力已经稳住，先处理电池。",
        "choice_rewrites": [],
    }


@pytest.mark.asyncio
async def test_roleplay_rejects_uncommitted_choice_result_after_one_repair(monkeypatch):
    """未命中 Choice 时，Actor 与 Repair 都不能把待选动作写成玩家已经完成的事实。"""  # noqa: DOCSTRING_CJK
    outputs = iter(
        [
            '{"narration":"她看着你把桌边的信封递到手边。",'
            '"dialogue":"谢谢你把信封递给我，我们现在就走吧。",'
            '"choice_rewrites":[{"choice_id":"choice_letter","label":"把信封交给她后一起离开"}]}',
            '{"narration":"你已经把桌边的信封交到了她手里。",'
            '"dialogue":"信封收好啦，那我们出发吧。",'
            '"choice_rewrites":[{"choice_id":"choice_letter","label":"把信封交给她后一起离开"}]}',
        ]
    )
    call_types: list[str] = []

    class _FakeClient:
        """连续返回两份结构合法但抢跑同一待选动作的演绎。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            # 首版与 Repair 共享迭代器，确保失败后没有第三次无界模型调用。
            return type("Result", (), {"content": next(outputs)})()

    async def _create_fake_client(*_args, **_kwargs):
        """绕过真实网络并复现公开演绎与权威状态分裂。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    def _record_call_type(value):
        """记录普通 Actor 后最多只进行一次 Repair。"""  # noqa: DOCSTRING_CJK
        call_types.append(value)

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    monkeypatch.setattr(llm, "set_call_type", _record_call_type)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="星遥",
        story={"background": "两人仍在安静的室内。"},
        scene={"text": "一只未拆封的信封仍放在桌边。"},
        node={"summary": "信封尚未交到任何人手里。"},
        user_message="我们现在就走吧",
        progress_kind="roleplay_response",
        callback="",
        state={"scene_notes": []},
        recent_turns=[],
        choice_options=[
            {
                "choice_id": "choice_letter",
                "label": "把桌边的信封递给她",
                "author_label": "把桌边的信封递给她",
                "choice_mode": "action",
                "callback": "你拿起桌边的信封，将它递到她手边。",
            }
        ],
    )
    assert result == {
        "narration": "",
        "dialogue": "眼前的下一步还没有替你决定；先让我理清楚，再好好回应你喵。",
        "choice_rewrites": [],
    }
    assert call_types == ["theater_actor", "theater_repair"]


def test_uncommitted_choice_checker_allows_environment_result_and_imperative_action():
    """环境已经起火不等于玩家已完成 Choice，紧急命令也不能被完成态检查误杀。"""  # noqa: DOCSTRING_CJK
    parsed = {
        "narration": "主电池组冒出刺鼻白烟，火花在裂缝间噼啪作响。",
        "dialogue": "火苗窜出来了！别愣着，快用绝缘扳手切断汇流排，动作要快！",
    }
    options = [
        {
            "choice_id": "choice_isolate_main_bus",
            "label": "用绝缘扳手隔离冒烟的主电池汇流排",
            "author_label": "用绝缘扳手隔离冒烟的主电池汇流排",
            "choice_mode": "action",
            "callback": "你用绝缘扳手断开冒烟的主电池汇流排。",
        }
    ]

    assert (
        llm._claims_uncommitted_choice_result(
            parsed,
            user_message="主电池着火了",
            choice_options=options,
        )
        is False
    )


@pytest.mark.asyncio
async def test_roleplay_keeps_first_output_without_repair_for_soft_semantic_suspicion(
    monkeypatch,
):
    """低置信语义问题只影响文风，普通 Actor 不得为此调用 Repair。"""  # noqa: DOCSTRING_CJK
    outputs = iter(
        [
            '{"narration":"","dialogue":"你先决定我们去哪里吧？","choice_rewrites":[]}',
            '{"narration":"","dialogue":"那你告诉我第一站要去哪里？","choice_rewrites":[]}',
        ]
    )
    call_types: list[str] = []

    class _FakeClient:
        """连续返回可解析但仍把去向问题抛回玩家的软语义结果。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return type("Result", (), {"content": next(outputs)})()

    async def _create_fake_client(*_args, **_kwargs):
        """隔离真实网络，只验证 Repair 后的软护栏降级策略。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    def _record_call_type(value):
        """确认软语义问题仍只允许一次 Repair。"""  # noqa: DOCSTRING_CJK
        call_types.append(value)

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    monkeypatch.setattr(llm, "set_call_type", _record_call_type)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="糖糖",
        story={"background": "两人站在公开测试区入口。"},
        scene={"text": "入口前有两条公开路线。"},
        node={"node_id": "node_route"},
        user_message="我们先去哪里？",
        progress_kind="roleplay_response",
        callback="",
        state={"scene_notes": []},
        recent_turns=[],
        choice_options=[],
    )

    assert call_types == ["theater_actor"]
    assert result == {
        "narration": "",
        "dialogue": "你先决定我们去哪里吧？",
        "choice_rewrites": [],
    }


def test_turn_parser_discards_choice_rewrites_without_authority():
    """模型即使返回合法 Choice ID，也不能让改写进入可提交演绎结果。"""  # noqa: DOCSTRING_CJK
    parsed = llm._parse_output(
        '{"narration":"","dialogue":"我们可以继续聊。","choice_rewrites":'
        '[{"choice_id":"choice_letter","label":"带着信封离开"}]}',
        progress_kind="roleplay_response",
    )
    assert parsed is not None
    assert parsed["choice_rewrites"] == []


def test_roleplay_guard_allows_player_action_explicitly_present_in_current_input():
    """玩家本轮确实实施的动作可以被普通回应承认，护栏不能把所有第二人称旁白一概拦截。"""  # noqa: DOCSTRING_CJK
    reason = llm._performance_repair_reason(
        {
            "narration": "她接过你递来的信封，放在桌角。",
            "dialogue": "谢谢，我会收好它。",
            "choice_rewrites": [],
        },
        progress_kind="roleplay_response",
        user_message="我把桌边的信封递给她",
        node={},
        character_profile="",
        choice_options=[
            {
                "choice_id": "choice_letter",
                "label": "把桌边的信封递给她",
                "author_label": "把桌边的信封递给她",
                "choice_mode": "action",
                "callback": "你拿起桌边的信封，将它递到她手边。",
            }
        ],
    )
    assert reason == ""


@pytest.mark.asyncio
async def test_graph_progress_repairs_authored_forbidden_topic_phrase(monkeypatch):
    """作者声明暂时禁用的话题词被模型擅自补入时，必须纠错后再展示。"""  # noqa: DOCSTRING_CJK
    outputs = [
        '{"narration":"你收下测试牌。","dialogue":"那份内部验证清单就留到下一步再说。","choice_rewrites":[]}',
        '{"narration":"你收下测试牌。","dialogue":"当前只确认公开步骤，到了验证区再核对记录。","choice_rewrites":[]}',
    ]
    calls = 0

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            nonlocal calls
            content = outputs[calls]
            calls += 1
            return type("Result", (), {"content": content})()

    async def _create_fake_client(*_args, **_kwargs):
        return _FakeClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="希尔",
        story={"background": "验证开始前的测试室"},
        scene={"text": "两枚测试牌放在透明盒里。"},
        node={
            "summary": "玩家接受共同验证。",
            "scripted_dialogue": "当前只确认公开步骤，到了验证区再核对记录。",
            "runtime_generation_guide": {
                "forbidden_dialogue_phrases": ["内部验证清单"]
            },
        },
        user_message="开始验证",
        progress_kind="graph_progress",
        callback="你收下测试牌。",
        state={"scene_notes": []},
        recent_turns=[],
    )

    assert calls == 2
    assert "内部验证清单" not in result["dialogue"]
    assert "公开步骤" in result["dialogue"]


def test_authored_performance_requires_declared_self_name_when_speaking_about_self():
    """人格明确声明自称时，作者演出不得退化成无人格的第一人称近义改写。"""  # noqa: DOCSTRING_CJK
    reason = llm._performance_repair_reason(
        {"dialogue": "我本来想把它重缝。"},
        progress_kind="graph_progress",
        user_message="接住挂坠",
        node={"scripted_dialogue": "我本来想把它重缝。"},
        character_profile="自称: 本小姐\n核心特质: 傲娇嘴硬",
    )
    assert reason == "persona_self_name_missing"


def test_roleplay_rejects_mirroring_players_location_question():
    """玩家问第一站时，猫娘不得只把同一个去向问题反问回来。"""  # noqa: DOCSTRING_CJK
    bad_reason = llm._performance_repair_reason(
        {"narration": "", "dialogue": "主人先告诉糖糖，我们第一站要去哪里呀？"},
        progress_kind="roleplay_response",
        user_message="我们先去哪里？",
        node={},
        character_profile="",
    )
    good_reason = llm._performance_repair_reason(
        {"narration": "", "dialogue": "先去公开验证区吧，那里离入口最近。"},
        progress_kind="roleplay_response",
        user_message="我们先去哪里？",
        node={},
        character_profile="",
    )
    assert bad_reason == "current_question_mirrored"
    assert good_reason == ""


def test_roleplay_rejects_unintroduced_named_destination():
    """回答去向时不得临场发明公开上下文里不存在的命名摊位。"""  # noqa: DOCSTRING_CJK
    bad_reason = llm._performance_repair_reason(
        {"narration": "", "dialogue": "先去入口旁的「星愿风铃」摊吧。"},
        progress_kind="roleplay_response",
        user_message="我们先去哪里？",
        node={},
        character_profile="",
        grounding_text="已经公开的最近目的地是测试区入口。",
    )
    good_reason = llm._performance_repair_reason(
        {"narration": "", "dialogue": "先到测试区入口吧，之后再看公开的验证路线。"},
        progress_kind="roleplay_response",
        user_message="我们先去哪里？",
        node={},
        character_profile="",
        grounding_text="已经公开的最近目的地是测试区入口。",
    )
    assert bad_reason == "ungrounded_named_destination"
    assert good_reason == ""


def test_story_output_guardrails_cover_narration_and_dialogue():
    """关系边界不能只检查对白，旁白中的越界接触也必须拦截。"""  # noqa: DOCSTRING_CJK
    reason = llm._performance_repair_reason(
        {"narration": "糖糖顺势挽住你的手臂。", "dialogue": "我们出发吧喵。"},
        progress_kind="roleplay_response",
        user_message="出发吧。",
        node={},
        character_profile="",
        story={
            "runtime_guardrails": {
                "conditional_output_guards": [
                    {
                        "until_fact": {
                            "subject": "player",
                            "predicate": "chooses",
                            "object": "relationship",
                        },
                        "forbidden_phrases": ["挽住你的手臂"],
                    }
                ]
            }
        },
        state={"narrative_facts": []},
    )
    assert reason == "forbidden_output_phrase_used"

    allowed_after_confirmation = llm._performance_repair_reason(
        {"narration": "糖糖征得同意后挽住你的手臂。", "dialogue": "这样可以吗？"},
        progress_kind="roleplay_response",
        user_message="可以。",
        node={},
        character_profile="",
        story={
            "runtime_guardrails": {
                "conditional_output_guards": [
                    {
                        "until_fact": {
                            "subject": "player",
                            "predicate": "chooses",
                            "object": "relationship",
                        },
                        "forbidden_phrases": ["挽住你的手臂"],
                    }
                ]
            }
        },
        state={
            "narrative_facts": [
                {"subject": "player", "predicate": "chooses", "object": "relationship"}
            ]
        },
    )
    assert allowed_after_confirmation == ""


def test_story_silent_rules_cannot_be_explained_in_dialogue():
    """内部规则只能改变行为，猫娘不能把它们组织成免责声明说给玩家。"""  # noqa: DOCSTRING_CJK
    story = {
        "runtime_guardrails": {
            "forbidden_output_patterns": [
                "中途.{0,16}(?:停|换).{0,20}(?:商量|决定)",
                "测试牌.{0,16}(?:不会|不能|不).{0,16}(?:安排|决定)",
            ]
        }
    }
    rule_dump = llm._performance_repair_reason(
        {"narration": "", "dialogue": "中途想停或者换地方，我们都可以一起商量喵。"},
        progress_kind="graph_progress",
        user_message="出发",
        node={},
        character_profile="",
        story=story,
    )
    natural_reply = llm._performance_repair_reason(
        {"narration": "", "dialogue": "那就开始吧，先去测试区入口。"},
        progress_kind="graph_progress",
        user_message="出发",
        node={},
        character_profile="",
        story=story,
    )
    assert rule_dump == "internal_rule_exposed"
    assert natural_reply == ""


@pytest.mark.asyncio
async def test_choice_rewrite_failure_keeps_first_answer_without_retry(monkeypatch):
    """推荐项不合规时直接保留首版回答和作者按钮，不让第二次调用改变正文。"""  # noqa: DOCSTRING_CJK
    outputs = [
        (
            '{"narration":"","dialogue":"先到测试区入口吧，到了那里再看验证路线。",'
            '"choice_rewrites":['
            '{"choice_id":"choice_route","label":"抵达入口后，把愿意公开的路线面递给她看"}]}'
        ),
        (
            '{"narration":"","dialogue":"主人先告诉糖糖，我们第一站要去哪里呀？",'
            '"choice_rewrites":['
            '{"choice_id":"choice_route","label":"到了入口，把想分享的路线交给她"}]}'
        ),
    ]
    calls = 0

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            nonlocal calls
            content = outputs[calls]
            calls += 1
            return type("Result", (), {"content": content})()

    async def _create_fake_client(*_args, **_kwargs):
        return _FakeClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="糖糖",
        story={"background": "两人正前往测试区入口。"},
        scene={"text": "测试区入口就在前方。"},
        node={"node_id": "node_depart"},
        user_message="我们先去哪里？",
        progress_kind="roleplay_response",
        callback="",
        state={"scene_notes": []},
        recent_turns=[],
        choice_options=[
            {
                "choice_id": "choice_route",
                "label": "抵达入口后，把愿意公开的路线面递给她看",
                "author_label": "抵达入口后，把愿意公开的路线面递给她看",
                "choice_mode": "action",
            }
        ],
    )

    assert calls == 1
    assert result["dialogue"] == "先到测试区入口吧，到了那里再看验证路线。"
    assert result["choice_rewrites"] == []


@pytest.mark.parametrize(
    "performed_dialogue",
    [
        "要是中途本小姐想停或者换地方，我们就当场商量，不许有意见喵。",
        "临时起意的事由我们共同决定，不过得先问过本小姐才行。",
    ],
)
def test_author_consent_boundary_rejects_real_single_party_approval_phrases(
    performed_dialogue,
):
    """真实演绎出现的单方否决或批准句式不能伪装成共同决定。"""  # noqa: DOCSTRING_CJK
    assert (
        llm._violates_author_consent_boundary(
            "中途想停或改路线时一起商量，由我们两个决定。",
            performed_dialogue,
            self_name="本小姐",
        )
        is True
    )


@pytest.mark.asyncio
async def test_graph_progress_uses_author_callback_for_narration(monkeypatch):
    """模型不得把“等待同意”擅自写成已经按下播放键。"""  # noqa: DOCSTRING_CJK

    class _FakeClient:
        """返回会抢跑下一节点动作的可控模型结果。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return type(
                "Result",
                (),
                {
                    "content": '{"narration":"她直接按下了播放键。","dialogue":"等我准备好再说喵。","choice_rewrites":[]}'
                },
            )()

    async def _create_fake_client(*_args, **_kwargs):
        """绕过真实网络并返回抢跑剧情的客户端。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="霜瞳",
        story={"background": "旧档案室"},
        scene={"text": "磁带机还没有启动。"},
        node={"scripted_dialogue": "我还没准备好喵。", "summary": "玩家等待猫娘同意。"},
        user_message="把手停在播放键旁，等她亲自决定",
        progress_kind="graph_progress",
        callback="你没有碰播放键，只把手收回桌边，等猫娘自己作出决定。",
        state={"scene_notes": []},
        recent_turns=[],
    )
    assert result["narration"] == "你没有碰播放键，只把手收回桌边，等猫娘自己作出决定。"
    assert "按下" not in result["narration"]


@pytest.mark.asyncio
async def test_route_model_selects_only_the_current_stable_choice(monkeypatch):
    """独立路由模型只返回当前稳定 Choice，不生成或改写公开演绎。"""  # noqa: DOCSTRING_CJK
    call_types: list[str] = []

    class _FakeClient:
        """返回自由输入对应的稳定 Choice ID。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return type(
                "Result",
                (),
                {
                    "content": '{"route_kind":"authored_choice","matched_choice_id":'
                    '"choice_return_photo","authored_intent_id":"","free_intent":{}}'
                },
            )()

    async def _create_fake_client(*_args, **_kwargs):
        """绕过真实网络并返回自然语言路由结果。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    def _record_call_type(value):
        """记录 Router 的独立职责标签，避免它被算作角色演绎。"""  # noqa: DOCSTRING_CJK
        call_types.append(value)

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    monkeypatch.setattr(llm, "set_call_type", _record_call_type)
    result = await llm.route_free_input_async(
        config_manager=_ModelConfig(),
        story={"background": "酒店走廊"},
        scene={"text": "旧合照落在两人之间。"},
        user_message="我把照片放回文件袋。",
        state={"scene_notes": []},
        recent_turns=[],
        choice_options=[
            {
                "choice_id": "choice_return_photo",
                "label": "把照片放回文件袋，不追问她为何留着",
                "author_label": "把照片放回文件袋，不追问她为何留着",
                "choice_mode": "action",
                "callback": "你将照片平整地放回文件袋。",
                "target_summary": "玩家归还照片。",
                "target_catgirl_intent": "猫娘嘴硬地接过照片。",
                "target_scripted_dialogue": "照片只是夹在旧文件里忘了扔喵。",
            }
        ],
        latent_transitions=[],
    )
    assert result["matched_choice_id"] == "choice_return_photo"
    assert result["authored_intent_id"] == ""
    assert "route_delivery" not in result
    assert call_types == ["theater_router"]


@pytest.mark.asyncio
async def test_route_technical_fallback_has_private_delivery_marker():
    """Router 没有模型配置时必须标记技术降级，不能伪装成真实玩家语义 idle。"""  # noqa: DOCSTRING_CJK
    result = await llm.route_free_input_async(
        config_manager=None,
        story={"background": "室内故事"},
        scene={"text": "桌面放着可见物品。"},
        user_message="继续刚才的行动",
        state={"scene_notes": []},
        recent_turns=[],
        choice_options=[],
        latent_transitions=[],
    )

    assert result["route_kind"] == "idle"
    assert result["route_delivery"] == "technical_degraded"


@pytest.mark.asyncio
async def test_route_model_repairs_uncommitted_invalid_json_once(monkeypatch):
    """Router 坏 JSON 可在更新意图计数前修复一次。"""  # noqa: DOCSTRING_CJK
    call_types: list[str] = []
    outputs = iter(
        [
            "不是 JSON",
            '{"route_kind":"free_intent","matched_choice_id":"","authored_intent_id":"",'
            '"free_intent":{"summary":"查看当前场景内的物品","relation":"new","confidence":0.9},'
            '"residual_intent":{}}',
        ]
    )

    class _FakeClient:
        """返回坏格式首版和合法自由意图修复版。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return type("Result", (), {"content": next(outputs)})()

    async def _create_fake_client(*_args, **_kwargs):
        """绕过网络并复用输出迭代器。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    def _record_call_type(value):
        """确认首版与修复版职责分离。"""  # noqa: DOCSTRING_CJK
        call_types.append(value)

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    monkeypatch.setattr(llm, "set_call_type", _record_call_type)
    result = await llm.route_free_input_async(
        config_manager=_ModelConfig(),
        story={"background": "用户提供的室内故事"},
        scene={"text": "桌面放着几件可见物品。"},
        user_message="我想看看桌上的东西",
        state={"scene_notes": []},
        recent_turns=[],
        choice_options=[],
        latent_transitions=[],
    )
    assert result["route_kind"] == "free_intent"
    assert call_types == ["theater_router", "theater_repair"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_payload", "expected"),
    [
        (
            {
                "classification": "continue_branch",
                "intent_summary": "",
                "exit_evidence_excerpt": "",
                "next_evidence_excerpt": "",
                "confidence": 0.94,
                "response_focus": {},
            },
            {
                "classification": "continue_branch",
                "intent_summary": "",
                "exit_evidence_excerpt": "",
                "next_evidence_excerpt": "",
                "confidence": 0.94,
                "response_focus": {},
                "route_delivery": "accepted",
            },
        ),
        (
            {
                "classification": "intent_handoff",
                "intent_summary": "改去检查备用电源",
                "exit_evidence_excerpt": "先停下天线校准",
                "next_evidence_excerpt": "改去检查备用电源",
                "confidence": 0.96,
                "response_focus": {},
            },
            {
                "classification": "intent_handoff",
                "intent_summary": "改去检查备用电源",
                "exit_evidence_excerpt": "先停下天线校准",
                "next_evidence_excerpt": "改去检查备用电源",
                "confidence": 0.96,
                "response_focus": {},
                "route_delivery": "accepted",
            },
        ),
        (
            {
                "classification": "uncertain",
                "intent_summary": "",
                "exit_evidence_excerpt": "",
                "next_evidence_excerpt": "",
                "confidence": 0.42,
                "response_focus": {},
            },
            {
                "classification": "uncertain",
                "intent_summary": "",
                "exit_evidence_excerpt": "",
                "next_evidence_excerpt": "",
                "confidence": 0.42,
                "response_focus": {},
                "route_delivery": "accepted",
            },
        ),
    ],
)
async def test_active_branch_handoff_classifier_returns_only_semantics_and_hides_authority(
    monkeypatch,
    model_payload,
    expected,
):
    """支线分类器只返回六项模型语义，提示词不得携带任何服务端身份与预算。"""  # noqa: DOCSTRING_CJK
    observed: dict[str, str] = {}

    class _FakeClient:
        """返回可控的支线续演或转交分类。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, messages):
            observed["prompt"] = "\n".join(
                str(getattr(item, "content", "")) for item in messages
            )
            return type(
                "Result", (), {"content": json.dumps(model_payload, ensure_ascii=False)}
            )()

    async def _create_fake_client(*_args, **_kwargs):
        """隔离真实供应商，并保留实际提示词供隐私断言。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    active_branch = {
        "branch_id": "branch_private_classifier",
        "patch": {
            "seed_intent": "继续完成天线校准",
            "objective": "恢复公开通信",
            "turn_budget": 4,
        },
        "created_revision": 3,
        "return_anchor": {"node_id": "node_private_anchor", "goal_id": "goal_private"},
        "turn_budget": 4,
        "max_nonprogress_turns": 2,
        "turns_used": 1,
        "nonprogress_turns": 0,
    }
    result = await llm.classify_active_branch_handoff_async(
        config_manager=_ModelConfig(),
        story={"background": "双人维护一座轨道通信站"},
        scene={"title": "通信舱", "text": "天线控制台仍在等待校准。"},
        user_message="先停下天线校准，改去检查备用电源",
        state={
            "dynamic_intent": {
                "intent_key": "intent_private_classifier",
                "intent_summary": "继续完成天线校准",
                "origin_node_id": "node_private_anchor",
                "streak": 2,
                "evidence_messages": ["先把天线对准"],
            },
            "branch_facts": [
                {
                    "fact_id": "fact_private_classifier",
                    "branch_id": "branch_private_classifier",
                    "subject": "pair",
                    "predicate": "started",
                    "object": "calibration",
                }
            ],
        },
        recent_turns=[{"role": "assistant", "text": "我们继续校准。"}],
        active_branch=active_branch,
    )

    assert result == expected
    assert set(result) == {
        "classification",
        "intent_summary",
        "exit_evidence_excerpt",
        "next_evidence_excerpt",
        "confidence",
        "response_focus",
        "route_delivery",
    }
    assert "继续完成天线校准" in observed["prompt"]
    assert "先停下天线校准，改去检查备用电源" in observed["prompt"]
    for private_value in (
        "branch_private_classifier",
        "intent_private_classifier",
        "node_private_anchor",
        "goal_private",
        "fact_private_classifier",
    ):
        assert private_value not in observed["prompt"]


@pytest.mark.parametrize(
    ("player_message", "response_focus"),
    [
        (
            "校准值为什么又往回掉了？",
            {
                "focus_type": "question",
                "evidence_excerpt": "为什么又往回掉了",
                "requires_state_change": False,
            },
        ),
        (
            "我把频率旋钮调到绿色刻度",
            {
                "focus_type": "action",
                "evidence_excerpt": "把频率旋钮调到绿色刻度",
                "requires_state_change": True,
            },
        ),
    ],
)
def test_branch_handoff_response_focus_preserves_current_branch_question_or_action(
    player_message, response_focus
):
    """活动分类器在不增加调用的前提下保留当前支线最需要回应的原话焦点。"""  # noqa: DOCSTRING_CJK
    parsed = llm._parse_branch_handoff_output(
        json.dumps(
            {
                "classification": "continue_branch",
                "intent_summary": "",
                "exit_evidence_excerpt": "",
                "next_evidence_excerpt": "",
                "confidence": 0.95,
                "response_focus": response_focus,
            },
            ensure_ascii=False,
        ),
        user_message=player_message,
    )

    assert parsed["classification"] == "continue_branch"
    assert parsed["response_focus"] == response_focus
    assert "response_focus" in THEATER_BRANCH_HANDOFF_SYSTEM_PROMPT
    assert "不得改写、删词、补词" in THEATER_BRANCH_HANDOFF_SYSTEM_PROMPT


@pytest.mark.parametrize(
    "response_focus",
    [
        {
            "focus_type": "question",
            "evidence_excerpt": "玩家没有说过的故障",
            "requires_state_change": False,
        },
        {
            "focus_type": "question",
            "evidence_excerpt": "为什么又往回掉了",
            "requires_state_change": False,
            "fact_id": "model_owned",
        },
    ],
)
def test_branch_handoff_rejects_unproven_or_overprivileged_response_focus(
    response_focus,
):
    """补造摘录或夹带事实字段会使整个 handoff 合同失效，不能删字段后继续。"""  # noqa: DOCSTRING_CJK
    raw = json.dumps(
        {
            "classification": "continue_branch",
            "intent_summary": "",
            "exit_evidence_excerpt": "",
            "next_evidence_excerpt": "",
            "confidence": 0.95,
            "response_focus": response_focus,
        },
        ensure_ascii=False,
    )
    assert (
        llm._parse_branch_handoff_output(
            raw,
            user_message="校准值为什么又往回掉了？",
        )
        is None
    )


@pytest.mark.asyncio
async def test_active_branch_handoff_classifier_technical_fallback_is_uncertain():
    """分类模型不可用时必须返回私有技术降级，不能猜测玩家已经退出旧支线。"""  # noqa: DOCSTRING_CJK
    result = await llm.classify_active_branch_handoff_async(
        config_manager=None,
        story={"background": "旧档案室"},
        scene={"text": "整理工作尚未完成。"},
        user_message="先聊点别的",
        state={},
        recent_turns=[],
        active_branch={
            "patch": {"seed_intent": "整理旧档案", "objective": "完成公开归档"}
        },
    )

    assert result == {
        "classification": "uncertain",
        "intent_summary": "",
        "exit_evidence_excerpt": "",
        "next_evidence_excerpt": "",
        "confidence": 0.0,
        "response_focus": {},
        "route_delivery": "technical_degraded",
    }


@pytest.mark.asyncio
async def test_active_branch_handoff_classifier_rejects_model_authority_fields(
    monkeypatch,
):
    """模型单次夹带支线身份时整份分类失效，不能只删字段后继续执行 handoff。"""  # noqa: DOCSTRING_CJK
    model_calls = 0

    class _ForgedClient:
        """返回一次带服务端字段的非法对象。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            nonlocal model_calls
            model_calls += 1
            return type(
                "Result",
                (),
                {
                    "content": json.dumps(
                        {
                            "classification": "intent_handoff",
                            "intent_summary": "检查备用电源",
                            "exit_evidence_excerpt": "先停下校准",
                            "next_evidence_excerpt": "检查备用电源",
                            "confidence": 0.99,
                            "response_focus": {},
                            "branch_id": "model_owned_branch",
                        },
                        ensure_ascii=False,
                    )
                },
            )()

    async def _create_forged_client(*_args, **_kwargs):
        """隔离网络，验证非法字段不会触发第二次模型调用。"""  # noqa: DOCSTRING_CJK
        return _ForgedClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_forged_client)
    result = await llm.classify_active_branch_handoff_async(
        config_manager=_ModelConfig(),
        story={"background": "轨道通信站"},
        scene={"text": "校准仍在继续。"},
        user_message="先停下校准，检查备用电源",
        state={},
        recent_turns=[],
        active_branch={
            "patch": {
                "seed_intent": "完成天线校准",
                "objective": "恢复公开通信",
            }
        },
    )

    assert result["classification"] == "uncertain"
    assert result["route_delivery"] == "technical_degraded"
    assert "branch_id" not in result
    # 低置信 continue 同样不能被旧支线消费，否则会把潜在新意图重新交给 Branch Actor。
    assert (
        llm._parse_branch_handoff_output(
            json.dumps(
                {
                    "classification": "continue_branch",
                    "intent_summary": "",
                    "exit_evidence_excerpt": "",
                    "next_evidence_excerpt": "",
                    "confidence": 0.01,
                    "response_focus": {},
                }
            ),
            user_message="先聊点别的",
        )
        is None
    )
    assert model_calls == 1


def test_near_duplicate_dialogue_ignores_punctuation_and_final_neko_particle():
    """只删除句尾“喵”的上一句复述仍应被识别，避免机械连续对白。"""  # noqa: DOCSTRING_CJK
    recent = [
        {
            "role": "assistant",
            "text": "你还记得……算了，记得也不代表什么。今晚我只是来交设备的喵。",
        }
    ]
    assert (
        llm._repeats_recent_dialogue(
            "你还记得，算了，记得也不代表什么。今晚我只是来交设备的。", recent
        )
        is True
    )
    assert llm._repeats_recent_dialogue("我其实还没想好该怎么面对你。", recent) is False


def test_attitude_response_must_not_reanswer_previous_question():
    """玩家评价上一回答时，Actor 不能再次解释上一轮已经回答的问题。"""  # noqa: DOCSTRING_CJK
    recent = [
        {"role": "user", "text": "甜点的制作灵感是什么"},
        {
            "role": "assistant",
            "text": "灵感来自你品尝时放松下来的那一瞬间，我想把那份温柔藏进奶油里。",
        },
    ]
    assert (
        llm._reanswers_previous_question(
            "灵感就是想让尝到的人感受到被珍视的温暖呀，能得到这样的评价我很开心。",
            current_user_message="确实很有层次",
            recent_turns=recent,
            response_focus={
                "focus_type": "attitude",
                "evidence_excerpt": "确实很有层次",
                "requires_state_change": False,
            },
        )
        is True
    )
    assert (
        llm._performance_repair_reason(
            {
                "narration": "",
                "dialogue": "灵感就是想让尝到的人感受到被珍视的温暖呀。",
            },
            progress_kind="roleplay_response",
            user_message="确实很有层次",
            node={},
            character_profile="",
            recent_turns=recent,
            response_focus={
                "focus_type": "attitude",
                "evidence_excerpt": "确实很有层次",
                "requires_state_change": False,
            },
        )
        == "previous_question_reanswered"
    )
    assert (
        llm._reanswers_previous_question(
            "能被你注意到这些层次，我真的很开心。",
            current_user_message="确实很有层次",
            recent_turns=recent,
            response_focus={
                "focus_type": "attitude",
                "evidence_excerpt": "确实很有层次",
                "requires_state_change": False,
            },
        )
        is False
    )


def test_model_choice_rewrites_never_receive_authority():
    """即使模型命中当前稳定 ID，所有静态 Choice 改写也必须被丢弃。"""  # noqa: DOCSTRING_CJK
    result = llm._parse_output(
        '{"narration":"","dialogue":"我一直留着它喵。","choice_rewrites":['
        '{"choice_id":"choice_keep","label":"收好照片，回应她刚才的坦白"},'
        '{"choice_id":"choice_keep","label":"重复覆盖"},'
        '{"choice_id":"choice_unknown","label":"跳到未知结局"},'
        '{"choice_id":"choice_wait","label":"查看 node_id"}]}',
        progress_kind="roleplay_response",
    )
    assert result == {
        "narration": "",
        "dialogue": "我一直留着它喵。",
        "choice_rewrites": [],
    }


def test_model_latent_intent_only_accepts_current_author_whitelist():
    """路由模型只能选择当前作者白名单；可见 Choice 命中时必须压过隐藏意图。"""  # noqa: DOCSTRING_CJK
    observed = llm._parse_route_output(
        '{"route_kind":"authored_intent","matched_choice_id":"",'
        '"authored_intent_id":"intent_impression","free_intent":{}}',
        allowed_choice_ids={"choice_main"},
        allowed_intent_ids={"intent_impression"},
    )
    assert observed["authored_intent_id"] == "intent_impression"

    legacy_observed = llm._parse_route_output(
        '{"matched_choice_id":"","observed_intent_id":"intent_impression"}',
        allowed_choice_ids=set(),
        allowed_intent_ids={"intent_impression"},
    )
    # 升级期仍读取旧字段，但立即归一化为 v2.5 authored_intent_id，不让旧名扩散到后续状态。
    assert legacy_observed["authored_intent_id"] == "intent_impression"

    unknown = llm._parse_route_output(
        '{"route_kind":"authored_intent","matched_choice_id":"",'
        '"authored_intent_id":"intent_unknown","free_intent":{}}',
        allowed_choice_ids=set(),
        allowed_intent_ids={"intent_impression"},
    )
    assert unknown["authored_intent_id"] == ""

    visible_wins = llm._parse_route_output(
        '{"route_kind":"free_intent","matched_choice_id":"choice_main",'
        '"authored_intent_id":"intent_impression","free_intent":{'
        '"summary":"检查记录板","relation":"new","confidence":0.99}}',
        allowed_choice_ids={"choice_main"},
        allowed_intent_ids={"intent_impression"},
    )
    assert visible_wins["matched_choice_id"] == "choice_main"
    assert visible_wins["authored_intent_id"] == ""
    assert "推荐选项优先于隐藏语义候选" in THEATER_ROUTE_SYSTEM_PROMPT


def test_model_free_intent_only_returns_semantics_without_state_authority():
    """通用自由意图只允许摘要、关系和置信度，服务端 ID 与次数不能由模型注入。"""  # noqa: DOCSTRING_CJK
    parsed = llm._parse_route_output(
        '{"route_kind":"free_intent","matched_choice_id":"","authored_intent_id":"",'
        '"free_intent":{"summary":"打开窗边的收音机","relation":"refine","confidence":0.93}}',
        allowed_choice_ids=set(),
        allowed_intent_ids=set(),
    )
    assert parsed == {
        "route_kind": "free_intent",
        "matched_choice_id": "",
        "authored_intent_id": "",
        "free_intent": {
            "summary": "打开窗边的收音机",
            "relation": "refine",
            "confidence": 0.93,
        },
        "residual_intent": {},
        "response_focus": {},
    }

    forged = llm._parse_route_output(
        '{"route_kind":"free_intent","matched_choice_id":"","authored_intent_id":"",'
        '"free_intent":{"summary":"打开窗边的收音机","relation":"continue",'
        '"confidence":0.99,"intent_key":"model_owned","streak":9}}',
        allowed_choice_ids=set(),
        allowed_intent_ids=set(),
    )
    assert forged["route_kind"] == "idle"
    assert forged["free_intent"] == {}


def test_model_choice_residual_intent_is_strict_and_has_no_state_authority():
    """复合输入只允许 Choice 携带短摘要，模型不能借 residual 注入状态字段。"""  # noqa: DOCSTRING_CJK
    parsed = llm._parse_route_output(
        '{"route_kind":"authored_choice","matched_choice_id":"choice_confirm",'
        '"authored_intent_id":"","free_intent":{},"residual_intent":{'
        '"summary":"进入验证区后检查记录板","evidence_excerpt":"然后检查记录板"}}',
        allowed_choice_ids={"choice_confirm"},
        allowed_intent_ids=set(),
    )
    assert parsed == {
        "route_kind": "authored_choice",
        "matched_choice_id": "choice_confirm",
        "authored_intent_id": "",
        "free_intent": {},
        "residual_intent": {
            "summary": "进入验证区后检查记录板",
            "evidence_excerpt": "然后检查记录板",
        },
        "response_focus": {},
    }

    forged = llm._parse_route_output(
        '{"route_kind":"authored_choice","matched_choice_id":"choice_confirm",'
        '"authored_intent_id":"","free_intent":{},"residual_intent":{'
        '"summary":"进入验证区后检查记录板","evidence_excerpt":"然后检查记录板",'
        '"streak":9}}',
        allowed_choice_ids={"choice_confirm"},
        allowed_intent_ids=set(),
    )
    # Choice 仍可安全提交，但越权 residual 必须被整体丢弃。
    assert forged["matched_choice_id"] == "choice_confirm"
    assert forged["residual_intent"] == {}


def test_route_response_focus_requires_exact_current_input_excerpt():
    """回应焦点必须逐字来自本轮完整输入，模型补造或越权字段都不能进入 Actor。"""  # noqa: DOCSTRING_CJK
    player_message = "先把门关上。控制台为什么突然变红了？"
    parsed = llm._parse_route_output(
        '{"route_kind":"authored_choice","matched_choice_id":"choice_close",'
        '"authored_intent_id":"","free_intent":{},"residual_intent":{},'
        '"response_focus":{"focus_type":"question",'
        '"evidence_excerpt":"控制台为什么突然变红了",'
        '"requires_state_change":false}}',
        allowed_choice_ids={"choice_close"},
        allowed_intent_ids=set(),
        user_message=player_message,
    )
    assert parsed["response_focus"] == {
        "focus_type": "question",
        "evidence_excerpt": "控制台为什么突然变红了",
        "requires_state_change": False,
    }

    forged = llm._parse_route_output(
        '{"route_kind":"authored_choice","matched_choice_id":"choice_close",'
        '"authored_intent_id":"","free_intent":{},"residual_intent":{},'
        '"response_focus":{"focus_type":"question",'
        '"evidence_excerpt":"引擎已经彻底损坏",'
        '"requires_state_change":false}}',
        allowed_choice_ids={"choice_close"},
        allowed_intent_ids=set(),
        user_message=player_message,
    )
    assert forged["matched_choice_id"] == "choice_close"
    assert forged["response_focus"] == {}


def test_idle_route_keeps_valid_response_focus_for_vertical_drilling():
    """普通追问即使不推进任何边，也必须把有来源的焦点交给同节点 Actor。"""  # noqa: DOCSTRING_CJK
    player_message = "你刚才看到那封信时，为什么突然沉默了？"
    parsed = llm._parse_route_output(
        '{"route_kind":"idle","matched_choice_id":"",'
        '"authored_intent_id":"","free_intent":{},"residual_intent":{},'
        '"response_focus":{"focus_type":"attitude",'
        '"evidence_excerpt":"为什么突然沉默了",'
        '"requires_state_change":false}}',
        allowed_choice_ids=set(),
        allowed_intent_ids=set(),
        user_message=player_message,
    )

    assert parsed["route_kind"] == "idle"
    assert parsed["response_focus"] == {
        "focus_type": "attitude",
        "evidence_excerpt": "为什么突然沉默了",
        "requires_state_change": False,
    }


def test_model_residual_intent_is_ignored_without_authored_choice():
    """没有命中当前 Choice 时，模型不能单独创建 Pending Intent。"""  # noqa: DOCSTRING_CJK
    parsed = llm._parse_route_output(
        '{"route_kind":"free_intent","matched_choice_id":"","authored_intent_id":"",'
        '"free_intent":{"summary":"检查记录板","relation":"new","confidence":0.92},'
        '"residual_intent":{"summary":"进入验证区后检查记录板","evidence_excerpt":"然后检查记录板"}}',
        allowed_choice_ids=set(),
        allowed_intent_ids=set(),
    )
    assert parsed["route_kind"] == "free_intent"
    assert parsed["residual_intent"] == {}


@pytest.mark.parametrize(
    ("relation", "confidence"),
    [("same", 0.99), ("continue", 0.49), ("continue", True), ("continue", 1.01)],
)
def test_model_free_intent_rejects_unknown_relation_or_invalid_confidence(
    relation, confidence
):
    """未知关系和低可信判断只能退回普通闲聊，不能改变动态意图状态。"""  # noqa: DOCSTRING_CJK
    raw = json.dumps(
        {
            "route_kind": "free_intent",
            "matched_choice_id": "",
            "authored_intent_id": "",
            "free_intent": {
                "summary": "打开窗边的收音机",
                "relation": relation,
                "confidence": confidence,
            },
        },
        ensure_ascii=False,
    )
    parsed = llm._parse_route_output(
        raw, allowed_choice_ids=set(), allowed_intent_ids=set()
    )
    assert parsed["route_kind"] == "idle"
    assert parsed["free_intent"] == {}


def test_roleplay_prompt_routes_only_explicit_current_choice_completion():
    """独立路由器结合上下文、推荐边和隐藏边判断，演绎提示不再承担推进。"""  # noqa: DOCSTRING_CJK
    assert "询问原因" in THEATER_ROUTE_SYSTEM_PROMPT
    assert "否定" in THEATER_ROUTE_SYSTEM_PROMPT
    assert "唯一" in THEATER_ROUTE_SYSTEM_PROMPT
    assert "复合句" in THEATER_ROUTE_SYSTEM_PROMPT
    assert "“那就……出发？”" in THEATER_ROUTE_SYSTEM_PROMPT
    assert "“为什么出发？”“现在出发吗？”“你想出发吗？”" in THEATER_ROUTE_SYSTEM_PROMPT
    assert "choice_rewrites 必须始终返回空数组" in THEATER_TURN_SYSTEM_PROMPT
    # 演绎器只理解待选动作，不再拥有任何 Choice 显示层编辑能力。
    assert "Choice 显示文案完全由作者 Story Package 控制" in THEATER_TURN_SYSTEM_PROMPT

    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="霜瞳",
        story={"background": "活动厅", "theme": "久别重逢"},
        scene={"title": "灯影里的重逢", "text": "旧合照落在两人之间。"},
        node={"title": "认出彼此", "summary": "两人已经认出对方。"},
        user_message="我把照片放回文件袋。",
        progress_kind="roleplay_response",
        callback="",
        public_state={},
        recent_turns=[],
        character_profile="",
        choice_options=[
            {
                "choice_id": "choice_return_photo",
                "label": "把照片放回文件袋，不追问她为何留着",
                "choice_mode": "action",
                "callback": "你将照片平整地放回文件袋。",
                "target_summary": "玩家归还照片并尊重猫娘是否解释。",
                "target_catgirl_intent": "猫娘嘴硬地接过照片。",
                "completion_phrases": ["放回去", "还给她"],
            }
        ],
    )
    payload, internal_rules = _prompt_sections(user_prompt)
    candidate = payload["当前可推进选项"][0]
    assert candidate["choice_id"] == "choice_return_photo"
    # 未提交回调不能进入普通 Actor 的公开演绎上下文，避免被误写成已经发生的事实。
    assert "作者回调" not in candidate
    assert "尚未执行" in internal_rules["未提交选项边界"]
    assert "目标结果" not in candidate
    assert "作者完成表达" not in candidate
    assert "当前选项路由语义" not in internal_rules
    assert (
        "句末单独的问号不能覆盖玩家已经明确说出的行动或接受"
        in internal_rules["本轮回应要求"]
    )


def test_character_profile_only_reads_current_configured_catgirl(tmp_path):
    """人格摘要只能读取当前已配置猫娘，路径片段和其他猫娘都必须被拒绝。"""  # noqa: DOCSTRING_CJK
    safe_path = tmp_path / "memory" / "安全猫娘" / "persona.json"
    safe_path.parent.mkdir(parents=True)
    safe_path.write_text(
        json.dumps({"neko": {"facts": [{"text": "喜欢雨天散步"}]}}),
        encoding="utf-8",
    )
    escaped_path = tmp_path / "private" / "persona.json"
    escaped_path.parent.mkdir(parents=True)
    escaped_path.write_text(
        json.dumps({"neko": {"facts": [{"text": "不应泄露的秘密"}]}}),
        encoding="utf-8",
    )
    config = _CharacterConfig(tmp_path)

    assert llm._load_character_profile(config, "安全猫娘") == "喜欢雨天散步"
    assert llm._load_character_profile(config, "../private") == ""
    assert llm._load_character_profile(config, "其他猫娘") == ""


@pytest.mark.asyncio
async def test_authority_surfaces_reject_truncated_player_message_without_model_call(
    monkeypatch,
):
    """玩家原话只要超过完整预算，Router、Handoff 与 Branch Actor 都必须直接降级。"""  # noqa: DOCSTRING_CJK
    model_calls = 0

    async def _unexpected_model_call(*_args, **_kwargs):
        """任何模型调用都表示截断片段仍进入了权威语义链。"""  # noqa: DOCSTRING_CJK
        nonlocal model_calls
        model_calls += 1
        raise AssertionError("truncated player message must not reach model")

    monkeypatch.setattr(llm, "_invoke_model_once", _unexpected_model_call)
    long_message = "继续完成眼前动作，" * 220 + "但是不要执行当前按钮"

    route = await llm.route_free_input_async(
        config_manager=_ModelConfig(),
        story={"background": "合成测试空间"},
        scene={"title": "档案室", "text": "桌面仍保持原样。"},
        user_message=long_message,
        state={},
        recent_turns=[],
        choice_options=[{"choice_id": "choice_archive", "label": "提交档案"}],
        latent_transitions=[],
    )
    handoff = await llm.classify_active_branch_handoff_async(
        config_manager=_ModelConfig(),
        story={"background": "合成测试空间"},
        scene={"title": "档案室", "text": "桌面仍保持原样。"},
        user_message=long_message,
        state={},
        recent_turns=[],
        active_branch={"patch": {"seed_intent": "整理档案", "objective": "完成归档"}},
    )
    actor = await llm.generate_branch_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="测试猫娘",
        story={"background": "合成测试空间"},
        scene={"title": "档案室", "text": "桌面仍保持原样。"},
        user_message=long_message,
        state={},
        recent_turns=[],
        active_branch={
            "branch_id": "branch_private",
            "patch": {
                "seed_intent": "整理档案",
                "objective": "完成归档",
                "allowed_new_facts": [],
                "beat_outline": [],
                "exit_candidates": [],
            },
        },
        branch_facts=[],
    )

    assert route["route_delivery"] == "technical_degraded"
    assert handoff["route_delivery"] == "technical_degraded"
    assert actor["turn_delivery"] == "technical_degraded"
    assert actor["fact_candidates"] == []
    assert "不要执行当前按钮" not in actor["dialogue"]
    assert model_calls == 0


@pytest.mark.asyncio
async def test_planner_rejects_truncated_intent_evidence_without_model_call(
    monkeypatch,
):
    """Planner 不能把截断后的意图摘要或证据规划成可执行 Patch。"""  # noqa: DOCSTRING_CJK

    async def _unexpected_model_call(*_args, **_kwargs):
        """截断证据必须在传输前被拒绝。"""  # noqa: DOCSTRING_CJK
        raise AssertionError("truncated planner evidence must not reach model")

    monkeypatch.setattr(llm, "_invoke_model_once", _unexpected_model_call)
    result = await llm.plan_runtime_branch_async(
        config_manager=_ModelConfig(),
        story={"background": "合成测试空间"},
        scene={"title": "温室", "text": "灌溉台仍待确认。"},
        current_node_id="node_fixture",
        current_node={"node_id": "node_fixture", "title": "检查温室"},
        state={},
        dynamic_intent={
            "intent_summary": "检查灌溉台",
            "evidence_messages": ["继续检查灌溉台，" * 180 + "但先不要打开阀门"],
        },
        recent_turns=[],
    )

    assert result is None


@pytest.mark.asyncio
async def test_branch_actor_rejects_internal_identifier_leak_and_uses_safe_fallback(
    monkeypatch,
):
    """模型把 Goal 等机器引用说出口时，整份演出和事实候选都不能公开或提交。"""  # noqa: DOCSTRING_CJK

    async def _leaking_model_call(*_args, **_kwargs):
        """返回结构合法但泄漏稳定 Goal ID 的活动支线演出。"""  # noqa: DOCSTRING_CJK
        return type(
            "Result",
            (),
            {
                "content": (
                    '{"narration":"她看向记录板。","dialogue":"目标 goal_archive_ready 已准备好了。",'
                    '"fact_candidates":[]}'
                )
            },
        )()

    monkeypatch.setattr(llm, "_invoke_model_once", _leaking_model_call)
    result = await llm.generate_branch_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="测试猫娘",
        story={"background": "合成测试空间"},
        scene={"title": "档案室", "text": "记录板仍在桌面。"},
        user_message="我再核对一次记录",
        state={},
        recent_turns=[],
        active_branch={
            "branch_id": "branch-archive",
            "patch": {
                "seed_intent": "核对 branch-archive 的归档记录",
                "objective": "完成公开核对",
                "allowed_new_facts": [],
                "beat_outline": [],
                "exit_candidates": [
                    {"kind": "converge", "goal_id": "goal_archive_ready"}
                ],
            },
        },
        branch_facts=[],
    )

    assert result["turn_delivery"] == "technical_degraded"
    assert result["fact_candidates"] == []
    assert "goal_archive_ready" not in result["dialogue"]
    assert "branch-archive" not in result["dialogue"]
    assert llm._exposes_internal_runtime_detail("我们还剩两个回合预算", set()) is True


def test_internal_identifier_guard_includes_machine_fact_values():
    """模型可见的事实三元组值若是机器 token，也不能被原样说给玩家。"""  # noqa: DOCSTRING_CJK
    identifiers = llm._private_runtime_identifiers(
        {
            "narrative_facts": [
                {
                    "subject": "player",
                    "predicate": "follows",
                    "object": "seven_item_date_list",
                }
            ],
            "catalog_items": [
                {
                    "content_id": "content_hot_cocoa",
                    "fact_object": "hot_cocoa_machine_value",
                }
            ],
        }
    )

    assert "seven_item_date_list" in identifiers
    assert "content_hot_cocoa" in identifiers
    assert "hot_cocoa_machine_value" in identifiers
    assert (
        llm._exposes_internal_runtime_detail(
            "我们继续 seven_item_date_list 的安排。",
            identifiers,
        )
        is True
    )


def test_contextual_fallback_uses_only_bounded_public_anchors():
    """不同题材只复用各自公开锚点，内部字段和机器 ID 必须被清掉。"""  # noqa: DOCSTRING_CJK
    archive = llm.fallback_branch_turn(
        lanlan_name="测试猫娘",
        scene={"title": "旧档案室"},
        user_message="任意原话不应被复述",
        activity_summary="核对移交清单",
        has_committed_progress=True,
    )
    greenhouse = llm.fallback_branch_turn(
        lanlan_name="测试猫娘",
        scene={"title": "玻璃温室"},
        user_message="另一段原话也不应被复述",
        activity_summary="检查滴灌管线",
        has_committed_progress=False,
    )
    filtered = llm.fallback_branch_turn(
        lanlan_name="测试猫娘",
        scene={"title": "node_id_private"},
        user_message="不要复述",
        activity_summary="处理 goal_private",
    )

    assert "旧档案室" in archive["dialogue"]
    assert "核对移交清单" in archive["dialogue"]
    assert "已经发生的进展都还算数" in archive["dialogue"]
    assert "玻璃温室" in greenhouse["dialogue"]
    assert "检查滴灌管线" in greenhouse["dialogue"]
    assert archive["dialogue"] != greenhouse["dialogue"]
    assert "node_id_private" not in filtered["dialogue"]
    assert "goal_private" not in filtered["dialogue"]


def test_historical_long_user_turn_is_omitted_instead_of_truncated():
    """历史玩家原话超过预算时整条退出模型上下文，不能只留下正向前缀。"""  # noqa: DOCSTRING_CJK
    long_message = "继续执行当前行动，" * 80 + "但是最后决定不要执行"

    result = llm._recent_public_turns(
        [
            {"role": "user", "text": long_message},
            {"role": "assistant", "text": "我会先停下来确认。", "narration": ""},
        ]
    )

    assert result == [
        {"role": "assistant", "dialogue": "我会先停下来确认。", "narration": ""}
    ]


@pytest.mark.parametrize(
    ("dialogue", "narration"),
    [
        (
            "我们继续按原计划前进。" * 80 + "不过最后不要执行这个计划。",
            "她看向前方。",
        ),
        (
            "我先确认一下喵。",
            "她伸手准备打开舱门。" * 80 + "但她最后没有打开舱门。",
        ),
    ],
)
def test_historical_long_assistant_turn_is_omitted_instead_of_truncated(
    dialogue,
    narration,
):
    """猫娘对白或旁白句尾无法完整保留时，整回合退出上下文而不是留下相反前缀。"""  # noqa: DOCSTRING_CJK
    result = llm._recent_public_turns(
        [
            {"role": "assistant", "text": dialogue, "narration": narration},
            {"role": "assistant", "text": "我会完整确认后再行动。", "narration": ""},
        ]
    )

    assert result == [
        {
            "role": "assistant",
            "dialogue": "我会完整确认后再行动。",
            "narration": "",
        }
    ]


def test_internal_detail_guard_uses_exact_ids_without_blocking_story_debug_action():
    """状态 ID 和明确框架术语必须拦截，但题材内 debug 动作不应被误伤。"""  # noqa: DOCSTRING_CJK
    private_ids = llm._private_runtime_identifiers(
        {"used_prop_ids": ["prop_secret_item"], "clue_ids": ["clue_hidden_mark"]}
    )

    assert (
        llm._exposes_internal_runtime_detail("拿起 prop_secret_item", private_ids)
        is True
    )
    assert (
        llm._exposes_internal_runtime_detail("让我先 debug 一下这台设备", private_ids)
        is False
    )
    assert (
        llm._exposes_internal_runtime_detail("系统 debug 字段已经开启", private_ids)
        is False
    )
    assert (
        llm._exposes_internal_runtime_detail("内部模型 prompt 已经开启", private_ids)
        is True
    )
    assert llm._bounded_public_fallback_anchor("prop_secret_item") == ""
