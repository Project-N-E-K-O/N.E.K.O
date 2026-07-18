"""验证 v2.5 活动支线回合的事实提交、Goal 证据和预算决策。"""  # noqa: DOCSTRING_CJK

from copy import deepcopy

import pytest

from services.theater import branch_lifecycle, branch_runtime, story_loader
from tests.utils.theater_story_fixture import THEATER_TEST_STORY_ID


def _validated_patch() -> dict:
    """构造当前测试 Story 合同允许的公开交换 Patch。"""  # noqa: DOCSTRING_CJK
    return {
        "origin_node_id": "node_contract_anchor",
        "seed_intent": "选择桌上的公开测试牌",
        "objective": "完成一次双方都公开参与的测试牌交换",
        "entry_callback": "双方仍站在记录板前，尚未完成交换。",
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
        "forbidden_assumptions": [],
        "beat_outline": [
            {
                "beat_id": "beat_select",
                "objective": "确认玩家选择",
                "observable_action": "玩家公开选定一枚测试牌",
                "player_choice_label": "拿起测试牌，确认自己的选择",
                "exit_preparation": ["player_selected_item"],
            },
            {
                "beat_id": "beat_exchange",
                "objective": "完成双方交换",
                "observable_action": "把双方确认的测试牌递到猫娘手中",
                "player_choice_label": "把测试牌递给她并确认公开结果",
                "exit_preparation": ["catgirl_received_item", "public_exchange_completed"],
            },
        ],
        "exit_candidates": [{"kind": "converge", "goal_id": "goal_complete_public_exchange"}],
    }


def _active_branch(*, max_nonprogress_turns: int = 2) -> dict:
    """使用服务端身份构造可执行活动支线。"""  # noqa: DOCSTRING_CJK
    return branch_lifecycle.build_active_runtime_branch(
        _validated_patch(),
        branch_id="branch_server_runtime",
        created_revision=2,
        return_anchor={"node_id": "node_contract_anchor", "goal_id": "goal_complete_public_exchange"},
        max_nonprogress_turns=max_nonprogress_turns,
    )


def _fact_candidate(fact_role: str) -> dict:
    """按事实角色构造一个将在本轮公开演出中发生的候选事实。"""  # noqa: DOCSTRING_CJK
    definitions = {
        "player_selected_item": (
            "ordinary_test_item",
            "player",
            "selected_item",
            "public_test_token",
            "slot_public_exchange_item",
        ),
        "catgirl_received_item": (
            "observable_action",
            "active_catgirl",
            "received_item",
            "public_test_token",
            "",
        ),
        "public_exchange_completed": (
            "observable_action",
            "pair",
            "completed_exchange",
            "public_test_exchange",
            "",
        ),
    }
    fact_type, subject, predicate, object_value, slot_id = definitions[fact_role]
    return {
        "goal_id": "goal_complete_public_exchange",
        "fact_type": fact_type,
        "fact_role": fact_role,
        "subject": subject,
        "predicate": predicate,
        "object": object_value,
        "content_slot_id": slot_id,
    }


@pytest.mark.asyncio
async def test_branch_turn_commits_valid_fact_with_server_identity_and_progress():
    """合法公开事实获得服务端 ID，并成为重置非推进计数的唯一进度依据。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    branch = _active_branch()
    branch["nonprogress_turns"] = 1
    result = branch_runtime.apply_actor_turn(
        story=story,
        active_branch=branch,
        existing_facts=[],
        fact_candidates=[_fact_candidate("player_selected_item")],
        source_revision=3,
    )
    assert result["ok"] is True
    assert result["active_branch"]["turns_used"] == 1
    assert result["active_branch"]["nonprogress_turns"] == 0
    assert result["decision"] == {"action": "continue", "exit_kind": ""}
    committed = result["branch_facts"][0]
    assert committed["branch_id"] == "branch_server_runtime"
    assert committed["fact_id"].startswith("branch_fact_")
    assert committed["source_revision"] == 3


@pytest.mark.asyncio
async def test_branch_turn_completes_goal_only_after_all_author_evidence():
    """只有作者 Goal 的全部证据角色都已提交时，生命周期才允许汇流。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    result = branch_runtime.apply_actor_turn(
        story=story,
        active_branch=_active_branch(),
        existing_facts=[],
        fact_candidates=[
            _fact_candidate("player_selected_item"),
            _fact_candidate("catgirl_received_item"),
            _fact_candidate("public_exchange_completed"),
        ],
        source_revision=3,
    )
    assert result["ok"] is True
    assert result["completed_goal_id"] == "goal_complete_public_exchange"
    assert result["decision"] == {
        "action": "converge",
        "exit_kind": "goal_converged",
        "goal_id": "goal_complete_public_exchange",
    }


