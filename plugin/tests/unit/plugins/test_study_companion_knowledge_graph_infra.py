from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from plugin.plugins.study_companion.entry_tutor_context_support import (
    _TutorContextSupportMixin,
)
from plugin.plugins.study_companion.entry_knowledge_entries import _KnowledgeEntriesMixin
from plugin.plugins.study_companion._graph_utils import topic_id, topic_label
from plugin.plugins.study_companion.knowledge_graph_guidance import (
    _build_diagnosis_questions,
    build_topic_edges,
    build_knowledge_guidance_payload,
    match_topics,
)
from plugin.plugins.study_companion.knowledge_retrieval_eval import (
    evaluate_knowledge_retrieval_queries,
)
from plugin.plugins.study_companion.knowledge_seed_validator import (
    main as validate_knowledge_seed_main,
    validate_knowledge_seed_manifest,
)

pytestmark = pytest.mark.unit


def test_bundled_seed_manifest_validates_all_topics() -> None:
    seed = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "study_companion"
        / "static"
        / "knowledge_graph_seed.json"
    )

    result = validate_knowledge_seed_manifest(seed)

    assert result.is_valid
    assert len(result.topics) == 820
    assert result.report["schema_ready_topics"] == len(result.topics)


def test_chinese_main_idea_bridge_extends_into_junior_narrative_reading() -> None:
    seed = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "study_companion"
        / "static"
        / "knowledge_seeds"
        / "chinese.json"
    )
    payload = json.loads(seed.read_text(encoding="utf-8"))
    topics = payload["topics"]
    main_idea = next(
        topic
        for topic in topics
        if topic.get("id") == "chinese_primary_paragraph_main_idea"
    )
    narrative = next(
        topic
        for topic in topics
        if topic.get("id") == "chinese_junior_narrative_reading"
    )

    edges = build_topic_edges([main_idea, narrative])
    bridge = next(
        edge
        for edge in edges
        if {
            edge.get("from"),
            edge.get("to"),
        }
        == {
            "chinese_primary_paragraph_main_idea",
            "chinese_junior_narrative_reading",
        }
    )

    assert bridge["relation"] == "extends"
    assert bridge["from"] == "chinese_primary_paragraph_main_idea"
    assert bridge["to"] == "chinese_junior_narrative_reading"


def test_compact_confusion_labels_use_related_topic_label() -> None:
    payload = build_knowledge_guidance_payload(
        topics=[
            {
                "id": "focus",
                "name": "Focus Topic",
                "subject": "math",
                "stage": "junior_high",
                "chapter": "chapter",
                "unit": "unit",
                "prerequisites": [],
                "related": [{"id": "other", "relation": "confusable"}],
            },
            {
                "id": "other",
                "name": "Other Topic",
                "subject": "math",
                "stage": "junior_high",
                "chapter": "chapter",
                "unit": "unit",
                "prerequisites": [],
                "related": [],
            },
        ],
        topic_id="focus",
    )

    assert payload["model_context"]["confusions"] == ["Other Topic"]


def test_graph_topic_helpers_skip_blank_candidates() -> None:
    assert topic_id({"id": "   ", "topic_id": "fallback_id"}) == "fallback_id"
    assert (
        topic_label(
            {"name": "   ", "label": "", "topic_id": "topic_key"},
            fallback="Fallback Label",
        )
        == "topic_key"
    )
    assert topic_label(None, fallback="  Fallback Label  ") == "Fallback Label"


def test_knowledge_guidance_cache_can_be_invalidated() -> None:
    class Host(_TutorContextSupportMixin):
        pass

    host = Host()
    host._knowledge_guidance_topics_cache = {"all:5000": [{"id": "stale"}]}

    host._invalidate_knowledge_guidance_cache()

    assert host._knowledge_guidance_topics_cache == {}


def test_related_prerequisite_edges_point_from_prerequisite_to_topic() -> None:
    edges = build_topic_edges(
        [
            {
                "id": "advanced",
                "name": "Advanced",
                "related": [
                    {
                        "id": "foundation",
                        "relation": "prerequisite",
                        "reason": "Foundation comes first.",
                    }
                ],
            },
            {"id": "foundation", "name": "Foundation", "related": []},
        ]
    )

    assert [(edge["from"], edge["to"]) for edge in edges] == [
        ("foundation", "advanced")
    ]


