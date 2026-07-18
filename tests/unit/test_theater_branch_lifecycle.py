"""验证 v2.5 Pending Intent 与活动支线的确定性状态转换。"""  # noqa: DOCSTRING_CJK

from copy import deepcopy

import pytest

from services.theater import branch_lifecycle


def _active_patch() -> dict:
    """返回只包含状态机需要字段的已验证 Patch。"""  # noqa: DOCSTRING_CJK
    return {
        "origin_node_id": "node_contract_anchor",
        "turn_budget": 4,
    }


def test_pending_intent_only_revalidates_at_expected_target():
    """复合输入的剩余意图只能在目标节点和短期 revision 内重新判断。"""  # noqa: DOCSTRING_CJK
    pending = branch_lifecycle.build_pending_intent(
        summary="进入目标节点后检查记录板",
        evidence_excerpt="然后检查记录板",
        source_node_id="node_contract_start",
        target_node_id="node_contract_anchor",
        target_scene_id="scene_contract_setup",
        created_revision=1,
    )
    decision = branch_lifecycle.evaluate_pending_intent(
        pending,
        current_node_id="node_contract_anchor",
        current_scene_id="scene_contract_setup",
        current_revision=1,
        player_denied=False,
        scene_compatible=True,
    )
    assert decision == "revalidate"
    # Pending 只提供下一步路由证据，结构中没有 streak 或活动支线字段。
    assert "streak" not in pending
    assert "active_runtime_branch" not in pending


@pytest.mark.parametrize(
    ("current_node_id", "current_scene_id", "current_revision", "player_denied", "scene_compatible", "expected"),
    [
        (
            "node_contract_anchor",
            "scene_contract_setup",
            3,
            False,
            True,
            "discard_expired",
        ),
        (
            "node_contract_anchor",
            "scene_contract_setup",
            1,
            True,
            True,
            "discard_denied",
        ),
        (
            "node_other",
            "scene_contract_setup",
            1,
            False,
            True,
            "discard_incompatible",
        ),
        (
            "node_contract_anchor",
            "scene_other",
            1,
            False,
            False,
            "discard_incompatible",
        ),
    ],
)
def test_pending_intent_discard_rules_are_deterministic(
    current_node_id,
    current_scene_id,
    current_revision,
    player_denied,
    scene_compatible,
    expected,
):
    """过期、否定、节点或 Scene 不兼容都会直接清除 pending。"""  # noqa: DOCSTRING_CJK
    pending = branch_lifecycle.build_pending_intent(
        summary="进入目标节点后检查记录板",
        evidence_excerpt="然后检查记录板",
        source_node_id="node_contract_start",
        target_node_id="node_contract_anchor",
        target_scene_id="scene_contract_setup",
        created_revision=1,
    )
    assert branch_lifecycle.evaluate_pending_intent(
        pending,
        current_node_id=current_node_id,
        current_scene_id=current_scene_id,
        current_revision=current_revision,
        player_denied=player_denied,
        scene_compatible=scene_compatible,
    ) == expected


def test_active_branch_starts_with_server_identity_and_zero_counters():
    """活动支线只由服务端创建，并从零预算消耗开始。"""  # noqa: DOCSTRING_CJK
    branch = branch_lifecycle.build_active_runtime_branch(
        _active_patch(),
        branch_id="branch_server_1",
        created_revision=2,
        return_anchor={"node_id": "node_contract_anchor", "goal_id": "goal_complete_public_exchange"},
        max_nonprogress_turns=2,
    )
    assert branch["branch_id"] == "branch_server_1"
    assert branch["turns_used"] == 0
    assert branch["nonprogress_turns"] == 0
    assert branch["patch"] == _active_patch()


