"""提交活动支线 Actor 的候选事实，并执行确定性预算、Goal 与结局域判断。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from copy import deepcopy
from typing import Any
import uuid

from config import (
    THEATER_BRANCH_FACT_CANDIDATE_MAX_ITEMS,
    THEATER_BRANCH_RECALL_MAX_FACTS,
    THEATER_BRANCH_RECALL_MAX_HISTORIES,
)

from . import branch_contracts, branch_lifecycle, observability


def apply_actor_turn(
    *,
    story: dict[str, Any],
    active_branch: dict[str, Any],
    existing_facts: list[dict[str, Any]],
    fact_candidates: Any,
    source_revision: int,
    completed_goal_ids: list[str] | None = None,
) -> dict[str, Any]:
    """校验并提交一次 Actor 结果；候选整体非法时不产生任何部分事实或计数。"""  # noqa: DOCSTRING_CJK
    if type(source_revision) is not int or source_revision < 0:
        return {"ok": False, "reason": "source_revision_invalid"}
    if (
        not isinstance(fact_candidates, list)
        or len(fact_candidates) > THEATER_BRANCH_FACT_CANDIDATE_MAX_ITEMS
    ):
        # 只有模型候选本身的形状错误计入合同越界；Session/revision 损坏使用独立返回码。
        observability.record_result(
            responsibility="theater_actor",
            surface="branch_turn",
            result_kind="fact_contract",
            outcome="rejected",
        )
        return {"ok": False, "reason": "fact_candidate_invalid"}
    if not isinstance(active_branch, dict) or not isinstance(
        active_branch.get("patch"), dict
    ):
        return {"ok": False, "reason": "active_branch_invalid"}
    patch = active_branch["patch"]
    branch_id = str(active_branch.get("branch_id") or "").strip()
    if not branch_id:
        return {"ok": False, "reason": "active_branch_invalid"}

    validated_candidates: list[dict[str, Any]] = []
    try:
        for value in fact_candidates:
            # Actor 结果只有在同一响应准备公开提交时才进入这里，因此候选对应的动作属于本轮公开演出。
            validated_candidates.append(
                branch_contracts.validate_branch_fact_candidate(
                    value,
                    story=story,
                    patch=patch,
                    publicly_observed=True,
                )
            )
    except ValueError:
        # 一项越权就拒绝整组，避免对白声称多个结果但权威状态只接受其中一部分。
        observability.record_result(
            responsibility="theater_actor",
            surface="branch_turn",
            result_kind="fact_contract",
            outcome="rejected",
        )
        return {"ok": False, "reason": "fact_candidate_invalid"}

    # 候选整体通过形状与 World Contract 校验；后续去重和生命周期不会改变这一合同结论。
    observability.record_result(
        responsibility="theater_actor",
        surface="branch_turn",
        result_kind="fact_contract",
        outcome="accepted",
    )

    facts = [deepcopy(item) for item in existing_facts if isinstance(item, dict)]
    known = {_fact_identity(item) for item in facts}
    committed_this_turn: list[dict[str, Any]] = []
    for candidate in validated_candidates:
        identity = _fact_identity(candidate)
        if identity in known:
            # 重复描述已经提交的同一事实不是新进度，也不能生成第二个服务端 fact_id。
            continue
        has_public_entity = isinstance(candidate.get("public_entity"), dict)
        committed = branch_contracts.build_committed_branch_fact(
            candidate,
            branch_id=branch_id,
            fact_id=f"branch_fact_{uuid.uuid4()}",
            source_revision=source_revision,
            public_entity_id=f"branch_entity_{uuid.uuid4()}"
            if has_public_entity
            else "",
        )
        facts.append(committed)
        committed_this_turn.append(committed)
        known.add(identity)

    completed_goal_id = _completed_goal_id(
        story,
        patch=patch,
        branch_id=branch_id,
        branch_facts=facts,
    )
    combined_goal_ids = list(
        dict.fromkeys([*(completed_goal_ids or []), completed_goal_id])
    )
    ending_domain_id = _completed_ending_domain_id(
        story,
        patch=patch,
        branch_id=branch_id,
        branch_facts=facts,
        completed_goal_ids=[item for item in combined_goal_ids if item],
    )
    try:
        updated_branch, decision = branch_lifecycle.advance_active_branch(
            active_branch,
            event="branch_turn",
            # 只有新提交的合同合法事实才算进度；模型不能用布尔字段自行重置非推进计数。
            made_progress=bool(committed_this_turn),
            completed_goal_id=completed_goal_id,
            ending_domain_id=ending_domain_id,
        )
    except ValueError:
        return {"ok": False, "reason": "active_branch_invalid"}
    return {
        "ok": True,
        "active_branch": updated_branch,
        "branch_facts": facts,
        "committed_facts": committed_this_turn,
        "completed_goal_id": completed_goal_id,
        "ending_domain_id": ending_domain_id,
        "decision": decision,
    }


def apply_technical_degraded_turn(
    *,
    active_branch: dict[str, Any],
    existing_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    """提交无权威变化的技术降级回合，保留支线预算、事实、Goal 与 Ending。"""  # noqa: DOCSTRING_CJK
    if not isinstance(existing_facts, list) or any(
        not isinstance(item, dict) for item in existing_facts
    ):
        return {"ok": False, "reason": "branch_facts_invalid"}
    try:
        updated_branch, decision = branch_lifecycle.advance_active_branch(
            active_branch,
            event="technical_degraded",
        )
    except ValueError:
        return {"ok": False, "reason": "active_branch_invalid"}
    # 复制已有事实以维持候选副本隔离；本路径不校验或接受任何模型 Fact Candidate。
    return {
        "ok": True,
        "active_branch": updated_branch,
        "branch_facts": deepcopy(existing_facts),
        "committed_facts": [],
        "completed_goal_id": "",
        "ending_domain_id": "",
        "decision": decision,
    }


def dynamic_choice_options(
    active_branch: Any,
    branch_facts: Any,
) -> list[dict[str, str]]:
    """从首个未完成且有事实证据的 Beat 派生一个可验证临时行动建议。"""  # noqa: DOCSTRING_CJK
    if not branch_lifecycle.active_runtime_branch_is_valid(active_branch):
        return []
    branch_id = str(active_branch.get("branch_id") or "").strip()
    if not branch_id:
        return []
    observed_roles = {
        str(item.get("fact_role") or "")
        for item in branch_facts or []
        if isinstance(item, dict) and str(item.get("branch_id") or "") == branch_id
    }
    for beat in active_branch["patch"].get("beat_outline") or []:
        if not isinstance(beat, dict):
            continue
        beat_id = str(beat.get("beat_id") or "").strip()
        label = str(beat.get("player_choice_label") or "").strip()
        raw_prepared_roles = beat.get("exit_preparation")
        if not isinstance(raw_prepared_roles, list):
            continue
        prepared_roles = {
            str(item).strip()
            for item in raw_prepared_roles
            if isinstance(item, str) and str(item).strip()
        }
        # 没有可提交证据的 Beat 无法确定按钮何时完成，因此只供 Actor 编排，不公开为可执行 Choice。
        if not prepared_roles or prepared_roles.issubset(observed_roles):
            continue
        if not beat_id or not label:
            # 首个未完成证据 Beat 没有玩家文案时停止投影，绝不能跳到后续 Beat 或回退公开舞台描述。
            return []
        # UUID5 让恢复后的按钮保持稳定，同时不会把私有 branch_id 或 Planner beat_id 直接暴露给前端。
        opaque_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"neko-theater-runtime-choice:{branch_id}:{beat_id}",
        )
        return [
            {
                "choice_id": f"branch_choice_{opaque_id}",
                "label": label,
                "choice_mode": "action",
                "beat_id": beat_id,
            }
        ]
    return []


def resolve_dynamic_choice(
    active_branch: Any,
    branch_facts: Any,
    choice_id: str,
) -> dict[str, str]:
    """只解析当前仍可见的临时按钮，拒绝伪造、已完成或旧支线 Choice。"""  # noqa: DOCSTRING_CJK
    normalized_id = str(choice_id or "").strip()
    for option in dynamic_choice_options(active_branch, branch_facts):
        if option["choice_id"] == normalized_id:
            return dict(option)
    return {}


def completed_branch_recall(
    *,
    story: dict[str, Any],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """从合法 History 精确选取已提交事实，并移除所有服务端身份字段。"""  # noqa: DOCSTRING_CJK
    raw_facts = (
        state.get("branch_facts") if isinstance(state.get("branch_facts"), list) else []
    )
    validated_facts: list[dict[str, Any]] = []
    fact_index: dict[str, dict[str, Any]] = {}
    for value in raw_facts:
        try:
            # 提示词召回不信任裸 Session；结构损坏的事实既不报错扩散，也不能进入模型上下文。
            fact = branch_contracts.validate_committed_branch_fact_structure(
                value,
                story=story,
            )
        except ValueError:
            continue
        fact_id = str(fact.get("fact_id") or "")
        validated_facts.append(fact)
        fact_index[fact_id] = fact

    goal_summaries = {
        str(item.get("goal_id") or ""): str(item.get("summary") or "").strip()
        for item in story.get("narrative_goals") or []
        if isinstance(item, dict) and str(item.get("goal_id") or "").strip()
    }
    raw_histories = (
        state.get("branch_history")
        if isinstance(state.get("branch_history"), list)
        else []
    )
    recalled: list[dict[str, Any]] = []
    recalled_fact_ids: set[str] = set()
    # 从最新 History 向前取样，确保事实预算优先留给玩家最近完成的用户剧本内容。
    for value in reversed(raw_histories):
        if len(recalled) >= THEATER_BRANCH_RECALL_MAX_HISTORIES:
            break
        try:
            history = branch_contracts.validate_branch_history_entry(
                value,
                story=story,
                branch_facts=validated_facts,
            )
        except ValueError:
            # 单条 History 无法证明其引用时整条忽略，绝不改绑到相似事实或其他支线。
            continue
        facts: list[dict[str, Any]] = []
        # 单段支线也可能产生很多事实；逆序选择可让全局预算优先保留最接近汇流点的结果。
        for fact_id in reversed(history.get("key_fact_ids") or []):
            if (
                fact_id in recalled_fact_ids
                or len(recalled_fact_ids) >= THEATER_BRANCH_RECALL_MAX_FACTS
            ):
                continue
            fact = fact_index.get(str(fact_id))
            if not fact:
                continue
            recalled_fact_ids.add(str(fact_id))
            projected = {
                "subject": str(fact.get("subject") or ""),
                "predicate": str(fact.get("predicate") or ""),
                "object": str(fact.get("object") or ""),
            }
            public_entity = fact.get("public_entity")
            if isinstance(public_entity, dict):
                # 公开实体只保留演绎语义；entity_id 与 Fact 身份一样永不交给模型。
                projected["public_entity"] = {
                    key: str(public_entity.get(key) or "")
                    for key in ("kind", "label", "status")
                }
            facts.append(projected)
        facts.reverse()
        summaries = [
            goal_summaries[goal_id]
            for goal_id in history.get("completed_goal_ids") or []
            if goal_summaries.get(str(goal_id))
        ]
        if facts or summaries:
            recalled.append({"completed_goal_summaries": summaries, "facts": facts})
    # Prompt 按真实发生顺序阅读；选取阶段逆序只用于优先保障最新记录的预算。
    return list(reversed(recalled))


def _completed_goal_id(
    story: dict[str, Any],
    *,
    patch: dict[str, Any],
    branch_id: str,
    branch_facts: list[dict[str, Any]],
) -> str:
    """按 Patch 出口顺序返回首个具备全部作者证据角色的 Narrative Goal。"""  # noqa: DOCSTRING_CJK
    goal_index = {
        str(item.get("goal_id") or ""): item
        for item in story.get("narrative_goals") or []
        if isinstance(item, dict)
    }
    for exit_candidate in patch.get("exit_candidates") or []:
        if (
            not isinstance(exit_candidate, dict)
            or exit_candidate.get("kind") != "converge"
        ):
            continue
        goal_id = str(exit_candidate.get("goal_id") or "")
        goal = goal_index.get(goal_id)
        if not isinstance(goal, dict):
            continue
        required_roles = {
            str(item) for item in goal.get("completion_evidence") or [] if str(item)
        }
        observed_roles = {
            str(item.get("fact_role") or "")
            for item in branch_facts
            if isinstance(item, dict)
            and str(item.get("branch_id") or "") == branch_id
            and str(item.get("goal_id") or "") == goal_id
        }
        if required_roles and required_roles.issubset(observed_roles):
            return goal_id
    return ""


def _completed_ending_domain_id(
    story: dict[str, Any],
    *,
    patch: dict[str, Any],
    branch_id: str,
    branch_facts: list[dict[str, Any]],
    completed_goal_ids: list[str],
) -> str:
    """只按作者 Domain 的 Goal、事实类型和事实角色证据选择允许结局域。"""  # noqa: DOCSTRING_CJK
    domain_index = {
        str(item.get("ending_domain_id") or ""): item
        for item in story.get("ending_domains") or []
        if isinstance(item, dict)
    }
    current_facts = [
        item
        for item in branch_facts
        if isinstance(item, dict) and str(item.get("branch_id") or "") == branch_id
    ]
    fact_types = {str(item.get("fact_type") or "") for item in current_facts}
    fact_roles = {str(item.get("fact_role") or "") for item in current_facts}
    completed_goals = set(completed_goal_ids)
    for exit_candidate in patch.get("exit_candidates") or []:
        if (
            not isinstance(exit_candidate, dict)
            or exit_candidate.get("kind") != "ending_domain"
        ):
            continue
        domain_id = str(exit_candidate.get("ending_domain_id") or "")
        domain = domain_index.get(domain_id)
        if not isinstance(domain, dict):
            continue
        required_goals = {
            str(item) for item in domain.get("required_goal_ids") or [] if str(item)
        }
        required_types = {
            str(item) for item in domain.get("required_fact_types") or [] if str(item)
        }
        required_roles = {
            str(item) for item in domain.get("required_fact_roles") or [] if str(item)
        }
        forbidden_roles = {
            str(item) for item in domain.get("forbidden_fact_roles") or [] if str(item)
        }
        if (
            required_goals.issubset(completed_goals)
            and required_types.issubset(fact_types)
            and required_roles.issubset(fact_roles)
            and fact_roles.isdisjoint(forbidden_roles)
        ):
            return domain_id
    return ""


def _fact_identity(value: dict[str, Any]) -> tuple[str, ...]:
    """用稳定语义字段识别重复事实，忽略服务端 ID、revision 与展示实体状态。"""  # noqa: DOCSTRING_CJK
    return tuple(
        str(value.get(field) or "")
        for field in (
            "goal_id",
            "fact_type",
            "fact_role",
            "subject",
            "predicate",
            "object",
            "content_slot_id",
            "content_id",
        )
    )
