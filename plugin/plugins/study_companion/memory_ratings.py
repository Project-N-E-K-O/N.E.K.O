from __future__ import annotations

from .fsrs_bridge import StudyFsrsRating


WORD_ERROR_RATINGS = {
    "unknown_word": StudyFsrsRating.Again,
    "spelling": StudyFsrsRating.Hard,
    "meaning_confused": StudyFsrsRating.Hard,
    "example_misunderstood": StudyFsrsRating.Good,
    "correct": StudyFsrsRating.Easy,
}


def rating_from_word_result(
    error_type: str, *, correct: bool | None = None
) -> StudyFsrsRating:
    if correct is True:
        return StudyFsrsRating.Easy
    normalized = str(error_type or "").strip().lower()
    return WORD_ERROR_RATINGS.get(
        normalized, StudyFsrsRating.Good if correct else StudyFsrsRating.Again
    )


def rating_from_recitation_score(score: float) -> StudyFsrsRating:
    value = max(0.0, min(1.0, float(score or 0.0)))
    if value >= 0.92:
        return StudyFsrsRating.Easy
    if value >= 0.70:
        return StudyFsrsRating.Good
    if value >= 0.40:
        return StudyFsrsRating.Hard
    return StudyFsrsRating.Again


def normalize_rating(value: str | int | StudyFsrsRating) -> StudyFsrsRating:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "again": StudyFsrsRating.Again,
            "forgot": StudyFsrsRating.Again,
            "unknown_word": StudyFsrsRating.Again,
            "hard": StudyFsrsRating.Hard,
            "spelling": StudyFsrsRating.Hard,
            "meaning_confused": StudyFsrsRating.Hard,
            "good": StudyFsrsRating.Good,
            "example_misunderstood": StudyFsrsRating.Good,
            "easy": StudyFsrsRating.Easy,
            "correct": StudyFsrsRating.Easy,
        }
        if normalized in aliases:
            return aliases[normalized]
    try:
        return StudyFsrsRating(int(value))
    except (TypeError, ValueError):
        return StudyFsrsRating.Good


__all__ = [
    "WORD_ERROR_RATINGS",
    "normalize_rating",
    "rating_from_recitation_score",
    "rating_from_word_result",
]