def test_topic_matching_has_no_implicit_math_bonus() -> None:
    topics = [
        {"id": "math_common", "name": "Common", "subject": "math"},
        {"id": "history_common", "name": "Common", "subject": "history"},
    ]

    neutral = match_topics(topics, query="common", limit=2)
    assert {item["score"] for item in neutral} == {neutral[0]["score"]}

    hinted = match_topics(topics, query="history common", limit=2)
    assert hinted[0]["id"] == "history_common"


def test_prerequisite_question_cap_keeps_later_application_questions() -> None:
    learning_path = [
        {
            "from": f"pre_{index}",
            "to": "focus",
            "from_label": f"Prerequisite {index}",
            "to_label": "Focus",
            "relation": "prerequisite",
        }
        for index in range(4)
    ]
    learning_path.append(
        {
            "from": "focus",
            "to": "application",
            "from_label": "Focus",
            "to_label": "Application",
            "relation": "application",
        }
    )

    questions = _build_diagnosis_questions(
        selected_id="focus",
        selected_label="Focus",
        learning_path=learning_path,
        confusions=[],
        next_practice=[],
    )

    assert sum(item["kind"] == "prerequisite_probe" for item in questions) == 3
    assert any(item["kind"] == "application_practice" for item in questions)


def test_nested_seed_manifests_are_expanded(tmp_path: Path) -> None:
    seed_path = tmp_path / "seed.json"
    child_manifest = tmp_path / "child.json"
    root_manifest = tmp_path / "root.json"
    seed_path.write_text(
        json.dumps(
            {
                "subject": "art",
                "topics": [
                    {
                        "id": "color",
                        "name": "Color",
                        "subject": "art",
                        "stage": "primary",
                        "chapter": "Basics",
                        "unit": "Color",
                        "prerequisites": [],
                        "related": [],
                        "skills": ["observe"],
                        "question_types": ["identify"],
                        "examples": [],
                        "typical_misconceptions": ["tone equals hue"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    child_manifest.write_text(
        json.dumps({"files": [{"path": seed_path.name}]}), encoding="utf-8"
    )
    root_manifest.write_text(
        json.dumps({"files": [{"path": child_manifest.name}]}), encoding="utf-8"
    )

    result = validate_knowledge_seed_manifest(root_manifest)

    assert result.is_valid
    assert [topic.data["id"] for topic in result.topics] == ["color"]


def test_nested_manifest_topic_count_includes_descendant_topics(
    tmp_path: Path,
) -> None:
    seed_path = tmp_path / "seed.json"
    child_manifest = tmp_path / "child.json"
    root_manifest = tmp_path / "root.json"
    seed_path.write_text(
        json.dumps(
            {
                "subject": "art",
                "topics": [
                    {
                        "id": "color",
                        "name": "Color",
                        "subject": "art",
                        "stage": "primary",
                        "chapter": "Basics",
                        "unit": "Color",
                        "prerequisites": [],
                        "related": [],
                        "skills": ["observe"],
                        "question_types": ["identify"],
                        "examples": [],
                        "typical_misconceptions": ["tone equals hue"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    child_manifest.write_text(
        json.dumps({"files": [{"path": seed_path.name, "topic_count": 1}]}),
        encoding="utf-8",
    )
    root_manifest.write_text(
        json.dumps({"files": [{"path": child_manifest.name, "topic_count": 1}]}),
        encoding="utf-8",
    )

    result = validate_knowledge_seed_manifest(root_manifest)

    assert result.is_valid
    assert [topic.data["id"] for topic in result.topics] == ["color"]


def test_nested_manifest_reports_invalid_references(tmp_path: Path) -> None:
    child_manifest = tmp_path / "child.json"
    invalid_json = tmp_path / "invalid.json"
    root_manifest = tmp_path / "root.json"
    child_manifest.write_text(
        json.dumps({"files": [{"path": root_manifest.name}]}),
        encoding="utf-8",
    )
    invalid_json.write_text("not json", encoding="utf-8")
    root_manifest.write_text(
        json.dumps(
            {
                "files": [
                    {"path": child_manifest.name},
                    {"path": child_manifest.name},
                    {"path": "missing.json"},
                    {},
                    {"path": invalid_json.name},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = validate_knowledge_seed_manifest(root_manifest)
    issue_codes = {issue.code for issue in result.issues}

    assert {
        "circular_manifest_reference",
        "duplicate_manifest_file",
        "missing_manifest_file",
        "invalid_manifest_file",
        "invalid_json",
    } <= issue_codes


def test_bundled_seed_uses_semantic_prerequisite_and_support_relations() -> None:
    seed = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "study_companion"
        / "static"
        / "knowledge_graph_seed.json"
    )
    result = validate_knowledge_seed_manifest(seed)
    topics = {str(topic.data["id"]): topic.data for topic in result.topics}

    sentence_expansion = topics["chinese_primary_sentence_expansion"]
    assert any(
        ref["id"] == "chinese_primary_pinyin_character"
        for ref in sentence_expansion["prerequisites"]
    )
    assert not any(
        ref["id"] == "chinese_primary_pinyin_character"
        and ref.get("relation") == "prerequisite"
        for ref in sentence_expansion["related"]
    )

    industrial_location = topics["geo_senior_industrial_location"]
    history_ref = next(
        ref
        for ref in industrial_location["related"]
        if ref["id"] == "history_junior_industrial_revolution"
    )
    assert history_ref["relation"] == "co_occurs"


def test_validator_cli_formats_bundled_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "study_companion"
        / "static"
        / "knowledge_graph_seed.json"
    )

    exit_code = validate_knowledge_seed_main([str(seed)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Knowledge Seed Quality Report" in output
    assert "validated 820 knowledge seed topics" in output


def test_related_prerequisites_participate_in_cycle_detection(tmp_path: Path) -> None:
    def topic(current: str, prerequisite: str) -> dict[str, object]:
        return {
            "id": current,
            "name": current,
            "subject": "art",
            "stage": "primary",
            "chapter": "Cycle",
            "unit": "Cycle",
            "prerequisites": [],
            "related": [
                {
                    "id": prerequisite,
                    "relation": "prerequisite",
                    "reason": "Cycle fixture.",
                    "priority": "core",
                    "context": "diagnosis",
                    "confidence": 0.9,
                    "use_cases": ["learning_path"],
                }
            ],
            "skills": ["observe"],
            "question_types": ["identify"],
            "examples": [],
            "typical_misconceptions": ["cycle"],
        }

    seed = tmp_path / "cycle.json"
    seed.write_text(
        json.dumps({"topics": [topic("a", "b"), topic("b", "a")]}),
        encoding="utf-8",
    )

    result = validate_knowledge_seed_manifest(seed)

    assert result.report["cycles_in_prerequisites"] == 2


def test_cross_subject_expectation_is_part_of_eval_result() -> None:
    report = evaluate_knowledge_retrieval_queries(
        topics=[
            {
                "id": "biology",
                "name": "Genetics",
                "subject": "biology",
                "stage": "senior_high",
                "chapter": "Genetics",
                "unit": "Genetics",
                "prerequisites": [],
                "related": [],
            },
            {
                "id": "math",
                "name": "Probability",
                "subject": "math",
                "stage": "senior_high",
                "chapter": "Probability",
                "unit": "Probability",
                "prerequisites": [],
                "related": [],
            },
        ],
        cases=[
            {
                "topic_id": "biology",
                "expected_topic_ids": ["biology"],
                "expect_cross_subject": True,
            }
        ],
    )

    assert report["summary"]["failed_count"] == 1
    assert report["results"][0]["passed"] is False
    assert report["results"][0]["failure_reasons"] == [
        "expected cross-subject edge was not returned"
    ]


def test_guidance_default_limit_covers_bundled_manifest() -> None:
    signature = inspect.signature(_KnowledgeEntriesMixin.study_knowledge_guidance)

    assert signature.parameters["limit"].default == 1000
