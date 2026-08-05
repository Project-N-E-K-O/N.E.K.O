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
_CONDITIONAL_CLAUSE_MARKERS = frozenset({
    "assuming", "if", "provided", "supposing", "unless", "whether",
})
_CONDITIONAL_CLAUSE_TOKEN_PHRASES = (
    ("as", "long", "as"),
    ("even", "if"),
    ("in", "case"),
    ("on", "condition", "that"),
    ("only", "if"),
)
_EPISTEMIC_MODALS = frozenset({"could", "may", "might"})
_EPISTEMIC_LEXICAL_MARKERS = frozenset({
    "maybe", "perhaps", "possible", "possibly", "probable", "probably",
})
_NON_UNIVERSAL_FREQUENCY_MARKERS = frozenset({
    "frequently", "generally", "occasionally", "often", "periodically",
    "rarely", "seldom", "sometimes", "usually",
})
_NON_UNIVERSAL_FREQUENCY_TOKEN_PHRASES = (
    ("at", "times"),
    ("from", "time", "to", "time"),
)
_CJK_NON_UNIVERSAL_FREQUENCY_MARKERS = (
    "偶尔", "偶爾", "经常", "經常", "时常", "時常", "通常",
)
_NON_UNIVERSAL_QUANTIFIER_MARKERS = frozenset({
    "few", "many", "several", "some",
})
_NON_UNIVERSAL_CARDINAL_MARKERS = frozenset({
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
})
_CJK_NON_UNIVERSAL_QUANTIFIER_MARKERS = (
    "有些", "几个", "幾個", "很多", "若干", "许多", "許多",
)
_REPORTING_VERBS = frozenset({
    "acknowledge", "acknowledged", "acknowledges", "admit", "admits", "admitted",
    "allege", "alleged", "alleges", "announce", "announced", "announces",
    "assert", "asserted", "asserts", "believe", "believed", "believes",
    "claim", "claimed", "claims", "confirm", "confirmed", "confirms",
    "declare", "declared", "declares", "discover", "discovered", "discovers",
    "explain", "explained", "explains", "find", "finds", "found", "hear",
    "heard", "hears", "indicate", "indicated", "indicates", "mention",
    "mentioned", "mentions", "note", "noted", "notes", "observe", "observed",
    "observes", "recall", "recalled", "recalls", "remember", "remembered",
    "remembers", "report", "reported", "reports", "say", "said", "says",
    "state", "stated", "states", "suggest", "suggested", "suggests", "tell",
    "tells", "think", "thinks", "thought", "told", "understand", "understands",
    "understood", "write", "writes", "wrote",
})
_EMBEDDED_CLAUSE_MARKERS = frozenset({
    "that", "when", "where", "which", "who", "whom", "whose",
})
_TEMPORAL_CLAUSE_MARKERS = frozenset({
    "after", "before", "until", "whenever", "while",
})
_CJK_CONDITIONAL_MARKERS = (
    "如果", "若", "假如", "假设", "倘若", "要是", "只要", "一旦",
    "即使", "除非",
)
_CJK_TEMPORAL_CLAUSE_MARKERS = (
    "之前", "之後", "之后", "以前", "以後", "以后", "期间", "期間",
)
_CJK_EPISTEMIC_MARKERS = ("也许", "或许", "大概", "可能")
_CJK_REPORTING_MARKERS = (
    "说", "表示", "声称", "认为", "觉得", "相信", "报告", "告诉",
    "宣布", "写道", "指出", "承认", "提到", "透露", "强调", "回忆",
    "发现", "证实", "确认",
)
_WORD_RE = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.IGNORECASE)


