"""Deterministic speaker-trust helpers.

Trust values never come from model output.  The model may arbitrate semantic
content, but exact scores, preference thresholds, and evolution events are
computed from request provenance and code-side predicates only.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from config import SPEAKER_TRUST_ARBITRATION_MARGIN, SPEAKER_TRUST_DEFAULT
from utils.llm_client import HumanMessage


_CJK_NEGATION_MARKERS = (
    "不", "没", "无", "未", "否", "讨厌", "拒绝", "错误", "假的",
)
_WORD_NEGATION_MARKERS = frozenset({
    "not", "never", "no", "hate", "dislike", "false", "wrong",
})
_WORD_RE = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.IGNORECASE)


def normalize_trust(value, default: float = SPEAKER_TRUST_DEFAULT) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return max(0.0, min(1.0, float(default)))


def trust_band(value) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "unknown"
    score = normalize_trust(value)
    if score >= 0.75:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def preferred_by_trust(old, new) -> str | None:
    """Return ``old``/``new`` only when the deterministic margin is met."""
    old_score = normalize_trust(old)
    new_score = normalize_trust(new)
    if old_score - new_score >= SPEAKER_TRUST_ARBITRATION_MARGIN:
        return "old"
    if new_score - old_score >= SPEAKER_TRUST_ARBITRATION_MARGIN:
        return "new"
    return None


def stable_speaker_id(value) -> str | None:
    """Accept the internal ``platform:id`` shape without making it prompt-facing."""
    text = str(value or "").strip()
    if not text or len(text) > 96 or any(ch.isspace() or not ch.isprintable() for ch in text):
        return None
    platform, sep, actor = text.partition(":")
    if not sep or not platform or not actor:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", platform):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.:@-]+", actor):
        return None
    return f"{platform.lower()}:{actor}"


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD_RE.finditer(str(text or ""))}


def _polarity(text: str) -> bool:
    lowered = str(text or "").lower()
    return (
        any(marker in lowered for marker in _CJK_NEGATION_MARKERS)
        or bool(_tokens(lowered) & _WORD_NEGATION_MARKERS)
    )


def _proposition_tokens(text: str) -> tuple[str, ...]:
    """Return content tokens after removing explicit polarity markers."""
    cleaned = str(text or "").lower()
    for marker in sorted(_CJK_NEGATION_MARKERS, key=len, reverse=True):
        cleaned = cleaned.replace(marker, " ")
    return tuple(
        match.group(0).lower()
        for match in _WORD_RE.finditer(cleaned)
        if match.group(0).lower() not in _WORD_NEGATION_MARKERS
    )


def deterministic_relation(old_text: str, new_text: str) -> str | None:
    """Return confirmation/correction only for conservative code-side matches."""
    old_norm = " ".join(str(old_text or "").split()).casefold()
    new_norm = " ".join(str(new_text or "").split()).casefold()
    if not old_norm or not new_norm:
        return None
    if old_norm == new_norm:
        return "confirmation"
    old_tokens = _proposition_tokens(old_norm)
    new_tokens = _proposition_tokens(new_norm)
    if (
        old_tokens
        and old_tokens == new_tokens
        and _polarity(old_norm) != _polarity(new_norm)
    ):
        return "correction"
    return None


def trust_event_id(kind: str, source_fact_id: str, target_speaker_id: str) -> str:
    raw = f"{kind}|{source_fact_id}|{target_speaker_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def observation_texts(messages: Iterable[Any]) -> list[str]:
    """Extract user-authored text without trusting any model-produced fields."""
    texts: list[str] = []
    for message in messages or []:
        if isinstance(message, dict):
            if message.get("role") != "user":
                continue
            content = message.get("content")
        elif isinstance(message, HumanMessage):
            content = message.content
        else:
            continue
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text = " ".join(
                str(part.get("text") or "").strip()
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
        else:
            text = ""
        if text:
            texts.append(text)
    return texts


def provenance_of_entries(entries: Iterable[dict]) -> dict:
    """Conservatively fold same-source provenance into a derived entry."""
    rows = list(entries)
    if not rows or any(not isinstance(entry, dict) for entry in rows):
        return {}
    speaker_ids = [stable_speaker_id(entry.get("speaker_id")) for entry in rows]
    if any(speaker_id is None for speaker_id in speaker_ids):
        return {}
    speaker_ids = set(speaker_ids)
    if len(speaker_ids) != 1:
        return {}
    speaker_id = next(iter(speaker_ids))
    trusts = [
        normalize_trust(entry.get("speaker_trust")) for entry in rows
        if isinstance(entry.get("speaker_trust"), (int, float))
        and not isinstance(entry.get("speaker_trust"), bool)
    ]
    result = {"speaker_id": speaker_id}
    if len(trusts) == len(rows):
        result["speaker_trust"] = min(trusts)
    labels = {
        str(entry.get("speaker_label") or "").strip() for entry in rows
        if str(entry.get("speaker_label") or "").strip()
    }
    if len(labels) == 1:
        result["speaker_label"] = next(iter(labels))[:64]
    return result
