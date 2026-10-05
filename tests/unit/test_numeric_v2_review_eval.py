"""普通正文复核固定评测器的冻结样本与计分测试。"""  # noqa: DOCSTRING_CJK

from scripts.evaluate_numeric_v2_review import (
    FIXED_CASES,
    FIXTURE_VERSION,
    _evaluation_story,
    summarize_rows,
)
from services.theater.numeric_v2_runtime import NumericV2Engine


def test_review_eval_fixture_is_frozen_balanced_and_compiles():
    """固定集同时覆盖合法正文、三类正文拒绝、邀请和推荐安全。"""  # noqa: DOCSTRING_CJK

    assert FIXTURE_VERSION == "ordinary_review_zh_cn_v2"
    assert len(FIXED_CASES) == 14
    assert len({case.case_id for case in FIXED_CASES}) == len(FIXED_CASES)
    assert sum(case.expect_body_rejection for case in FIXED_CASES) == 4
    assert sum(case.expect_offer_present and case.expect_valid for case in FIXED_CASES) == 1
    assert sum(case.expect_offer_present and not case.expect_valid for case in FIXED_CASES) == 1
    assert sum(bool(case.expected_unsafe_indexes) for case in FIXED_CASES) == 2
    assert [case.case_id for case in FIXED_CASES if case.check_missed_initiation] == [
        "legal_explicit_movement"
    ]
    assert [case.case_id for case in FIXED_CASES if case.expect_missed_initiation] == [
        "legal_explicit_movement"
    ]
    NumericV2Engine.from_mapping(_evaluation_story())


def test_review_eval_reports_false_reject_false_accept_and_effective_result():
    """原始与框架有效结果必须分开统计，并列出误杀与漏放样本。"""  # noqa: DOCSTRING_CJK

    rows = [
        {
            "case_id": "legal",
            "expect_body_rejection": False,
            "expect_offer_present": False,
            "expect_valid": False,
            "expected_unsafe_indexes": [],
            "expect_missed_initiation": False,
            "raw_body_violations": ["player_action"],
            "raw_body_contract_match": False,
            "raw_offer_present": False,
            "raw_valid": False,
            "raw_unsafe_indexes": [],
            "raw_missed_initiation": False,
            "effective_body_violations": [],
            "effective_body_contract_match": True,
            "effective_offer_present": False,
            "effective_valid": False,
            "effective_unsafe_indexes": [],
            "effective_missed_initiation": False,
            "duration_ms": 100.0,
            "error": "",
        },
        {
            "case_id": "illegal",
            "expect_body_rejection": True,
            "expect_offer_present": False,
            "expect_valid": False,
            "expected_unsafe_indexes": [],
            "expect_missed_initiation": False,
            "raw_body_violations": [],
            "raw_body_contract_match": False,
            "raw_offer_present": False,
            "raw_valid": False,
            "raw_unsafe_indexes": [],
            "raw_missed_initiation": False,
            "effective_body_violations": [],
            "effective_body_contract_match": False,
            "effective_offer_present": False,
            "effective_valid": False,
            "effective_unsafe_indexes": [],
            "effective_missed_initiation": False,
            "duration_ms": 300.0,
            "error": "",
        },
    ]

    summary = summarize_rows(rows)

    assert summary["raw"]["false_reject_case_ids"] == ["legal"]
    assert summary["raw"]["false_accept_case_ids"] == ["illegal"]
    assert summary["effective"]["false_reject_case_ids"] == []
    assert summary["effective"]["false_accept_case_ids"] == ["illegal"]
    assert summary["raw"]["exact_matches"] == 0
    assert summary["effective"]["exact_matches"] == 1
    assert summary["raw"]["missed_initiation_recovery"]["accuracy"] == 1.0
    assert summary["duration_ms"] == {"min": 100.0, "median": 200.0, "max": 300.0}
