"""校验小剧场回合请求、幂等结果和低基数执行分类。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from copy import deepcopy
from typing import Any


# 幂等缓存只服务近期网络重试，避免长剧本存档无限增长。
MAX_IDEMPOTENT_RESULTS = 32
# 自由演绎允许长段输入，但必须限制 Session JSON 和后续模型上下文的最坏体积。
MAX_FREE_INPUT_CHARS = 4000


def _turn_submit_outcome(result: Any) -> str:
    """把公开业务结果收敛成固定低基数指标，未知原因不得直接成为维度。"""  # noqa: DOCSTRING_CJK
    if isinstance(result, dict) and result.get("ok") is True:
        return "success"
    reason = str(result.get("reason") or "") if isinstance(result, dict) else ""
    if reason.startswith("invalid_") or reason == "free_input_too_long":
        return "invalid_request"
    if reason in {
        "session_not_found",
        "session_ended",
        "session_story_unavailable",
        "session_story_revision_mismatch",
        "session_state_invalid",
    }:
        return "session_unavailable"
    if reason in {
        "session_character_mismatch",
        "stale_session",
        "state_revision_conflict",
    }:
        return "state_conflict"
    if reason == "choice_not_available":
        return "choice_unavailable"
    return "rejected_other"


def _turn_execution_surface(
    *,
    request: dict[str, Any],
    response: dict[str, Any],
    candidate: dict[str, Any],
    before_active_branch: bool,
) -> str:
    """从候选提交结果派生固定执行场景，不把节点、Choice 或剧情文本写进指标。"""  # noqa: DOCSTRING_CJK
    if request.get("input_kind") == "user_exit":
        return "user_exit"
    trace = response.get("scenario_trace")
    if isinstance(trace, dict) and trace.get("progress_kind") == "graph_progress":
        return "graph_progress"
    if before_active_branch:
        return "branch_turn"
    state = candidate.get("story_state")
    after_active_branch = bool(
        isinstance(state, dict)
        and isinstance(state.get("active_runtime_branch"), dict)
        and state.get("active_runtime_branch")
    )
    return "branch_entry" if after_active_branch else "roleplay_response"


def _verified_residual_evidence_excerpt(
    user_message: Any, evidence_excerpt: Any
) -> str:
    """只接受能在本轮玩家原话中逐字找到的规范化摘录，拒绝模型改写或虚构证据。"""  # noqa: DOCSTRING_CJK
    normalized_message = " ".join(str(user_message or "").strip().split())
    normalized_excerpt = " ".join(str(evidence_excerpt or "").strip().split())
    if not normalized_excerpt or normalized_excerpt not in normalized_message:
        return ""
    return normalized_excerpt

def _normalize_request(
    *,
    input_kind: str,
    choice_id: str,
    message: str,
    client_turn_id: str,
    base_revision: Any,
) -> tuple[dict[str, Any], str]:
    """校验三类互斥输入和客户端幂等字段。"""  # noqa: DOCSTRING_CJK
    kind = str(input_kind or "").strip()
    if kind not in {"choice", "free_input", "user_exit"}:
        return {}, "invalid_input_kind"
    client_id = str(client_turn_id or "").strip()
    if not client_id or len(client_id) > 128:
        return {}, "invalid_client_turn_id"
    if base_revision is not None and (
        not isinstance(base_revision, int)
        or isinstance(base_revision, bool)
        or base_revision < 0
    ):
        return {}, "invalid_base_revision"
    normalized_choice = str(choice_id or "").strip()
    normalized_message = str(message or "").strip()
    if kind == "choice" and (not normalized_choice or normalized_message):
        return {}, "invalid_choice_input"
    if kind == "free_input" and (not normalized_message or normalized_choice):
        return {}, "invalid_free_input"
    if kind == "free_input" and len(normalized_message) > MAX_FREE_INPUT_CHARS:
        # 不静默截断玩家演绎；明确拒绝后前端可以保留原文，让玩家自行精简再提交。
        return {}, "free_input_too_long"
    if kind == "user_exit" and (normalized_message or normalized_choice):
        return {}, "invalid_user_exit"
    return {
        "input_kind": kind,
        "choice_id": normalized_choice,
        "message": normalized_message,
        "client_turn_id": client_id,
        "base_revision": base_revision,
    }, ""


def _cached_result(session: dict[str, Any], client_turn_id: str) -> dict[str, Any]:
    """回放首次提交结果，不重复调用模型或推进剧情。"""  # noqa: DOCSTRING_CJK
    index = session.get("turn_results_by_client_id")
    cached = index.get(client_turn_id) if isinstance(index, dict) else None
    return deepcopy(cached) if isinstance(cached, dict) else {}


def _revision_conflict(revision: int) -> dict[str, Any]:
    """返回前端可恢复的版本冲突。"""  # noqa: DOCSTRING_CJK
    return {
        "ok": False,
        "reason": "state_revision_conflict",
        "retryable": True,
        "state_revision": revision,
    }
