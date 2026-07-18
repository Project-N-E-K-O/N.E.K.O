"""执行候选回合中的活动支线、支线进入、收束和退出子流程。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import math
from typing import Any
import uuid

from . import (
    branch_contracts,
    branch_lifecycle,
    branch_planner,
    branch_runtime,
    intent_tracker,
    llm,
    observability,
    projector,
    rules,
    session_store,
    story_graph,
    story_loader,
)
from .turn_history import _now_ms
from .turn_request_contracts import _verified_residual_evidence_excerpt


def _active_branch_handoff_is_verified(
    user_message: str,
    handoff_route: dict[str, Any],
) -> bool:
    """在状态提交边界复核高置信分类和两段玩家原话证据。"""  # noqa: DOCSTRING_CJK
    summary = " ".join(str(handoff_route.get("intent_summary") or "").strip().split())
    exit_evidence = _verified_residual_evidence_excerpt(
        user_message,
        handoff_route.get("exit_evidence_excerpt"),
    )
    next_evidence = _verified_residual_evidence_excerpt(
        user_message,
        handoff_route.get("next_evidence_excerpt"),
    )
    confidence = handoff_route.get("confidence")
    return bool(
        2 <= len(summary) <= 160
        and exit_evidence
        and len(exit_evidence) <= 240
        and next_evidence
        and len(next_evidence) <= 240
        and exit_evidence != next_evidence
        and exit_evidence not in next_evidence
        and next_evidence not in exit_evidence
        and not isinstance(confidence, bool)
        and isinstance(confidence, (int, float))
        and math.isfinite(float(confidence))
        and 0.85 <= float(confidence) <= 1.0
    )


def _active_branch_continue_is_verified(handoff_route: dict[str, Any]) -> bool:
    """只让达到明确语义阈值的 continue 进入旧 Branch Actor。"""  # noqa: DOCSTRING_CJK
    confidence = handoff_route.get("confidence")
    return bool(
        not isinstance(confidence, bool)
        and isinstance(confidence, (int, float))
        and math.isfinite(float(confidence))
        and llm.THEATER_BRANCH_HANDOFF_CONTINUE_MIN_CONFIDENCE
        <= float(confidence)
        <= 1.0
    )


def _prepare_active_branch_handoff(
    *,
    session: dict[str, Any],
    story: dict[str, Any],
    state: dict[str, Any],
    current_node: dict[str, Any],
    message: str,
    handoff_route: dict[str, Any],
) -> dict[str, Any]:
    """关闭旧支线并把新行动保存为锚点范围内的一次待确认证据。"""  # noqa: DOCSTRING_CJK
    active_branch = state.get("active_runtime_branch")
    if not branch_lifecycle.active_runtime_branch_is_valid(active_branch):
        raise ValueError("Active Runtime Branch is invalid")
    summary = " ".join(str(handoff_route.get("intent_summary") or "").strip().split())
    next_evidence = _verified_residual_evidence_excerpt(
        message,
        handoff_route.get("next_evidence_excerpt"),
    )
    if not _active_branch_handoff_is_verified(message, handoff_route):
        raise ValueError("Active Runtime Branch handoff evidence is invalid")

    anchor = active_branch.get("return_anchor")
    anchor_id = str(anchor.get("node_id") or "") if isinstance(anchor, dict) else ""
    target_node = story_graph.node_by_id(story, anchor_id)
    if not target_node:
        raise ValueError("Runtime Branch return anchor is missing")
    updated_branch, decision = branch_lifecycle.advance_active_branch(
        active_branch,
        event="intent_handoff",
    )
    if decision.get("action") != "close_to_anchor":
        raise ValueError("Runtime Branch handoff produced an invalid decision")
    next_revision = session_store.state_revision(session) + 1
    _finish_runtime_branch(
        state,
        story=story,
        active_branch=updated_branch,
        decision=decision,
        ended_revision=next_revision,
    )
    # 支线锚点已经在进入时应用过，这里只恢复其身份，不能再次提交节点增量或重复完成 Goal。
    state["current_node_id"] = anchor_id
    rules.clear_latent_intent_tracking(state)
    phase = str(target_node.get("belong_phase") or session.get("phase") or "setup")
    scene = story_loader.scene_for_phase(story, phase)
    state["pending_intent"] = branch_lifecycle.build_pending_intent(
        summary=summary,
        evidence_excerpt=next_evidence,
        source_node_id=str(current_node.get("node_id") or ""),
        target_node_id=anchor_id,
        target_scene_id=str(scene.get("id") or ""),
        created_revision=next_revision,
    )
    # 本回合不调用 Planner、Branch Actor 或普通 Actor，固定回应只确认暂停与待确认，不能抢跑新行动。
    patch = (
        active_branch.get("patch")
        if isinstance(active_branch.get("patch"), dict)
        else {}
    )
    return {
        "performance": llm.fallback_branch_handoff(
            scene_title=str(scene.get("title") or ""),
            activity_summary=str(patch.get("seed_intent") or ""),
            private_identifiers=llm._private_runtime_identifiers(
                active_branch,
                patch,
                state,
            ),
        ),
        "target_node": target_node,
        "ending": None,
    }


def _prepare_technical_degraded_active_branch_turn(
    *,
    story: dict[str, Any],
    state: dict[str, Any],
    current_node: dict[str, Any],
    message: str,
    lanlan_name: str,
) -> dict[str, Any]:
    """在转交分类不可用时生成无事实、无预算变化的安全支线回合。"""  # noqa: DOCSTRING_CJK
    active_branch = state.get("active_runtime_branch")
    all_branch_facts = state.get("branch_facts")
    if not isinstance(active_branch, dict) or not active_branch:
        raise ValueError("Active Runtime Branch is missing")
    if not isinstance(all_branch_facts, list) or any(
        not isinstance(item, dict) for item in all_branch_facts
    ):
        raise ValueError("Runtime Branch facts are invalid")
    transition = branch_runtime.apply_technical_degraded_turn(
        active_branch=active_branch,
        existing_facts=all_branch_facts,
    )
    if transition.get("ok") is not True:
        raise ValueError("Active Runtime Branch technical fallback failed")
    state["active_runtime_branch"] = transition["active_branch"]
    state["branch_facts"] = transition["branch_facts"]
    phase = str(current_node.get("belong_phase") or "setup")
    scene = story_loader.scene_for_phase(story, phase)
    patch = (
        active_branch.get("patch")
        if isinstance(active_branch.get("patch"), dict)
        else {}
    )
    branch_id = str(active_branch.get("branch_id") or "")
    actor_result = llm.fallback_branch_turn(
        lanlan_name=lanlan_name,
        scene=scene,
        user_message=message,
        activity_summary=str(patch.get("seed_intent") or ""),
        has_committed_progress=any(
            str(item.get("branch_id") or "") == branch_id for item in all_branch_facts
        ),
        private_identifiers=llm._private_runtime_identifiers(
            active_branch,
            patch,
            all_branch_facts,
            state,
        ),
    )
    performance = {
        "narration": str(actor_result.get("narration") or ""),
        "dialogue": str(actor_result.get("dialogue") or ""),
        "choice_rewrites": [],
        "fact_candidates": [],
    }
    return {"performance": performance, "target_node": current_node, "ending": None}


async def _prepare_active_runtime_branch_turn(
    *,
    session: dict[str, Any],
    story: dict[str, Any],
    state: dict[str, Any],
    current_node: dict[str, Any],
    message: str,
    lanlan_name: str,
    config_manager: Any | None,
    response_focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行活动支线 Actor、事实合同和生命周期，并返回统一回合投影所需结果。"""  # noqa: DOCSTRING_CJK
    active_branch = state.get("active_runtime_branch")
    if not isinstance(active_branch, dict) or not active_branch:
        raise ValueError("Active Runtime Branch is missing")
    phase = str(current_node.get("belong_phase") or session.get("phase") or "setup")
    scene = story_loader.scene_for_phase(story, phase)
    branch_id = str(active_branch.get("branch_id") or "")
    all_branch_facts = state.get("branch_facts")
    if not isinstance(all_branch_facts, list) or any(
        not isinstance(item, dict) for item in all_branch_facts
    ):
        # 损坏的权威事实集合不能经技术降级被重新保存；恢复层应先明确修复或拒绝该 Session。
        raise ValueError("Runtime Branch facts are invalid")
    existing_facts = [
        item
        for item in all_branch_facts
        if isinstance(item, dict) and str(item.get("branch_id") or "") == branch_id
    ]
    active_patch = (
        active_branch.get("patch")
        if isinstance(active_branch.get("patch"), dict)
        else {}
    )
    verified_response_focus = llm.verify_response_focus(
        response_focus,
        user_message=message,
    )
    actor_result = await llm.generate_branch_turn_async(
        config_manager=config_manager,
        lanlan_name=lanlan_name,
        story=story,
        scene=scene,
        user_message=message,
        state=state,
        recent_turns=list(session.get("turns") or []),
        active_branch=active_branch,
        branch_facts=existing_facts,
        # 活动支线是当前作者节点内的临时演绎，不能丢失节点级禁用对白与意图边界。
        node=current_node,
        response_focus=verified_response_focus,
    )
    if str(actor_result.get("turn_delivery") or "") == "technical_degraded":
        transition = branch_runtime.apply_technical_degraded_turn(
            active_branch=active_branch,
            existing_facts=all_branch_facts,
        )
    else:
        transition = branch_runtime.apply_actor_turn(
            story=story,
            active_branch=active_branch,
            existing_facts=all_branch_facts,
            # 回应焦点只是演出义务；只有玩家本轮已实施的动作才允许候选进入事实合同。
            fact_candidates=(
                actor_result.get("fact_candidates")
                if verified_response_focus.get("focus_type") == "action"
                and verified_response_focus.get("requires_state_change") is True
                else []
            ),
            source_revision=session_store.state_revision(session) + 1,
            completed_goal_ids=list(state.get("completed_goal_ids") or []),
        )
    if transition.get("reason") == "fact_candidate_invalid":
        # Fact 合同整组拒绝属于技术降级：丢弃模型正文与候选，但不能扣减玩家的剧情预算。
        actor_result = llm.fallback_branch_turn(
            lanlan_name=lanlan_name,
            scene=scene,
            user_message=message,
            activity_summary=str(active_patch.get("seed_intent") or ""),
            has_committed_progress=bool(existing_facts),
            private_identifiers=llm._private_runtime_identifiers(
                active_branch,
                active_patch,
                existing_facts,
                state,
            ),
        )
        # Actor 候选虽已生成，但 Fact 合同拒绝后真正展示的是安全回退；必须计入回退率。
        observability.record_result(
            responsibility="theater_actor",
            surface="branch_turn",
            result_kind="generation",
            outcome="safe_fallback",
        )
        transition = branch_runtime.apply_technical_degraded_turn(
            active_branch=active_branch,
            existing_facts=all_branch_facts,
        )
    if transition.get("ok") is not True:
        # revision、活动状态或已提交事实损坏不能伪装成模型降级；抛出后外层不会保存候选副本。
        raise ValueError("Active Runtime Branch transition failed")

    state["branch_facts"] = transition["branch_facts"]
    updated_branch = transition["active_branch"]
    decision = transition["decision"]
    action = str(decision.get("action") or "")
    performance = {
        "narration": str(actor_result.get("narration") or ""),
        "dialogue": str(actor_result.get("dialogue") or ""),
        "choice_rewrites": [],
        "fact_candidates": [],
    }
    result: dict[str, Any] = {
        "performance": performance,
        "target_node": current_node,
        "ending": None,
    }
    if action == "continue":
        # 未到出口时只更新活动支线计数与已提交事实，当前作者节点保持不变。
        state["active_runtime_branch"] = updated_branch
        return result

    completed_goal_id = str(transition.get("completed_goal_id") or "")
    completed_goal_ids = [completed_goal_id] if completed_goal_id else []
    _finish_runtime_branch(
        state,
        story=story,
        active_branch=updated_branch,
        decision=decision,
        ended_revision=session_store.state_revision(session) + 1,
        completed_goal_ids=completed_goal_ids,
    )
    if action == "converge":
        goal = _narrative_goal(story, completed_goal_id)
        target_node = story_graph.node_by_id(
            story, str(goal.get("converge_to_node_id") or "")
        )
        if not target_node:
            raise ValueError("Runtime Branch convergence target is missing")
        # History 先保留支线事实索引，再进入作者目标节点；apply_node 不会删除 Branch Fact 或 History。
        rules.apply_node(story, state, target_node)
        state["completed_goal_ids"] = list(
            dict.fromkeys([*(state.get("completed_goal_ids") or []), completed_goal_id])
        )
        performance["narration"] = str(goal.get("fallback_convergence_callback") or "")
        result["target_node"] = target_node
        return result
    if action == "ending_domain":
        domain_id = str(transition.get("ending_domain_id") or "")
        domain = _ending_domain(story, domain_id)
        ending_id = str(domain.get("ending_id") or "")
        if not ending_id:
            raise ValueError(
                "Runtime Branch ending domain is missing its authored ending"
            )
        result["ending"] = {
            "should_offer_ending": True,
            "should_end_session": True,
            "ending_id": ending_id,
            "reason": "branch_ending_domain",
        }
        return result
    if action == "close_to_anchor":
        contract = (
            story.get("world_contract")
            if isinstance(story.get("world_contract"), dict)
            else {}
        )
        abort_policy = (
            contract.get("branch_abort_policy")
            if isinstance(contract.get("branch_abort_policy"), dict)
            else {}
        )
        # 达到预算后使用作者中性回退，不让模型临时编造 Goal 完成或具体失败结果。
        performance["narration"] = str(abort_policy.get("neutral_callback") or "")
        anchor = (
            updated_branch.get("return_anchor")
            if isinstance(updated_branch.get("return_anchor"), dict)
            else {}
        )
        target_node = story_graph.node_by_id(story, str(anchor.get("node_id") or ""))
        if not target_node:
            raise ValueError("Runtime Branch return anchor is missing")
        result["target_node"] = target_node
        return result
    raise ValueError("Runtime Branch produced an unsupported decision")


