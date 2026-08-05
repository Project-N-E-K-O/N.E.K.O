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
    collection_id: str
    entry_title: str
    source_tag: str
    match_mode: str
    card_delivered: bool
    result: str
    error_type: str = ""


_records: deque[KnowledgeRouteDiagnostic] = deque(maxlen=_MAX_RECORDS)
_lock = threading.Lock()


def record_knowledge_route(
    *,
    collection_id: str = "",
    entry_title: str = "",
    source_tag: str = "",
    match_mode: str = "none",
    card_delivered: bool = False,
    result: str = "miss",
    error_type: str = "",
) -> None:
    record = KnowledgeRouteDiagnostic(
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        collection_id=str(collection_id or ""),
        entry_title=str(entry_title or "")[:500],
        source_tag=str(source_tag or "")[:100],
        match_mode=str(match_mode or "none")[:40],
        card_delivered=bool(card_delivered),
        result=str(result or "miss")[:40],
        error_type=str(error_type or "")[:100],
    )
    with _lock:
        _records.append(record)


def list_recent_knowledge_routes() -> tuple[dict, ...]:
    with _lock:
        return tuple(asdict(record) for record in reversed(_records))


def clear_knowledge_route_diagnostics() -> None:
    with _lock:
        _records.clear()
