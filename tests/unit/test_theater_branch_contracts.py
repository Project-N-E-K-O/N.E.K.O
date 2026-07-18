"""验证 v2.5 临时支线候选与服务端权威对象的合同边界。"""  # noqa: DOCSTRING_CJK

from copy import deepcopy

import pytest

from services.theater import branch_contracts, branch_planner, story_loader
from tests.utils.theater_story_fixture import THEATER_TEST_STORY_ID


def _valid_exchange_patch() -> dict:
    """构造能完成公开交换 Goal 的最小测试支线候选。"""  # noqa: DOCSTRING_CJK
    return {
        "origin_node_id": "node_contract_anchor",
        "seed_intent": "选择公开测试牌并交给猫娘",
        "objective": "完成一段围绕测试牌选择与交换的双人互动",
        "entry_callback": "玩家仍把公开测试牌留在手中，尚未完成交换。",
        "turn_budget": 4,
        "content_slot_ids": ["slot_public_exchange_item"],
        "allowed_new_facts": [
            {
                "fact_type": "ordinary_test_item",
                "fact_role": "player_selected_item",
                "content_slot_id": "slot_public_exchange_item",
            },
            {
                "fact_type": "observable_action",
                "fact_role": "catgirl_received_item",
                "content_slot_id": "",
            },
            {
                "fact_type": "observable_action",
                "fact_role": "public_exchange_completed",
                "content_slot_id": "",
            },
        ],
        "forbidden_assumptions": [
            {
                "subject": "outside_character",
                "predicate": "speaks_directly",
                "object": "scene_dialogue",
            }
        ],
        "beat_outline": [
            {
                "beat_id": "beat_confirm_test_token",
                "objective": "确认玩家仍选择公开测试牌",
                "observable_action": "公开测试牌仍在玩家手中",
                "player_choice_label": "拿起选中的测试牌并确认编号",
                "exit_preparation": ["player_selected_item"],
            },
            {
                "beat_id": "beat_exchange_test_token",
                "objective": "公开完成测试牌交换",
                "observable_action": "猫娘接过测试牌，双方结束验证",
                "player_choice_label": "把选好的测试牌递给她并确认结果",
                "exit_preparation": [
                    "catgirl_received_item",
                    "public_exchange_completed",
                ],
            },
        ],
        "exit_candidates": [
            {"kind": "converge", "goal_id": "goal_complete_public_exchange"}
        ],
    }


def _valid_exchange_fact_candidate() -> dict:
    """构造尚未携带任何服务端 ID 的公开物件事实候选。"""  # noqa: DOCSTRING_CJK
    return {
        "goal_id": "goal_complete_public_exchange",
        "fact_type": "ordinary_test_item",
        "fact_role": "player_selected_item",
        "subject": "player",
        "predicate": "selected_item",
        "object": "public_test_token",
        "content_slot_id": "slot_public_exchange_item",
        "public_entity": {
            "kind": "prop",
            "label": "公开测试牌",
            "status": "selected",
        },
    }


@pytest.mark.asyncio
async def test_runtime_branch_patch_accepts_authorized_goal_slots_and_evidence():
    """合法 Patch 必须匹配当前节点、作者预算、内容槽位和 Goal 证据。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    patch = _valid_exchange_patch()
    validated = branch_contracts.validate_runtime_branch_patch(
        patch,
        story=story,
        current_node_id="node_contract_anchor",
    )
    # 校验器返回隔离副本，并由服务端强制并入作者 seed 边界；Planner 原对象保持不变。
    expected = dict(patch)
    expected["forbidden_assumptions"] = story["seed"]["forbidden_assumptions"]
    assert validated == expected
    assert validated is not patch
    assert patch["forbidden_assumptions"] == [
        {
            "subject": "outside_character",
            "predicate": "speaks_directly",
            "object": "scene_dialogue",
        }
    ]


@pytest.mark.asyncio
async def test_runtime_branch_patch_accepts_story_without_seed_forbidden_assumptions():
    """作者未声明 seed 禁止事实时按空集合处理，不能阻断合法旧 Story。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    story = deepcopy(story)
    story.pop("seed", None)
    patch = _valid_exchange_patch()

    validated = branch_contracts.validate_runtime_branch_patch(
        patch,
        story=story,
        current_node_id="node_contract_anchor",
    )

    assert validated == patch
    assert validated is not patch


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_label",
    [
        "双方完成测试牌交换并确认结果",
        "把测试牌递过去，猫娘点头收下并露出笑容",
    ],
)
async def test_runtime_branch_patch_rejects_choice_label_that_controls_other_participants(
    bad_label,
):
    """玩家按钮不能使用舞台总结，也不能预先规定猫娘或双方的反应与结果。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    patch = _valid_exchange_patch()
    patch["beat_outline"][1]["player_choice_label"] = bad_label
    with pytest.raises(ValueError, match="player choice label"):
        branch_contracts.validate_runtime_branch_patch(
            patch,
            story=story,
            current_node_id="node_contract_anchor",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_label",
    [
        "执行 goal_complete_public_exchange",
        "查看 node_id 再决定",
        "Open node-123 before leaving",
        "Inspect prop-secret-item",
    ],
)
async def test_runtime_branch_patch_rejects_machine_reference_before_choice_projection(
    bad_label,
):
    """Planner 的内部字段或稳定引用必须在动态按钮投影前拒绝。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    patch = _valid_exchange_patch()
    patch["beat_outline"][0]["player_choice_label"] = bad_label

    with pytest.raises(ValueError, match="player choice label"):
        branch_contracts.validate_runtime_branch_patch(
            patch,
            story=story,
            current_node_id="node_contract_anchor",
        )


