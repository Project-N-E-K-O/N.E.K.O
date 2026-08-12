from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
import re
from typing import Literal, TypeAlias, cast


Subject: TypeAlias = Literal[
    "math", "chinese", "english", "physics", "chemistry", "biology",
    "history", "geography", "politics", "economics", "computer_science",
    "unknown",
]
KnowledgeGuidanceStatus: TypeAlias = Literal[
    "applied", "not_matched", "low_confidence", "routing_unavailable",
    "not_applicable",
]
ResponseMode: TypeAlias = Literal[
    "problem_solving", "general_explanation", "general_discussion", "unknown"
]

ALLOWED_SUBJECTS = frozenset(
    {
        "math", "chinese", "english", "physics", "chemistry", "biology",
        "history", "geography", "politics", "economics", "computer_science",
        "unknown",
    }
)
KNOWLEDGE_GUIDANCE_STATUSES = frozenset(
    {"applied", "not_matched", "low_confidence", "routing_unavailable", "not_applicable"}
)
RESPONSE_MODES = frozenset(
    {"problem_solving", "general_explanation", "general_discussion", "unknown"}
)
MAX_RETRIEVAL_CONCEPTS = 6
MAX_RETRIEVAL_CONCEPT_LENGTH = 64
MAX_CLASSIFICATION_LENGTH = 64
MAX_ENTITY_LENGTH = 120
_SEMANTIC_FIELDS = frozenset(
    {
        "subject", "content_type", "intent", "response_mode", "entity",
        "retrieval_concepts", "confidence",
    }
)
_JSON_FENCE_RE = re.compile(r"\A```(?:json)?\s*\n(?P<body>.*)\n```\s*\Z", re.DOTALL)


@dataclass(frozen=True, slots=True)
class StudyInputSemantics:
    subject: Subject
    content_type: str
    intent: str
    response_mode: ResponseMode
    entity: str
    retrieval_concepts: tuple[str, ...]
    confidence: float


def _mapping_from_payload(payload: object) -> Mapping[str, object] | None:
    if isinstance(payload, Mapping):
        return payload
    if not isinstance(payload, str):
        return None
    raw = payload.strip()
    fenced = _JSON_FENCE_RE.fullmatch(raw)
    if fenced is not None:
        raw = fenced.group("body").strip()
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _bounded_text(value: object, *, limit: int, allow_empty: bool) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (not normalized and not allow_empty) or len(normalized) > limit:
        return None
    return normalized


def parse_study_input_semantics(payload: object) -> StudyInputSemantics | None:
    """Validate a semantic-routing result without accepting graph node claims."""
    values = _mapping_from_payload(payload)
    if values is None or frozenset(values) != _SEMANTIC_FIELDS:
        return None
    subject_value = values.get("subject")
    if not isinstance(subject_value, str):
        return None
    subject = subject_value.strip().lower()
    if subject not in ALLOWED_SUBJECTS:
        return None
    content_type = _bounded_text(
        values.get("content_type"), limit=MAX_CLASSIFICATION_LENGTH, allow_empty=False
    )
    intent = _bounded_text(
        values.get("intent"), limit=MAX_CLASSIFICATION_LENGTH, allow_empty=False
    )
    response_mode_value = values.get("response_mode")
    if not isinstance(response_mode_value, str):
        return None
    response_mode = response_mode_value.strip().lower()
    if response_mode not in RESPONSE_MODES:
        return None
    entity = _bounded_text(values.get("entity"), limit=MAX_ENTITY_LENGTH, allow_empty=True)
    if content_type is None or intent is None or entity is None:
        return None
    raw_concepts = values.get("retrieval_concepts")
    if not isinstance(raw_concepts, (list, tuple)) or len(raw_concepts) > MAX_RETRIEVAL_CONCEPTS:
        return None
    concepts: list[str] = []
    for item in raw_concepts:
        concept = _bounded_text(item, limit=MAX_RETRIEVAL_CONCEPT_LENGTH, allow_empty=False)
        if concept is None:
            return None
        if concept not in concepts:
            concepts.append(concept)
    if subject == "unknown" and concepts:
        return None
    confidence_value = values.get("confidence")
    if isinstance(confidence_value, bool) or not isinstance(confidence_value, (int, float)):
        return None
    confidence = float(confidence_value)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        return None
    return StudyInputSemantics(
        subject=cast(Subject, subject),
        content_type=content_type,
        intent=intent,
        response_mode=cast(ResponseMode, response_mode),
        entity=entity,
        retrieval_concepts=tuple(concepts),
        confidence=confidence,
    )


def build_semantic_routing_messages(
    *, text: str, language: str, has_images: bool = False
) -> list[dict[str, str]]:
    """Build a classification-only prompt; image bytes are attached by the caller."""
    schema = {
        "subject": sorted(ALLOWED_SUBJECTS),
        "content_type": "string, max 64 chars",
        "intent": "string, max 64 chars",
        "response_mode": sorted(RESPONSE_MODES),
        "entity": "string, max 120 chars; empty when absent",
        "retrieval_concepts": "array of up to 6 strings, each max 64 chars",
        "confidence": "number from 0 to 1",
    }
    request = {
        "language": str(language or "").strip() or "unknown",
        "image_present": bool(has_images),
        "study_input": str(text or "").strip(),
        "response_schema": schema,
    }
    system = (
        "You route study inputs by their complete semantic meaning, discussion "
        "object, and task intent. Never choose a subject from keyword overlap alone. "
        "Return exactly one JSON object matching the supplied schema. Do not answer "
        "the study question, explain your classification, or reveal chain of thought. "
        "retrieval_concepts must contain only short concepts for searching the local "
        "knowledge graph. Never return graph node IDs or claim that any graph node "
        "was matched. When the subject cannot be determined reliably, set subject to "
        "unknown and retrieval_concepts to an empty array."
        " Classify response_mode from the complete request: use problem_solving only "
        "for a concrete calculation, proof, option judgement, verification, or exercise; "
        "use general_explanation for explaining a concept or mechanism; use "
        "general_discussion for discussing a work, person, event, viewpoint, or value "
        "judgement. The same literary work can be general_discussion when discussing "
        "its meaning and problem_solving when answering a specific exam question. "
        "When task intent is uncertain, set response_mode to unknown."
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(request, ensure_ascii=False, separators=(",", ": ")),
        },
    ]


__all__ = [
    "ALLOWED_SUBJECTS", "KNOWLEDGE_GUIDANCE_STATUSES", "KnowledgeGuidanceStatus",
    "RESPONSE_MODES", "ResponseMode", "StudyInputSemantics", "Subject", "build_semantic_routing_messages",
    "parse_study_input_semantics",
]