def test_active_branch_rejects_nested_patch_creation():
    """已有活动支线时不得创建第二个 Runtime Branch Patch。"""  # noqa: DOCSTRING_CJK
    existing = branch_lifecycle.build_active_runtime_branch(
        _active_patch(),
        branch_id="branch_server_1",
        created_revision=2,
        return_anchor={"node_id": "node_contract_anchor", "goal_id": "goal_complete_public_exchange"},
        max_nonprogress_turns=2,
    )
    with pytest.raises(ValueError, match="nested"):
        branch_lifecycle.build_active_runtime_branch(
            _active_patch(),
            branch_id="branch_server_2",
            created_revision=3,
            return_anchor={"node_id": "node_contract_anchor", "goal_id": "goal_complete_public_exchange"},
            max_nonprogress_turns=2,
            existing_branch=existing,
        )


def test_committed_branch_turn_updates_budget_and_resets_nonprogress():
    """只有公开提交的支线回合消耗预算，形成进度后清零连续不推进次数。"""  # noqa: DOCSTRING_CJK
    branch = branch_lifecycle.build_active_runtime_branch(
        _active_patch(),
        branch_id="branch_server_1",
        created_revision=2,
        return_anchor={"node_id": "node_contract_anchor", "goal_id": "goal_complete_public_exchange"},
        max_nonprogress_turns=2,
    )
    branch["nonprogress_turns"] = 1
    updated, decision = branch_lifecycle.advance_active_branch(
        branch,
        event="branch_turn",
        made_progress=True,
    )
    assert updated["turns_used"] == 1
    assert updated["nonprogress_turns"] == 0
    assert decision == {"action": "continue", "exit_kind": ""}
    # 状态机使用候选副本，失败或 revision 冲突时原状态仍可安全丢弃。
    assert branch["turns_used"] == 0


def test_nonprogress_limit_closes_to_anchor_without_faking_goal():
    """连续不推进达到上限时使用作者安全出口，不得伪造 Goal 完成。"""  # noqa: DOCSTRING_CJK
    patch = _active_patch()
    branch = branch_lifecycle.build_active_runtime_branch(
        patch,
        branch_id="branch_server_1",
        created_revision=2,
        return_anchor={"node_id": "node_contract_anchor", "goal_id": "goal_complete_public_exchange"},
        max_nonprogress_turns=2,
    )
    branch["nonprogress_turns"] = 1
    updated, decision = branch_lifecycle.advance_active_branch(
        branch,
        event="branch_turn",
        made_progress=False,
    )
    assert updated["nonprogress_turns"] == 2
    assert decision == {"action": "close_to_anchor", "exit_kind": "nonprogress_exhausted"}
    assert "completed_goal_id" not in decision


def test_goal_completion_wins_over_budget_exhaustion_on_same_commit():
    """最后一个预算回合若已提交完整证据，应正常汇流而不是误判为超时。"""  # noqa: DOCSTRING_CJK
    branch = branch_lifecycle.build_active_runtime_branch(
        _active_patch(),
        branch_id="branch_server_1",
        created_revision=2,
        return_anchor={"node_id": "node_contract_anchor", "goal_id": "goal_complete_public_exchange"},
        max_nonprogress_turns=2,
    )
    branch["turns_used"] = 3
    updated, decision = branch_lifecycle.advance_active_branch(
        branch,
        event="branch_turn",
        made_progress=True,
        completed_goal_id="goal_complete_public_exchange",
    )
    assert updated["turns_used"] == 4
    assert decision == {
        "action": "converge",
        "exit_kind": "goal_converged",
        "goal_id": "goal_complete_public_exchange",
    }


def test_exhausted_branch_cannot_consume_an_extra_turn():
    """已经达到总预算的状态不再被视为活动支线，不能多执行一次。"""  # noqa: DOCSTRING_CJK
    branch = branch_lifecycle.build_active_runtime_branch(
        _active_patch(),
        branch_id="branch_server_1",
        created_revision=2,
        return_anchor={"node_id": "node_contract_anchor", "goal_id": "goal_complete_public_exchange"},
        max_nonprogress_turns=2,
    )
    branch["turns_used"] = 4
    # 调用方必须先按上一回合 decision 关闭支线，不能把非法状态继续向前滚动。
    with pytest.raises(ValueError, match="turn budget"):
        branch_lifecycle.advance_active_branch(
            branch,
            event="branch_turn",
            made_progress=True,
        )