@pytest.mark.asyncio
async def test_branch_planner_service_returns_only_contract_validated_patch(
    monkeypatch,
):
    """Planner 服务只返回通过作者合同的隔离 Patch，不附加或提交服务端权威字段。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    model_patch = _valid_exchange_patch()

    async def _plan_candidate(**_kwargs):
        """模拟模型返回合法候选，实际合同仍由服务层执行。"""  # noqa: DOCSTRING_CJK
        return model_patch

    monkeypatch.setattr(
        "services.theater.llm.plan_runtime_branch_async", _plan_candidate
    )
    result = await branch_planner.plan_validated_runtime_branch(
        config_manager=None,
        story=story,
        scene={"title": "合同验证区"},
        current_node_id="node_contract_anchor",
        current_node={"title": "确认测试牌"},
        state={"narrative_facts": []},
        dynamic_intent={
            "intent_summary": "选择公开测试牌",
            "evidence_messages": ["看看测试牌"],
        },
        recent_turns=[],
    )
    expected_patch = dict(model_patch)
    expected_patch["forbidden_assumptions"] = story["seed"]["forbidden_assumptions"]
    assert result == {"ok": True, "patch": expected_patch}
    assert result["patch"] is not model_patch


@pytest.mark.asyncio
async def test_branch_planner_rejects_convergence_to_completed_goal(monkeypatch):
    """模型即使再次提出合法形状的旧 Goal Patch，服务端也必须按当前 Session 拒绝。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)

    async def _plan_completed_goal(**_kwargs):
        """模拟 Planner 忽略提示并再次选择已经完成的作者 Goal。"""  # noqa: DOCSTRING_CJK
        return _valid_exchange_patch()

    monkeypatch.setattr(
        "services.theater.llm.plan_runtime_branch_async", _plan_completed_goal
    )
    result = await branch_planner.plan_validated_runtime_branch(
        config_manager=None,
        story=story,
        scene={"title": "任意用户场景"},
        current_node_id="node_contract_anchor",
        current_node={"title": "当前作者节点"},
        state={"completed_goal_ids": ["goal_complete_public_exchange"]},
        dynamic_intent={
            "intent_summary": "再次完成相同行动",
            "evidence_messages": ["再来一次"],
        },
        recent_turns=[],
    )
    assert result == {"ok": False, "reason": "patch_invalid"}


@pytest.mark.asyncio
async def test_branch_planner_service_rejects_invalid_or_missing_candidate(monkeypatch):
    """模型失败或越过作者槽位时不得产生可激活 Patch，更不能留下半支线状态。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    invalid_patch = _valid_exchange_patch()
    invalid_patch["content_slot_ids"] = ["slot_model_invented"]
    candidates = [invalid_patch, None]

    async def _plan_candidate(**_kwargs):
        """依次模拟合同非法输出与模型调用失败。"""  # noqa: DOCSTRING_CJK
        return candidates.pop(0)

    monkeypatch.setattr(
        "services.theater.llm.plan_runtime_branch_async", _plan_candidate
    )
    arguments = {
        "config_manager": None,
        "story": story,
        "scene": {"title": "合同验证区"},
        "current_node_id": "node_contract_anchor",
        "current_node": {"title": "确认测试牌"},
        "state": {"narrative_facts": []},
        "dynamic_intent": {
            "intent_summary": "选择测试牌",
            "evidence_messages": ["看看测试牌"],
        },
        "recent_turns": [],
    }
    assert await branch_planner.plan_validated_runtime_branch(**arguments) == {
        "ok": False,
        "reason": "patch_invalid",
    }
    assert await branch_planner.plan_validated_runtime_branch(**arguments) == {
        "ok": False,
        "reason": "planner_failed",
    }


@pytest.mark.asyncio
async def test_runtime_branch_patch_rejects_protected_server_fields():
    """Planner 不得伪造 branch ID、创建 revision 或当前猫娘身份。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    patch = _valid_exchange_patch()
    patch["branch_id"] = "branch_model_chosen"
    with pytest.raises(ValueError, match="protected field"):
        branch_contracts.validate_runtime_branch_patch(
            patch,
            story=story,
            current_node_id="node_contract_anchor",
        )


