# -*- coding: utf-8 -*-
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
"""Vector similarity between memory entries for the reflection pipeline."""

from __future__ import annotations

from ._shared import logger


def _ready_service():
    """Return ``(service, model_id)`` when vectors are usable, else None."""
    try:
        from memory.embeddings import get_embedding_service
        service = get_embedding_service()
        # INIT/LOADING reports is_disabled=False but is_available=False, so
        # both have to be checked (same gate as the related-context block).
        if service.is_disabled() or not service.is_available():
            return None
        model_id = service.model_id()
        if not model_id:
            return None
        return service, model_id
    except Exception:  # noqa: BLE001 - module missing or service broken
        return None


def vectors_ready() -> bool:
    """Cheap gate so callers can skip loading candidates without vectors."""
    return _ready_service() is not None


async def ascore_against_queries(
    query_entries: list[dict],
    candidate_entries: list[dict],
) -> list[float | None] | None:
    """Max cosine of each candidate against any of the query entries.

    Returns ``None`` when vectors are unusable (service not ready, no model
    id, no query vector, or an unexpected failure) so callers can take their
    non-vector path unchanged. Otherwise returns a list aligned with
    ``candidate_entries``; a candidate without a usable vector scores ``None``.

    Queries (a handful of fresh entries) are embedded on the fly when their
    cache is missing. Candidates only use the cache the embedding worker
    keeps filled: a persona pool runs to thousands of rows, and embedding a
    stale one here would hold the shared model for seconds and starve the
    chat-time recall that runs under a one-second budget.
    """
    if not query_entries or not candidate_entries:
        return None
    ready = _ready_service()
    if ready is None:
        return None
    service, model_id = ready
    try:
        import numpy as np
        from memory.embeddings import (
            decode_embedding,
            decode_valid_cached_embedding,
            parse_dim_from_model_id,
        )

        def _cached_vectors(entries: list[dict]) -> tuple[list, list[str]]:
            texts = [str(e.get('text') or '') for e in entries]
            return [
                decode_valid_cached_embedding(e, text, model_id) if text else None
                for e, text in zip(entries, texts)
            ], texts

        query_vectors, query_texts = _cached_vectors(query_entries)
        missing = [
            i for i, v in enumerate(query_vectors) if v is None and query_texts[i]
        ]
        if missing:
            fresh = await service.embed_batch([query_texts[i] for i in missing])
            for i, raw in zip(missing, fresh):
                if raw is not None:
                    query_vectors[i] = decode_embedding(raw)
        target_dim = parse_dim_from_model_id(model_id)
        if target_dim is None:
            target_dim = next(
                (int(v.size) for v in query_vectors if v is not None and v.size),
                None,
            )
        if target_dim is None:
            return None

        def _unit_rows(vectors: list) -> tuple[list[int], list]:
            index: list[int] = []
            rows: list = []
            for i, v in enumerate(vectors):
                if v is None or v.size != target_dim:
                    continue
                norm = float(np.linalg.norm(v))
                if not np.isfinite(norm) or norm <= 0:
                    continue
                index.append(i)
                rows.append(v / norm)
            return index, rows

        _, query_rows = _unit_rows(query_vectors)
        if not query_rows:
            return None
        candidate_index, candidate_rows = _unit_rows(
            _cached_vectors(candidate_entries)[0],
        )
        scores: list[float | None] = [None] * len(candidate_entries)
        if candidate_rows:
            # (N, D) @ (D, Q) -> (N, Q); max over queries -> (N,)
            matrix = np.stack(candidate_rows) @ np.stack(query_rows).T
            for slot, score in zip(candidate_index, matrix.max(axis=1)):
                scores[slot] = float(score)
        return scores
    except Exception as e:  # noqa: BLE001 - vectors are an optional signal
        logger.warning(f"[ReflectionSimilarity] 向量打分失败，退回非向量路径: {e}")
        return None
