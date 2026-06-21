"""recall_fusion.py — thin testbench adapter over the hybrid-recall fusion layer.

Scope (UPSTREAM_SYNC_2026-06 Phase 3, RAG items #2 = M, #3 = O)
--------------------------------------------------------------
Re-exposes the **semantic-contract** half of the ``recall_memory`` backend —
the deterministic, embedding-free retrieval primitives — so the testbench can
exercise "given this pool + query (+ time window), what comes back and in what
order" *without* the **runtime-mechanism** half (ONNX ``EmbeddingService`` /
cosine path / sqlite ``timeindex`` / the ``recall_memory`` tool's HTTP+TTS+
WebSocket plumbing), all of which are explicitly out-of-scope per Phase 3.0.

Concretely, this wraps production pure helpers:
  - ``memory.hybrid_recall._tokenize``      (BM25 tokenizer, CJK n-gram + Latin)
  - ``memory.hybrid_recall._bm25_rank``     (Okapi BM25)
  - ``memory.hybrid_recall._rrf_fuse``      (Reciprocal Rank Fusion)
  - ``memory.hybrid_recall._tag_tier``      (tier stamping)
  - ``memory.hybrid_recall._overlaps_window`` (event-time window membership)
  - ``memory.recall.MemoryRecallReranker._hard_filter`` (#3)
  - ``memory.temporal.parse_time_window``   (time spec -> [start, end))

The **cosine path is deliberately the only thing not reproduced** — it needs
the embedding model (delivery/infra). ``recall_bm25_fuse`` therefore mirrors
``hybrid_recall.hybrid_recall``'s pipeline order with the cosine ranking fixed
to empty: ``hard_filter -> [time filter] -> BM25 -> threshold+cap -> RRF``.
This is the "dyno bench measures torque" path: retrieval correctness minus the
embedding delivery mechanism.

Why import directly (no byte-copy): same reasoning as ``evidence_sim.py`` —
these are pure functions; importing ``memory.*`` is already done at runtime by
``memory_runner``. The coupling is wanted: if upstream changes a formula, the
paired ``p28_recall_fusion_smoke.py`` should break. Imports are lazy inside
functions because ``memory/__init__`` pulls heavy deps at package-import time.
"""
from __future__ import annotations

from typing import Any


def _hr():
    """Lazily resolve ``memory.hybrid_recall`` (single chokepoint)."""
    from memory import hybrid_recall as _mod  # noqa: WPS433 — intentional lazy import
    return _mod


def _hard_filter_fn():
    """Lazily resolve the production ``_hard_filter`` staticmethod.

    Accessed as a plain function — we never construct ``MemoryRecallReranker``
    (its ``__init__`` grabs the embedding-service singleton, which is runtime
    mechanism we keep out of the testbench).
    """
    from memory.recall import MemoryRecallReranker  # noqa: WPS433
    return MemoryRecallReranker._hard_filter  # noqa: SLF001 — public-by-contract


# ── pure helper pass-throughs (import chokepoint) ──────────────────────


def tokenize(text: str, stop_names: list[str] | None = None) -> list[str]:
    """BM25 tokens (multiplicity preserved) — production's ``_tokenize``."""
    return _hr()._tokenize(text, stop_names)  # noqa: SLF001


def bm25_rank(
    query: str,
    pool: list[dict],
    *,
    stop_names: list[str] | None = None,
) -> list[tuple[dict, float]]:
    """Okapi BM25 ranking ``[(doc, score)]`` DESC — production's ``_bm25_rank``."""
    return _hr()._bm25_rank(query, pool, stop_names=stop_names)  # noqa: SLF001


def rrf_fuse(
    bm25_ranking: list[tuple[dict, float]],
    cosine_ranking: list[tuple[dict, float]] | None = None,
    *,
    k: int | None = None,
    budget_total: int | None = None,
) -> list[dict]:
    """Reciprocal Rank Fusion — production's ``_rrf_fuse``.

    ``cosine_ranking`` defaults to empty (testbench has no embedding path).
    ``k`` / ``budget_total`` default to ``HYBRID_RECALL_RRF_K`` /
    ``HYBRID_RECALL_BUDGET_TOTAL`` from config.
    """
    from config import HYBRID_RECALL_BUDGET_TOTAL, HYBRID_RECALL_RRF_K
    return _hr()._rrf_fuse(  # noqa: SLF001
        bm25_ranking,
        cosine_ranking or [],
        k=HYBRID_RECALL_RRF_K if k is None else k,
        budget_total=HYBRID_RECALL_BUDGET_TOTAL if budget_total is None else budget_total,
    )


def hard_filter(pool: list[dict]) -> list[dict]:
    """Drop score<0 / suppressed / protected / terminal-reflection / empty-text
    rows — production's ``MemoryRecallReranker._hard_filter`` (#3)."""
    return _hard_filter_fn()(pool)


def tag_tier(items: list[dict], tier: str) -> list[dict]:
    """Shallow-copy + stamp ``_tier`` / ``target_type`` — production's ``_tag_tier``."""
    return _hr()._tag_tier(items, tier)  # noqa: SLF001


def parse_time_window(spec: str | None):
    """``time`` spec -> ``(start, end)`` half-open naive interval, or None."""
    from memory.temporal import parse_time_window as _ptw
    return _ptw(spec)


def entry_in_window(entry: dict, win_start, win_end) -> bool:
    """Whether the entry's event interval intersects ``[win_start, win_end)`` —
    production's ``_overlaps_window``."""
    return _hr()._overlaps_window(entry, win_start, win_end)  # noqa: SLF001


# ── BM25-only fusion harness (cosine OOS) ──────────────────────────────


def recall_bm25_fuse(
    query: str,
    pool: list[dict],
    *,
    stop_names: list[str] | None = None,
    time_window: tuple | None = None,
    bm25_threshold: float | None = None,
    budget_each: int | None = None,
    budget_total: int | None = None,
    rrf_k: int | None = None,
) -> list[dict]:
    """Run ``hybrid_recall``'s pipeline with the cosine ranking fixed empty.

    Mirrors ``memory.hybrid_recall.hybrid_recall`` step-for-step minus the
    embedding path (which is out-of-scope): ``hard_filter`` -> optional event-
    time window filter -> BM25 -> per-side threshold + cap -> RRF. All numeric
    knobs default to the production ``HYBRID_RECALL_*`` constants.

    ``pool`` items should carry ``id`` + ``text`` (and, for ``time_window``,
    ``event_start_at`` / ``event_end_at`` / ``created_at``). Returns the fused
    ``list[dict]`` (each carrying ``_rrf_score``), same shape RRF emits.
    """
    from config import (
        HYBRID_RECALL_BM25_THRESHOLD,
        HYBRID_RECALL_BUDGET_EACH,
    )

    threshold = HYBRID_RECALL_BM25_THRESHOLD if bm25_threshold is None else bm25_threshold
    each = HYBRID_RECALL_BUDGET_EACH if budget_each is None else budget_each

    filtered = hard_filter(pool)
    if time_window is not None:
        ws, we = time_window
        filtered = [d for d in filtered if entry_in_window(d, ws, we)]

    bm25_scored = bm25_rank(query, filtered, stop_names=stop_names)
    bm25_top = [(d, s) for d, s in bm25_scored if s >= threshold][:each]

    return rrf_fuse(bm25_top, [], k=rrf_k, budget_total=budget_total)


__all__ = [
    "bm25_rank",
    "entry_in_window",
    "hard_filter",
    "parse_time_window",
    "recall_bm25_fuse",
    "rrf_fuse",
    "tag_tier",
    "tokenize",
]
