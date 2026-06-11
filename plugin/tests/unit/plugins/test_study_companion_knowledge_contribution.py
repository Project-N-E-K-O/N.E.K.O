from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from plugin.plugins.study_companion.knowledge_contribution import PublicGraphContributionBuilder
from plugin.plugins.study_companion.knowledge_quality import KnowledgeCandidateType, KnowledgeEvidenceType, KnowledgeQualityStore
from plugin.plugins.study_companion.models import StudyConfig
from plugin.plugins.study_companion.store import StudyStore


class _Logger:
    def warning(self, *args, **kwargs):
        return None


def _store(tmp_path: Path) -> StudyStore:
    seed = Path(__file__).resolve().parents[3] / "plugins" / "study_companion" / "static" / "knowledge_graph_seed.json"
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger(), seed)
    store.open()
    return store


def _seed_question_type(quality: KnowledgeQualityStore) -> dict:
    item = quality.upsert_candidate(
        KnowledgeCandidateType.QUESTION_TYPE.value,
        {"topic_id": "quadratic_vertex_form", "question_type_key": "single_step", "text": "raw source should hash only"},
        "llm",
        KnowledgeEvidenceType.MENTIONED.value,
        {"source": "llm"},
    )
    quality.add_evidence(item["id"], KnowledgeEvidenceType.ANSWER_IMPROVED.value, 1.0, {"source": "eval"})
    quality.add_evidence(item["id"], KnowledgeEvidenceType.REVIEW_RETAINED.value, 1.0, {"source": "review"})
    return quality.promote_or_deprecate(item["id"])