def finite_trust_score(value) -> float | None:
    """Return one finite numeric trust value, or ``None`` if malformed."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            score = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if math.isfinite(score):
            return score
    return None


def normalize_trust(value, default: float = SPEAKER_TRUST_DEFAULT) -> float:
    score = finite_trust_score(value)
    if score is not None:
        return max(0.0, min(1.0, score))
    fallback = finite_trust_score(default)
    if fallback is None:
        fallback = finite_trust_score(SPEAKER_TRUST_DEFAULT) or 0.0
    return max(0.0, min(1.0, fallback))


def trust_band(value) -> str:
    score = finite_trust_score(value)
    if score is None:
        return "unknown"
    score = normalize_trust(score)
    if score >= 0.75:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def preferred_by_trust(old, new) -> str | None:
    """Return ``old``/``new`` only when the deterministic margin is met."""
    old_raw = finite_trust_score(old)
    new_raw = finite_trust_score(new)
    if old_raw is None or new_raw is None:
        return None
    old_score = normalize_trust(old_raw)
    new_score = normalize_trust(new_raw)
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
    if marker == "not" and index + 1 < len(tokens) and tokens[index + 1] in {
        "only", "just", "merely", "necessarily",
    }:
        # Focus constructions do not assert the opposite proposition:
        # ``is not only smart`` is compatible with ``is only smart`` for this
        # deliberately conservative code-side trust signal. False negatives
        # are safer than penalising a speaker for rhetorical focus.
        return False
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


def _contains_token_phrase(
    tokens: tuple[str, ...], phrase: tuple[str, ...],
) -> bool:
    """Return whether one exact token phrase occurs in ``tokens``."""
    width = len(phrase)
    return any(
        tokens[index:index + width] == phrase
        for index in range(len(tokens) - width + 1)
    )


def _fragment_has_embedded_clause_negation(text: str) -> bool:
    """Reject negations scoped under an embedded marker in one sentence."""
    tokens = _word_tokens(text)
    negation_indices = [
        index for index in range(len(tokens))
        if _is_word_negation(tokens, index)
    ]
    if (
        negation_indices
        and (
            any(token in _CONDITIONAL_CLAUSE_MARKERS for token in tokens)
            or any(token in _TEMPORAL_CLAUSE_MARKERS for token in tokens)
            or any(
                _contains_token_phrase(tokens, phrase)
                for phrase in _CONDITIONAL_CLAUSE_TOKEN_PHRASES
            )
        )
    ):
        return True
    marker_indices = [
        index for index, token in enumerate(tokens)
        if token in _EMBEDDED_CLAUSE_MARKERS or token in _REPORTING_VERBS
    ]
    return bool(marker_indices) and any(
        index > marker_indices[0] for index in negation_indices
    )


def _has_embedded_clause_negation(text: str) -> bool:
    """Reject word negations scoped under a relative or embedded clause."""
    return any(
        _fragment_has_embedded_clause_negation(fragment)
        for fragment in re.split(r"[.!?;。！？；\r\n]+", text)
        if fragment.strip()
    )


def _has_epistemic_modal_negation(text: str) -> bool:
    """Reject uncertainty modals from deterministic polarity matching."""
    tokens = _word_tokens(text)
    if any(
        modal_index < index
        and all(
            token in _NEGATION_AUXILIARIES
            for token in tokens[modal_index + 1:index]
        )
        and _is_word_negation(tokens, index)
        for modal_index, modal in enumerate(tokens)
        if modal in _EPISTEMIC_MODALS
        for index in range(len(tokens))
    ):
        return True
    return any(
        re.search(rf"\b{modal}\b\s*{re.escape(negative)}", text, re.IGNORECASE)
        for modal in _EPISTEMIC_MODALS
        for negative, _ in _CJK_NEGATED_PREDICATES
    )


def _has_lexical_epistemic_negation(text: str) -> bool:
    """Reject lexical uncertainty markers that govern a later negation."""
    tokens = _word_tokens(text)
    marker_indices = [
        index for index, token in enumerate(tokens)
        if token in _EPISTEMIC_LEXICAL_MARKERS
    ]
    return bool(marker_indices) and any(
        index > marker_indices[0]
        and _is_word_negation(tokens, index)
        for index in range(len(tokens))
    )


def _has_non_universal_frequency(text: str) -> bool:
    """Reject time-varying claims from deterministic polarity matching."""
    tokens = _word_tokens(text)
    return (
        any(token in _NON_UNIVERSAL_FREQUENCY_MARKERS for token in tokens)
        or any(
            _contains_token_phrase(tokens, phrase)
            for phrase in _NON_UNIVERSAL_FREQUENCY_TOKEN_PHRASES
        )
        or any(
            marker in text
            for marker in _CJK_NON_UNIVERSAL_FREQUENCY_MARKERS
        )
    )


def _has_non_universal_quantifier(text: str) -> bool:
    """Reject quantified claims that need not cover the same individuals."""
    tokens = _word_tokens(text)
    return (
        # Limit indefinite articles to subject position.  An object or
        # complement such as ``Alice is a doctor`` still describes one
        # definite subject and can safely participate in polarity matching.
        (bool(tokens) and tokens[0] in {"a", "an"})
        or any(token in _NON_UNIVERSAL_QUANTIFIER_MARKERS for token in tokens)
        or any(
            token.isdigit() or token in _NON_UNIVERSAL_CARDINAL_MARKERS
            for token in tokens
        )
        or any(
            marker in text
            for marker in _CJK_NON_UNIVERSAL_QUANTIFIER_MARKERS
        )
    )


def _has_cjk_epistemic_negation(text: str) -> bool:
    """Reject uncertain CJK markers that precede a negated predicate."""
    for marker in _CJK_EPISTEMIC_MARKERS:
        marker_index = text.find(marker)
        if marker_index < 0:
            continue
        suffix = text[marker_index + len(marker):]
        if any(negative in suffix for negative, _ in _CJK_NEGATED_PREDICATES):
            return True
        tokens = _word_tokens(suffix)
        if any(_is_word_negation(tokens, index) for index in range(len(tokens))):
            return True
    return False


def _has_cjk_reported_negation(text: str) -> bool:
    """Reject CJK negations embedded under a reporting predicate."""
    for marker in _CJK_REPORTING_MARKERS:
        marker_index = text.find(marker)
        if marker_index < 0:
            continue
        suffix = text[marker_index + len(marker):]
        if any(negative in suffix for negative, _ in _CJK_NEGATED_PREDICATES):
            return True
        tokens = _word_tokens(suffix)
        if any(_is_word_negation(tokens, index) for index in range(len(tokens))):
            return True
    return False


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


def _has_cjk_conditional_negation(text: str) -> bool:
    """Reject predicate negations inside a CJK conditional clause."""
    return (
        any(marker in text for marker in _CJK_CONDITIONAL_MARKERS)
        and any(negative in text for negative, _ in _CJK_NEGATED_PREDICATES)
    )


def _has_cjk_temporal_clause_negation(text: str) -> bool:
    """Reject predicate negations inside a CJK temporal clause."""
    return (
        any(marker in text for marker in _CJK_TEMPORAL_CLAUSE_MARKERS)
        and any(negative in text for negative, _ in _CJK_NEGATED_PREDICATES)
    )


def _has_disjunction(text: str) -> bool:
    """Reject polarity matches that alter only one disjunct."""
    return (
        "or" in _word_tokens(text)
        or any(marker in text for marker in ("或者", "还是", "或"))
    )


def deterministic_relation(old_text: str, new_text: str) -> str | None:
    """Return confirmation/correction only for conservative code-side matches."""
    old_norm = " ".join(str(old_text or "").split()).casefold()
    new_norm = " ".join(str(new_text or "").split()).casefold()
    if not old_norm or not new_norm:
        return None
    if any(marker in text for marker in ("?", "？") for text in (old_norm, new_norm)):
        # A question does not assert either polarity, so it cannot safely
        # confirm a fact or penalise its speaker as a correction.
        return None
    if old_norm == new_norm:
        return "confirmation"
    if _has_disjunction(old_norm) or _has_disjunction(new_norm):
        return None
    if (
        _has_non_universal_quantifier(old_norm)
        or _has_non_universal_quantifier(new_norm)
    ):
        # Non-universal claims can refer to different members of a set, so
        # ``many P`` and ``many not P`` are not deterministic opposites.
        return None
    if (
        _has_non_universal_frequency(old_norm)
        or _has_non_universal_frequency(new_norm)
    ):
        # ``sometimes P`` and ``sometimes not P`` can both be true at
        # different times.  Frequency-qualified observations therefore do
        # not provide a safe deterministic trust signal.
        return None
    if (
        (
            old_norm in _cjk_positive_variants(new_norm)
            or new_norm in _cjk_positive_variants(old_norm)
        )
        and not _has_cjk_conditional_negation(old_norm)
        and not _has_cjk_conditional_negation(new_norm)
        and not _has_cjk_temporal_clause_negation(old_norm)
        and not _has_cjk_temporal_clause_negation(new_norm)
        and not _has_cjk_epistemic_negation(old_norm)
        and not _has_cjk_epistemic_negation(new_norm)
        and not _has_cjk_reported_negation(old_norm)
        and not _has_cjk_reported_negation(new_norm)
        and not _has_epistemic_modal_negation(old_norm)
        and not _has_epistemic_modal_negation(new_norm)
        and not _has_lexical_epistemic_negation(old_norm)
        and not _has_lexical_epistemic_negation(new_norm)
    ):
        return "correction"
    old_tokens = _proposition_tokens(old_norm)
    new_tokens = _proposition_tokens(new_norm)
    if (
        old_tokens
        and old_tokens == new_tokens
        and _polarity(old_norm) != _polarity(new_norm)
        and not _has_embedded_clause_negation(old_norm)
        and not _has_embedded_clause_negation(new_norm)
        and not _has_epistemic_modal_negation(old_norm)
        and not _has_epistemic_modal_negation(new_norm)
        and not _has_lexical_epistemic_negation(old_norm)
        and not _has_lexical_epistemic_negation(new_norm)
        and not _has_cjk_epistemic_negation(old_norm)
        and not _has_cjk_epistemic_negation(new_norm)
    ):
        return "correction"
    return None


def trust_event_id(kind: str, source_fact_id: str, target_speaker_id: str) -> str:
    raw = f"{kind}|{source_fact_id}|{target_speaker_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def trust_observation_id(text: str) -> str:
    """Return a content-stable, non-plaintext identity for one owner message."""
    normalized = " ".join(str(text or "").split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


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


def same_provenance_source(a: dict, b: dict) -> bool | None:
    """Three-state: are these two rows the same PERSON?

    ``True`` / ``False`` / ``None``, where ``None`` means "unknown" and is a
    first-class answer, not a soft ``False``.

    | condition                                                    | result |
    |--------------------------------------------------------------|--------|
    | ``speaker_id`` byte-equal                                     | True   |
    | both carry ``speaker_entity_id`` and they are equal           | True   |
    | pool loaded, both ids valid, resolve to the same entity       | True   |
    | pool loaded, both ids valid, do not resolve to the same entity| False  |
    | pool NOT loaded, or either id missing/malformed               | None   |

    DEVIATION FROM THE DESIGN DOC, ON PURPOSE. The spec's table also returned
    ``None`` when either account is merely *unregistered*. That is too broad and
    silently retires an existing safety property: most accounts are unregistered
    (a row only enters the pool once it has activity or a signal), so ``None``
    would become the common answer and ``speaker_provenance_mixed`` would stop
    being written for two genuinely different people — the exact regression
    I-P-6 forbids.

    An unregistered account is provably a SINGLETON entity: binding registers
    both sides, so an account absent from ``account_index`` shares an entity
    with nobody. Two different account strings, at least one of them
    unregistered, therefore cannot be one person, and ``False`` is both correct
    and regression-preserving. The doc's own justification for ``None`` is
    exclusively the pool-unavailable window, which this keeps.

    The mis-bind → unbind case still resolves to ``True`` without any pool
    lookup: a row written while B was bound carries A's entity in its persisted
    ``speaker_entity_id``, and the equality check above fires first. So there is
    still no "mixed pump" after an unbind.

    WHY ``None`` MUST EXIST. Canonical write routing makes two accounts of one
    person share a subject for the first time, and the fact hash is salted with
    the subject, so the same sentence now collides. The pre-existing ``else``
    branch of ``_reconcile_existing_provenance`` stamps such a collision
    ``speaker_provenance_mixed``, which is an absorbing state with no clearing
    path anywhere in the repo: the row stops producing trust signals, is
    excluded from refine and from dedup arbitration, and loses its speaker_id /
    label / trust. "I do not know" must never be recorded as "I know these are
    mixed" — that is the one unconditional rule here.

    Callers must treat ``None`` as ABSTAIN (leave the existing provenance
    exactly as it is), never as "different".
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    left_id = stable_speaker_id(a.get("speaker_id"))
    right_id = stable_speaker_id(b.get("speaker_id"))
    if left_id is not None and left_id == right_id:
        return True
    left_entity = str(a.get("speaker_entity_id") or "").strip()
    right_entity = str(b.get("speaker_entity_id") or "").strip()
    if left_entity and left_entity == right_entity:
        return True
    if left_id is None or right_id is None:
        return None
    try:
        from memory.trust_store import trust_snapshot
    except ImportError:  # pragma: no cover - defensive
        return None
    snap = trust_snapshot()
    if not snap.loaded:
        # The one genuinely unknown state: this process could not read the
        # pool, yet still accepts scoped writes. Abstain.
        return None
    left_resolved = snap.entity_of(left_id)
    right_resolved = snap.entity_of(right_id)
    if left_resolved is None or right_resolved is None:
        # At least one side is a singleton entity, so with different account
        # strings these are two different people.
        return False
    return left_resolved == right_resolved


