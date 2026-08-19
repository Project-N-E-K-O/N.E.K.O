from __future__ import annotations

import json

import pytest

from plugin.plugins.study_companion._semantic_routing import (
    ALLOWED_SUBJECTS,
    KNOWLEDGE_GUIDANCE_STATUSES,
    RESPONSE_MODES,
    StudyInputSemantics,
    build_semantic_routing_messages,
    parse_study_input_semantics,
)


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "subject": "chinese",
        "content_type": "literary_work",
        "intent": "interpretation",
        "response_mode": "general_discussion",
        "entity": "\u300a\u8fb9\u57ce\u300b",
        "retrieval_concepts": ["\u6587\u5b66\u7c7b\u6587\u672c\u9605\u8bfb", "\u5c0f\u8bf4\u4e3b\u9898", "\u4eba\u7269\u5f62\u8c61"],
        "confidence": 0.95,
    }
    payload.update(overrides)
    return payload


def test_semantic_contract_exposes_only_supported_subjects_and_statuses() -> None:
    assert ALLOWED_SUBJECTS == frozenset(
        {"math", "chinese", "english", "physics", "chemistry", "biology", "history", "geography", "politics", "economics", "computer_science", "unknown"}
    )
    assert KNOWLEDGE_GUIDANCE_STATUSES == frozenset(
        {"applied", "not_matched", "low_confidence", "routing_unavailable", "not_applicable"}
    )
    assert RESPONSE_MODES == frozenset(
        {"problem_solving", "general_explanation", "general_discussion", "unknown"}
    )


def test_parse_semantics_accepts_mapping_and_normalizes_text() -> None:
    parsed = parse_study_input_semantics(
        _valid_payload(entity="  \u300a\u8fb9\u57ce\u300b  ", retrieval_concepts=[" \u6587\u5b66\u7c7b\u6587\u672c\u9605\u8bfb ", "\u4eba\u7269\u5f62\u8c61", "\u4eba\u7269\u5f62\u8c61"])
    )
    assert parsed == StudyInputSemantics(
        subject="chinese", content_type="literary_work", intent="interpretation",
        response_mode="general_discussion",
        entity="\u300a\u8fb9\u57ce\u300b", retrieval_concepts=("\u6587\u5b66\u7c7b\u6587\u672c\u9605\u8bfb", "\u4eba\u7269\u5f62\u8c61"), confidence=0.95,
    )


def test_parse_semantics_accepts_json_and_a_single_json_fence() -> None:
    raw = json.dumps(_valid_payload(), ensure_ascii=False)
    assert parse_study_input_semantics(raw) == parse_study_input_semantics(f"```json\n{raw}\n```")


@pytest.mark.parametrize(
    "payload",
    [
        None, [], "not json", "{}", _valid_payload(subject="literature"),
        _valid_payload(confidence=-0.01), _valid_payload(confidence=1.01),
        _valid_payload(confidence=True), _valid_payload(retrieval_concepts="literature"),
        _valid_payload(retrieval_concepts=["x"] * 7),
        _valid_payload(retrieval_concepts=["x" * 65]), _valid_payload(entity="x" * 121),
        _valid_payload(content_type="x" * 65), _valid_payload(intent="x" * 65),
        _valid_payload(response_mode="essay"),
        _valid_payload(extra="not allowed"), _valid_payload(topic_id="forbidden"),
    ],
)
def test_parse_semantics_rejects_malformed_or_out_of_contract_payloads(payload: object) -> None:
    assert parse_study_input_semantics(payload) is None


def test_unknown_subject_must_not_supply_graph_retrieval_concepts() -> None:
    assert parse_study_input_semantics(
        _valid_payload(subject="unknown", content_type="unknown", intent="unknown", response_mode="unknown", entity="", retrieval_concepts=[], confidence=0.2)
    ) == StudyInputSemantics(subject="unknown", content_type="unknown", intent="unknown", response_mode="unknown", entity="", retrieval_concepts=(), confidence=0.2)
    assert parse_study_input_semantics(_valid_payload(subject="unknown", retrieval_concepts=["math"])) is None


def test_semantic_routing_messages_request_meaning_not_keyword_matching() -> None:
    messages = build_semantic_routing_messages(
        text="Discuss your understanding of a literary work", language="en", has_images=False
    )
    assert [message["role"] for message in messages] == ["system", "user"]
    combined = "\n".join(message["content"] for message in messages)
    assert "complete semantic meaning" in combined
    assert "Never choose a subject from keyword overlap alone" in combined
    assert "Never return graph node IDs" in combined
    assert "general_discussion" in combined
    assert "general_explanation" in combined
    assert "problem_solving" in combined
    assert "chain of thought" in combined
    assert '"image_present": false' in combined
    assert "Discuss your understanding of a literary work" in combined
    assert all("topic_id" not in message["content"] for message in messages)


def test_semantic_routing_messages_mark_images_without_embedding_them() -> None:
    messages = build_semantic_routing_messages(text="analyze the image", language="en", has_images=True)
    assert '"image_present": true' in messages[-1]["content"]
    assert "data:image" not in messages[-1]["content"]
