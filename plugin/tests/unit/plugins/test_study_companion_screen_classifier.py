from __future__ import annotations

import pytest

from plugin.plugins.study_companion.screen_classifier import (
    ScreenClassification,
    classify_screen_from_ocr,
    normalize_screen_type,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("blank", "idle"),
        ("lesson", "reading"),
        ("quiz", "question"),
        ("answer", "answering"),
        ("evaluation", "review"),
        ("notes", "notes"),
        ("unknown", "idle"),
    ],
)
def test_normalize_screen_type_accepts_aliases(alias: str, expected: str) -> None:
    assert normalize_screen_type(alias) == expected


@pytest.mark.parametrize(
    ("text", "title", "expected"),
    [
        ("Chapter 1 definition concept explanation", "Lesson", "reading"),
        ("Question: Why does this happen?", "Quiz", "question"),
        ("Submit answer and view score feedback", "Answer Review", "answering"),
        ("Wrong mistake retry review note", "Review", "review"),
        ("My notes outline memo", "Notebook", "notes"),
        ("Session summary recap", "Summary", "summary"),
        ("", "", "idle"),
    ],
)
def test_classify_screen_from_ocr_covers_core_scene_types(
    text: str, title: str, expected: str
) -> None:
    result = classify_screen_from_ocr(text, window_title=title)

    assert result.screen_type == expected
    assert 0.0 <= result.confidence <= 1.0
    assert result.to_payload()["screen_type"] == expected


def test_classify_screen_from_ocr_uses_recent_majority_to_smooth_low_confidence() -> None:
    recent = [
        ScreenClassification(screen_type="question", confidence=0.72),
        {"screen_type": "question", "confidence": 0.70},
        {"screen_type": "question", "screen_confidence": 0.68},
        {"not": "classification"},
    ]

    result = classify_screen_from_ocr("short", recent_classifications=recent)

    assert result.screen_type == "question"
    assert result.signals["smoothed_from"] == "question"
    assert "smoothed" in result.reason