_PROVENANCE_KEYS = (
    "speaker_id", "speaker_label", "speaker_trust", "speaker_entity_id",
    "speaker_provenance_mixed",
)


def _provenance_fields(entry: dict) -> dict:
    return {key: entry[key] for key in _PROVENANCE_KEYS if key in entry}


def provenance_of_entries(entries: Iterable[dict]) -> dict:
    """Conservatively fold same-source provenance into a derived entry.

    ROW ORDER IS SIGNIFICANT and deliberately so: ``entries[0]`` is the
    survivor / representative. When the rows turn out to be the SAME PERSON
    under different accounts, this returns row 0's provenance VERBATIM rather
    than folding.

    Folding across accounts of one person would be a one-way ratchet on a
    durable field: ``min(trusts)`` would rewrite an owner row already stamped
    1.0 down to whatever their low-tier channel scored (e.g. 0.32) the moment
    they say the same thing there. ``min`` only ever descends, so the row's
    band drops from 'high' to 'low' permanently — and because it is not marked
    mixed, ``scoped_refine`` no longer filters it out, so it enters
    deterministic arbitration carrying 0.32 and loses to any ordinary member at
    0.5. That is abstention turning into "participates and loses".
    """
    rows = list(entries)
    if not rows or any(not isinstance(entry, dict) for entry in rows):
        return {}
    if any(entry.get("speaker_provenance_mixed") is True for entry in rows):
        return {"speaker_provenance_mixed": True}
    speaker_ids = [stable_speaker_id(entry.get("speaker_id")) for entry in rows]
    known_speaker_ids = [
        speaker_id for speaker_id in speaker_ids if speaker_id is not None
    ]
    if not known_speaker_ids:
        return {}
    if len(known_speaker_ids) != len(rows):
        return {"speaker_provenance_mixed": True}
    speaker_ids = set(known_speaker_ids)
    if len(speaker_ids) != 1:
        # Different account strings. Only "different PERSON" justifies mixed;
        # "unknown" must not be recorded as known-mixed, so an unresolved
        # comparison keeps the representative row untouched as well.
        verdicts = [
            same_provenance_source(rows[0], other) for other in rows[1:]
        ]
        if any(verdict is False for verdict in verdicts):
            return {"speaker_provenance_mixed": True}
        return _provenance_fields(rows[0])
    speaker_id = next(iter(speaker_ids))
    trust_scores = [
        finite_trust_score(entry.get("speaker_trust")) for entry in rows
    ]
    trusts = [normalize_trust(score) for score in trust_scores if score is not None]
    result = {"speaker_id": speaker_id}
    if len(trusts) == len(rows):
        result["speaker_trust"] = min(trusts)
    labels = {
        str(entry.get("speaker_label") or "").strip() for entry in rows
        if str(entry.get("speaker_label") or "").strip()
    }
    if len(labels) == 1:
        result["speaker_label"] = next(iter(labels))[:64]
    entity_ids = {
        str(entry.get("speaker_entity_id") or "").strip() for entry in rows
        if str(entry.get("speaker_entity_id") or "").strip()
    }
    if len(entity_ids) == 1:
        result["speaker_entity_id"] = next(iter(entity_ids))
    return result
