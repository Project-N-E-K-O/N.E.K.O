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

"""Bounded, process-local diagnostics for the management page.

A query record keeps the outcome of every lookup, including the ones that
produced nothing to show (timeout, busy, error, disabled, miss); it never keeps
the query text or any vector. ``result`` is one of ``QUERY_RESULTS``.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict, dataclass

from .registry import utc_now


_MAX_RECORDS = 50
QUERY_RESULTS = frozenset(
    {"matched", "miss", "timeout", "busy", "error", "disabled", "unavailable"}
)


@dataclass(frozen=True, slots=True)
class QueryRecord:
    timestamp: str
    mode: str
    result: str
    retrieval_mode: str
    hits: int
    entry_title: str
    pack_id: str
    elapsed_ms: int
    error_type: str


@dataclass(frozen=True, slots=True)
class IndexBatchRecord:
    timestamp: str
    model_id: str
    selected: int
    stored: int
    failed: int
    elapsed_ms: int


class KnowledgeDiagnostics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queries: deque[QueryRecord] = deque(maxlen=_MAX_RECORDS)
        self._batches: deque[IndexBatchRecord] = deque(maxlen=_MAX_RECORDS)

    def record_query(
        self,
        *,
        mode: str,
        result: str,
        retrieval_mode: str = "",
        hits: int = 0,
        entry_title: str = "",
        pack_id: str = "",
        elapsed_ms: int = 0,
        error_type: str = "",
    ) -> None:
        record = QueryRecord(
            timestamp=utc_now(),
            mode=str(mode)[:20],
            result=result if result in QUERY_RESULTS else "error",
            retrieval_mode=str(retrieval_mode)[:20],
            hits=max(int(hits), 0),
            entry_title=str(entry_title)[:200],
            pack_id=str(pack_id)[:64],
            elapsed_ms=max(int(elapsed_ms), 0),
            error_type=str(error_type)[:80],
        )
        with self._lock:
            self._queries.append(record)

    def record_index_batch(
        self, *, model_id: str, selected: int, stored: int, failed: int, elapsed_ms: int
    ) -> None:
        record = IndexBatchRecord(
            timestamp=utc_now(),
            model_id=str(model_id)[:120],
            selected=max(int(selected), 0),
            stored=max(int(stored), 0),
            failed=max(int(failed), 0),
            elapsed_ms=max(int(elapsed_ms), 0),
        )
        with self._lock:
            self._batches.append(record)

    def snapshot(self) -> dict[str, list[dict]]:
        with self._lock:
            return {
                "queries": [asdict(record) for record in reversed(self._queries)],
                "index_batches": [asdict(record) for record in reversed(self._batches)],
            }
