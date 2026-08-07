"""Two-stage tactical document retrieval.

Stage one narrows the corpus, stage two ranks what survived:

1. **Tags.** Front matter `maps/ships/classes/modes/topics` are matched against
   the live battle context with an indexed lookup. This is the primary key, and
   it keeps working at any corpus size.
2. **Terms**, only when no tag matched: the persisted 2-gram index must yield at
   least `min_term_hits` *distinct* query terms in one chunk.
3. **BM25** over the surviving candidates, plus a tag bonus and a 3-gram
   precision bonus computed on the loaded text.

The gate is deliberately strict: no tag hit and fewer than two term hits means
nothing is injected. Reference text that only vaguely relates to the moment is
worse than no reference at all, because the model will try to use it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..domain.contracts import TacticExcerpt, TacticQuery
from .store import TAG_KINDS, KnowledgeStore
from .tokenize import index_terms, trigrams

# Okapi BM25 constants, same values the host uses in `memory/hybrid_recall.py`.
BM25_K1 = 1.5
BM25_B = 0.75

# How much a 3-gram overlap can add on top of the BM25 score. Small on purpose:
# 3-grams refine an ordering that the 2-gram recall set already produced.
TRIGRAM_BONUS_WEIGHT = 0.8

# Hard ceiling on how many candidates get loaded and scored in one query, so a
# very common tag cannot turn one frame into a full-corpus scan.
MAX_CANDIDATES = 400


@dataclass
class SearchDiagnostics:
    """Why the last search returned what it did, for the panel."""

    query_text: str = ""
    tags_used: list[str] = field(default_factory=list)
    tag_candidates: int = 0
    term_candidates: int = 0
    best_term_hits: int = 0
    scored: int = 0
    gated: bool = False
    gate_reason: str = ""
    hits: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query_text": self.query_text,
            "tags_used": list(self.tags_used),
            "tag_candidates": self.tag_candidates,
            "term_candidates": self.term_candidates,
            "best_term_hits": self.best_term_hits,
            "scored": self.scored,
            "gated": self.gated,
            "gate_reason": self.gate_reason,
            "hits": list(self.hits),
        }


class WowsTacticsRepository:
    """The real `TacticsRepository`, backed by the imported document store."""

    def __init__(self, store: KnowledgeStore, cfg) -> None:
        self.store = store
        self.cfg = cfg
        self.diagnostics = SearchDiagnostics()

    def apply_config(self, cfg) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------------
    def search(
        self,
        query: TacticQuery,
        *,
        limit: int = 3,
        budget: int = 0,
    ) -> tuple[TacticExcerpt, ...]:
        cfg = self.cfg
        query_text = query.text()
        tags = _query_tags(query)
        diagnostics = SearchDiagnostics(
            query_text=query_text,
            tags_used=[f"{kind}:{value}" for kind, value in tags],
        )

        try:
            tag_hits = self.store.chunk_ids_for_tags(tags)
        except Exception:
            # A document-store failure must never stop a battle call-out; the
            # reference block is optional by construction.
            self.diagnostics = diagnostics
            diagnostics.gated = True
            diagnostics.gate_reason = "store unavailable"
            return ()
        diagnostics.tag_candidates = len(tag_hits)

        terms = index_terms(query_text)
        postings, document_frequency = self.store.postings_for_terms(terms)
        diagnostics.term_candidates = len(postings)
        diagnostics.best_term_hits = max(
            (len(matched) for matched in postings.values()), default=0)

        min_hits = max(1, int(cfg.tactics_min_term_hits))
        if not tag_hits and diagnostics.best_term_hits < min_hits:
            diagnostics.gated = True
            diagnostics.gate_reason = (
                f"无标签命中且最多只有 {diagnostics.best_term_hits} 个查询词命中"
                f"（需要 {min_hits}）"
            )
            self.diagnostics = diagnostics
            return ()

        candidates = self._candidate_ids(tag_hits, postings, min_hits)
        if not candidates:
            diagnostics.gated = True
            diagnostics.gate_reason = "没有候选段落"
            self.diagnostics = diagnostics
            return ()

        rows = self.store.load_chunks(candidates)
        corpus = self.store.stats()
        total_chunks = max(1, corpus["indexed_chunks"])
        average_length = (
            corpus["indexed_tokens"] / total_chunks) if total_chunks else 1.0
        query_trigrams = set(trigrams(query_text))

        scored: list[tuple[float, dict[str, Any]]] = []
        for chunk_id, row in rows.items():
            score = _bm25(
                postings.get(chunk_id, {}),
                document_frequency,
                total_chunks=total_chunks,
                length=row["token_count"] or 1,
                average_length=average_length or 1.0,
            )
            score += float(cfg.tactics_tag_weight) * tag_hits.get(chunk_id, 0)
            if query_trigrams:
                overlap = query_trigrams.intersection(trigrams(row["text"]))
                score += TRIGRAM_BONUS_WEIGHT * (len(overlap) / len(query_trigrams))
            if score <= 0.0:
                continue
            scored.append((score, row))

        diagnostics.scored = len(scored)
        # Ties break on chunk id so a replay of the same corpus is reproducible.
        scored.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        top = scored[:max(1, int(limit))]

        doc_tags = self.store.tags_for_documents([row["doc_id"] for _score, row in top])
        excerpts: list[TacticExcerpt] = []
        for score, row in top:
            heading = row["heading"]
            # A single-heading document has the same text in both fields; showing
            # it twice just makes the panel harder to read.
            title = (
                f"{row['title']} / {heading}"
                if heading and heading != row["title"]
                else row["title"]
            )
            excerpts.append(TacticExcerpt(
                doc_id=row["doc_id"],
                title=title,
                text=row["text"],
                score=round(score, 4),
                tags=tuple(doc_tags.get(row["doc_id"], ())),
            ))
            diagnostics.hits.append({
                "doc_id": row["doc_id"],
                "title": title,
                "score": round(score, 4),
                "tag_hits": tag_hits.get(row["chunk_id"], 0),
                "term_hits": len(postings.get(row["chunk_id"], {})),
            })

        self.diagnostics = diagnostics
        return tuple(excerpts)

    # ------------------------------------------------------------------
    def _candidate_ids(
        self,
        tag_hits: dict[int, int],
        postings: dict[int, dict[str, int]],
        min_hits: int,
    ) -> list[int]:
        """Tag matches first, then term matches that clear the hit threshold."""
        ordered: list[int] = sorted(
            tag_hits, key=lambda chunk_id: (-tag_hits[chunk_id], chunk_id))
        seen = set(ordered)
        extras = [
            chunk_id for chunk_id, matched in postings.items()
            if chunk_id not in seen and len(matched) >= min_hits
        ]
        extras.sort(key=lambda chunk_id: (-len(postings[chunk_id]), chunk_id))
        ordered.extend(extras)
        return ordered[:MAX_CANDIDATES]


def _query_tags(query: TacticQuery) -> list[tuple[str, str]]:
    """Map the battle context onto front-matter tag kinds."""
    pairs: list[tuple[str, str]] = []
    for kind, values in query.tag_candidates().items():
        if kind not in TAG_KINDS:
            continue
        for value in values:
            cleaned = str(value or "").strip()
            if cleaned:
                pairs.append((kind, cleaned))
    return list(dict.fromkeys(pairs))


def _bm25(
    matched: dict[str, int],
    document_frequency: dict[str, int],
    *,
    total_chunks: int,
    length: int,
    average_length: float,
) -> float:
    score = 0.0
    for term, tf in matched.items():
        df = max(1, document_frequency.get(term, 1))
        idf = math.log(1.0 + (total_chunks - df + 0.5) / (df + 0.5))
        denominator = tf + BM25_K1 * (
            1.0 - BM25_B + BM25_B * (length / max(1e-9, average_length))
        )
        score += idf * (tf * (BM25_K1 + 1.0)) / max(1e-9, denominator)
    return score


__all__ = [
    "BM25_B",
    "BM25_K1",
    "MAX_CANDIDATES",
    "TRIGRAM_BONUS_WEIGHT",
    "SearchDiagnostics",
    "WowsTacticsRepository",
]
