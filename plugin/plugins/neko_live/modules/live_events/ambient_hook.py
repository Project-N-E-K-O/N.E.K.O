"""Deterministic selection of one passive co-stream callback candidate."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Any


AMBIENT_HOOK_SCAN_LIMIT = 5

_LOW_VALUE_DENSE = {
    "233",
    "2333",
    "666",
    "hhh",
    "hhhh",
    "www",
    "好耶",
    "来了",
    "哈哈",
    "哈哈哈",
    "确实",
}
_VAGUE_FRAGMENTS = {
    "这个呢",
    "那个呢",
    "刚才那个",
    "就是那个",
    "然后呢",
    "然后就是",
}
_QUESTION_MARKERS = (
    "?",
    "？",
    "为什么",
    "怎么",
    "有没有",
    "是不是",
    "能不能",
    "可以吗",
    "讲讲",
    "说说",
)
_MOOD_MARKERS = (
    "笑死",
    "绷不住",
    "离谱",
    "破防",
    "救命",
    "可爱",
    "好惨",
    "太逗",
    "哈哈哈",
    "难过",
    "委屈",
    "紧张",
    "开心",
    "害怕",
    "心疼",
    "想哭",
    "好累",
)
_TOPIC_TOKEN_STOP = {
    "一个",
    "一下",
    "不是",
    "今天",
    "什么",
    "刚才",
    "可以",
    "感觉",
    "怎么",
    "就是",
    "现在",
    "真的",
    "这个",
    "那个",
}


@dataclass(frozen=True, slots=True)
class AmbientHookSelection:
    """One selected row plus privacy-safe diagnostics."""

    row: dict[str, object] | None = None
    reason: str = "no_candidates"
    score: int = 0
    candidate_count: int = 0


@dataclass(frozen=True, slots=True)
class _Candidate:
    row: dict[str, object]
    index: int
    uid: str
    text: str
    dense: str
    tokens: frozenset[str]
    chorus: bool = False


def select_ambient_hook(rows: list[dict[str, object]]) -> AmbientHookSelection:
    """Pick one complete, un-replied row without a model call.

    Input order is newest first. Exact repeats and single-viewer floods are
    excluded from hook selection but remain untouched in the factual tail.
    """

    clean_rows = [row for row in rows[:AMBIENT_HOOK_SCAN_LIMIT] if isinstance(row, dict)]
    if not clean_rows:
        return AmbientHookSelection()

    text_counts = Counter(
        _dense(row.get("text"))
        for row in clean_rows
        if _dense(row.get("text"))
    )
    text_viewers: dict[str, set[str]] = {}
    for row in clean_rows:
        dense = _dense(row.get("text"))
        uid = _clean_uid(row.get("uid"))
        if dense and uid:
            text_viewers.setdefault(dense, set()).add(uid)
    uid_counts = Counter(
        _clean_uid(row.get("uid")) or "<anonymous>"
        for row in clean_rows
    )
    rejected = Counter()
    candidates: list[_Candidate] = []
    for index, row in enumerate(clean_rows):
        if row.get("selected") is True:
            rejected["already_replied"] += 1
            continue
        text = _clean_text(row.get("text"))
        dense = _dense(text)
        if _is_low_value(dense):
            rejected["low_value"] += 1
            continue
        if dense in _VAGUE_FRAGMENTS or len(dense) < 4:
            rejected["fragment"] += 1
            continue
        uid = _clean_uid(row.get("uid")) or "<anonymous>"
        chorus = text_counts[dense] > 1 and len(text_viewers.get(dense, ())) >= 2
        if (text_counts[dense] > 1 and not chorus) or uid_counts[uid] >= 3:
            rejected["duplicate_or_flood"] += 1
            continue
        candidates.append(
            _Candidate(
                row=row,
                index=index,
                uid=uid,
                text=text,
                dense=dense,
                tokens=frozenset(_topic_tokens(text)),
                chorus=chorus,
            )
        )

    scored: list[tuple[int, int, str, _Candidate]] = []
    for candidate in candidates:
        continuity = any(
            candidate.uid != other.uid
            and bool(candidate.tokens & other.tokens)
            for other in candidates
            if other is not candidate
        )
        question = _looks_like_question(candidate.text, candidate.dense)
        mood = any(marker in candidate.text.casefold() for marker in _MOOD_MARKERS)
        complete = len(candidate.dense) >= 6 or continuity or question or mood
        if not complete:
            rejected["fragment"] += 1
            continue
        score = min(len(candidate.dense), 20)
        score += max(0, 3 - candidate.index)
        if candidate.chorus:
            score += 8
        if continuity:
            score += 7
        if question:
            score += 6
        if mood:
            score += 4
        if candidate.text.rstrip().endswith(("。", "！", "？", ".", "!", "?")):
            score += 2
        reason = (
            "selected.chorus"
            if candidate.chorus
            else "selected.continuity"
            if continuity
            else "selected.question"
            if question
            else "selected.mood"
            if mood
            else "selected.complete"
        )
        scored.append((score, -candidate.index, reason, candidate))

    if not scored:
        return AmbientHookSelection(
            reason=_empty_reason(rejected),
            candidate_count=0,
        )
    score, _newest_tiebreak, reason, winner = max(scored)
    return AmbientHookSelection(
        row=dict(winner.row),
        reason=reason,
        score=score,
        candidate_count=len(scored),
    )


def _empty_reason(rejected: Counter[str]) -> str:
    for reason in (
        "duplicate_or_flood",
        "already_replied",
        "low_value",
        "fragment",
    ):
        if rejected[reason]:
            return reason
    return "no_suitable"


def _is_low_value(dense: str) -> bool:
    if not dense or dense in _LOW_VALUE_DENSE:
        return True
    return len(set(dense)) == 1


def _looks_like_question(text: str, dense: str) -> bool:
    lowered = text.casefold()
    return (
        any(marker in lowered for marker in _QUESTION_MARKERS)
        or dense.endswith(("吗", "呢", "么"))
    )


def _topic_tokens(text: str) -> set[str]:
    lowered = text.casefold()
    tokens = {
        token
        for token in re.findall(r"[a-z0-9_]{3,}", lowered)
        if token not in _TOPIC_TOKEN_STOP
    }
    dense_cjk = "".join(char for char in lowered if "\u4e00" <= char <= "\u9fff")
    tokens.update(
        token
        for token in (
            dense_cjk[index : index + 2]
            for index in range(max(0, len(dense_cjk) - 1))
        )
        if token not in _TOPIC_TOKEN_STOP
    )
    return tokens


def _clean_text(value: Any) -> str:
    return " ".join(value.split())[:120] if isinstance(value, str) else ""


def _clean_uid(value: Any) -> str:
    return str(value or "").strip()[:128]


def _dense(value: Any) -> str:
    return "".join(
        char
        for char in str(value or "").casefold()
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )


__all__ = [
    "AMBIENT_HOOK_SCAN_LIMIT",
    "AmbientHookSelection",
    "select_ambient_hook",
]
