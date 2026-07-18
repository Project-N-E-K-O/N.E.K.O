"""提供 v2.6 通用自由意图短期线程的服务端确定性累计规则。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from typing import Any


# 第二次连续坚持才允许进入下一阶段；模型置信度不能绕过这个产品阈值。
DYNAMIC_INTENT_PLANNING_THRESHOLD = 2
# 只保留规划所需的近期原话，避免 Session 和后续 Router 上下文无限增长。
MAX_DYNAMIC_INTENT_EVIDENCE = 3
MAX_DYNAMIC_INTENT_SUMMARY_CHARS = 160
MAX_DYNAMIC_INTENT_EVIDENCE_CHARS = 240
ALLOWED_DYNAMIC_INTENT_RELATIONS = frozenset({"new", "continue", "refine", "replace"})
ALLOWED_DYNAMIC_INTENT_THREAD_STATES = frozenset({"active", "dormant"})


def evidence_message_fits(value: Any) -> bool:
    """判断完整玩家证据能否无损写入意图线程，禁止静默截掉句尾语义。"""  # noqa: DOCSTRING_CJK
    normalized = " ".join(str(value or "").strip().split())
    return bool(normalized) and len(normalized) <= MAX_DYNAMIC_INTENT_EVIDENCE_CHARS


def update_dynamic_intent(
    current_intent: Any,
    *,
    new_intent_key: str,
    summary: str,
    relation: str,
    evidence_message: str,
    origin_node_id: str,
    confirmed_pending_evidence: str = "",
) -> dict[str, Any]:
    """按 Router 语义更新意图；身份、次数和阈值始终由服务端掌握。"""  # noqa: DOCSTRING_CJK
    if not evidence_message_fits(evidence_message):
        # Router 必须在调用本函数前把超界输入降级；这里保留最终写入边界，避免其他调用方静默截断。
        raise ValueError("Dynamic Intent evidence would be truncated")
    normalized_key = _required_text(new_intent_key, "intent_key", max_chars=128)
    normalized_summary = _required_text(
        summary,
        "intent_summary",
        max_chars=MAX_DYNAMIC_INTENT_SUMMARY_CHARS,
    )
    normalized_evidence = _required_text(
        evidence_message,
        "evidence_message",
        max_chars=MAX_DYNAMIC_INTENT_EVIDENCE_CHARS,
    )
    normalized_origin = _required_text(origin_node_id, "origin_node_id", max_chars=160)
    normalized_relation = str(relation or "").strip()
    if normalized_relation not in ALLOWED_DYNAMIC_INTENT_RELATIONS:
        raise ValueError("Dynamic Intent has invalid relation")

    can_continue = normalized_relation in {"continue", "refine"} and _can_continue(
        current_intent,
        origin_node_id=normalized_origin,
    )
    if can_continue:
        # 同一意图只使用已有服务端身份；即使调用方预生成了新 ID，也不能让模型关系间接更换身份。
        intent_key = str(current_intent["intent_key"])
        streak = min(
            int(current_intent["streak"]) + 1,
            DYNAMIC_INTENT_PLANNING_THRESHOLD,
        )
        evidence = _existing_evidence(current_intent)
        stored_relation = normalized_relation
    else:
        intent_key = normalized_key
        evidence = []
        pending_evidence = _optional_text(
            confirmed_pending_evidence,
            max_chars=MAX_DYNAMIC_INTENT_EVIDENCE_CHARS,
        )
        if normalized_relation in {"continue", "refine"} and pending_evidence:
            # Pending 没有身份或次数；只有 Router 明确确认承接时，上一轮原话才作为第一条玩家证据。
            evidence.append(pending_evidence)
            streak = DYNAMIC_INTENT_PLANNING_THRESHOLD
            stored_relation = normalized_relation
        else:
            streak = 1
            # 没有可延续状态时，误报的 continue/refine 只能按新意图起算；显式 replace 仍保留审计语义。
            stored_relation = (
                normalized_relation
                if normalized_relation in {"new", "replace"}
                else "new"
            )

    evidence.append(normalized_evidence)
    return {
        "intent_key": intent_key,
        "intent_summary": normalized_summary,
        "origin_node_id": normalized_origin,
        "streak": streak,
        "evidence_messages": evidence[-MAX_DYNAMIC_INTENT_EVIDENCE:],
        "relation": stored_relation,
        "thread_state": "active",
    }


def should_plan_branch(dynamic_intent: Any, *, current_node_id: str) -> bool:
    """返回是否达到规划阈值；本函数本身不创建 Patch 或改变 Session。"""  # noqa: DOCSTRING_CJK
    if not isinstance(dynamic_intent, dict):
        return False
    normalized_current_node_id = str(current_node_id or "").strip()
    if not normalized_current_node_id or not _can_continue(
        dynamic_intent,
        origin_node_id=normalized_current_node_id,
    ):
        # 错节点、坏次数或不完整证据都不能送入 Planner 形成权威 Patch。
        return False
    # 旧 v2.5 存档缺少 thread_state 时按 active 读取；休眠线程必须先由本轮明确承接恢复。
    if str(dynamic_intent.get("thread_state") or "active") != "active":
        return False
    streak = dynamic_intent.get("streak")
    return type(streak) is int and streak == DYNAMIC_INTENT_PLANNING_THRESHOLD


def mark_dynamic_intent_idle(state: dict[str, Any], *, current_node_id: str) -> None:
    """第一次 idle 休眠同节点线程，连续第二次 idle 或坏状态才清理。"""  # noqa: DOCSTRING_CJK
    current_intent = state.get("dynamic_intent")
    normalized_origin = str(current_node_id or "").strip()
    if not normalized_origin or not _can_continue(
        current_intent, origin_node_id=normalized_origin
    ):
        clear_dynamic_intent(state)
        return
    if str(current_intent.get("thread_state") or "active") == "dormant":
        clear_dynamic_intent(state)
        return
    # 只保存协议内私有字段，既保留服务端身份和证据，也不让旧存档中的任意扩展字段继续传播。
    state["dynamic_intent"] = {
        "intent_key": str(current_intent["intent_key"]),
        "intent_summary": str(current_intent["intent_summary"]),
        "origin_node_id": normalized_origin,
        "streak": int(current_intent["streak"]),
        "evidence_messages": _existing_evidence(current_intent),
        "relation": (
            str(current_intent.get("relation") or "new")
            if str(current_intent.get("relation") or "new")
            in ALLOWED_DYNAMIC_INTENT_RELATIONS
            else "new"
        ),
        "thread_state": "dormant",
    }


def clear_dynamic_intent(state: dict[str, Any]) -> None:
    """清除当前节点的通用意图，不改动 scene notes 或任何正式事实。"""  # noqa: DOCSTRING_CJK
    state["dynamic_intent"] = {}


def _can_continue(current_intent: Any, *, origin_node_id: str) -> bool:
    """只让结构完整且仍位于同一作者节点的意图继续累计。"""  # noqa: DOCSTRING_CJK
    if not isinstance(current_intent, dict):
        return False
    streak = current_intent.get("streak")
    thread_state = str(current_intent.get("thread_state") or "active")
    normalized_origin_node_id = str(origin_node_id or "").strip()
    return (
        bool(str(current_intent.get("intent_key") or "").strip())
        and 0
        < len(" ".join(str(current_intent.get("intent_summary") or "").strip().split()))
        <= MAX_DYNAMIC_INTENT_SUMMARY_CHARS
        and bool(normalized_origin_node_id)
        and str(current_intent.get("origin_node_id") or "").strip()
        == normalized_origin_node_id
        and type(streak) is int
        and 1 <= streak <= DYNAMIC_INTENT_PLANNING_THRESHOLD
        and _evidence_matches_streak(
            current_intent.get("evidence_messages"),
            streak=streak,
        )
        and thread_state in ALLOWED_DYNAMIC_INTENT_THREAD_STATES
    )


def _existing_evidence(current_intent: dict[str, Any]) -> list[str]:
    """清洗旧 Session 证据；超界项整条丢弃，绝不保存截断前缀。"""  # noqa: DOCSTRING_CJK
    evidence: list[str] = []
    for item in current_intent.get("evidence_messages") or []:
        normalized = " ".join(str(item or "").strip().split())
        if evidence_message_fits(normalized):
            evidence.append(normalized)
    return evidence[-MAX_DYNAMIC_INTENT_EVIDENCE:]


def _evidence_list_is_complete(value: Any) -> bool:
    """确认旧意图的每条玩家证据都能无损进入当前合同。"""  # noqa: DOCSTRING_CJK
    return bool(
        isinstance(value, list)
        and value
        and len(value) <= MAX_DYNAMIC_INTENT_EVIDENCE
        and all(isinstance(item, str) and evidence_message_fits(item) for item in value)
    )


def _evidence_matches_streak(value: Any, *, streak: int) -> bool:
    """校验证据数量与服务端次数一致，并允许阈值后的有界规划重试。"""  # noqa: DOCSTRING_CJK
    if not _evidence_list_is_complete(value):
        return False
    evidence_count = len(value)
    if streak < DYNAMIC_INTENT_PLANNING_THRESHOLD:
        return evidence_count == streak
    # 达到阈值后 Planner 可能技术失败；后续明确坚持仍保留最近三条证据，但 streak 不再增长。
    return (
        streak == DYNAMIC_INTENT_PLANNING_THRESHOLD
        and streak <= evidence_count <= MAX_DYNAMIC_INTENT_EVIDENCE
    )


def _required_text(value: Any, field: str, *, max_chars: int) -> str:
    """规范化服务端短文本；超界时拒绝，不能静默丢掉末尾语义。"""  # noqa: DOCSTRING_CJK
    normalized = " ".join(str(value or "").strip().split())
    if not normalized or len(normalized) > max_chars:
        raise ValueError(f"Dynamic Intent has invalid {field}")
    return normalized


def _optional_text(value: Any, *, max_chars: int) -> str:
    """规范化可选玩家证据；空值表示本轮没有经过确认的 Pending。"""  # noqa: DOCSTRING_CJK
    normalized = " ".join(str(value or "").strip().split())
    return normalized if len(normalized) <= max_chars else ""
