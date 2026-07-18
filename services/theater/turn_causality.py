"""构造小剧场单回合私有因果记录，不参与公开投影或剧情权威。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any


def build_record(
    *,
    session_id: str,
    request: dict[str, Any],
    response_focus: dict[str, Any],
    model_return_records: list[dict[str, Any]],
    response: dict[str, Any],
    before_state: Any,
    after_state: Any,
    base_revision: int,
    result_revision: int,
    session_ended: bool,
) -> dict[str, Any]:
    """把一次成功提交收敛为可按回合身份直接复盘的私有记录。"""  # noqa: DOCSTRING_CJK
    before = before_state if isinstance(before_state, dict) else {}
    after = after_state if isinstance(after_state, dict) else {}
    return {
        "session_id": str(session_id or ""),
        "client_turn_id": str(request.get("client_turn_id") or ""),
        "base_revision": int(base_revision),
        "result_revision": int(result_revision),
        "input": {
            "input_kind": str(request.get("input_kind") or ""),
            "message": str(request.get("message") or ""),
            "choice_id": str(request.get("choice_id") or ""),
        },
        # 焦点已经由 Turn Service 按本轮完整原话复核；这里只做隔离复制，不再推断语义。
        "response_focus": deepcopy(response_focus)
        if isinstance(response_focus, dict)
        else {},
        # 原始正文继续只保存在 llm_return_records；这里保留同回合内的稳定调用引用。
        "model_return_refs": [
            {
                "call_index": item.get("call_index"),
                "call_type": str(item.get("call_type") or ""),
                "surface": str(item.get("surface") or ""),
                "status": str(item.get("status") or ""),
            }
            for item in model_return_records
            if isinstance(item, dict)
        ],
        # 只复制排查演出所需的公开子集，避免把 Board 和推荐项重复写入私有诊断。
        "final_public_output": {
            key: deepcopy(response.get(key))
            for key in ("narration", "dialogue", "scenario_trace", "ending")
        },
        "commit_summary": {
            "current_node_id_before": str(before.get("current_node_id") or ""),
            "current_node_id_after": str(after.get("current_node_id") or ""),
            "narrative_facts_added": _added_values(
                before.get("narrative_facts"), after.get("narrative_facts")
            ),
            "branch_facts_added": _added_values(
                before.get("branch_facts"), after.get("branch_facts")
            ),
            "completed_goal_ids_added": _added_values(
                before.get("completed_goal_ids"), after.get("completed_goal_ids")
            ),
            "branch_history_entries_added": _added_values(
                before.get("branch_history"), after.get("branch_history")
            ),
            "active_runtime_branch_before": _has_active_branch(before),
            "active_runtime_branch_after": _has_active_branch(after),
            "session_ended": bool(session_ended),
        },
    }


def _added_values(before_value: Any, after_value: Any) -> list[Any]:
    """按 JSON 值的出现次数求新增项，保留提交后的真实顺序和重复语义。"""  # noqa: DOCSTRING_CJK
    before = before_value if isinstance(before_value, list) else []
    after = after_value if isinstance(after_value, list) else []
    remaining: dict[str, int] = {}
    for item in before:
        identity = _stable_identity(item)
        remaining[identity] = remaining.get(identity, 0) + 1
    added: list[Any] = []
    for item in after:
        identity = _stable_identity(item)
        if remaining.get(identity, 0) > 0:
            remaining[identity] -= 1
            continue
        added.append(deepcopy(item))
    return added


def _stable_identity(value: Any) -> str:
    """用 Session 可序列化形态构造稳定比较键，不修改原对象。"""  # noqa: DOCSTRING_CJK
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _has_active_branch(state: dict[str, Any]) -> bool:
    """只记录活动支线是否存在，不复制整个运行时 Patch。"""  # noqa: DOCSTRING_CJK
    value = state.get("active_runtime_branch")
    return isinstance(value, dict) and bool(value)