@pytest.mark.asyncio
async def test_runtime_branch_patch_rejects_stale_origin_and_unknown_slot():
    """Patch 必须绑定当前提交节点，并且不能引用作者未开放的动态槽位。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    stale = _valid_exchange_patch()
    with pytest.raises(ValueError, match="stale origin"):
        branch_contracts.validate_runtime_branch_patch(
            stale,
            story=story,
            current_node_id="node_contract_exchange",
        )

    unknown_slot = _valid_exchange_patch()
    unknown_slot["content_slot_ids"] = ["slot_model_invented"]
    with pytest.raises(ValueError, match="unknown content slot"):
        branch_contracts.validate_runtime_branch_patch(
            unknown_slot,
            story=story,
            current_node_id="node_contract_anchor",
        )


@pytest.mark.asyncio
async def test_runtime_branch_patch_rejects_missing_goal_evidence_and_bad_exit():
    """Patch 必须能够完成声明的 Goal，且不能自动改绑到未知出口。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    incomplete = _valid_exchange_patch()
    incomplete["allowed_new_facts"] = incomplete["allowed_new_facts"][:1]
    with pytest.raises(ValueError, match="missing goal evidence"):
        branch_contracts.validate_runtime_branch_patch(
            incomplete,
            story=story,
            current_node_id="node_contract_anchor",
        )

    bad_exit = _valid_exchange_patch()
    bad_exit["exit_candidates"] = [
        {"kind": "converge", "goal_id": "goal_model_invented"}
    ]
    with pytest.raises(ValueError, match="unknown convergence goal"):
        branch_contracts.validate_runtime_branch_patch(
            bad_exit,
            story=story,
            current_node_id="node_contract_anchor",
        )


@pytest.mark.asyncio
async def test_branch_fact_candidate_requires_public_observation_and_patch_permission():
    """模型事实只有公开发生且被 Patch 预授权后才可进入提交步骤。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    patch = branch_contracts.validate_runtime_branch_patch(
        _valid_exchange_patch(),
        story=story,
        current_node_id="node_contract_anchor",
    )
    candidate = _valid_exchange_fact_candidate()
    validated = branch_contracts.validate_branch_fact_candidate(
        candidate,
        story=story,
        patch=patch,
        publicly_observed=True,
    )
    assert validated == candidate

    # 未公开动作即使结构完整也只是模型猜测，不能成为权威事实。
    with pytest.raises(ValueError, match="not publicly observed"):
        branch_contracts.validate_branch_fact_candidate(
            candidate,
            story=story,
            patch=patch,
            publicly_observed=False,
        )


@pytest.mark.asyncio
async def test_branch_fact_candidate_rejects_author_forbidden_assumption():
    """模型不能把作者 seed 明确禁止的三元组伪装成合法 Goal 证据。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    patch = branch_contracts.validate_runtime_branch_patch(
        _valid_exchange_patch(),
        story=story,
        current_node_id="node_contract_anchor",
    )
    candidate = _valid_exchange_fact_candidate()
    candidate.update(story["seed"]["forbidden_assumptions"][0])

    with pytest.raises(ValueError, match="forbidden assumption"):
        branch_contracts.validate_branch_fact_candidate(
            candidate,
            story=story,
            patch=patch,
            publicly_observed=True,
        )