def test_author_choice_closes_branch_without_consuming_branch_budget():
    """作者 Choice 由主线路由执行，关闭活动支线但不算作支线演绎回合。"""  # noqa: DOCSTRING_CJK
    branch = branch_lifecycle.build_active_runtime_branch(
        _active_patch(),
        branch_id="branch_server_1",
        created_revision=2,
        return_anchor={"node_id": "node_contract_anchor", "goal_id": "goal_complete_public_exchange"},
        max_nonprogress_turns=2,
    )
    original = deepcopy(branch)
    updated, decision = branch_lifecycle.advance_active_branch(
        branch,
        event="author_choice",
    )
    assert updated == original
    assert decision == {"action": "author_choice", "exit_kind": "author_choice"}


def test_technical_degraded_turn_preserves_branch_and_all_budgets():
    """技术降级只允许继续显示安全文字，不能被记成玩家非推进或触发任何出口。"""  # noqa: DOCSTRING_CJK
    branch = branch_lifecycle.build_active_runtime_branch(
        _active_patch(),
        branch_id="branch_server_1",
        created_revision=2,
        return_anchor={"node_id": "node_contract_anchor", "goal_id": "goal_complete_public_exchange"},
        max_nonprogress_turns=2,
    )
    branch["turns_used"] = 1
    branch["nonprogress_turns"] = 1
    original = deepcopy(branch)

    updated, decision = branch_lifecycle.advance_active_branch(
        branch,
        event="technical_degraded",
    )

    assert updated == original
    assert branch == original
    assert decision == {"action": "continue", "exit_kind": ""}


def test_ending_domain_and_user_exit_have_explicit_transitions():
    """正式结局域与主动离场使用不同退出原因，不能混成模型自由文本。"""  # noqa: DOCSTRING_CJK
    branch = branch_lifecycle.build_active_runtime_branch(
        _active_patch(),
        branch_id="branch_server_1",
        created_revision=2,
        return_anchor={"node_id": "node_contract_anchor", "goal_id": "goal_complete_public_exchange"},
        max_nonprogress_turns=2,
    )
    _, ending = branch_lifecycle.advance_active_branch(
        branch,
        event="branch_turn",
        made_progress=True,
        ending_domain_id="ending_domain_gentle_pause",
    )
    _, user_exit = branch_lifecycle.advance_active_branch(branch, event="user_exit")
    assert ending == {
        "action": "ending_domain",
        "exit_kind": "ending_domain",
        "ending_domain_id": "ending_domain_gentle_pause",
    }
    assert user_exit == {"action": "user_exit", "exit_kind": "user_exit"}


def test_intent_handoff_closes_to_anchor_without_consuming_branch_budget():
    """显式转交只关闭旧支线，不能消耗预算、伪造 Goal 或直接创建新支线。"""  # noqa: DOCSTRING_CJK
    branch = branch_lifecycle.build_active_runtime_branch(
        _active_patch(),
        branch_id="branch_server_handoff",
        created_revision=2,
        return_anchor={"node_id": "node_contract_anchor", "goal_id": "goal_complete_public_exchange"},
        max_nonprogress_turns=2,
    )
    branch["turns_used"] = 2
    branch["nonprogress_turns"] = 1
    original = deepcopy(branch)

    updated, decision = branch_lifecycle.advance_active_branch(
        branch,
        event="intent_handoff",
    )

    assert updated == original
    assert branch == original
    assert decision == {"action": "close_to_anchor", "exit_kind": "intent_handoff"}
    assert "goal_id" not in decision
    assert "ending_domain_id" not in decision