def _finish_runtime_branch(
    state: dict[str, Any],
    *,
    story: dict[str, Any],
    active_branch: dict[str, Any],
    decision: dict[str, Any],
    ended_revision: int,
    completed_goal_ids: list[str] | None = None,
) -> None:
    """把退出支线收敛为结构化 History，并清除不再有效的活动状态与自由意图。"""  # noqa: DOCSTRING_CJK
    branch_id = str(active_branch.get("branch_id") or "")
    branch_facts = [
        item for item in state.get("branch_facts") or [] if isinstance(item, dict)
    ]
    key_fact_ids = [
        str(item.get("fact_id") or "")
        for item in branch_facts
        if str(item.get("branch_id") or "") == branch_id
        and str(item.get("fact_id") or "")
    ]
    history = branch_contracts.validate_branch_history_entry(
        {
            "branch_id": branch_id,
            "completed_goal_ids": list(completed_goal_ids or []),
            "key_fact_ids": key_fact_ids,
            "exit_kind": str(decision.get("exit_kind") or ""),
            "ended_revision": ended_revision,
        },
        story=story,
        branch_facts=branch_facts,
    )
    histories = [
        item for item in state.get("branch_history") or [] if isinstance(item, dict)
    ]
    histories.append(history)
    state["branch_history"] = histories
    state["active_runtime_branch"] = {}
    # 旧连续意图已经完成或退出；后续新支线必须重新取得两次连续证据。
    intent_tracker.clear_dynamic_intent(state)


