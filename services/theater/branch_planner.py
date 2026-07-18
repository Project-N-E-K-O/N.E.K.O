"""生成并校验 v2.5 Runtime Branch Patch，不在该阶段激活或写入 Session。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from typing import Any

from . import branch_contracts, llm, observability


async def plan_validated_runtime_branch(
    *,
    config_manager: Any | None,
    story: dict[str, Any],
    scene: dict[str, Any],
    current_node_id: str,
    current_node: dict[str, Any],
    state: dict[str, Any],
    dynamic_intent: dict[str, Any],
    recent_turns: list[dict[str, Any]],
) -> dict[str, Any]:
    """返回合同合法的隔离 Patch；任何失败都只产生无权威的原因码。"""  # noqa: DOCSTRING_CJK
    candidate = await llm.plan_runtime_branch_async(
        config_manager=config_manager,
        story=story,
        scene=scene,
        current_node_id=current_node_id,
        current_node=current_node,
        state=state,
        dynamic_intent=dynamic_intent,
        recent_turns=recent_turns,
    )
    if candidate is None:
        # 模型缺失、超时或坏 JSON 都不能退化为服务端猜测的支线方案。
        return {"ok": False, "reason": "planner_failed"}
    try:
        validated = branch_contracts.validate_runtime_branch_patch(
            candidate,
            story=story,
            current_node_id=current_node_id,
            # Planner 输出即使忽略提示也必须服从当前 Session 已完成目标集合。
            completed_goal_ids=list(state.get("completed_goal_ids") or []),
        )
    except ValueError:
        # 不向调用方暴露模型原文或内部合同细节，也不保留半合法 Patch。
        observability.record_result(
            responsibility="theater_planner",
            surface="branch_entry",
            result_kind="patch_contract",
            outcome="rejected",
        )
        return {"ok": False, "reason": "patch_invalid"}
    # 校验器已经返回深拷贝；后续原子激活可以安全补充服务端 ID 与 revision。
    observability.record_result(
        responsibility="theater_planner",
        surface="branch_entry",
        result_kind="patch_contract",
        outcome="accepted",
    )
    return {"ok": True, "patch": validated}
