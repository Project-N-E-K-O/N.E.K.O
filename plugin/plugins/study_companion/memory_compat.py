from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .fsrs_bridge import FSRSBridge, retrievability
from .memory_text import normalize_tags


def compat_card_payload(
    item: dict[str, Any],
    *,
    get_fsrs_card: Callable[[str], dict[str, Any] | None],
    fsrs: FSRSBridge,
) -> dict[str, Any]:
    card = item.get("fsrs_card") or get_fsrs_card(str(item.get("id") or "")) or {}
    metadata = item.get("metadata") or {}
    topic_id = str(metadata.get("topic_id") or metadata.get("legacy_topic_id") or item.get("id") or "")
    raw_card = card.get("card") if isinstance(card, dict) else {}
    raw_card = raw_card if isinstance(raw_card, dict) else {}
    due_reviews = fsrs.get_due_reviews([raw_card]) if raw_card else []
    due_item = due_reviews[0] if due_reviews else None
    return {
        "id": str(item.get("id") or ""),
        "topic_id": topic_id,
        "item_id": str(item.get("id") or ""),
        "deck_id": str(item.get("deck_id") or ""),
        "front": str(
            item.get("prompt") or raw_card.get("front") or raw_card.get("prompt") or ""
        ),
        "back": str(
            item.get("answer") or raw_card.get("back") or raw_card.get("answer") or ""
        ),
        "tags": normalize_tags(metadata.get("tags")),
        "source": str(metadata.get("source") or ""),
        "card_type": "memory",
        "due": str(raw_card.get("due") or ""),
        "is_due": due_item is not None,
        "retrievability": round(
            float(due_item.get("retrievability"))
            if due_item
            else retrievability(raw_card),
            4,
        )
        if raw_card
        else 0.0,
        "state": str(raw_card.get("state") or ""),
        "scheduled_days": float(raw_card.get("scheduled_days") or 0.0),
        "reps": int(raw_card.get("reps") or 0),
        "lapses": int(raw_card.get("lapses") or 0),
        "last_review": str(raw_card.get("last_review") or ""),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "last_rating": int(card.get("last_rating") or 0)
        if isinstance(card, dict)
        else 0,
        "item": item,
        "fsrs_card": card,
    }


__all__ = ["compat_card_payload"]
