from __future__ import annotations

import pytest

from plugin.plugins.study_companion.fsrs_bridge import StudyFsrsRating
from plugin.plugins.study_companion.memory_ratings import (
    WORD_ERROR_RATINGS,
    normalize_rating,
    rating_from_recitation_score,
    rating_from_word_result,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("error_type", "correct", "expected"),
    [
        ("unknown_word", None, StudyFsrsRating.Again),
        ("spelling", None, StudyFsrsRating.Hard),
        ("meaning_confused", None, StudyFsrsRating.Hard),
        ("example_misunderstood", None, StudyFsrsRating.Good),
        ("correct", True, StudyFsrsRating.Easy),
        ("unlisted", False, StudyFsrsRating.Again),
        ("unlisted", None, StudyFsrsRating.Again),
    ],
)
def test_rating_from_word_result_maps_errors_and_correct_override(
    error_type: str, correct: bool | None, expected: StudyFsrsRating
) -> None:
    assert rating_from_word_result(error_type, correct=correct) == expected
    assert WORD_ERROR_RATINGS["correct"] == StudyFsrsRating.Easy


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (-1.0, StudyFsrsRating.Again),
        (0.39, StudyFsrsRating.Again),
        (0.40, StudyFsrsRating.Hard),
        (0.70, StudyFsrsRating.Good),
        (0.92, StudyFsrsRating.Easy),
        (2.0, StudyFsrsRating.Easy),
    ],
)
def test_rating_from_recitation_score_clamps_and_buckets(
    score: float, expected: StudyFsrsRating
) -> None:
    assert rating_from_recitation_score(score) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("forgot", StudyFsrsRating.Again),
        ("spelling", StudyFsrsRating.Hard),
        ("example_misunderstood", StudyFsrsRating.Good),
        ("correct", StudyFsrsRating.Easy),
        (4, StudyFsrsRating.Easy),
        (StudyFsrsRating.Hard, StudyFsrsRating.Hard),
        ("not-a-rating", StudyFsrsRating.Good),
    ],
)
def test_normalize_rating_accepts_aliases_numbers_and_defaults(
    value: str | int | StudyFsrsRating, expected: StudyFsrsRating
) -> None:
    assert normalize_rating(value) == expected
