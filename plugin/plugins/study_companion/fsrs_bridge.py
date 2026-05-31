from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum
import math
from typing import Any, Iterable


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: object, default: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass
    return default or _utc_now()


def _iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


def _clamp(value: float, minimum: float, maximum: float) -> float:
    if not math.isfinite(value):
        return minimum
    return max(minimum, min(maximum, value))


class StudyFsrsRating(IntEnum):
    Again = 1
    Hard = 2
    Good = 3
    Easy = 4
    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4


@dataclass(slots=True)
class StudyFsrsCard:
    topic_id: str
    due: str
    stability: float = 1.0
    difficulty: float = 5.0
    elapsed_days: float = 0.0
    scheduled_days: float = 0.0
    reps: int = 0
    lapses: int = 0
    state: str = "new"
    last_review: str = ""
    created_at: str = ""
    card_type: str = "topic"
    front: str = ""
    back: str = ""
    source: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StudyFsrsCard":
        return cls(
            topic_id=str(payload.get("topic_id") or ""),
            due=str(payload.get("due") or ""),
            stability=float(payload.get("stability") or 1.0),
            difficulty=float(payload.get("difficulty") or 5.0),
            elapsed_days=float(payload.get("elapsed_days") or 0.0),
            scheduled_days=float(payload.get("scheduled_days") or 0.0),
            reps=int(payload.get("reps") or 0),
            lapses=int(payload.get("lapses") or 0),
            state=str(payload.get("state") or "new"),
            last_review=str(payload.get("last_review") or ""),
            created_at=str(payload.get("created_at") or ""),
            card_type=str(payload.get("card_type") or "topic"),
            front=str(payload.get("front") or ""),
            back=str(payload.get("back") or ""),
            source=str(payload.get("source") or ""),
            tags=[str(item) for item in payload.get("tags") or [] if str(item).strip()]
            if isinstance(payload.get("tags"), list)
            else [],
        )


def create_card(topic_id: str, now: datetime | None = None) -> StudyFsrsCard:
    current = now or _utc_now()
    stamped = _iso(current)
    return StudyFsrsCard(
        topic_id=str(topic_id or "").strip() or "general",
        due=stamped,
        stability=1.0,
        difficulty=5.0,
        elapsed_days=0.0,
        scheduled_days=0.0,
        reps=0,
        lapses=0,
        state="new",
        created_at=stamped,
        card_type="topic",
    )


def retrievability(
    card: StudyFsrsCard | dict[str, Any], now: datetime | None = None
) -> float:
    current = now or _utc_now()
    parsed = StudyFsrsCard.from_dict(card) if isinstance(card, dict) else card
    anchor = _parse_dt(parsed.last_review or parsed.created_at or parsed.due, current)
    elapsed_days = max(0.0, (current - anchor).total_seconds() / 86400.0)
    stability = max(0.05, float(parsed.stability or 0.05))
    return _clamp(0.5 ** (elapsed_days / stability), 0.0, 1.0)


def _rating(value: StudyFsrsRating | int) -> StudyFsrsRating:
    try:
        return StudyFsrsRating(int(value))
    except (TypeError, ValueError):
        return StudyFsrsRating.Good