@pytest.mark.asyncio
async def test_invalid_fact_rejects_actor_candidates_without_partial_commit():
    """任一候选越过 Patch 权限时整组候选失效，不能只提交其中看似合法的部分。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    branch = _active_branch()
    original = deepcopy(branch)
    invalid = _fact_candidate("player_selected_item")
    invalid["fact_role"] = "model_invented_role"
    result = branch_runtime.apply_actor_turn(
        story=story,
        active_branch=branch,
        existing_facts=[],
        fact_candidates=[_fact_candidate("player_selected_item"), invalid],
        source_revision=3,
    )
    assert result == {"ok": False, "reason": "fact_candidate_invalid"}
    assert branch == original


@pytest.mark.asyncio
async def test_empty_fact_turn_consumes_budget_and_closes_at_nonprogress_limit():
    """没有合法公开事实的回应仍消耗回合，并按作者非推进上限安全关闭。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    result = branch_runtime.apply_actor_turn(
        story=story,
        active_branch=_active_branch(max_nonprogress_turns=1),
        existing_facts=[],
        fact_candidates=[],
        source_revision=3,
    )
    assert result["ok"] is True
    assert result["decision"] == {
        "action": "close_to_anchor",
        "exit_kind": "nonprogress_exhausted",
    }
    assert result["active_branch"]["turns_used"] == 1
    assert result["active_branch"]["nonprogress_turns"] == 1


def test_technical_degraded_turn_preserves_facts_and_skips_goal_evaluation():
    """技术降级路径不消费 Fact Candidate，也不改变支线预算、事实、Goal 或 Ending。"""  # noqa: DOCSTRING_CJK
    branch = _active_branch()
    branch["turns_used"] = 1
    branch["nonprogress_turns"] = 1
    existing_fact = {
        "branch_id": "branch_server_runtime",
        "fact_id": "branch_fact_existing",
        "source_revision": 2,
    }
    original_branch = deepcopy(branch)
    original_facts = [deepcopy(existing_fact)]

    result = branch_runtime.apply_technical_degraded_turn(
        active_branch=branch,
        existing_facts=original_facts,
    )

    assert result["ok"] is True
    assert result["active_branch"] == original_branch
    assert result["branch_facts"] == original_facts
    assert result["branch_facts"] is not original_facts
    assert result["committed_facts"] == []
    assert result["completed_goal_id"] == ""
    assert result["ending_domain_id"] == ""
    assert result["decision"] == {"action": "continue", "exit_kind": ""}


def test_dynamic_choice_is_stable_and_advances_only_from_committed_roles():
    """动态按钮绑定当前未完成 Beat；刷新稳定，事实完成后才切换到下一行动方向。"""  # noqa: DOCSTRING_CJK
    branch = _active_branch()
    first = branch_runtime.dynamic_choice_options(branch, [])
    restored = branch_runtime.dynamic_choice_options(deepcopy(branch), [])
    assert first == restored
    assert len(first) == 1
    assert first[0]["label"] == "拿起测试牌，确认自己的选择"
    assert first[0]["choice_mode"] == "action"
    assert first[0]["choice_id"].startswith("branch_choice_")
    assert branch["branch_id"] not in first[0]["choice_id"]
    assert first[0]["beat_id"] not in first[0]["choice_id"]

    committed = {
        "branch_id": branch["branch_id"],
        "fact_role": "player_selected_item",
    }
    second = branch_runtime.dynamic_choice_options(branch, [committed])
    assert len(second) == 1
    assert second[0]["label"] == "把测试牌递给她并确认公开结果"
    assert second[0]["choice_id"] != first[0]["choice_id"]
    assert branch_runtime.resolve_dynamic_choice(branch, [committed], second[0]["choice_id"]) == second[0]
    assert branch_runtime.resolve_dynamic_choice(branch, [committed], first[0]["choice_id"]) == {}
    assert branch_runtime.resolve_dynamic_choice(branch, [committed], "branch_choice_forged") == {}
    corrupted = deepcopy(branch)
    corrupted["turns_used"] = corrupted["turn_budget"]
    assert branch_runtime.dynamic_choice_options(corrupted, []) == []


def test_legacy_beat_without_player_choice_label_never_exposes_stage_direction():
    """旧 Patch 没有玩家文案时继续允许自由输入，但绝不把 observable_action 投影成按钮。"""  # noqa: DOCSTRING_CJK
    branch = _active_branch()
    for beat in branch["patch"]["beat_outline"]:
        # 模拟升级前已经保存的活动 Patch；内部舞台描述仍供 Actor 使用。
        beat.pop("player_choice_label")
    assert branch_runtime.dynamic_choice_options(branch, []) == []