def _record_committed_branch_outcomes(before_state: Any, after_state: Any) -> None:
    """只在 Session 落盘后记录新终态，候选冲突或丢弃不得污染完成率。"""  # noqa: DOCSTRING_CJK
    before_histories = (
        before_state.get("branch_history")
        if isinstance(before_state, dict)
        and isinstance(before_state.get("branch_history"), list)
        else []
    )
    after_histories = (
        after_state.get("branch_history")
        if isinstance(after_state, dict)
        and isinstance(after_state.get("branch_history"), list)
        else []
    )
    committed_keys = {
        (
            str(item.get("branch_id") or ""),
            item.get("ended_revision"),
            str(item.get("exit_kind") or ""),
        )
        for item in before_histories
        if isinstance(item, dict)
    }
    for item in after_histories:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("branch_id") or ""),
            item.get("ended_revision"),
            str(item.get("exit_kind") or ""),
        )
        if key in committed_keys or not key[0] or not key[2]:
            continue
        observability.record_result(
            responsibility="theater_actor",
            surface="branch_turn",
            result_kind="branch_outcome",
            outcome=key[2],
        )


def _narrative_goal(story: dict[str, Any], goal_id: str) -> dict[str, Any]:
    """按稳定 Goal ID 读取作者汇流定义，不做相似目标猜测或自动改绑。"""  # noqa: DOCSTRING_CJK
    return next(
        (
            item
            for item in story.get("narrative_goals") or []
            if isinstance(item, dict)
            and str(item.get("goal_id") or "") == str(goal_id or "")
        ),
        {},
    )


