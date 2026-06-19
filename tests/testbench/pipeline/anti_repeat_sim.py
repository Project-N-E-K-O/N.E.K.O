"""anti_repeat_sim.py — thin testbench adapter over the anti-repeat BM25 scorer.

Scope (UPSTREAM_SYNC_2026-06 Phase 3, RAG item #5 = O)
-----------------------------------------------------
Re-exposes the **semantic-contract** half of ``memory.anti_repeat`` — the pure
"repetitiveness BM25" scorer that decides whether a fresh AI draft re-treads a
recently-discussed topic — without the **runtime-mechanism** half (the
``AntiRepeatCorpus`` class: per-character JSON corpus on disk, threading locks,
rolling-window persistence). The disk/lock layer is out-of-scope per Phase 3.0.

The repetition variant of BM25 is the contract worth guarding: unlike search
BM25, it computes IDF over the **background** window but accumulates TF over the
small **foreground** window, so "rare overall + frequent lately" scores high
while filler words (generic greetings and interjections) wash out.

Wraps:
  - ``memory.anti_repeat.bm25_score``  (pure: list[ngram] in, score out)
  - ``memory.anti_repeat._ngrams``     (production tokenizer, for the end-to-end
    text harness; reuses persona ``_extract_keywords`` + stop names)

Lazy import for the same reason as the sibling adapters; the coupling is wanted.
"""
from __future__ import annotations


def _ar():
    from memory import anti_repeat as _mod  # noqa: WPS433 — intentional lazy import
    return _mod


def bm25_score(
    draft_ngrams: list[str],
    fg_docs: list[list[str]],
    bg_docs: list[list[str]] | None = None,
    *,
    k1: float | None = None,
    b: float | None = None,
) -> tuple[float, dict[str, float]]:
    """Repetitiveness BM25 — production's ``bm25_score``.

    ``k1`` / ``b`` default to ``ANTI_REPEAT_BM25_K1`` / ``ANTI_REPEAT_BM25_B``
    (the repetition-tuned constants, deliberately distinct from retrieval's
    1.5/0.75). Returns ``(total, per_term_sorted_desc)``.
    """
    from config import ANTI_REPEAT_BM25_B, ANTI_REPEAT_BM25_K1
    return _ar().bm25_score(
        draft_ngrams,
        fg_docs,
        bg_docs,
        k1=ANTI_REPEAT_BM25_K1 if k1 is None else k1,
        b=ANTI_REPEAT_BM25_B if b is None else b,
    )


def ngrams(text: str) -> list[str]:
    """Production tokenizer (persona ngrams + stop-name strip) — ``_ngrams``."""
    return _ar()._ngrams(text)  # noqa: SLF001


def score_texts(
    draft_text: str,
    recent_texts: list[str],
) -> tuple[float, dict[str, float]]:
    """End-to-end repetition score from raw text: tokenize ``draft_text`` and
    each of ``recent_texts`` with the production ``_ngrams``, then run
    ``bm25_score`` with the recent window as both foreground (TF) and background
    (DF). "Given these recent AI outputs, how repetitive is this new draft."
    """
    mod = _ar()
    draft = mod._ngrams(draft_text)  # noqa: SLF001
    docs = [d for d in (mod._ngrams(t) for t in recent_texts) if d]  # noqa: SLF001
    if not draft or not docs:
        return 0.0, {}
    return bm25_score(draft, docs, docs)


__all__ = [
    "bm25_score",
    "ngrams",
    "score_texts",
]
