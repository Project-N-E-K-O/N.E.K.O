"""Pure constructors used by reflection refinement."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

def build_split_reflection(
    src: dict,
    produce_item: dict,
    entity: str,
    now: datetime,
    *,
    split_count: int,
    id_factory: Callable[[str], str],
    normalizer: Callable[[dict], dict],
) -> dict:
    """Build one reflection produced by a split action."""
    text = str(produce_item.get("text", "")).strip()
    relation_type = produce_item.get("relation_type") or src.get("relation_type")
    temporal_scope = produce_item.get("temporal_scope") or src.get("temporal_scope")
    denominator = max(int(split_count), 2)
    return normalizer(
        {
            "id": id_factory(text),
            "text": text,
            "entity": entity,
            "status": src.get("status") or "pending",
            "source_fact_ids": list(src.get("source_fact_ids") or []),
            "created_at": now.isoformat(),
            "reinforcement": float(src.get("reinforcement", 0) or 0) / denominator,
            "relation_type": relation_type,
            "temporal_scope": temporal_scope,
            "subject": src.get("subject"),
            "event_when_raw": src.get("event_when_raw"),
            "event_start_at": src.get("event_start_at"),
            "event_end_at": src.get("event_end_at"),
            "schema_version": src.get("schema_version", 2),
        }
    )


def build_merge_reflection(
    srcs: list[dict],
    fact_source_ids: list[str],
    produce: dict,
    entity: str,
    now: datetime,
    *,
    id_factory: Callable[[str], str],
    normalizer: Callable[[dict], dict],
) -> dict:
    """Build one reflection produced by a merge action."""
    text = str(produce.get("text", "")).strip()
    relation_type = produce.get("relation_type") or (
        srcs[0].get("relation_type") if srcs else None
    )
    temporal_scope = produce.get("temporal_scope") or (
        srcs[0].get("temporal_scope") if srcs else None
    )
    combined_fact_ids: set[str] = set(fact_source_ids)
    for source in srcs:
        combined_fact_ids.update(source.get("source_fact_ids") or [])
    status_priority = {"promoted": 3, "confirmed": 2, "pending": 1}
    best_status = max(
        (source.get("status", "pending") for source in srcs),
        key=lambda status: status_priority.get(status, 0),
        default="pending",
    )
    max_reinforcement = max(
        (float(source.get("reinforcement", 0) or 0) for source in srcs),
        default=0.0,
    )
    first = srcs[0] if srcs else {}
    return normalizer(
        {
            "id": id_factory(text),
            "text": text,
            "entity": entity,
            "status": best_status,
            "source_fact_ids": sorted(combined_fact_ids),
            "created_at": now.isoformat(),
            "reinforcement": max_reinforcement,
            "relation_type": relation_type,
            "temporal_scope": temporal_scope,
            "subject": first.get("subject"),
            "event_when_raw": first.get("event_when_raw"),
            "event_start_at": first.get("event_start_at"),
            "event_end_at": first.get("event_end_at"),
            "schema_version": first.get("schema_version", 2),
        }
    )
