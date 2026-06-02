from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re
import time
from typing import Any, Iterable


_VALID_SCREEN_TYPES = frozenset({"idle", "reading", "question", "answering", "review", "notes", "summary"})
_SCREEN_TYPE_ALIASES = {
    "blank": "idle",
    "empty": "idle",
    "lecture": "reading",
    "lesson": "reading",
    "answer": "answering",
    "evaluation": "review",
    "quiz": "question",
    "exercise": "question",
    "study_question": "question",
    "study_answer": "answering",
    "study_review": "review",
    "study_notes": "notes",
}

_QUESTION_KEYWORDS = (
    "question",
    "quiz",
    "exercise",
    "problem",
    "choose",
    "select",
    "why",
    "how",
    "what",
    "which",
    "题",
    "问题",
    "选择",
    "练习",
    "填空",
    "思考",
)
_ANSWER_KEYWORDS = (
    "answer",
    "submit",
    "score",
    "correct",
    "incorrect",
    "result",
    "feedback",
    "答案",
    "解析",
    "判题",
    "作答",
    "评价",
    "订正",
)
_REVIEW_KEYWORDS = (
    "review",
    "reviewing",
    "retry",
    "mistake",
    "wrong",
    "note",
    "review note",
    "复习",
    "回顾",
    "错题",
    "反思",
)
_SUMMARY_KEYWORDS = (
    "summary",
    "summarize",
    "recap",
    "session summary",
    "总结",
    "小结",
    "归纳",
)
_NOTES_KEYWORDS = (
    "note",
    "notes",
    "memo",
    "outline",
    "笔记",
    "大纲",
    "整理",
    "摘录",
)
_READING_KEYWORDS = (
    "chapter",
    "section",
    "lesson",
    "text",
    "article",
    "reading",
    "解释",
    "定义",
    "概念",
    "说明",
    "知识点",
)


@dataclass(slots=True)
class ScreenClassification:
    screen_type: str = "idle"
    confidence: float = 0.0
    reason: str = ""
    signals: dict[str, Any] = field(default_factory=dict)
    text_excerpt: str = ""
    window_title: str = ""
    at: float = 0.0

    def to_payload(self) -> dict[str, Any]:
        return {
            "screen_type": normalize_screen_type(self.screen_type),
            "confidence": max(0.0, min(1.0, float(self.confidence or 0.0))),
            "reason": str(self.reason or ""),
            "signals": dict(self.signals),
            "text_excerpt": str(self.text_excerpt or ""),
            "window_title": str(self.window_title or ""),
            "at": float(self.at or 0.0),
        }


def normalize_screen_type(screen_type: str | None) -> str:
    candidate = str(screen_type or "").strip().lower()
    if candidate in _VALID_SCREEN_TYPES:
        return candidate
    return _SCREEN_TYPE_ALIASES.get(candidate, "idle")


def _clean_line(value: str) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _split_text(text: str) -> list[str]:
    lines = [_clean_line(line) for line in str(text or "").splitlines()]
    return [line for line in lines if line]


def _excerpt(lines: list[str]) -> str:
    if not lines:
        return ""
    combined = " ".join(lines[:2]).strip()
    if len(combined) <= 140:
        return combined
    return f"{combined[:140]}..."


def _keyword_hits(text: str, keywords: Iterable[str]) -> list[str]:
    hits = []
    lowered = text.lower()
    for keyword in keywords:
        candidate = str(keyword or "").strip()
        if not candidate:
            continue
        if candidate.lower() in lowered:
            hits.append(candidate)
    return hits


def _score_category(lines: list[str], title: str, keywords: tuple[str, ...], *, extra: float = 0.0) -> tuple[float, list[str]]:
    text = " ".join(lines)
    hits = _keyword_hits(text, keywords)
    title_hits = _keyword_hits(title, keywords)
    punctuation_hits = text.count("?") + text.count("？")
    score = float(len(hits)) + 0.5 * float(len(title_hits)) + extra
    if punctuation_hits and keywords is _QUESTION_KEYWORDS:
        score += min(2.0, punctuation_hits * 0.6)
    if lines and len(lines) <= 3 and keywords is _ANSWER_KEYWORDS:
        score += 0.3
    return score, hits + title_hits


