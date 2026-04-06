import os
import sys

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_routers.system_router import _normalize_emotion_label


@pytest.mark.parametrize(
    ("raw_emotion", "expected"),
    [
        ("не грустно", "neutral"),
        ("не счастлива", "neutral"),
        ("안 행복", "neutral"),
        ("행복하지 않다", "neutral"),
    ],
)
def test_normalize_emotion_label_preserves_localized_negation(raw_emotion, expected):
    assert _normalize_emotion_label(raw_emotion, 0.95) == expected


@pytest.mark.parametrize(
    ("raw_emotion", "expected"),
    [
        ("грустно", "sad"),
        ("행복해", "happy"),
    ],
)
def test_normalize_emotion_label_keeps_positive_localized_aliases(raw_emotion, expected):
    assert _normalize_emotion_label(raw_emotion, 0.95) == expected
