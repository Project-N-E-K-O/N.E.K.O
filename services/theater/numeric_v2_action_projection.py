"""Numeric v2 player-action outcome projection.

Retain only short states directly provable from the player's words or Runtime's committed outcomes. This is not
a natural-language interpreter and does not infer destinations, successful outcomes, emotions, or implicit intent.
"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import re
from typing import Any, Mapping


PLAYER_ACTION_PROJECTION_SCHEMA = "neko.script.player_action_projection.numeric.v1"
_MAX_EVIDENCE_CHARS = 180
_MAX_ROWS = 8

# These action words span genres and denote no particular script object. Generate a player-action line only
# when an explicit completion/execution structure is present without future or question markers.
_ACTION_TERMS = (
    "签", "拿", "交", "放", "打开", "关上", "关闭", "填写", "报名", "参加",
    "接受", "拒绝", "选择", "开始", "继续", "离开", "回去", "回家", "前往",
    "进入", "出来", "走", "复习", "学习", "留下", "等待", "等", "检查", "核对",
    "整理", "收拾", "确认", "按下", "接过", "还给", "递给", "读", "看",
)
_DEPARTURE_TERMS = (
    "先回去", "先回家", "先走", "走了", "离开这里", "离开", "回家",
    "走出门", "走出去", "推门离开", "转身离开", "起身离开", "那我走", "我们走",
)
_EXIT_APPROACH_TERMS = ("走向门口", "走向巷口")
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


# An immediately preceding negation, optionally with one or two modal characters (e.g. will not, has not again, never),
# means the action/departure is not completed; "不得不" (have to) is a double negative and still counts as execution.
_NEGATION_BEFORE_TERM = re.compile(
    r"(?<!不得)[不没别未勿莫甭][有会要再能想用肯敢得准许必可]{0,2}$"
)


def _negated_at(text: str, start: int) -> bool:
    return bool(_NEGATION_BEFORE_TERM.search(text[:start]))


def _term_starts(text: str, term: str) -> list[int]:
    starts: list[int] = []
    start = text.find(term)
    while start >= 0:
        starts.append(start)
        start = text.find(term, start + 1)
    return starts


def _evidence(value: Any) -> str:
    return " ".join(str(value or "").strip().split())[:_MAX_EVIDENCE_CHARS]


def _is_question(text: str) -> bool:
    return any(marker in text for marker in _QUESTION_MARKERS)


def _has_future_or_uncommitted_marker(text: str) -> bool:
    return any(marker in text for marker in _UNCOMMITTED_MARKERS)


def _explicit_subject(text: str) -> bool:
    # Expressions such as going back to study or let's go directly omit the subject; "带我去" (take me there) asks
    # the catgirl to act and must not count as the player having moved.
    return bool(re.search(r"(?:^|[，,。；;：:\s])(?:我|我们|咱们|先|那我|这就)", text))


def _action_term(text: str) -> str:
    for term in sorted(_ACTION_TERMS, key=len, reverse=True):
        for start in _term_starts(text, term):
            if _negated_at(text, start):
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
    """Retain action evidence by natural clauses in player input, without treating later agreements as negating an earlier clause."""  # noqa: DOCSTRING_CJK

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
            has_term = any(
                not _negated_at(clause, start)
                and not any(
                    start < approach_start + len(approach)
                    and start + len(term) > approach_start
                    for approach in _EXIT_APPROACH_TERMS
                    for approach_start in _term_starts(clause, approach)
                )
                for term in terms
                for start in _term_starts(clause, term)
            )
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


def _exit_approach_clause(text: str) -> str:
    # When departure is explicitly canceled, prior approach actions must not suppress a new invitation; do not infer a return destination.
    if any(_negated_at(text, start) for term in _DEPARTURE_TERMS
           for start in _term_starts(text, term)):
        return ""
    for clause in _clauses(text):
        if any(term in clause for term in _EXIT_APPROACH_TERMS) and _explicit_action_clause(clause):
            return clause
    return ""


def _action_is_explicit(text: str) -> bool:
    clause = _explicit_action_clause(text)
    if not clause:
        return False
    # "我签" / "好，我签" (I sign / okay, I sign) directly execute; other actions require a completed form or
    # an explicit current-execution prefix, avoiding projection of wanting to sign as completed signing.
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
    """Generate a bounded player-action outcome projection.

    `player_input` supplies only source evidence; add it to `confirmed_actions` only when it satisfies
    a conservative direct-execution structure. Runtime transitions and fact operations are separate deterministic sources.
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
    approach_clause = _exit_approach_clause(text) if not confirmed else ""
    action_clause = _explicit_action_clause(text)
    if approach_clause:
        confirmed.append({
            "kind": "approach_scene_exit",
            "status": "performed",
            "source": "player_input",
            "evidence_quote": approach_clause,
        })
    elif text and not confirmed and _action_is_explicit(action_clause):
        confirmed.append({
            "kind": "player_action",
            "status": "performed",
            "action_term": _action_term(action_clause),
            "source": "player_input",
            "evidence_quote": action_clause,
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
        "player_departure_pending": any(
            row.get("kind") == "approach_scene_exit" for row in confirmed
        ),
    }


def normalize_player_action_projection(value: Any) -> dict[str, Any]:
    """Validate and copy the projection for Ledger/Prompt consumption, rejecting externally injected free-form fields."""  # noqa: DOCSTRING_CJK

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
        "player_departure_pending": any(
            row.get("kind") == "approach_scene_exit" for row in normalized_confirmed
        ),
    }


__all__ = [
    "PLAYER_ACTION_PROJECTION_SCHEMA",
    "normalize_player_action_projection",
    "project_player_action_result",
]