def _coerce_history_item(item: object) -> ScreenClassification | None:
    if isinstance(item, ScreenClassification):
        return item
    if not isinstance(item, dict):
        return None
    screen_type = normalize_screen_type(item.get("screen_type"))
    try:
        confidence = float(item.get("confidence") or item.get("screen_confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return ScreenClassification(
        screen_type=screen_type,
        confidence=confidence,
        reason=str(item.get("reason") or ""),
        signals=dict(item.get("signals") or item.get("debug") or {}),
        text_excerpt=str(item.get("text_excerpt") or ""),
        window_title=str(item.get("window_title") or ""),
        at=float(item.get("at") or item.get("captured_at") or 0.0),
    )


def _smooth_classification(
    current: ScreenClassification,
    recent_classifications: Iterable[object] | None,
) -> ScreenClassification:
    recent = [_coerce_history_item(item) for item in list(recent_classifications or [])]
    recent = [item for item in recent if item is not None]
    if not recent:
        return current

    last = recent[-1]
    if last.screen_type == current.screen_type:
        current.confidence = max(current.confidence, min(0.98, last.confidence + 0.03))
        return current

    window = recent[-5:]
    counts = Counter(item.screen_type for item in window if item.screen_type != "idle")
    if not counts:
        return current
    majority, majority_count = counts.most_common(1)[0]
    if majority == current.screen_type:
        return current
    majority_items = [item for item in window if item.screen_type == majority]
    if majority_count >= 3 and current.confidence < 0.6:
        avg_confidence = sum(item.confidence for item in majority_items) / max(1, len(majority_items))
        return ScreenClassification(
            screen_type=majority,
            confidence=max(current.confidence, min(0.95, avg_confidence + 0.05)),
            reason=f"{current.reason}; smoothed from recent {majority}".strip("; "),
            signals={**current.signals, "smoothed_from": majority, "recent_majority": majority_count},
            text_excerpt=current.text_excerpt,
            window_title=current.window_title,
            at=current.at,
        )

    if last.screen_type == majority and current.confidence < 0.45:
        return ScreenClassification(
            screen_type=majority,
            confidence=max(current.confidence, min(0.9, last.confidence + 0.02)),
            reason=f"{current.reason}; held by recent majority {majority}".strip("; "),
            signals={**current.signals, "held_from": majority},
            text_excerpt=current.text_excerpt,
            window_title=current.window_title,
            at=current.at,
        )
    return current


def classify_screen_from_ocr(
    ocr_text: str,
    *,
    window_title: str = "",
    recent_classifications: Iterable[object] | None = None,
) -> ScreenClassification:
    lines = _split_text(ocr_text)
    title = _clean_line(window_title)
    now = time.time()

    if not lines and not title:
        return ScreenClassification(
            screen_type="idle",
            confidence=0.96,
            reason="empty_text",
            signals={"line_count": 0, "title_hits": []},
            text_excerpt="",
            window_title=title,
            at=now,
        )

    text = " ".join(lines)
    question_score, question_hits = _score_category(lines, title, _QUESTION_KEYWORDS, extra=0.2 if "?" in text or "？" in text else 0.0)
    answer_score, answer_hits = _score_category(lines, title, _ANSWER_KEYWORDS, extra=0.1)
    review_score, review_hits = _score_category(lines, title, _REVIEW_KEYWORDS, extra=0.15)
    summary_score, summary_hits = _score_category(lines, title, _SUMMARY_KEYWORDS, extra=0.35)
    notes_score, notes_hits = _score_category(lines, title, _NOTES_KEYWORDS, extra=0.1)
    reading_score, reading_hits = _score_category(lines, title, _READING_KEYWORDS, extra=0.05 + min(1.0, len(text) / 180.0))

    title_lower = title.lower()
    if any(token in title_lower for token in ("quiz", "exercise", "question", "problem", "练习", "题")):
        question_score += 0.7
    if any(token in title_lower for token in ("answer", "review", "score", "答案", "解析", "错题")):
        answer_score += 0.6
        review_score += 0.4
    if any(token in title_lower for token in ("summary", "summarize", "recap", "总结", "小结")):
        summary_score += 0.7
    if any(token in title_lower for token in ("note", "memo", "笔记")):
        notes_score += 0.6
    if any(token in title_lower for token in ("lesson", "chapter", "reading", "article", "lecture", "概念", "定义")):
        reading_score += 0.4

    scores = {
        "question": question_score,
        "answering": answer_score,
        "review": review_score,
        "summary": summary_score,
        "notes": notes_score,
        "reading": reading_score,
    }
    screen_type = max(scores, key=scores.get)
    score = float(scores[screen_type])
    if not lines and screen_type == "reading":
        screen_type = "idle"
        score = 0.2
    if score < 0.35 and len(text) < 16:
        screen_type = "idle"
        score = max(score, 0.4 if title else 0.2)

    confidence = min(0.98, 0.22 + score * 0.18)
    if screen_type == "question" and question_hits:
        confidence = min(0.98, confidence + 0.12)
    if screen_type == "answering" and answer_hits:
        confidence = min(0.98, confidence + 0.1)
    if screen_type == "review" and review_hits:
        confidence = min(0.98, confidence + 0.08)
    if screen_type == "summary" and summary_hits:
        confidence = min(0.98, confidence + 0.1)
    if screen_type == "notes" and notes_hits:
        confidence = min(0.98, confidence + 0.08)
    if screen_type == "reading" and reading_hits:
        confidence = min(0.96, confidence + 0.06)
    if screen_type == "idle":
        confidence = max(confidence, 0.45 if not title else 0.55)

    reasons = {
        "question": question_hits,
        "answering": answer_hits,
        "review": review_hits,
        "summary": summary_hits,
        "notes": notes_hits,
        "reading": reading_hits,
    }
    current = ScreenClassification(
        screen_type=screen_type,
        confidence=confidence,
        reason="; ".join(
            part
            for part in (
                f"question:{','.join(question_hits[:3])}" if question_hits else "",
                f"answer:{','.join(answer_hits[:3])}" if answer_hits else "",
                f"review:{','.join(review_hits[:3])}" if review_hits else "",
                f"summary:{','.join(summary_hits[:3])}" if summary_hits else "",
                f"notes:{','.join(notes_hits[:3])}" if notes_hits else "",
                f"reading:{','.join(reading_hits[:3])}" if reading_hits else "",
            )
            if part
        )
        or "keyword_scoring",
        signals={
            "line_count": len(lines),
            "question_score": question_score,
            "answer_score": answer_score,
            "review_score": review_score,
            "summary_score": summary_score,
            "notes_score": notes_score,
            "reading_score": reading_score,
            "question_hits": question_hits,
            "answer_hits": answer_hits,
            "review_hits": review_hits,
            "summary_hits": summary_hits,
            "notes_hits": notes_hits,
            "reading_hits": reading_hits,
        },
        text_excerpt=_excerpt(lines),
        window_title=title,
        at=now,
    )
    smoothed = _smooth_classification(current, recent_classifications)
    return smoothed


__all__ = [
    "ScreenClassification",
    "classify_screen_from_ocr",
    "normalize_screen_type",
]
