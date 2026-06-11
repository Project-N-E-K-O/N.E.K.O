from __future__ import annotations

from pathlib import Path

import pytest

from plugin.plugins.study_companion.knowledge_quality import (
    KnowledgeCandidateStatus,
    KnowledgeCandidateType,
    KnowledgeEvidenceType,
    KnowledgeQualityStore,
)
from plugin.plugins.study_companion.knowledge_tracker import KnowledgeGraph, KnowledgeTracker
from plugin.plugins.study_companion.store import StudyStore

pytestmark = pytest.mark.unit


class _Logger:
    def warning(self, *args, **kwargs):
        return None


def _store(tmp_path: Path) -> StudyStore:
    seed = Path(__file__).resolve().parents[3] / "plugins" / "study_companion" / "static" / "knowledge_graph_seed.json"
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger(), seed)
    store.open()
    return store


def test_candidates_upsert_and_duplicate_keys_merge(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        quality = KnowledgeQualityStore(store)
        first = quality.upsert_candidate(
            KnowledgeCandidateType.TOPIC.value,
            {"subject": "math", "name": "Vector Projection", "topic_id": "vector_projection"},
            "llm",
            KnowledgeEvidenceType.MENTIONED.value,
            {"source": "llm"},
        )
        second = quality.upsert_candidate(
            KnowledgeCandidateType.TOPIC.value,
            {"subject": "math", "name": "vector projection", "topic_id": "vector_projection"},
            "wrong_question",
            KnowledgeEvidenceType.MENTIONED.value,
            {"source": "wrong_question"},
        )

        assert second["id"] == first["id"]
        assert len(quality.list_candidates(item_type=KnowledgeCandidateType.TOPIC.value)) == 1

        edge = quality.upsert_candidate(
            KnowledgeCandidateType.EDGE.value,
            {"from_topic_id": "a", "to_topic_id": "b", "relation": "prerequisite"},
            "llm",
            KnowledgeEvidenceType.MENTIONED.value,
            {},
        )
        misconception = quality.upsert_candidate(
            KnowledgeCandidateType.MISCONCEPTION.value,
            {"topic_id": "a", "misconception_key": "sign_reversal"},
            "eval",
            KnowledgeEvidenceType.MENTIONED.value,
            {},
        )
        question_type = quality.upsert_candidate(
            KnowledgeCandidateType.QUESTION_TYPE.value,
            {"topic_id": "a", "question_type_key": "single_step"},
            "eval",
            KnowledgeEvidenceType.MENTIONED.value,
            {},
        )
        assert edge["item_type"] == "edge"
        assert misconception["item_type"] == "misconception"
        assert question_type["item_type"] == "question_type"
    finally:
        store.close()


def test_detect_duplicate_or_reverse_edge_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        quality = KnowledgeQualityStore(store)
        edge = quality.upsert_candidate(
            KnowledgeCandidateType.EDGE.value,
            {"from_topic_id": "a", "to_topic_id": "b", "relation": "prerequisite"},
            "llm",
            KnowledgeEvidenceType.MENTIONED.value,
            {},
        )

        duplicate = quality.detect_duplicate_or_conflict(
            {
                "item_type": KnowledgeCandidateType.EDGE.value,
                "from_topic_id": "a",
                "to_topic_id": "b",
                "relation": "prerequisite",
            }
        )
        conflict = quality.detect_duplicate_or_conflict(
            {
                "item_type": KnowledgeCandidateType.EDGE.value,
                "from_topic_id": "b",
                "to_topic_id": "a",
                "relation": "prerequisite",
            }
        )

        assert duplicate["dedupe_key"] == "a:b:prerequisite"
        assert duplicate["duplicate"]["id"] == edge["id"]
        assert conflict["dedupe_key"] == "b:a:prerequisite"
        assert conflict["conflict"]["id"] == edge["id"]
    finally:
        store.close()


def test_evidence_recomputes_score_and_lifecycle_thresholds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        quality = KnowledgeQualityStore(store)
        item = quality.upsert_candidate(
            KnowledgeCandidateType.QUESTION_TYPE.value,
            {"topic_id": "quadratic_vertex_form", "question_type_key": "transfer"},
            "llm",
            KnowledgeEvidenceType.MENTIONED.value,
            {"source": "llm"},
        )
        quality.add_evidence(item["id"], KnowledgeEvidenceType.ANSWER_IMPROVED.value, 3.0, {"source": "eval"})
        quality.add_evidence(item["id"], KnowledgeEvidenceType.USER_CONFIRMED.value, 3.0, {"source": "user"})
        active = quality.promote_or_deprecate(item["id"])
        assert active["status"] == KnowledgeCandidateStatus.ACTIVE.value
        assert active["score"] >= 0.35

        for _ in range(4):
            quality.add_evidence(item["id"], KnowledgeEvidenceType.USED_IN_PROMPT.value, 0.35, {"source": "prompt"})
        for _ in range(2):
            quality.add_evidence(item["id"], KnowledgeEvidenceType.REVIEW_RETAINED.value, 1.0, {"source": "review"})
        trusted = quality.promote_or_deprecate(item["id"])
        assert trusted["status"] == KnowledgeCandidateStatus.TRUSTED.value
        assert trusted["positive_count"] >= 4

        summary = quality.status_summary(limit=3)
        assert summary["total"] >= 1
        assert summary["by_status"][KnowledgeCandidateStatus.TRUSTED.value] >= 1
        assert summary["recent_evidence"]

        prompt_summary = quality.prompt_evidence_summary(topic_id="quadratic_vertex_form")
        assert prompt_summary
        assert prompt_summary[0]["status"] == KnowledgeCandidateStatus.TRUSTED.value
        assert prompt_summary[0]["payload_summary"]["question_type_key"] == "transfer"
    finally:
        store.close()


def test_prompt_evidence_summary_includes_edges_when_topic_is_to_topic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        quality = KnowledgeQualityStore(store)
        edge = quality.upsert_candidate(
            KnowledgeCandidateType.EDGE.value,
            {
                "from_topic_id": "linear_function_kb",
                "to_topic_id": "quadratic_vertex_form",
                "relation": "prerequisite",
            },
            "llm",
            KnowledgeEvidenceType.MENTIONED.value,
            {"source": "llm"},
        )
        quality.add_evidence(edge["id"], KnowledgeEvidenceType.ANSWER_IMPROVED.value, 3.0, {"source": "eval"})
        quality.add_evidence(edge["id"], KnowledgeEvidenceType.USER_CONFIRMED.value, 3.0, {"source": "user"})

        summary = quality.prompt_evidence_summary(topic_id="quadratic_vertex_form")

        assert any(item["id"] == edge["id"] for item in summary)
        edge_summary = next(item for item in summary if item["id"] == edge["id"])
        assert edge_summary["payload_summary"]["to_topic_id"] == "quadratic_vertex_form"
    finally:
        store.close()


def test_prompt_evidence_summary_filters_topic_before_limit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        quality = KnowledgeQualityStore(store)
        target = quality.upsert_candidate(
            KnowledgeCandidateType.QUESTION_TYPE.value,
            {"topic_id": "quadratic_vertex_form", "question_type_key": "older_matching"},
            "llm",
            KnowledgeEvidenceType.MENTIONED.value,
            {"source": "llm"},
        )
        quality.add_evidence(target["id"], KnowledgeEvidenceType.ANSWER_IMPROVED.value, 3.0, {"source": "eval"})
        quality.add_evidence(target["id"], KnowledgeEvidenceType.USER_CONFIRMED.value, 3.0, {"source": "user"})

        for index in range(6):
            unrelated = quality.upsert_candidate(
                KnowledgeCandidateType.QUESTION_TYPE.value,
                {"topic_id": f"unrelated_{index}", "question_type_key": f"newer_{index}"},
                "llm",
                KnowledgeEvidenceType.MENTIONED.value,
                {"source": "llm"},
            )
            quality.add_evidence(unrelated["id"], KnowledgeEvidenceType.ANSWER_IMPROVED.value, 3.0, {"source": "eval"})
            quality.add_evidence(unrelated["id"], KnowledgeEvidenceType.USER_CONFIRMED.value, 3.0, {"source": "user"})

        summary = quality.prompt_evidence_summary(topic_id="quadratic_vertex_form", limit=3)

        assert [item["id"] for item in summary] == [target["id"]]
        assert summary[0]["payload_summary"]["question_type_key"] == "older_matching"
    finally:
        store.close()


def test_trusted_candidate_can_deprecate_on_strong_negative_evidence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        quality = KnowledgeQualityStore(store, trusted_negative_threshold=2)

        item = quality.upsert_candidate(
            KnowledgeCandidateType.QUESTION_TYPE.value,
            {"topic_id": "quadratic_vertex_form", "question_type_key": "trusted_negative"},
            "llm",
            KnowledgeEvidenceType.MENTIONED.value,
            {"source": "llm"},
        )
        quality.add_evidence(item["id"], KnowledgeEvidenceType.ANSWER_IMPROVED.value, 3.0, {"source": "eval"})
        quality.add_evidence(item["id"], KnowledgeEvidenceType.USER_CONFIRMED.value, 3.0, {"source": "user"})
        for _ in range(4):
            quality.add_evidence(item["id"], KnowledgeEvidenceType.USED_IN_PROMPT.value, 0.35, {"source": "prompt"})
        trusted = quality.promote_or_deprecate(item["id"])
        assert trusted["status"] == KnowledgeCandidateStatus.TRUSTED.value

        quality.add_evidence(item["id"], KnowledgeEvidenceType.CONFLICT_DETECTED.value, -1.0, {"source": "review"})
        still_trusted = store.get_candidate_item(item["id"])
        assert still_trusted is not None
        assert still_trusted["status"] == KnowledgeCandidateStatus.TRUSTED.value

        quality.add_evidence(item["id"], KnowledgeEvidenceType.CONFLICT_DETECTED.value, -1.0, {"source": "review"})
        deprecated = store.get_candidate_item(item["id"])
        assert deprecated is not None
        assert deprecated["status"] == KnowledgeCandidateStatus.DEPRECATED.value

        rejected = quality.upsert_candidate(
            KnowledgeCandidateType.MISCONCEPTION.value,
            {"topic_id": "quadratic_vertex_form", "misconception_key": "trusted_rejected"},
            "llm",
            KnowledgeEvidenceType.MENTIONED.value,
            {"source": "llm"},
        )
        quality.add_evidence(rejected["id"], KnowledgeEvidenceType.ANSWER_IMPROVED.value, 3.0, {"source": "eval"})
        quality.add_evidence(rejected["id"], KnowledgeEvidenceType.USER_CONFIRMED.value, 3.0, {"source": "user"})
        for _ in range(4):
            quality.add_evidence(rejected["id"], KnowledgeEvidenceType.USED_IN_PROMPT.value, 0.35, {"source": "prompt"})
        assert quality.promote_or_deprecate(rejected["id"])["status"] == KnowledgeCandidateStatus.TRUSTED.value

        quality.add_evidence(rejected["id"], KnowledgeEvidenceType.USER_REJECTED.value, -1.0, {"source": "user"})
        deprecated_rejected = store.get_candidate_item(rejected["id"])
        assert deprecated_rejected is not None
        assert deprecated_rejected["status"] == KnowledgeCandidateStatus.DEPRECATED.value
    finally:
        store.close()


def test_negative_evidence_deprecates_candidate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        quality = KnowledgeQualityStore(store)
        item = quality.upsert_candidate(
            KnowledgeCandidateType.MISCONCEPTION.value,
            {"topic_id": "linear_equation", "misconception_key": "fake_rule"},
            "llm",
            KnowledgeEvidenceType.MENTIONED.value,
            {},
        )
        quality.add_evidence(item["id"], KnowledgeEvidenceType.USER_REJECTED.value, -1.0, {"source": "user"})
        deprecated = quality.add_evidence(item["id"], KnowledgeEvidenceType.CONFLICT_DETECTED.value, -1.0, {"source": "review"})
        assert store.get_candidate_item(item["id"])["status"] == KnowledgeCandidateStatus.DEPRECATED.value
        assert deprecated["event_type"] == KnowledgeEvidenceType.CONFLICT_DETECTED.value
    finally:
        store.close()


def test_answer_tracking_persists_discovered_runtime_topic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        assert store.get_topic("new_runtime_topic") is None
        graph = KnowledgeGraph(store)
        candidate_id = graph.discover_candidate("New Runtime Topic", {"source": "llm", "subject": "math"})
        assert candidate_id
        assert store.find_topic_by_name("New Runtime Topic") is None
        assert store.get_topic("new_runtime_topic") is None

        tracker = KnowledgeTracker(store)
        result = tracker.on_answer(
            topic_id="New Runtime Topic",
            question={"question": "Q", "answer": "A", "topic": "New Runtime Topic"},
            user_answer="B",
            eval_result={"verdict": "wrong", "score": 0, "error_type": "misconception"},
            mode="interactive",
        )
        assert store.find_topic_by_name("New Runtime Topic") is not None
        assert store.get_topic("new_runtime_topic") is not None
        assert result["mastery"]["topic_id"] == "new_runtime_topic"
    finally:
        store.close()
