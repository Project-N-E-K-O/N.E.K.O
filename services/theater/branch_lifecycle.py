"""提供 v2.6 Pending Intent 与活动支线的纯状态转换规则。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from copy import deepcopy
from typing import Any

# Pending 只跨越一次主线提交并保留一个后续 revision，避免旧意图在远端节点复活。
PENDING_INTENT_TTL_REVISIONS = 1
BRANCH_EVENTS = frozenset(
    {
        "branch_turn",
        "technical_degraded",
        "author_choice",
        "user_exit",
        "intent_handoff",
    }
)


def build_pending_intent(
    *,
    summary: str,
    evidence_excerpt: str,
    source_node_id: str,
    target_node_id: str,
    target_scene_id: str,
    created_revision: int,
) -> dict[str, Any]:
    """构造短期剩余意图；它不拥有 intent_key、streak 或支线激活权限。"""  # noqa: DOCSTRING_CJK
    if type(created_revision) is not int or created_revision < 0:
        raise ValueError("Pending Intent has invalid created revision")
    normalized = {
        "summary": _required_text(summary, "summary"),
        "evidence_excerpt": _required_text(evidence_excerpt, "evidence_excerpt"),
        "source_node_id": _required_text(source_node_id, "source_node_id"),
        "target_node_id": _required_text(target_node_id, "target_node_id"),
        "target_scene_id": _required_text(target_scene_id, "target_scene_id"),
        "created_revision": created_revision,
        "expires_revision": created_revision + PENDING_INTENT_TTL_REVISIONS,
    }
    return normalized


def evaluate_pending_intent(
    pending: dict[str, Any],
    *,
    current_node_id: str,
    current_scene_id: str,
    current_revision: int,
    player_denied: bool,
    scene_compatible: bool,
) -> str:
    """决定 pending 是交给 Router 重验还是立即丢弃。"""  # noqa: DOCSTRING_CJK
    _validate_pending_intent(pending)
    if type(current_revision) is not int or current_revision < 0:
        raise ValueError("Pending Intent evaluation has invalid revision")
    if player_denied is True:
        return "discard_denied"
    if current_revision > int(pending["expires_revision"]):
        return "discard_expired"
    # revision 倒退、目标节点/Scene 不符或语义检查失败都不能保留旧 pending。
    if (
        current_revision < int(pending["created_revision"])
        or str(current_node_id or "").strip() != pending["target_node_id"]
        or str(current_scene_id or "").strip() != pending["target_scene_id"]
        or scene_compatible is not True
    ):
        return "discard_incompatible"
    return "revalidate"


def build_active_runtime_branch(
    patch: dict[str, Any],
    *,
    branch_id: str,
    created_revision: int,
    return_anchor: dict[str, Any],
    max_nonprogress_turns: int,
    existing_branch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """用服务端身份激活已校验 Patch，并拒绝嵌套支线。"""  # noqa: DOCSTRING_CJK
    if isinstance(existing_branch, dict) and existing_branch:
        raise ValueError("Runtime Branch cannot create a nested branch")
    if not isinstance(patch, dict):
        raise ValueError("Validated Runtime Branch Patch must be an object")
    turn_budget = patch.get("turn_budget")
    if type(turn_budget) is not int or turn_budget < 1:
        raise ValueError("Runtime Branch Patch has invalid turn budget")
    if (
        type(max_nonprogress_turns) is not int
        or max_nonprogress_turns < 0
        or max_nonprogress_turns > turn_budget
    ):
        raise ValueError("Runtime Branch has invalid nonprogress budget")
    if type(created_revision) is not int or created_revision < 0:
        raise ValueError("Runtime Branch has invalid created revision")
    anchor = _validate_return_anchor(return_anchor)
    # Patch 保存在活动状态的隔离副本中，调用方后续修改模型原始对象不会污染 Session 候选。
    return {
        "branch_id": _required_text(branch_id, "branch_id"),
        "patch": deepcopy(patch),
        "created_revision": created_revision,
        "return_anchor": anchor,
        "turn_budget": turn_budget,
        "max_nonprogress_turns": max_nonprogress_turns,
        "turns_used": 0,
        "nonprogress_turns": 0,
    }


def advance_active_branch(
    branch: dict[str, Any],
    *,
    event: str,
    made_progress: bool = False,
    completed_goal_id: str = "",
    ending_domain_id: str = "",
) -> tuple[dict[str, Any], dict[str, str]]:
    """在已提交公开结果后更新支线计数，并返回唯一后续动作。"""  # noqa: DOCSTRING_CJK
    _validate_active_branch(branch)
    normalized_event = str(event or "").strip()
    if normalized_event not in BRANCH_EVENTS:
        raise ValueError("Runtime Branch received an invalid lifecycle event")
    candidate = deepcopy(branch)

    def _result(decision: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
        """返回纯状态候选；终态指标必须等 Session 真正落盘后再记录。"""  # noqa: DOCSTRING_CJK
        return candidate, decision

    if normalized_event == "author_choice":
        # 作者 Choice 由主线链执行，因此关闭支线但不占用支线演绎预算。
        return _result({"action": "author_choice", "exit_kind": "author_choice"})
    if normalized_event == "user_exit":
        # 主动离场直接结束 Session；支线 History 仍保留独立退出原因。
        return _result({"action": "user_exit", "exit_kind": "user_exit"})
    if normalized_event == "intent_handoff":
        # 玩家明确转向另一件事时只关闭旧支线，不把调度动作计作剧情预算或 Goal 进展。
        return _result({"action": "close_to_anchor", "exit_kind": "intent_handoff"})
    if normalized_event == "technical_degraded":
        # 技术回退可以提交安全文字，但既不是玩家非推进，也不能完成 Goal、Ending 或作者预算。
        return _result({"action": "continue", "exit_kind": ""})
    if type(made_progress) is not bool:
        raise ValueError("Runtime Branch progress marker must be boolean")

    # 本函数只构造候选；无提交、冲突或幂等重试是否生效由外层原子保存决定。
    candidate["turns_used"] = int(candidate["turns_used"]) + 1
    candidate["nonprogress_turns"] = (
        0 if made_progress else int(candidate["nonprogress_turns"]) + 1
    )
    normalized_goal_id = str(completed_goal_id or "").strip()
    normalized_domain_id = str(ending_domain_id or "").strip()

    # 完成证据在本轮已经提交时，Goal 汇流优先于结局域和预算判断。
    if normalized_goal_id:
        return _result(
            {
                "action": "converge",
                "exit_kind": "goal_converged",
                "goal_id": normalized_goal_id,
            }
        )
    if normalized_domain_id:
        return _result(
            {
                "action": "ending_domain",
                "exit_kind": "ending_domain",
                "ending_domain_id": normalized_domain_id,
            }
        )
    # 同一回合同时触发两种上限时，记录更具体的连续不推进原因，方便后续观测。
    if not made_progress and int(candidate["nonprogress_turns"]) >= int(
        candidate["max_nonprogress_turns"]
    ):
        return _result(
            {"action": "close_to_anchor", "exit_kind": "nonprogress_exhausted"}
        )
    if int(candidate["turns_used"]) >= int(candidate["turn_budget"]):
        return _result({"action": "close_to_anchor", "exit_kind": "budget_exhausted"})
    return _result({"action": "continue", "exit_kind": ""})


def active_runtime_branch_is_valid(value: Any) -> bool:
    """供恢复与公开投影安全检查活动状态，不抛出存档损坏细节。"""  # noqa: DOCSTRING_CJK
    try:
        _validate_active_branch(value)
    except ValueError:
        return False
    return True


def _validate_pending_intent(pending: Any) -> None:
    """验证待重验对象来自服务端固定结构，而不是模型任意字典。"""  # noqa: DOCSTRING_CJK
    required = {
        "summary",
        "evidence_excerpt",
        "source_node_id",
        "target_node_id",
        "target_scene_id",
        "created_revision",
        "expires_revision",
    }
    if not isinstance(pending, dict) or set(pending) != required:
        raise ValueError("Pending Intent has invalid fields")
    for field in (
        "summary",
        "evidence_excerpt",
        "source_node_id",
        "target_node_id",
        "target_scene_id",
    ):
        _required_text(pending.get(field), field)
    created_revision = pending.get("created_revision")
    expires_revision = pending.get("expires_revision")
    if (
        type(created_revision) is not int
        or type(expires_revision) is not int
        or created_revision < 0
        or expires_revision != created_revision + PENDING_INTENT_TTL_REVISIONS
    ):
        raise ValueError("Pending Intent has invalid revision scope")


def _validate_active_branch(branch: Any) -> None:
    """验证状态机消费的是服务端创建的活动支线结构。"""  # noqa: DOCSTRING_CJK
    required = {
        "branch_id",
        "patch",
        "created_revision",
        "return_anchor",
        "turn_budget",
        "max_nonprogress_turns",
        "turns_used",
        "nonprogress_turns",
    }
    if not isinstance(branch, dict) or set(branch) != required:
        raise ValueError("Runtime Branch has invalid fields")
    _required_text(branch.get("branch_id"), "branch_id")
    if not isinstance(branch.get("patch"), dict):
        raise ValueError("Runtime Branch has invalid Patch")
    _validate_return_anchor(branch.get("return_anchor"))
    integer_fields = (
        "created_revision",
        "turn_budget",
        "max_nonprogress_turns",
        "turns_used",
        "nonprogress_turns",
    )
    if any(
        type(branch.get(field)) is not int or int(branch[field]) < 0
        for field in integer_fields
    ):
        raise ValueError("Runtime Branch has invalid counters")
    # 达到总预算的支线应已在上一提交中关闭，不能以“活动”状态继续消费下一回合。
    if int(branch["turn_budget"]) < 1 or int(branch["turns_used"]) >= int(
        branch["turn_budget"]
    ):
        raise ValueError("Runtime Branch exceeds its turn budget")
    if int(branch["max_nonprogress_turns"]) > int(branch["turn_budget"]) or int(
        branch["nonprogress_turns"]
    ) > int(branch["max_nonprogress_turns"]):
        raise ValueError("Runtime Branch exceeds its nonprogress budget")


def _validate_return_anchor(value: Any) -> dict[str, str]:
    """校验安全退出只返回作者节点和可选 Narrative Goal。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, dict) or set(value) != {"node_id", "goal_id"}:
        raise ValueError("Runtime Branch has invalid return anchor")
    return {
        "node_id": _required_text(value.get("node_id"), "return anchor node_id"),
        "goal_id": str(value.get("goal_id") or "").strip(),
    }


def _required_text(value: Any, field: str) -> str:
    """校验服务端状态中的必填字符串并去除外层空白。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()