def _ending_domain(story: dict[str, Any], ending_domain_id: str) -> dict[str, Any]:
    """按稳定 Ending Domain ID 读取作者结局映射，不接受模型自由结局名称。"""  # noqa: DOCSTRING_CJK
    return next(
        (
            item
            for item in story.get("ending_domains") or []
            if isinstance(item, dict)
            and str(item.get("ending_domain_id") or "") == str(ending_domain_id or "")
        ),
        {},
    )


async def _prepare_runtime_branch_entry(
    *,
    session: dict[str, Any],
    story: dict[str, Any],
    state: dict[str, Any],
    current_node: dict[str, Any],
    current_scene: dict[str, Any],
    message: str,
    lanlan_name: str,
    config_manager: Any | None,
    response_focus: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """准备合法活动支线与入口演出；任一步失败都返回两个空值且不修改状态。"""  # noqa: DOCSTRING_CJK
    dynamic_intent = state.get("dynamic_intent")
    if not intent_tracker.should_plan_branch(
        dynamic_intent,
        current_node_id=str(current_node.get("node_id") or ""),
    ):
        return None, None
    if isinstance(state.get("active_runtime_branch"), dict) and state.get(
        "active_runtime_branch"
    ):
        # 活动支线禁止嵌套；阶段四会让后续输入直接进入现有 Branch Runtime，而不是重新规划。
        return None, None
    planned = await branch_planner.plan_validated_runtime_branch(
        config_manager=config_manager,
        story=story,
        scene=current_scene,
        current_node_id=str(current_node.get("node_id") or ""),
        current_node=current_node,
        state=state,
        dynamic_intent=dynamic_intent if isinstance(dynamic_intent, dict) else {},
        recent_turns=list(session.get("turns") or []),
    )
    patch = (
        planned.get("patch")
        if isinstance(planned, dict) and planned.get("ok") is True
        else None
    )
    if not isinstance(patch, dict):
        # Planner 失败和合同拒绝都只影响本次尝试，当前输入仍由普通 Actor 回应。
        return None, None

    contract = (
        story.get("world_contract")
        if isinstance(story.get("world_contract"), dict)
        else {}
    )
    budget = (
        contract.get("branch_turn_budget")
        if isinstance(contract.get("branch_turn_budget"), dict)
        else {}
    )
    max_nonprogress_turns = budget.get("max_nonprogress_turns")
    if type(max_nonprogress_turns) is not int:
        # Loader 正常会拦截坏合同；这里保守保护升级前或测试注入的非标准 Story。
        return None, None
    return_goal_id = next(
        (
            str(item.get("goal_id") or "")
            for item in patch.get("exit_candidates") or []
            if isinstance(item, dict) and item.get("kind") == "converge"
        ),
        "",
    )
    try:
        active_branch = branch_lifecycle.build_active_runtime_branch(
            patch,
            branch_id=f"branch_{uuid.uuid4()}",
            created_revision=session_store.state_revision(session) + 1,
            return_anchor={
                "node_id": str(current_node.get("node_id") or ""),
                "goal_id": return_goal_id,
            },
            max_nonprogress_turns=max_nonprogress_turns,
            existing_branch=(
                state.get("active_runtime_branch")
                if isinstance(state.get("active_runtime_branch"), dict)
                else None
            ),
        )
    except ValueError:
        # 服务端身份构造失败也不能留下仅含 Patch 的半状态。
        return None, None
    verified_response_focus = llm.verify_response_focus(
        response_focus,
        user_message=message,
    )
    entry_performance = await llm.generate_branch_entry_async(
        config_manager=config_manager,
        lanlan_name=lanlan_name,
        story=story,
        scene=current_scene,
        node=current_node,
        user_message=message,
        state=state,
        recent_turns=list(session.get("turns") or []),
        patch=patch,
        response_focus=verified_response_focus,
    )
    if entry_performance is None:
        # 入口锚点非法等不可执行结果才会到这里；模型输出故障已由入口层安全演出吸收。
        return None, None
    return active_branch, entry_performance


def _revalidatable_pending_intent(
    pending: Any,
    *,
    current_node_id: str,
    current_scene_id: str,
    current_revision: int,
) -> dict[str, Any]:
    """只把仍处于精确目标范围的 Pending 交给 Router，坏结构和过期对象直接清除。"""  # noqa: DOCSTRING_CJK
    if not isinstance(pending, dict) or not pending:
        return {}
    try:
        decision = branch_lifecycle.evaluate_pending_intent(
            pending,
            current_node_id=current_node_id,
            current_scene_id=current_scene_id,
            current_revision=current_revision,
            # 玩家是否否定和语义是否兼容由本次 Router 结果体现；非 free_intent 会在同回合清除。
            player_denied=False,
            scene_compatible=True,
        )
    except ValueError:
        # 升级前或损坏 Session 的私有辅助状态不能阻断公开剧情恢复。
        return {}
    return dict(pending) if decision == "revalidate" else {}


def _apply_exit(session: dict[str, Any], story: dict[str, Any]) -> dict[str, Any]:
    """结束本场演出，但不伪装成作者结局。"""  # noqa: DOCSTRING_CJK
    now = _now_ms()
    session["ended_at"] = now
    session["end_reason"] = "user_exit"
    session.pop("dormant_at", None)
    ending = {
        "should_offer_ending": False,
        "should_end_session": True,
        "ending_id": "",
        "reason": "user_exit",
    }
    state = (
        session.get("story_state")
        if isinstance(session.get("story_state"), dict)
        else {}
    )
    active_branch = state.get("active_runtime_branch")
    if isinstance(active_branch, dict) and active_branch:
        # 主动离场关闭支线但不消耗支线预算，已经公开提交的事实继续保存在 History 索引中。
        _, branch_decision = branch_lifecycle.advance_active_branch(
            active_branch,
            event="user_exit",
        )
        _finish_runtime_branch(
            state,
            story=story,
            active_branch=active_branch,
            decision=branch_decision,
            ended_revision=session_store.state_revision(session) + 1,
        )
    # Session 已结束时所有未提交意图都失效；正式事实和历史仍原样保留。
    state["dynamic_intent"] = {}
    state["pending_intent"] = {}
    session["story_state"] = state
    node = story_graph.current_node(story, state)
    phase = str(node.get("belong_phase") or session.get("phase") or "setup")
    scene = story_loader.scene_for_phase(story, phase)
    # 玩家离场是 Session 管理动作，不属于作者剧情；使用前端 i18n 状态提示，不伪造角色对白。
    dialogue = ""
    trace = projector.scenario_trace(progress_kind="user_exit")
    return projector.public_response(
        session=session,
        story=story,
        scene=scene,
        narration="",
        dialogue=dialogue,
        trace=trace,
        ending=ending,
        can_resume=False,
    )
