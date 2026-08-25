from __future__ import annotations

import pytest

from plugin.plugins.study_companion.practice_scope import (
    PracticeScopeError,
    build_practice_scope,
    filter_question_params_to_scope,
    ordered_scope_topics,
    practice_scope_matches_topic,
)


pytestmark = pytest.mark.unit


def test_filter_question_params_deduplicates_primary_retry_candidate() -> None:
    candidate = {"id": "wrong-1", "topic_id": "topic-1"}
    filtered = filter_question_params_to_scope(
        {
            "retry_wrong_question": candidate,
            "retry_wrong_questions": [candidate],
        },
        {"topic-1"},
    )

    assert filtered["retry_wrong_questions"] == [candidate]


def _topic(
    topic_id: str,
    *,
    stage: str = "junior_high",
    subject: str = "math",
    chapter: str = "Real numbers",
    unit: str = "Foundations",
    course_family: str = "",
    depth: int = 1,
    difficulty: float = 0.3,
) -> dict[str, object]:
    return {
        "id": topic_id,
        "name": topic_id.replace("_", " ").title(),
        "stage": stage,
        "subject": subject,
        "chapter": chapter,
        "unit": unit,
        "course_family": course_family,
        "depth": depth,
        "difficulty": difficulty,
        "examples": [{"prompt": f"Practice {topic_id}"}],
        "question_types": ["concept"],
    }


def test_build_practice_scope_canonicalizes_unit_and_generates_stable_key() -> None:
    topics = [_topic("absolute_value"), _topic("number_axis", depth=2)]
    requested = {
        "schema_version": 1,
        "mode": "explicit_scope",
        "stage": " junior-high ",
        "subject": " MATH ",
        "chapter": " Real numbers ",
        "unit": " Foundations ",
    }

    first = build_practice_scope(requested, topics, revision=7)
    second = build_practice_scope(requested, list(reversed(topics)), revision=8)

    assert first.stage == "junior_high"
    assert first.subject == "math"
    assert first.chapter == "Real numbers"
    assert first.unit == "Foundations"
    assert first.scope_revision == 7
    assert first.scope_key == second.scope_key
    assert first.display_path == [
        "junior_high",
        "math",
        "Real numbers",
        "Foundations",
    ]
    assert first.eligible_topic_ids == ["absolute_value", "number_axis"]
    assert "eligible_topic_ids" not in first.to_public_dict()


def test_explicit_topic_is_validated_against_the_complete_scope() -> None:
    selected = _topic("shared_name", stage="senior_high", subject="physics")
    same_name_other_scope = _topic(
        "shared_name_college",
        stage="college",
        subject="physics",
        chapter="College physics",
        unit="Electromagnetism",
        course_family="physics",
    )

    scope = build_practice_scope(
        {
            "schema_version": 1,
            "mode": "explicit_topic",
            "stage": "senior_high",
            "subject": "physics",
            "topic_id": "shared_name",
        },
        [selected, same_name_other_scope],
        revision=1,
    )

    assert scope.topic_id == "shared_name"
    assert scope.eligible_topic_ids == ["shared_name"]
    assert practice_scope_matches_topic(scope, selected)
    assert not practice_scope_matches_topic(scope, same_name_other_scope)


def test_college_scope_requires_course_family_and_empty_scope_never_falls_back() -> None:
    college_topic = _topic(
        "college_stack",
        stage="college",
        subject="computer_science",
        chapter="Data structures",
        unit="Stacks",
        course_family="data_structures",
    )

    with pytest.raises(PracticeScopeError) as missing_family:
        build_practice_scope(
            {
                "mode": "explicit_scope",
                "stage": "college",
                "subject": "computer_science",
            },
            [college_topic],
            revision=1,
        )
    assert missing_family.value.code == "INVALID_PRACTICE_SCOPE"

    with pytest.raises(PracticeScopeError) as empty:
        build_practice_scope(
            {
                "mode": "explicit_scope",
                "stage": "college",
                "subject": "computer_science",
                "course_family": "calculus",
            },
            [college_topic],
            revision=1,
        )
    assert empty.value.code == "NO_TOPICS_IN_SCOPE"


def test_scope_filter_removes_every_out_of_scope_candidate_source() -> None:
    allowed = {"absolute_value", "number_axis"}
    params = {
        "target_topic_id": "outside",
        "retry_wrong_question": {"topic_id": "outside"},
        "retry_wrong_questions": [
            {"topic_id": "outside"},
            {"topic_id": "absolute_value"},
        ],
        "due_reviews": [
            {"topic_id": "outside"},
            {"topic_id": "number_axis"},
        ],
        "weak_topics": [
            {"topic_id": "outside"},
            {"topic_id": "absolute_value"},
        ],
        "candidate_evidence": [
            {"id": "evidence-outside", "payload_summary": {"topic_id": "outside"}},
            {"id": "evidence-inside", "payload_summary": {"topic_id": "number_axis"}},
        ],
    }

    filtered = filter_question_params_to_scope(params, allowed)

    assert filtered["target_topic_id"] == ""
    assert filtered["retry_wrong_question"]["topic_id"] == "absolute_value"
    assert [item["topic_id"] for item in filtered["due_reviews"]] == ["number_axis"]
    assert [item["topic_id"] for item in filtered["weak_topics"]] == ["absolute_value"]
    assert filtered["candidate_evidence"] == [
        {"id": "evidence-inside", "payload_summary": {"topic_id": "number_axis"}}
    ]


def test_cold_start_order_is_unattempted_then_depth_difficulty_and_id() -> None:
    topics = [
        _topic("attempted_easy", depth=1, difficulty=0.1),
        _topic("later", depth=2, difficulty=0.1),
        _topic("harder", depth=1, difficulty=0.5),
        _topic("alpha", depth=1, difficulty=0.2),
    ]

    ordered = ordered_scope_topics(topics, attempted_topic_ids={"attempted_easy"})

    assert [topic["id"] for topic in ordered] == [
        "alpha",
        "harder",
        "later",
        "attempted_easy",
    ]