def test_builds_anonymous_stats_without_raw_learning_text(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        quality = KnowledgeQualityStore(store)
        _seed_question_type(quality)
        edge = quality.upsert_candidate(
            KnowledgeCandidateType.EDGE.value,
            {"from_topic_id": "a", "to_topic_id": "b", "relation": "prerequisite"},
            "llm",
            KnowledgeEvidenceType.MENTIONED.value,
            {},
        )
        quality.add_evidence(edge["id"], KnowledgeEvidenceType.USER_CONFIRMED.value, 1.0, {})

        builder = PublicGraphContributionBuilder(store, StudyConfig(knowledge_contribution_min_sample_count=2))
        stats = builder.build_anonymous_stats(min_sample_count=2)

        assert stats
        persisted = store.list_anonymous_knowledge_stats(limit=10)
        assert persisted
        for stat in stats:
            payload = stat["payload"]
            assert "question" not in payload
            assert "text" not in payload
            assert "user_answer" not in payload
            assert set(payload).issubset(
                {
                    "topic_id",
                    "edge",
                    "misconception_key",
                    "question_type_key",
                    "evidence_count",
                    "positive_count",
                    "negative_count",
                    "conflict_count",
                    "score_bucket",
                }
            )
    finally:
        store.close()


def test_topic_refs_are_anonymized_in_contribution_payloads(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        quality = KnowledgeQualityStore(store)
        raw_topic = "alice_private_calculus_goal"
        raw_related = "bob_secret_prerequisite"
        topic = quality.upsert_candidate(
            KnowledgeCandidateType.TOPIC.value,
            {"topic_id": raw_topic, "name": "Alice Private Calculus Goal"},
            "llm",
            KnowledgeEvidenceType.MENTIONED.value,
            {},
        )
        edge = quality.upsert_candidate(
            KnowledgeCandidateType.EDGE.value,
            {"from_topic_id": raw_related, "to_topic_id": raw_topic, "relation": "prerequisite"},
            "llm",
            KnowledgeEvidenceType.MENTIONED.value,
            {},
        )
        misconception = quality.upsert_candidate(
            KnowledgeCandidateType.MISCONCEPTION.value,
            {"topic_id": raw_topic, "misconception_key": "sign_error"},
            "llm",
            KnowledgeEvidenceType.MENTIONED.value,
            {},
        )
        for item in (topic, edge, misconception):
            quality.add_evidence(item["id"], KnowledgeEvidenceType.USER_CONFIRMED.value, 1.0, {})

        stats = PublicGraphContributionBuilder(store, StudyConfig()).build_anonymous_stats(min_sample_count=1)
        rendered = json.dumps(stats, ensure_ascii=False)

        assert raw_topic not in rendered
        assert raw_related not in rendered
        assert "topic:" in rendered
        for stat in stats:
            payload = stat["payload"]
            if "topic_id" in payload:
                assert payload["topic_id"].startswith("topic:")
            if "edge" in payload:
                assert payload["edge"]["from_topic_id"].startswith("topic:")
                assert payload["edge"]["to_topic_id"].startswith("topic:")
    finally:
        store.close()


def test_min_sample_stats_are_not_enqueued_and_opt_in_defaults_off(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        quality = KnowledgeQualityStore(store)
        _seed_question_type(quality)
        builder = PublicGraphContributionBuilder(store, StudyConfig())
        stats = builder.build_anonymous_stats(min_sample_count=4)
        assert all(not stat["min_sample_met"] for stat in stats)
        result = builder.enqueue_snapshot(stats)
        assert result["queued"] is False
        assert store.list_knowledge_contribution_queue() == []

        stats = builder.build_anonymous_stats(min_sample_count=3)
        result = builder.enqueue_snapshot(stats)
        assert result["queued"] is False
        assert result["status"] == "preview"
        assert store.list_knowledge_contribution_queue() == []
    finally:
        store.close()


def test_opt_in_enqueue_and_clear_queue(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        quality = KnowledgeQualityStore(store)
        _seed_question_type(quality)
        builder = PublicGraphContributionBuilder(
            store,
            StudyConfig(knowledge_contribution_opt_in=True, knowledge_contribution_min_sample_count=3),
        )
        stats = builder.build_anonymous_stats(min_sample_count=3)
        result = builder.enqueue_snapshot(stats)
        assert result["queued"] is True
        assert builder.list_queue()
        assert builder.clear_queue() == 1
        assert builder.list_queue() == []
    finally:
        store.close()


def test_contribution_queue_trims_per_status(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        for index in range(3):
            store.enqueue_knowledge_contribution_snapshot(
                stats=[{"stat_type": "topic", "stat_key": f"topic:{index}"}],
                status="queued",
                history_limit=2,
            )
        store.enqueue_knowledge_contribution_snapshot(
            stats=[{"stat_type": "topic", "stat_key": "topic:preview"}],
            status="preview",
            history_limit=2,
        )

        queue = store.list_knowledge_contribution_queue(limit=10)
        queued = [item for item in queue if item["status"] == "queued"]
        preview = [item for item in queue if item["status"] == "preview"]

        assert len(queued) == 2
        assert len(preview) == 1
        assert {item["stats"][0]["stat_key"] for item in queued} == {"topic:1", "topic:2"}
    finally:
        store.close()


def test_preview_builds_local_stats_summary_without_upload_queue(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        quality = KnowledgeQualityStore(store)
        _seed_question_type(quality)
        builder = PublicGraphContributionBuilder(store, StudyConfig(knowledge_contribution_min_sample_count=3))

        preview = builder.preview(limit=5)

        assert preview["stats"]
        assert preview["summary"]["total"] >= 1
        assert preview["queue"] == []
        assert preview["opt_in"] is False
        for stat in preview["stats"]:
            builder.assert_no_raw_learning_text(stat)
    finally:
        store.close()


def test_sensitive_payload_is_rejected() -> None:
    builder = PublicGraphContributionBuilder(store=None, config=StudyConfig())
    with pytest.raises(ValueError):
        builder.assert_no_raw_learning_text({"ocr_text": "raw OCR"})
    with pytest.raises(ValueError):
        builder.assert_no_raw_learning_text({"question": "What is the answer?"})
    with pytest.raises(ValueError):
        builder.assert_no_raw_learning_text({"payload": {"reply": "LLM raw reply"}})
    with pytest.raises(ValueError):
        builder.assert_no_raw_learning_text({"topic_id": "a", "note": "x" * 81})
