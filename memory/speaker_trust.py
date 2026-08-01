"""Deterministic speaker-trust helpers.

Trust values never come from model output.  The model may arbitrate semantic
content, but exact scores, preference thresholds, and evolution events are
computed from request provenance and code-side predicates only.
"""

from __future__ import annotations

import hashlib
import math
import re
from decimal import Decimal
from typing import Any, Iterable

from config import SPEAKER_TRUST_ARBITRATION_MARGIN, SPEAKER_TRUST_DEFAULT
from utils.llm_client import HumanMessage


# Only high-confidence predicate forms participate in code-side CJK
# corrections.  Single characters such as ``无`` are lexical inside names
# (for example, 无锡), so removing them as arbitrary substrings can fabricate
# a contradiction and unfairly lower a speaker's trust.
_CJK_NEGATED_PREDICATES = (
    ("不喜欢", "喜欢"),
    ("不爱", "爱"),
    ("不是", "是"),
    ("没有", "有"),
    ("没在", "在"),
    ("不会", "会"),
    ("不能", "能"),
    ("不想", "想"),
    ("不需要", "需要"),
    ("不支持", "支持"),
    ("不同意", "同意"),
    ("不接受", "接受"),
    ("不相信", "相信"),
    ("不认识", "认识"),
    ("不住", "住"),
)
_WORD_NEGATION_MARKERS = frozenset({
    "not", "never", "no",
})
_NEGATION_AUXILIARIES = frozenset({
    "am", "are", "can", "could", "did", "do", "does", "had", "has",
    "have", "is", "may", "might", "must", "shall", "should", "was",
    "were", "will", "would",
})
_WORD_RE = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.IGNORECASE)


def normalize_trust(value, default: float = SPEAKER_TRUST_DEFAULT) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        score = float(value)
        if math.isfinite(score):
            return max(0.0, min(1.0, score))
    fallback = float(default)
    if not math.isfinite(fallback):
        fallback = float(SPEAKER_TRUST_DEFAULT)
    return max(0.0, min(1.0, fallback))


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
    margin = Decimal(str(SPEAKER_TRUST_ARBITRATION_MARGIN))
    difference = Decimal(str(old_score)) - Decimal(str(new_score))
    if difference >= margin:
        return "old"
    if -difference >= margin:
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


def _word_tokens(text: str) -> tuple[str, ...]:
    return tuple(
        match.group(0).lower()
        for match in _WORD_RE.finditer(str(text or "").lower())
    )


def _is_word_negation(tokens: tuple[str, ...], index: int) -> bool:
    marker = tokens[index]
    if marker not in _WORD_NEGATION_MARKERS:
        return False
    if marker == "no":
        # ``No 5 Main Street`` is numbering metadata, and ``the No button``
        # is a label.  Only an auxiliary-led predicate form such as
        # ``has no cats`` is safe enough for a code-side trust penalty.
        return (
            index > 0
            and tokens[index - 1] in _NEGATION_AUXILIARIES
            and index + 1 < len(tokens)
            and not tokens[index + 1].isdigit()
        )
    # Bare lexical occurrences are unsafe: ``the never button`` and
    # ``the not operator`` are modifiers, while hate/dislike are independent
    # predicates rather than removable polarity markers.  Restrict the two
    # remaining adverbs to explicit auxiliary-predicate forms.  False
    # negatives are safer than fabricating a trust penalty.
    return index > 0 and tokens[index - 1] in _NEGATION_AUXILIARIES


def _polarity(text: str) -> bool:
    lowered = str(text or "").lower()
    tokens = _word_tokens(lowered)
    return (
        any(negative in lowered for negative, _ in _CJK_NEGATED_PREDICATES)
        or any(_is_word_negation(tokens, index) for index in range(len(tokens)))
    )


def _proposition_tokens(text: str) -> tuple[str, ...]:
    """Return content tokens after removing explicit polarity markers."""
    tokens = _word_tokens(text)
    return tuple(
        token for index, token in enumerate(tokens)
        if not _is_word_negation(tokens, index)
    )


def _cjk_positive_variants(text: str) -> set[str]:
    """Return exact positive forms for conservative predicate negations."""
    variants: set[str] = set()
    predicate_forms = tuple({
        form for pair in _CJK_NEGATED_PREDICATES for form in pair
    })
    for negative, positive in _CJK_NEGATED_PREDICATES:
        start = 0
        while (index := text.find(negative, start)) >= 0:
            prefix = text[:index]
            suffix = text[index + len(negative):]
            # ``不喜欢猫的女孩来自北京`` negates a relative-clause modifier,
            # not the asserted ``来自北京`` predicate. A finite noun/predicate
            # vocabulary cannot reliably classify what follows ``的``;
            # reject the relative-clause shape directly instead.
            # False negatives are safer than fabricating a trust penalty.
            if "的" in suffix:
                start = index + len(negative)
                continue
            # Another predicate on either side makes this occurrence a
            # nested/modifying proposition rather than a safely identifiable
            # asserted predicate.  This also catches leading modifiers such
            # as ``不喜欢猫的人认识小明``. False negatives are safer than a
            # fabricated trust penalty.
            if not any(
                form in prefix or form in suffix for form in predicate_forms
            ):
                variants.add(
                    text[:index] + positive + text[index + len(negative):]
                )
            start = index + len(negative)
    return variants


def deterministic_relation(old_text: str, new_text: str) -> str | None:
    """Return confirmation/correction only for conservative code-side matches."""
    old_norm = " ".join(str(old_text or "").split()).casefold()
    new_norm = " ".join(str(new_text or "").split()).casefold()
    if not old_norm or not new_norm:
        return None
    if old_norm == new_norm:
        return "confirmation"
    if (
        old_norm in _cjk_positive_variants(new_norm)
        or new_norm in _cjk_positive_variants(old_norm)
    ):
        return "correction"
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
