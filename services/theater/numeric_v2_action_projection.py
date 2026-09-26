"""Numeric v2 的玩家动作结果投影。

这里只保留可以从玩家原话或 Runtime 已提交结果直接证明的短状态。它不是
自然语言理解器，也不尝试推断目的地、成功结果、情绪或隐含意图。
"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import re
from typing import Any, Mapping


PLAYER_ACTION_PROJECTION_SCHEMA = "neko.script.player_action_projection.numeric.v1"
_MAX_EVIDENCE_CHARS = 180
_MAX_ROWS = 8

# 这些是跨题材的动作词，不代表任何具体剧本对象。只有同时满足明确完成/实施
# 结构且没有未来或疑问标记时，才生成玩家动作行。
_ACTION_TERMS = (
    "签", "拿", "交", "放", "打开", "关上", "关闭", "填写", "报名", "参加",
    "接受", "拒绝", "选择", "开始", "继续", "离开", "回去", "回家", "前往",
    "进入", "出来", "走", "复习", "学习", "留下", "等待", "等", "检查", "核对",
    "整理", "收拾", "确认", "按下", "接过", "还给", "递给", "读", "看",
)
_DEPARTURE_TERMS = (
    "先回去", "先回家", "先走", "走了", "离开这里", "离开", "回家", "走向门口",
    "走向巷口", "走出门", "走出去", "推门离开", "转身离开", "起身离开", "那我走", "我们走",
)
_QUESTION_MARKERS = ("？", "?", "吗", "能不能", "可以吗", "要不要", "是否", "有没有")
_UNCOMMITTED_MARKERS = (
    "想", "希望", "准备", "打算", "考虑", "计划", "可能", "也许", "如果", "尝试", "试试",
    "以后再", "有空再",
)
_COMPLETION_MARKERS = (
    "已经", "已", "刚刚", "刚", "了", "吧", "好，", "好的，", "行，", "那我", "我就", "先",
)
_FUTURE_MARKERS = (
    "明天", "后天", "周一", "周二", "周三", "周四", "周五", "周六", "周日", "周天",
    "下周", "周末", "之后", "改天", "回头", "再来", "再见", "见面",
)
_SINGLE_ACTION_SUFFIXES = {
    "签": "了字名署",
    "拿": "起出到给走来着过",
    "交": "给上出付",
    "放": "下在到进回",
    "走": "了吧向到出进开去着在",
    "看": "完到见着书一下过了",
    "等": "待你一下到着",
    "读": "完着书到了",
}


def _evidence(value: Any) -> str:
    return " ".join(str(value or "").strip().split())[:_MAX_EVIDENCE_CHARS]


def _is_question(text: str) -> bool:
    return any(marker in text for marker in _QUESTION_MARKERS)


def _has_future_or_uncommitted_marker(text: str) -> bool:
    return any(marker in text for marker in _UNCOMMITTED_MARKERS)


def _explicit_subject(text: str) -> bool:
    # “先回去复习”“我们走吧”是日常省略主语的直接表达；“带我去”是对
    # 猫娘的请求，不把它当作玩家已经移动。
    return bool(re.search(r"(?:^|[，,。；;：:\s])(?:我|我们|咱们|先|那我|这就)", text))


def _action_term(text: str) -> str:
    for term in sorted(_ACTION_TERMS, key=len, reverse=True):
        start = text.find(term)
        if start < 0:
            continue
        if len(term) > 1:
            return term
        suffixes = _SINGLE_ACTION_SUFFIXES.get(term)
        if suffixes is None:
            return term
        end = start + len(term)
        if end == len(text) or text[end] in suffixes:
            return term
    return ""


def _clauses(text: str) -> list[str]:
    """按玩家输入里的自然分句保留动作证据，不把后续约定当成前句否定。"""  # noqa: DOCSTRING_CJK

    parenthetical = re.findall(r"（[^）]*）|\([^)]*\)", text)
    remainder = re.sub(r"（[^）]*）|\([^)]*\)", "", text)
    return parenthetical + [
        clause.strip()
        for clause in re.split(r"[，,。；;：:\n]+", remainder)
        if clause.strip()
    ]


def _is_parenthetical_action(clause: str) -> bool:
    return (
        len(clause) >= 2
        and clause[0] in "（("
        and clause[-1] in "）)"
        and bool(_action_term(clause))
    )


def _explicit_action_clause(text: str, *, departure: bool = False) -> str:
    terms = _DEPARTURE_TERMS if departure else _ACTION_TERMS
    for clause in _clauses(text):
        if departure:
            has_term = any(term in clause for term in terms)
        else:
            has_term = bool(_action_term(clause))
        if not has_term:
            continue
        if _is_question(clause) or _has_future_or_uncommitted_marker(clause):
            continue
        if _explicit_subject(clause) or _is_parenthetical_action(clause):
            return clause
    return ""


def _departure_is_explicit(text: str) -> bool:
    return bool(_explicit_action_clause(text, departure=True))


def _action_is_explicit(text: str) -> bool:
    clause = _explicit_action_clause(text)
    if not clause:
        return False
    # “我签”/“好，我签”属于直接执行表达；其它动作必须有完成态或
    # 明确的当下执行前缀，避免把“我想签”投影成已完成。
    return any(marker in clause for marker in _COMPLETION_MARKERS) or _is_parenthetical_action(clause)


def _future_reference(text: str) -> bool:
    if _is_question(text) or _has_future_or_uncommitted_marker(text):
        return False
    if not any(marker in text for marker in _FUTURE_MARKERS):
        return False
    return any(term in text for term in ("见", "回来", "再来", "联系", "继续", "碰面"))


def project_player_action_result(
    message: str,
    *,
    revision: int = 0,
    transition_intent: str = "unclear",
    route_changed: bool = False,
    fact_operations: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """生成有界的玩家动作结果投影。

    `player_input` 只提供证据原文；只有满足保守的直接执行结构时才进入
    `confirmed_actions`。Runtime 的转场和事实操作属于独立的确定性来源。
    """  # noqa: DOCSTRING_CJK

    text = _evidence(message)
    confirmed: list[dict[str, Any]] = []
    future: list[dict[str, Any]] = []
    if text and _departure_is_explicit(text):
        confirmed.append({
            "kind": "leave_current_scene",
            "status": "completed",
            "source": "player_input",
            "evidence_quote": text,
        })
    if text and not confirmed and _action_is_explicit(text):
        confirmed.append({
            "kind": "player_action",
            "status": "performed",
            "action_term": _action_term(text),
            "source": "player_input",
            "evidence_quote": text,
        })
    if text and _future_reference(text):
        future.append({
            "kind": "future_return_or_follow_up",
            "status": "future",
            "source": "player_input",
            "evidence_quote": text,
        })

    if route_changed and transition_intent in {"accept", "initiate"}:
        confirmed.append({
            "kind": "transition_committed",
            "status": "completed",
            "source": "runtime_transition",
        })
    for operation in fact_operations or ():
        if not isinstance(operation, Mapping):
            continue
        key = str(operation.get("key") or "").strip()
        if not key:
            continue
        confirmed.append({
            "kind": "runtime_result",
            "status": "confirmed",
            "source": "runtime_fact",
            "fact_key": key,
            "value": operation.get("value"),
        })

    confirmed = confirmed[:_MAX_ROWS]
    future = future[:_MAX_ROWS]
    return {
        "schema": PLAYER_ACTION_PROJECTION_SCHEMA,
        "source_revision": int(revision),
        "confirmed_actions": confirmed,
        "future_references": future,
        "has_confirmed_player_action": any(
            row.get("source") == "player_input" for row in confirmed
        ),
        "player_left_current_scene": any(
            row.get("kind") == "leave_current_scene" for row in confirmed
        ),
    }


def normalize_player_action_projection(value: Any) -> dict[str, Any]:
    """校验并复制投影，供 Ledger/Prompt 读取，拒绝外部注入的自由字段。"""  # noqa: DOCSTRING_CJK

    if not isinstance(value, Mapping) or value.get("schema") != PLAYER_ACTION_PROJECTION_SCHEMA:
        return project_player_action_result("")
    confirmed = value.get("confirmed_actions")
    future = value.get("future_references")
    if not isinstance(confirmed, list) or not isinstance(future, list):
        return project_player_action_result("")
    normalized_confirmed: list[dict[str, Any]] = []
    for row in confirmed[:_MAX_ROWS]:
        if not isinstance(row, Mapping):
            continue
        item = {
            "kind": str(row.get("kind") or ""),
            "status": str(row.get("status") or ""),
            "source": str(row.get("source") or ""),
        }
        for key in ("action_term", "evidence_quote", "fact_key"):
            if row.get(key) is not None and str(row.get(key) or ""):
                item[key] = _evidence(row.get(key))
        if "value" in row and isinstance(row.get("value"), (bool, int, str)):
            item["value"] = row.get("value")
        if item["kind"] and item["status"] and item["source"]:
            normalized_confirmed.append(item)
    normalized_future: list[dict[str, Any]] = []
    for row in future[:_MAX_ROWS]:
        if not isinstance(row, Mapping):
            continue
        item = {
            "kind": str(row.get("kind") or ""),
            "status": str(row.get("status") or ""),
            "source": str(row.get("source") or ""),
        }
        if row.get("evidence_quote"):
            item["evidence_quote"] = _evidence(row.get("evidence_quote"))
        if item["kind"] and item["status"] and item["source"]:
            normalized_future.append(item)
    return {
        "schema": PLAYER_ACTION_PROJECTION_SCHEMA,
        "source_revision": (
            value.get("source_revision")
            if type(value.get("source_revision")) is int and value.get("source_revision") >= 0
            else 0
        ),
        "confirmed_actions": normalized_confirmed,
        "future_references": normalized_future,
        "has_confirmed_player_action": any(
            row.get("source") == "player_input" for row in normalized_confirmed
        ),
        "player_left_current_scene": any(
            row.get("kind") == "leave_current_scene" for row in normalized_confirmed
        ),
    }


__all__ = [
    "PLAYER_ACTION_PROJECTION_SCHEMA",
    "normalize_player_action_projection",
    "project_player_action_result",
]
