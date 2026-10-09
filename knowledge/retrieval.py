# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Hybrid ranking: exact surface match, BM25 and cosine, fused with RRF.

Each signal must clear its own bar before it may vote, so an unrelated query
returns nothing instead of the least-bad card:

* an exact title / alias / recognition match always qualifies;
* a BM25 hit qualifies when it covers enough of the query's tokens;
* a vector hit qualifies when its cosine reaches ``SEMANTIC_THRESHOLD``.

Qualified candidates are ordered by reciprocal-rank fusion, with exact matches
pinned in front.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np

from .store import StoredEntry, VectorSnapshot
from .text import is_cjk_token, search_tokens, search_view, word_runs


RRF_K = 60
LEXICAL_CANDIDATES = 48
SEMANTIC_CANDIDATES = 24
SEMANTIC_THRESHOLD = 0.5
MIN_TOKEN_COVERAGE = 0.6


@dataclass(frozen=True, slots=True)
class RankedHit:
    entry_id: int
    score: float
    exact: bool
    lexical_rank: int | None
    semantic_score: float | None
    # Chunk that carried a vector-only match; lexical matches pick their
    # excerpt by token overlap instead.
    semantic_chunk: int | None = None


@dataclass(frozen=True, slots=True)
class SemanticMatch:
    entry_id: int
    score: float
    chunk_index: int | None


def token_coverage(query: str, entry: StoredEntry) -> float:
    """Share of the query's distinct tokens that also occur in the entry.

    Content can be long (all of it is indexed), so instead of tokenizing it
    each token is looked up as a substring of the normalized text.
    """
    wanted = set(search_tokens(query))
    if not wanted:
        return 0.0
    parts = [entry.title, entry.summary, entry.content, *entry.tags]
    for values in entry.terms.values():
        parts.extend(values)
    haystack = search_view("\n".join(parts))
    words = word_runs(haystack)
    # CJK bigrams may sit anywhere inside a run; other tokens are whole words,
    # matching how FTS indexed them ("he" is not in "the").
    present = sum(
        1 for token in wanted if (token in haystack if is_cjk_token(token) else token in words)
    )
    return present / len(wanted)


def names_in_query(query: str, entry: StoredEntry) -> bool:
    """Whether the query mentions the entry by its title or an alias.

    "Tell me about Python" names the entry "Python" even though most of its
    words are not in the entry; such a BM25 hit qualifies on its own.
    """
    wanted = set(search_tokens(query))
    for name in (entry.title, *entry.terms.get("alias", ())):
        tokens = set(search_tokens(name))
        if tokens and tokens <= wanted:
            return True
    return False


def semantic_candidates(
    snapshot: VectorSnapshot | None,
    query_vector: np.ndarray | None,
    *,
    allowed_pack_ids: Iterable[str],
    limit: int = SEMANTIC_CANDIDATES,
) -> list[SemanticMatch]:
    """Best-scoring chunk per entry over the allowed packs, best first."""
    if snapshot is None or query_vector is None:
        return []
    if query_vector.shape[0] != snapshot.matrix.shape[1]:
        return []
    wanted = set(allowed_pack_ids)
    allowed = [index for index, pack_id in enumerate(snapshot.pack_ids) if pack_id in wanted]
    if not allowed:
        return []
    mask = np.isin(snapshot.chunk_pack_index, np.asarray(allowed, dtype=np.int32))
    if not mask.any():
        return []
    scores = snapshot.matrix[mask] @ query_vector
    entry_ids = snapshot.entry_ids[mask].tolist()
    chunk_indexes = (
        snapshot.chunk_indexes[mask].tolist()
        if snapshot.chunk_indexes is not None
        else [None] * len(entry_ids)
    )
    best: dict[int, SemanticMatch] = {}
    for entry_id, score, chunk_index in zip(entry_ids, scores.tolist(), chunk_indexes):
        current = best.get(entry_id)
        if score >= SEMANTIC_THRESHOLD and (current is None or score > current.score):
            best[entry_id] = SemanticMatch(entry_id, score, chunk_index)
    return sorted(best.values(), key=lambda match: -match.score)[:limit]


def best_excerpt_index(query: str, bodies: Sequence[str], semantic_chunk: int | None) -> int:
    """Pick the chunk to show: the vector match, else the best token overlap."""
    if semantic_chunk is not None and 0 <= semantic_chunk < len(bodies):
        return semantic_chunk
    wanted = set(search_tokens(query))
    if not wanted or len(bodies) <= 1:
        return 0
    overlaps = [len(wanted & set(search_tokens(body))) for body in bodies]
    return max(range(len(bodies)), key=lambda index: (overlaps[index], -index))


def fuse(
    query: str,
    *,
    exact_ids: Sequence[int],
    lexical_ids: Sequence[int],
    semantic: Sequence[SemanticMatch],
    entries: Mapping[int, StoredEntry],
    usable: set[int],
    limit: int,
) -> list[RankedHit]:
    """Combine the three signals into at most ``limit`` qualified hits."""
    exact = [entry_id for entry_id in exact_ids if entry_id in usable]
    lexical = [
        entry_id
        for entry_id in lexical_ids
        if entry_id in usable
        and entry_id in entries
        and (
            token_coverage(query, entries[entry_id]) >= MIN_TOKEN_COVERAGE
            or names_in_query(query, entries[entry_id])
        )
    ]
    semantic_scores = {match.entry_id: match.score for match in semantic if match.entry_id in usable}
    semantic_chunks = {match.entry_id: match.chunk_index for match in semantic}
    scores: dict[int, float] = {}
    for rank, entry_id in enumerate(lexical):
        scores[entry_id] = scores.get(entry_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, (entry_id, _score) in enumerate(
        sorted(semantic_scores.items(), key=lambda item: -item[1])
    ):
        scores[entry_id] = scores.get(entry_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    lexical_rank = {entry_id: rank for rank, entry_id in enumerate(lexical)}
    exact_set = set(exact)
    ordered = list(dict.fromkeys([
        *exact,
        *sorted(scores, key=lambda entry_id: -scores[entry_id]),
    ]))
    return [
        RankedHit(
            entry_id=entry_id,
            score=scores.get(entry_id, 0.0) + (1.0 if entry_id in exact_set else 0.0),
            exact=entry_id in exact_set,
            lexical_rank=lexical_rank.get(entry_id),
            semantic_score=semantic_scores.get(entry_id),
            semantic_chunk=(
                semantic_chunks.get(entry_id)
                if entry_id not in exact_set and entry_id not in lexical_rank
                else None
            ),
        )
        for entry_id in ordered[:limit]
    ]
