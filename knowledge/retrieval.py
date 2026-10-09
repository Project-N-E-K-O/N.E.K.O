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

from .store import FTS_CONTENT_CHARS, StoredEntry, VectorSnapshot
from .text import search_tokens


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


def token_coverage(query: str, entry: StoredEntry) -> float:
    """Share of the query's distinct tokens that also occur in the entry."""
    wanted = set(search_tokens(query))
    if not wanted:
        return 0.0
    parts = [entry.title, entry.summary, entry.content[:FTS_CONTENT_CHARS], *entry.tags]
    for values in entry.terms.values():
        parts.extend(values)
    present = set(search_tokens("\n".join(parts)))
    return len(wanted & present) / len(wanted)


def semantic_candidates(
    snapshot: VectorSnapshot | None,
    query_vector: np.ndarray | None,
    *,
    allowed_pack_ids: Iterable[str],
    limit: int = SEMANTIC_CANDIDATES,
) -> list[tuple[int, float]]:
    """Best cosine per entry over the allowed packs: [(entry_id, score)]."""
    if snapshot is None or query_vector is None:
        return []
    if query_vector.shape[0] != snapshot.matrix.shape[1]:
        return []
    allowed = {snapshot.pack_ids.index(p) for p in allowed_pack_ids if p in snapshot.pack_ids}
    if not allowed:
        return []
    mask = np.isin(snapshot.chunk_pack_index, np.fromiter(allowed, dtype=np.int32))
    if not mask.any():
        return []
    scores = snapshot.matrix[mask] @ query_vector
    entry_ids = snapshot.entry_ids[mask]
    best: dict[int, float] = {}
    for entry_id, score in zip(entry_ids.tolist(), scores.tolist()):
        if score >= SEMANTIC_THRESHOLD and score > best.get(entry_id, -1.0):
            best[entry_id] = score
    return sorted(best.items(), key=lambda item: -item[1])[:limit]


def fuse(
    query: str,
    *,
    exact_ids: Sequence[int],
    lexical_ids: Sequence[int],
    semantic: Sequence[tuple[int, float]],
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
        and token_coverage(query, entries[entry_id]) >= MIN_TOKEN_COVERAGE
    ]
    semantic_scores = {entry_id: score for entry_id, score in semantic if entry_id in usable}
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
        )
        for entry_id in ordered[:limit]
    ]