def rate_answer(
    card: StudyFsrsCard | dict[str, Any],
    rating: StudyFsrsRating | int,
    now: datetime | None = None,
) -> tuple[StudyFsrsCard, dict[str, Any]]:
    current = now or _utc_now()
    previous = StudyFsrsCard.from_dict(card) if isinstance(card, dict) else card
    selected = _rating(rating)
    anchor = _parse_dt(
        previous.last_review or previous.created_at or previous.due, current
    )
    elapsed_days = max(0.0, (current - anchor).total_seconds() / 86400.0)
    stability = max(0.1, float(previous.stability or 1.0))
    difficulty = _clamp(float(previous.difficulty or 5.0), 1.0, 10.0)

    if selected == StudyFsrsRating.Again:
        new_stability = max(0.25, stability * 0.45)
        new_difficulty = _clamp(difficulty + 1.2, 1.0, 10.0)
        scheduled_days = 0.0
        state = "relearning" if previous.reps else "learning"
        lapses = int(previous.lapses) + 1
    elif selected == StudyFsrsRating.Hard:
        new_stability = max(
            0.75, stability * (1.18 + min(elapsed_days, stability * 2.0) * 0.03)
        )
        new_difficulty = _clamp(difficulty + 0.35, 1.0, 10.0)
        scheduled_days = max(1.0, new_stability * 0.9)
        state = "review"
        lapses = int(previous.lapses)
    elif selected == StudyFsrsRating.Easy:
        new_stability = max(3.0, stability * (3.0 + max(0.0, 5.0 - difficulty) * 0.08))
        new_difficulty = _clamp(difficulty - 0.55, 1.0, 10.0)
        scheduled_days = max(3.0, new_stability * 1.25)
        state = "review"
        lapses = int(previous.lapses)
    else:
        new_stability = max(2.0, stability * (2.05 + max(0.0, 5.5 - difficulty) * 0.05))
        new_difficulty = _clamp(difficulty - 0.18, 1.0, 10.0)
        scheduled_days = max(2.0, new_stability)
        state = "review"
        lapses = int(previous.lapses)

    due_at = current + timedelta(days=scheduled_days)
    updated = StudyFsrsCard(
        topic_id=previous.topic_id,
        due=_iso(due_at),
        stability=round(new_stability, 4),
        difficulty=round(new_difficulty, 4),
        elapsed_days=round(elapsed_days, 4),
        scheduled_days=round(scheduled_days, 4),
        reps=int(previous.reps) + 1,
        lapses=lapses,
        state=state,
        last_review=_iso(current),
        created_at=previous.created_at or _iso(current),
        card_type=previous.card_type,
        front=previous.front,
        back=previous.back,
        source=previous.source,
        tags=list(previous.tags),
    )
    schedule = {
        "topic_id": updated.topic_id,
        "rating": int(selected),
        "retrievability_before": retrievability(previous, current),
        "scheduled_days": updated.scheduled_days,
        "due": updated.due,
        "state": updated.state,
    }
    return updated, schedule


def get_due_reviews(
    cards: Iterable[StudyFsrsCard | dict[str, Any]],
    now: datetime | None = None,
    *,
    retention_target: float = 0.90,
) -> list[dict[str, Any]]:
    current = now or _utc_now()
    target = _clamp(float(retention_target or 0.90), 0.1, 0.99)
    due_reviews: list[dict[str, Any]] = []
    for raw in cards:
        card = StudyFsrsCard.from_dict(raw) if isinstance(raw, dict) else raw
        due_at = _parse_dt(card.due, current)
        retention = retrievability(card, current)
        is_due = due_at <= current or retention < target
        if not is_due:
            continue
        overdue_days = max(0.0, (current - due_at).total_seconds() / 86400.0)
        priority = overdue_days + (target - retention)
        due_reviews.append(
            {
                "topic_id": card.topic_id,
                "due": card.due,
                "retrievability": retention,
                "stability": card.stability,
                "difficulty": card.difficulty,
                "state": card.state,
                "scheduled_days": card.scheduled_days,
                "overdue_days": overdue_days,
                "priority": priority,
                "card": card.to_dict(),
            }
        )
    return sorted(
        due_reviews,
        key=lambda item: (-float(item["priority"]), float(item["retrievability"])),
    )


class FSRSBridge:
    def __init__(self, retention_target: float = 0.90) -> None:
        self.retention_target = _clamp(retention_target, 0.1, 0.99)

    def new_knowledge_card(
        self, topic_id: str, now: datetime | None = None
    ) -> StudyFsrsCard:
        return create_card(topic_id, now)

    def rate_answer(
        self,
        card: StudyFsrsCard | dict[str, Any],
        rating: StudyFsrsRating | int,
        now: datetime | None = None,
    ) -> tuple[StudyFsrsCard, dict[str, Any]]:
        return rate_answer(card, rating, now)

    def get_due_reviews(
        self,
        cards: Iterable[StudyFsrsCard | dict[str, Any]],
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        return get_due_reviews(cards, now, retention_target=self.retention_target)


__all__ = [
    "FSRSBridge",
    "StudyFsrsCard",
    "StudyFsrsRating",
    "create_card",
    "get_due_reviews",
    "rate_answer",
    "retrievability",
]