@pytest.mark.asyncio
async def test_branch_fact_candidate_rejects_patch_forbidden_assumption():
    """Planner 为当前支线追加的禁止三元组也必须在事实提交门生效。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    patch_candidate = _valid_exchange_patch()
    patch_candidate["forbidden_assumptions"].append(
        {
            "subject": "player",
            "predicate": "selected_item",
            "object": "public_test_token",
        }
    )
    patch = branch_contracts.validate_runtime_branch_patch(
        patch_candidate,
        story=story,
        current_node_id="node_contract_anchor",
    )

    with pytest.raises(ValueError, match="forbidden assumption"):
        branch_contracts.validate_branch_fact_candidate(
            _valid_exchange_fact_candidate(),
            story=story,
            patch=patch,
            publicly_observed=True,
        )


@pytest.mark.asyncio
async def test_branch_fact_candidate_rejects_immutable_fact_conflict():
    """同一主语和谓词不能用另一对象改写作者声明的不可变事实。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    story = deepcopy(story)
    story["world_contract"]["immutable_facts"] = [
        {
            "subject": "test_terminal",
            "predicate": "closes_at",
            "object": "scheduled_close",
        }
    ]
    patch = branch_contracts.validate_runtime_branch_patch(
        _valid_exchange_patch(),
        story=story,
        current_node_id="node_contract_anchor",
    )
    candidate = _valid_exchange_fact_candidate()
    candidate.update(
        {
            "subject": "test_terminal",
            "predicate": "closes_at",
            "object": "unexpected_close",
        }
    )

    with pytest.raises(ValueError, match="immutable fact"):
        branch_contracts.validate_branch_fact_candidate(
            candidate,
            story=story,
            patch=patch,
            publicly_observed=True,
        )


@pytest.mark.asyncio
async def test_branch_fact_candidate_cannot_forge_server_identity():
    """模型不得提交 fact ID、branch ID、revision 或公开实体 ID。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    patch = branch_contracts.validate_runtime_branch_patch(
        _valid_exchange_patch(),
        story=story,
        current_node_id="node_contract_anchor",
    )
    candidate = _valid_exchange_fact_candidate()
    candidate["source_revision"] = 12
    with pytest.raises(ValueError, match="protected field"):
        branch_contracts.validate_branch_fact_candidate(
            candidate,
            story=story,
            patch=patch,
            publicly_observed=True,
        )


@pytest.mark.asyncio
async def test_service_builds_committed_branch_fact_with_authoritative_ids():
    """服务端提交步骤补齐稳定 ID，且不改变已经校验的模型候选。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    patch = branch_contracts.validate_runtime_branch_patch(
        _valid_exchange_patch(),
        story=story,
        current_node_id="node_contract_anchor",
    )
    candidate = branch_contracts.validate_branch_fact_candidate(
        _valid_exchange_fact_candidate(),
        story=story,
        patch=patch,
        publicly_observed=True,
    )
    original = deepcopy(candidate)
    committed = branch_contracts.build_committed_branch_fact(
        candidate,
        branch_id="branch_server_1",
        fact_id="branch_fact_server_1",
        source_revision=12,
        public_entity_id="branch_entity_server_1",
    )
    assert candidate == original
    assert committed["branch_id"] == "branch_server_1"
    assert committed["fact_id"] == "branch_fact_server_1"
    assert committed["source_revision"] == 12
    assert committed["public_entity"]["entity_id"] == "branch_entity_server_1"
    assert (
        branch_contracts.validate_committed_branch_fact_structure(committed)
        == committed
    )

    malformed = deepcopy(committed)
    malformed["public_entity"].pop("entity_id")
    with pytest.raises(ValueError, match="public entity fields"):
        branch_contracts.validate_committed_branch_fact_structure(malformed)


@pytest.mark.asyncio
async def test_public_entity_rejects_status_outside_board_contract():
    """公开实体状态必须映射到现有 Board 分组，不能让模型创造前端状态。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    patch = branch_contracts.validate_runtime_branch_patch(
        _valid_exchange_patch(),
        story=story,
        current_node_id="node_contract_anchor",
    )
    candidate = _valid_exchange_fact_candidate()
    candidate["public_entity"]["status"] = "reserved"
    with pytest.raises(ValueError, match="invalid status"):
        branch_contracts.validate_branch_fact_candidate(
            candidate,
            story=story,
            patch=patch,
            publicly_observed=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_label",
    [
        "prop_secret_item",
        "goal_complete_public_exchange",
        "node-123",
        "公开物件\nsource_revision",
        "过长" * 41,
    ],
)
async def test_public_entity_rejects_internal_label_before_board_projection(bad_label):
    """Actor 的内部引用、换行或超长标签必须在写入 Board 前整项拒绝。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    patch = branch_contracts.validate_runtime_branch_patch(
        _valid_exchange_patch(),
        story=story,
        current_node_id="node_contract_anchor",
    )
    candidate = _valid_exchange_fact_candidate()
    candidate["public_entity"]["label"] = bad_label

    with pytest.raises(ValueError, match="public entity label"):
        branch_contracts.validate_branch_fact_candidate(
            candidate,
            story=story,
            patch=patch,
            publicly_observed=True,
        )


