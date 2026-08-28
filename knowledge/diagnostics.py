"""Bounded, process-local diagnostics for public-knowledge routing."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


_MAX_RECORDS = 20


@dataclass(frozen=True, slots=True)
class KnowledgeRouteDiagnostic:
    timestamp: str
    entry_title: str
    source_tag: str
    match_mode: str
    card_delivered: bool
    result: str
    error_type: str = ""
    knowledge_hits: int = 0
    corpus_hits: int = 0
    elapsed_ms: int = 0


@dataclass(frozen=True, slots=True)
class KnowledgeQueryDiagnostic:
    timestamp: str
    retrieval_mode: str
    embedding_service_state: str
    lexical_candidates: int
    semantic_candidates: int
    fallback_reason: str
    elapsed_ms: int


@dataclass(frozen=True, slots=True)
class KnowledgeIndexBatchDiagnostic:
    timestamp: str
    selected: int
    stored: int
    failed: int
    stale_writebacks: int
    elapsed_ms: int
    state: str


_records: deque[KnowledgeRouteDiagnostic] = deque(maxlen=_MAX_RECORDS)
_query_records: deque[KnowledgeQueryDiagnostic] = deque(maxlen=_MAX_RECORDS)
_index_batch_records: deque[KnowledgeIndexBatchDiagnostic] = deque(maxlen=_MAX_RECORDS)
_lock = threading.Lock()


def record_knowledge_route(
    *,
    entry_title: str = "",
    source_tag: str = "",
    match_mode: str = "none",
    card_delivered: bool = False,
    result: str = "miss",
    error_type: str = "",
    knowledge_hits: int = 0,
    corpus_hits: int = 0,
    elapsed_ms: int = 0,
) -> None:
    record = KnowledgeRouteDiagnostic(
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        entry_title=str(entry_title or "")[:500],
        source_tag=str(source_tag or "")[:100],
        match_mode=str(match_mode or "none")[:40],
        card_delivered=bool(card_delivered),
        result=str(result or "miss")[:40],
        error_type=str(error_type or "")[:100],
        knowledge_hits=max(int(knowledge_hits), 0),
        corpus_hits=max(int(corpus_hits), 0),
        elapsed_ms=max(int(elapsed_ms), 0),
    )
    with _lock:
        _records.append(record)


def list_recent_knowledge_routes() -> tuple[dict, ...]:
    with _lock:
        return tuple(asdict(record) for record in reversed(_records))


def record_knowledge_query(
    *,
    retrieval_mode: str,
    embedding_service_state: str,
    lexical_candidates: int,
    semantic_candidates: int,
    fallback_reason: str = "",
    elapsed_ms: int = 0,
) -> None:
    """Store content-free retrieval telemetry; never retain the query or vectors."""
    record = KnowledgeQueryDiagnostic(
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        retrieval_mode=str(retrieval_mode or "bm25")[:20],
        embedding_service_state=str(embedding_service_state or "unknown")[:40],
        lexical_candidates=max(int(lexical_candidates), 0),
        semantic_candidates=max(int(semantic_candidates), 0),
        fallback_reason=str(fallback_reason or "")[:80],
        elapsed_ms=max(int(elapsed_ms), 0),
    )
    with _lock:
        _query_records.append(record)


def list_recent_knowledge_queries() -> tuple[dict, ...]:
    with _lock:
        return tuple(asdict(record) for record in reversed(_query_records))


def record_knowledge_index_batch(
    *,
    selected: int,
    stored: int,
    failed: int,
    stale_writebacks: int,
    elapsed_ms: int,
    state: str,
) -> None:
    """Store only aggregate index telemetry, never chunk text or vectors."""
    record = KnowledgeIndexBatchDiagnostic(
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        selected=max(int(selected), 0),
        stored=max(int(stored), 0),
        failed=max(int(failed), 0),
        stale_writebacks=max(int(stale_writebacks), 0),
        elapsed_ms=max(int(elapsed_ms), 0),
        state=str(state or "unknown")[:40],
    )
    with _lock:
        _index_batch_records.append(record)


def list_recent_knowledge_index_batches() -> tuple[dict, ...]:
    with _lock:
        return tuple(asdict(record) for record in reversed(_index_batch_records))


def clear_knowledge_route_diagnostics() -> None:
    with _lock:
        _records.clear()
        _query_records.clear()
        _index_batch_records.clear()
