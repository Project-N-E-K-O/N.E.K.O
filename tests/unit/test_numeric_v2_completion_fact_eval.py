"""Frozen samples and scoring tests for the completion-fact evaluator."""  # noqa: DOCSTRING_CJK

from scripts.evaluate_numeric_v2_completion_facts import (
    CIVILIANS_SHELTERED,
    FIXED_CASES,
    FIXTURE_VERSION,
    INJURED_FREED,
    _evaluation_story,
    summarize_rows,
)
from services.theater.numeric_v2_runtime import NumericV2Engine


def test_completion_fact_eval_fixture_is_frozen_and_compiles():
    """The fixed set must cover positive, negative, counting, and evidence-source boundaries."""  # noqa: DOCSTRING_CJK

    assert FIXTURE_VERSION == "completion_fact_review_zh_cn_v3"
    assert len(FIXED_CASES) == 10
    assert len({case.case_id for case in FIXED_CASES}) == len(FIXED_CASES)
    assert sum(bool(case.expected_keys) for case in FIXED_CASES) == 4
    assert sum(not case.expected_keys for case in FIXED_CASES) == 6
    assert {key for case in FIXED_CASES for key in case.expected_keys} == {
        INJURED_FREED,
        CIVILIANS_SHELTERED,
    }
    NumericV2Engine.from_mapping(_evaluation_story())


def test_completion_fact_eval_reports_false_positive_and_false_negative():
    """Calculate false memories and omissions separately for model proposals and Runtime acceptance."""  # noqa: DOCSTRING_CJK

    rows = [
        {
            "case_id": "positive",
            "expected_keys": [INJURED_FREED],
            "proposed_keys": [INJURED_FREED],
            "accepted_keys": [],
            "duration_ms": 100.0,
            "error": "",
        },
        {
            "case_id": "negative",
            "expected_keys": [],
            "proposed_keys": [CIVILIANS_SHELTERED],
            "accepted_keys": [CIVILIANS_SHELTERED],
            "duration_ms": 300.0,
            "error": "",
        },
    ]

    summary = summarize_rows(rows)

    assert summary["proposed"] == {
        "true_positive": 1,
        "false_positive": 1,
        "false_negative": 0,
        "precision": 0.5,
        "recall": 1.0,
        "exact_match_rate": 0.5,
    }
    assert summary["accepted"] == {
        "true_positive": 0,
        "false_positive": 1,
        "false_negative": 1,
        "precision": 0.0,
        "recall": 0.0,
        "exact_match_rate": 0.0,
    }
    assert summary["duration_ms"] == {"min": 100.0, "median": 200.0, "max": 300.0}