def test_restored_public_entity_rejects_machine_label_before_board_projection():
    """恢复中的旧提交事实也必须重验公开标签，不能把历史脏值重新投影到 Board。"""  # noqa: DOCSTRING_CJK
    committed = branch_contracts.build_committed_branch_fact(
        _valid_exchange_fact_candidate(),
        branch_id="branch_server_1",
        fact_id="branch_fact_server_1",
        source_revision=12,
        public_entity_id="branch_entity_server_1",
    )
    committed["public_entity"]["label"] = "prop_secret_item"

    with pytest.raises(ValueError, match="public entity label"):
        branch_contracts.validate_committed_branch_fact_structure(committed)


@pytest.mark.asyncio
async def test_public_display_labels_allow_natural_chinese_and_english():
    """公开短文本可以使用正常中文、英文与单个自然连字符，不按题材关键词误杀。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    patch = _valid_exchange_patch()
    patch["beat_outline"][0]["player_choice_label"] = (
        "Pick the well-made notebook for a goal-oriented plan"
    )
    validated_patch = branch_contracts.validate_runtime_branch_patch(
        patch,
        story=story,
        current_node_id="node_contract_anchor",
    )
    candidate = _valid_exchange_fact_candidate()
    candidate["public_entity"]["label"] = "手工装订的 well-made notebook"

    validated_candidate = branch_contracts.validate_branch_fact_candidate(
        candidate,
        story=story,
        patch=validated_patch,
        publicly_observed=True,
    )

    assert (
        validated_patch["beat_outline"][0]["player_choice_label"]
        == "Pick the well-made notebook for a goal-oriented plan"
    )
    assert (
        validated_candidate["public_entity"]["label"] == "手工装订的 well-made notebook"
    )


@pytest.mark.asyncio
async def test_branch_history_only_references_committed_facts_from_same_branch():
    """Branch History 只保存结构化索引，不能引用别的支线或未知事实。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    committed_fact = branch_contracts.build_committed_branch_fact(
        _valid_exchange_fact_candidate(),
        branch_id="branch_server_1",
        fact_id="branch_fact_server_1",
        source_revision=12,
        public_entity_id="branch_entity_server_1",
    )
    history = {
        "branch_id": "branch_server_1",
        "completed_goal_ids": ["goal_complete_public_exchange"],
        "key_fact_ids": ["branch_fact_server_1"],
        "exit_kind": "goal_converged",
        "ended_revision": 13,
        "recap": "玩家选择公开测试牌并完成公开交换。",
    }
    validated = branch_contracts.validate_branch_history_entry(
        history,
        story=story,
        branch_facts=[committed_fact],
    )
    assert validated == history

    invalid = deepcopy(history)
    invalid["key_fact_ids"] = ["branch_fact_unknown"]
    with pytest.raises(ValueError, match="unknown branch fact"):
        branch_contracts.validate_branch_history_entry(
            invalid,
            story=story,
            branch_facts=[committed_fact],
        )


@pytest.mark.asyncio
async def test_branch_history_accepts_only_fixed_intent_handoff_exit_kind():
    """意图转交可索引旧支线事实，但退出原因必须来自服务端固定枚举。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    committed_fact = branch_contracts.build_committed_branch_fact(
        _valid_exchange_fact_candidate(),
        branch_id="branch_server_handoff",
        fact_id="branch_fact_before_handoff",
        source_revision=7,
        public_entity_id="branch_entity_before_handoff",
    )
    history = {
        "branch_id": "branch_server_handoff",
        "completed_goal_ids": [],
        "key_fact_ids": ["branch_fact_before_handoff"],
        "exit_kind": "intent_handoff",
        "ended_revision": 8,
    }

    validated = branch_contracts.validate_branch_history_entry(
        history,
        story=story,
        branch_facts=[committed_fact],
    )
    assert validated == {**history, "recap": ""}

    forged = {**history, "exit_kind": "model_requested_another_branch"}
    with pytest.raises(ValueError, match="invalid exit kind"):
        branch_contracts.validate_branch_history_entry(
            forged,
            story=story,
            branch_facts=[committed_fact],
        )
