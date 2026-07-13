from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REQUIRED_SCALAR_FIELDS = ("id", "name", "subject", "stage", "chapter")
REQUIRED_LIST_FIELDS = (
    "prerequisites",
    "related",
)
DEFAULTABLE_LIST_FIELDS = (
    "skills",
    "question_types",
    "examples",
    "typical_misconceptions",
)
QUALITY_LIST_FIELDS = (
    "skills",
    "question_types",
    "examples",
    "typical_misconceptions",
)
RESERVED_CONTEXT_FIELDS = ("curriculum_version", "exam_region", "exam_type")
TAXONOMY_FILE_NAME = "knowledge_seed_taxonomy.json"
ALLOWED_EDGE_RELATIONS = {
    "prerequisite",
    "application",
    "procedure_step",
    "confusable",
    "extends",
    "analogy",
    "co_occurs",
    "supports",
    # Legacy seed/UI values accepted during migration.
    "related",
    "similar",
    "next",
    "nearby",
    "compare",
}
SEMANTIC_EDGE_RELATIONS = {
    "application",
    "procedure_step",
    "confusable",
    "co_occurs",
    "supports",
    "analogy",
}
TYPED_EDGE_REQUIRED_FIELDS = ("id", "relation", "reason")
ALLOWED_EDGE_USE_CASES = {
    "diagnosis",
    "hint_generation",
    "learning_path",
    "practice_planning",
    "review",
}
ALLOWED_EDGE_PRIORITIES = {"core", "useful", "optional"}
ALLOWED_EDGE_CONTEXTS = {"diagnosis", "explanation", "practice", "review"}
SUBJECT_MINIMUM_STANDARDS: dict[str, dict[str, tuple[str, ...]]] = {
    "math": {
        "relations": ("prerequisite", "procedure_step", "confusable", "application"),
        "fields": (),
    },
    "physics": {
        "relations": ("prerequisite", "procedure_step", "application"),
        "fields": (),
    },
    "chemistry": {"relations": ("procedure_step", "confusable"), "fields": ()},
    "biology": {
        "relations": ("prerequisite", "application", "confusable"),
        "fields": (),
    },
    "english": {
        "relations": ("procedure_step",),
        "fields": ("question_types", "typical_misconceptions"),
    },
    "computer_science": {
        "relations": ("prerequisite", "procedure_step", "application"),
        "fields": (),
    },
    "chinese": {
        "relations": ("procedure_step", "application", "confusable"),
        "fields": (),
    },
    "economics": {
        "relations": ("procedure_step", "application", "confusable"),
        "fields": (),
    },
    "geography": {
        "relations": ("procedure_step", "application", "confusable"),
        "fields": (),
    },
    "history": {
        "relations": ("procedure_step", "application", "confusable"),
        "fields": (),
    },
    "politics": {
        "relations": ("procedure_step", "application", "confusable"),
        "fields": (),
    },
}
SUBJECT_MINIMUM_GAP_SAMPLE_LIMIT = 10
QUALITY_ACTION_LIST_LIMIT = 12
LEGACY_EDGE_SAMPLE_LIMIT = 20


@dataclass(frozen=True)
class KnowledgeSeedIssue:
    code: str
    message: str
    path: str
    topic_id: str = ""


@dataclass(frozen=True)
class KnowledgeSeedTopic:
    path: Path
    data: dict[str, Any]
    subject: str
    stage: str


@dataclass(frozen=True)
class KnowledgeSeedValidationResult:
    topics: tuple[KnowledgeSeedTopic, ...]
    issues: tuple[KnowledgeSeedIssue, ...]
    report: dict[str, Any] | None = None

    @property
    def is_valid(self) -> bool:
        return not self.issues


def _read_json(path: Path, issues: list[KnowledgeSeedIssue]) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        issues.append(
            KnowledgeSeedIssue(
                "invalid_json",
                f"cannot read seed json: {exc}",
                str(path),
            )
        )
        return None
    if not isinstance(payload, dict):
        issues.append(
            KnowledgeSeedIssue("invalid_payload", "seed payload must be an object", str(path))
        )
        return None
    return payload


def _load_taxonomy(
    manifest_path: Path,
    issues: list[KnowledgeSeedIssue],
) -> dict[str, set[str]]:
    taxonomy_path = manifest_path.parent / TAXONOMY_FILE_NAME
    if not taxonomy_path.is_file():
        return {}
    payload = _read_json(taxonomy_path, issues)
    if payload is None:
        return {}
    taxonomy: dict[str, set[str]] = {}
    for field in RESERVED_CONTEXT_FIELDS:
        raw_values = payload.get(field)
        if not isinstance(raw_values, dict):
            issues.append(
                KnowledgeSeedIssue(
                    "invalid_taxonomy",
                    f"taxonomy field must be an object: {field}",
                    str(taxonomy_path),
                )
            )
            continue
        taxonomy[field] = {
            str(value_id).strip()
            for value_id in raw_values
            if str(value_id).strip()
        }
    return taxonomy


def _default_stage(payload: dict[str, Any]) -> str:
    return str(
        payload.get("stage")
        or payload.get("grade_level")
        or payload.get("education_level")
        or payload.get("course_level")
        or ""
    ).strip()


def _iter_seed_files(
    manifest_path: Path,
    payload: dict[str, Any],
    issues: list[KnowledgeSeedIssue],
    seen_paths: set[Path] | None = None,
    manifest_stack: set[Path] | None = None,
    manifest_seed_paths: dict[Path, set[Path]] | None = None,
) -> Iterable[Path]:
    resolved_manifest = manifest_path.resolve()
    shared_seen = seen_paths if seen_paths is not None else set()
    active_manifests = manifest_stack if manifest_stack is not None else set()
    shared_manifest_seed_paths = (
        manifest_seed_paths if manifest_seed_paths is not None else {}
    )
    descendant_seed_paths = shared_manifest_seed_paths.setdefault(
        resolved_manifest, set()
    )
    if resolved_manifest in active_manifests:
        issues.append(
            KnowledgeSeedIssue(
                "circular_manifest_reference",
                f"manifest reference cycle includes: {resolved_manifest}",
                str(manifest_path),
            )
        )
        return
    if resolved_manifest in shared_seen:
        issues.append(
            KnowledgeSeedIssue(
                "duplicate_manifest_file",
                f"manifest references duplicate seed file: {resolved_manifest}",
                str(manifest_path),
            )
        )
        return

    shared_seen.add(resolved_manifest)
    active_manifests.add(resolved_manifest)
    try:
        files = payload.get("files")
        if not isinstance(files, list):
            descendant_seed_paths.add(resolved_manifest)
            yield resolved_manifest
            return
        for item in files:
            if isinstance(item, dict):
                raw_path = item.get("path") or item.get("file")
            else:
                raw_path = item
            child_name = str(raw_path or "").strip()
            if not child_name:
                issues.append(
                    KnowledgeSeedIssue(
                        "invalid_manifest_file",
                        "manifest file entry must include path",
                        str(manifest_path),
                    )
                )
                continue
            child_path = Path(child_name)
            if not child_path.is_absolute():
                child_path = manifest_path.parent / child_path
            child_path = child_path.resolve()
            if child_path in active_manifests:
                issues.append(
                    KnowledgeSeedIssue(
                        "circular_manifest_reference",
                        f"manifest reference cycle includes: {child_name}",
                        str(manifest_path),
                    )
                )
                continue
            if child_path in shared_seen:
                issues.append(
                    KnowledgeSeedIssue(
                        "duplicate_manifest_file",
                        f"manifest references duplicate seed file: {child_name}",
                        str(manifest_path),
                    )
                )
                continue
            if not child_path.is_file():
                issues.append(
                    KnowledgeSeedIssue(
                        "missing_manifest_file",
                        f"manifest seed file does not exist: {child_name}",
                        str(manifest_path),
                    )
                )
                continue
            child_payload = _read_json(child_path, issues)
            if child_payload is None:
                continue
            if isinstance(child_payload.get("files"), list):
                yield from _iter_seed_files(
                    child_path,
                    child_payload,
                    issues,
                    shared_seen,
                    active_manifests,
                    shared_manifest_seed_paths,
                )
                descendant_seed_paths.update(
                    shared_manifest_seed_paths.get(child_path, set())
                )
                continue
            shared_seen.add(child_path)
            descendant_seed_paths.add(child_path)
            yield child_path
    finally:
        active_manifests.discard(resolved_manifest)


def _normalize_topic(
    path: Path,
    payload: dict[str, Any],
    topic: dict[str, Any],
) -> KnowledgeSeedTopic:
    data = dict(topic)
    subject = str(topic.get("subject") or payload.get("subject") or "").strip()
    stage = str(
        topic.get("stage")
        or topic.get("grade_level")
        or topic.get("education_level")
        or topic.get("course_level")
        or _default_stage(payload)
    ).strip()
    if not str(data.get("unit") or "").strip():
        data["unit"] = str(data.get("chapter") or "").strip()
    for field in (*REQUIRED_LIST_FIELDS, *DEFAULTABLE_LIST_FIELDS):
        if field not in data:
            data[field] = []
    return KnowledgeSeedTopic(path=path, data=data, subject=subject, stage=stage)


def _validate_topic_fields(
    topic: KnowledgeSeedTopic,
    issues: list[KnowledgeSeedIssue],
    taxonomy: dict[str, set[str]],
) -> None:
    data = topic.data
    topic_id = str(data.get("id") or "").strip()
    scalar_values = {
        "id": topic_id,
        "name": str(data.get("name") or "").strip(),
        "subject": topic.subject,
        "stage": topic.stage,
        "chapter": str(data.get("chapter") or "").strip(),
    }
    for field, value in scalar_values.items():
        if not value:
            issues.append(
                KnowledgeSeedIssue(
                    "missing_required_field",
                    f"topic missing required field: {field}",
                    str(topic.path),
                    topic_id,
                )
            )
    for field in REQUIRED_LIST_FIELDS:
        value = data.get(field)
        if not isinstance(value, list):
            issues.append(
                KnowledgeSeedIssue(
                    "invalid_required_list",
                    f"topic field must be a list: {field}",
                    str(topic.path),
                    topic_id,
                )
            )
            continue
    for field in DEFAULTABLE_LIST_FIELDS:
        value = data.get(field)
        if not isinstance(value, list):
            issues.append(
                KnowledgeSeedIssue(
                    "invalid_quality_list",
                    f"topic quality field must be a list when present: {field}",
                    str(topic.path),
                    topic_id,
                )
            )
    for field in RESERVED_CONTEXT_FIELDS:
        value = data.get(field)
        if value is None:
            continue
        if isinstance(value, str):
            values = [value.strip()]
            if not values[0]:
                issues.append(
                    KnowledgeSeedIssue(
                        "empty_reserved_field",
                        f"reserved field must not be blank when present: {field}",
                        str(topic.path),
                        topic_id,
                    )
                )
                continue
        elif isinstance(value, list) and all(str(item).strip() for item in value):
            values = [str(item).strip() for item in value]
        else:
            issues.append(
                KnowledgeSeedIssue(
                    "invalid_reserved_field",
                    f"reserved field must be a non-empty string or string list: {field}",
                    str(topic.path),
                    topic_id,
                )
            )
            continue
        allowed_values = taxonomy.get(field)
        if not allowed_values:
            continue
        for item in values:
            if item not in allowed_values:
                issues.append(
                    KnowledgeSeedIssue(
                        "unknown_reserved_field_value",
                        f"{field} contains unknown taxonomy value: {item}",
                        str(topic.path),
                        topic_id,
                    )
                )


def _validate_taxonomy_coverage(
    topics: Iterable[KnowledgeSeedTopic],
    issues: list[KnowledgeSeedIssue],
) -> None:
    # TODO: Re-enable hard taxonomy coverage validation once the bundled legacy seed
    # has complete curriculum context; current gaps are reported as quality metrics.
    return


def _validate_stage_specific_context(
    topics: Iterable[KnowledgeSeedTopic],
    issues: list[KnowledgeSeedIssue],
) -> None:
    for topic in topics:
        data = topic.data
        topic_id = str(data.get("id") or "").strip()
        regions = data.get("exam_region")
        region_values = regions if isinstance(regions, list) else [regions]
        region_set = {
            str(item).strip()
            for item in region_values
            if item is not None and str(item).strip()
        }
        if not region_set:
            continue
        if topic.stage == "junior_high" and not any(
            item.startswith("zhongkao_") for item in region_set
        ):
            issues.append(
                KnowledgeSeedIssue(
                    "missing_junior_exam_region",
                    "junior high topic should include a zhongkao exam region",
                    str(topic.path),
                    topic_id,
                )
            )
        if topic.stage == "senior_high" and not (
            {"new_gaokao_i", "new_gaokao_ii", "national_a", "national_b"}
            & region_set
        ):
            issues.append(
                KnowledgeSeedIssue(
                    "missing_senior_exam_region",
                    "senior high topic should include at least one gaokao paper style",
                    str(topic.path),
                    topic_id,
                )
            )
        if topic.stage == "college" and "college_course_generic" not in region_set:
            issues.append(
                KnowledgeSeedIssue(
                    "missing_college_exam_region",
                    "college topic should include college_course_generic",
                    str(topic.path),
                    topic_id,
                )
            )


def _validate_examples(
    topic: KnowledgeSeedTopic,
    issues: list[KnowledgeSeedIssue],
) -> None:
    examples = topic.data.get("examples")
    if not isinstance(examples, list):
        return
    topic_id = str(topic.data.get("id") or "").strip()
    for index, example in enumerate(examples):
        if not isinstance(example, dict):
            issues.append(
                KnowledgeSeedIssue(
                    "invalid_example",
                    f"example #{index + 1} must be an object",
                    str(topic.path),
                    topic_id,
                )
            )
            continue
        prompt = str(example.get("prompt") or "").strip()
        answer_outline = example.get("answer_outline")
        if not prompt:
            issues.append(
                KnowledgeSeedIssue(
                    "invalid_example",
                    f"example #{index + 1} missing prompt",
                    str(topic.path),
                    topic_id,
                )
            )
        if not isinstance(answer_outline, list) or not answer_outline:
            issues.append(
                KnowledgeSeedIssue(
                    "invalid_example",
                    f"example #{index + 1} missing answer_outline",
                    str(topic.path),
                    topic_id,
                )
            )


def _ref_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("topic_id") or "").strip()
    return str(value or "").strip()


def _edge_relation(field: str, value: Any) -> str:
    if isinstance(value, dict):
        relation = str(value.get("relation") or "").strip()
        if relation:
            return relation
    return "prerequisite" if field == "prerequisites" else "co_occurs"


def _is_typed_edge(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    relation = str(value.get("relation") or "").strip()
    return bool(
        value.get("reason")
        or value.get("use_cases")
        or relation in SEMANTIC_EDGE_RELATIONS
    )


def _validate_typed_edge(
    *,
    field: str,
    ref: Any,
    topic: KnowledgeSeedTopic,
    issues: list[KnowledgeSeedIssue],
) -> None:
    if not isinstance(ref, dict):
        return
    source_id = str(topic.data.get("id") or "").strip()
    relation = _edge_relation(field, ref)
    if relation not in ALLOWED_EDGE_RELATIONS:
        issues.append(
            KnowledgeSeedIssue(
                "unknown_edge_relation",
                f"{field} contains unknown relation: {relation}",
                str(topic.path),
                source_id,
            )
        )
    if _is_typed_edge(ref):
        for required_field in TYPED_EDGE_REQUIRED_FIELDS:
            if required_field == "relation" and field == "prerequisites":
                continue
            if not str(ref.get(required_field) or "").strip():
                issues.append(
                    KnowledgeSeedIssue(
                        "invalid_typed_edge",
                        f"{field} typed edge missing required field: {required_field}",
                        str(topic.path),
                        source_id,
                    )
                )
    use_cases = ref.get("use_cases")
    if use_cases is not None and not (
        isinstance(use_cases, list)
        and bool(use_cases)
        and all(str(item).strip() for item in use_cases)
    ):
        issues.append(
            KnowledgeSeedIssue(
                "invalid_typed_edge",
                f"{field} typed edge use_cases must be a non-empty string list",
                str(topic.path),
                source_id,
            )
        )
    elif isinstance(use_cases, list):
        for use_case in use_cases:
            normalized = str(use_case).strip()
            if normalized not in ALLOWED_EDGE_USE_CASES:
                issues.append(
                    KnowledgeSeedIssue(
                        "unknown_edge_use_case",
                        f"{field} typed edge contains unknown use_case: {normalized}",
                        str(topic.path),
                        source_id,
                    )
                )
    priority = ref.get("priority")
    if priority is not None and str(priority).strip() not in ALLOWED_EDGE_PRIORITIES:
        issues.append(
            KnowledgeSeedIssue(
                "invalid_edge_priority",
                f"{field} typed edge priority must be one of: core,useful,optional",
                str(topic.path),
                source_id,
            )
        )
    context = ref.get("context")
    if context is not None and str(context).strip() not in ALLOWED_EDGE_CONTEXTS:
        issues.append(
            KnowledgeSeedIssue(
                "invalid_edge_context",
                f"{field} typed edge context must be one of: diagnosis,explanation,practice,review",
                str(topic.path),
                source_id,
            )
        )
    if ref.get("confidence") is not None:
        try:
            confidence = float(ref.get("confidence"))
        except (TypeError, ValueError):
            confidence = -1.0
        if not 0.0 <= confidence <= 1.0:
            issues.append(
                KnowledgeSeedIssue(
                    "invalid_edge_confidence",
                    f"{field} typed edge confidence must be between 0.0 and 1.0",
                    str(topic.path),
                    source_id,
                )
            )


def _validate_references(
    topics: Iterable[KnowledgeSeedTopic],
    topic_ids: set[str],
    issues: list[KnowledgeSeedIssue],
) -> None:
    for topic in topics:
        source_id = str(topic.data.get("id") or "").strip()
        for field in ("prerequisites", "related"):
            refs = topic.data.get(field)
            if not isinstance(refs, list):
                continue
            for ref in refs:
                _validate_typed_edge(field=field, ref=ref, topic=topic, issues=issues)
                target_id = _ref_id(ref)
                if not target_id:
                    issues.append(
                        KnowledgeSeedIssue(
                            "invalid_reference",
                            f"{field} contains an empty reference",
                            str(topic.path),
                            source_id,
                        )
                    )
                    continue
                if target_id not in topic_ids:
                    issues.append(
                        KnowledgeSeedIssue(
                            "missing_reference",
                            f"{field} references missing topic: {target_id}",
                            str(topic.path),
                            source_id,
                        )
                    )


def _find_prerequisite_cycle_nodes(prerequisite_edges: dict[str, set[str]]) -> set[str]:
    cycle_nodes: set[str] = set()
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, path: tuple[str, ...]) -> None:
        if node in visiting:
            cycle_nodes.update(path[path.index(node):])
            return
        if node in visited:
            return
        visiting.add(node)
        for parent in prerequisite_edges.get(node, set()):
            if parent in prerequisite_edges:
                visit(parent, (*path, node))
        visiting.discard(node)
        visited.add(node)

    for topic_id in prerequisite_edges:
        visit(topic_id, ())
    return cycle_nodes


def _topic_schema_ready(topic: KnowledgeSeedTopic) -> bool:
    data = topic.data
    scalar_values = [
        str(data.get("id") or "").strip(),
        str(data.get("name") or "").strip(),
        topic.subject,
        topic.stage,
        str(data.get("chapter") or "").strip(),
        str(data.get("unit") or "").strip(),
    ]
    if not all(scalar_values):
        return False
    for field in (*REQUIRED_LIST_FIELDS, *DEFAULTABLE_LIST_FIELDS):
        if not isinstance(data.get(field), list):
            return False
    for field in QUALITY_LIST_FIELDS:
        if not data.get(field):
            return False
    return True


def _build_quality_report(topics: tuple[KnowledgeSeedTopic, ...]) -> dict[str, Any]:
    topic_ids = {str(topic.data.get("id") or "").strip() for topic in topics}
    topic_ids.discard("")
    topic_subject_by_id = {
        str(topic.data.get("id") or "").strip(): topic.subject or "<missing>"
        for topic in topics
        if str(topic.data.get("id") or "").strip()
    }
    inbound: dict[str, int] = {topic_id: 0 for topic_id in topic_ids}
    outbound: dict[str, int] = {topic_id: 0 for topic_id in topic_ids}
    stage_counts: dict[str, int] = {}
    subject_counts: dict[str, int] = {}
    edge_counts: dict[str, int] = {}
    typed_edges = 0
    legacy_edges = 0
    missing_stage = 0
    missing_college_course_family: list[str] = []
    over_connected_topics: list[str] = []
    duplicate_name_keys: dict[str, int] = {}
    prerequisite_edges: dict[str, set[str]] = {}
    schema_ready_topics = 0
    subject_schema_ready_counts: dict[str, int] = {}
    subject_minimum_standard_topic_counts: dict[str, int] = {}
    subject_minimum_standard_ready_counts: dict[str, int] = {}
    subject_minimum_standard_gap_counts: dict[str, int] = {}
    subject_minimum_standard_relation_gap_counts: dict[str, dict[str, int]] = {}
    subject_minimum_standard_field_gap_counts: dict[str, dict[str, int]] = {}
    subject_minimum_standard_uncovered_subject_counts: dict[str, int] = {}
    subject_minimum_standard_gap_samples: dict[str, list[dict[str, Any]]] = {}
    minimum_gap_candidates: dict[str, list[dict[str, Any]]] = {}
    chapter_ready_counts: dict[str, dict[str, int]] = {}
    chapter_gap_counts: dict[str, dict[str, int]] = {}
    cross_subject_edge_counts: dict[str, dict[str, int]] = {}
    cross_subject_relation_counts: dict[str, int] = {}
    legacy_edge_samples: list[dict[str, str]] = []

    for topic in topics:
        topic_id = str(topic.data.get("id") or "").strip()
        stage = topic.stage or "<missing>"
        subject = topic.subject or "<missing>"
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        subject_counts[subject] = subject_counts.get(subject, 0) + 1
        if _topic_schema_ready(topic):
            schema_ready_topics += 1
            subject_schema_ready_counts[subject] = (
                subject_schema_ready_counts.get(subject, 0) + 1
            )
        if not topic.stage:
            missing_stage += 1
        if topic.stage == "college" and not str(
            topic.data.get("course_family") or ""
        ).strip():
            missing_college_course_family.append(topic_id)
        name_key = "|".join(
            [
                subject,
                stage,
                str(topic.data.get("name") or "").strip(),
            ]
        )
        duplicate_name_keys[name_key] = duplicate_name_keys.get(name_key, 0) + 1
        local_edges = 0
        local_relations: set[str] = set()
        for field in ("prerequisites", "related"):
            refs = topic.data.get(field)
            if not isinstance(refs, list):
                continue
            for ref in refs:
                target_id = _ref_id(ref)
                if not target_id:
                    continue
                relation = _edge_relation(field, ref)
                local_relations.add(relation)
                edge_counts[relation] = edge_counts.get(relation, 0) + 1
                local_edges += 1
                if _is_typed_edge(ref):
                    typed_edges += 1
                else:
                    legacy_edges += 1
                    if len(legacy_edge_samples) < LEGACY_EDGE_SAMPLE_LIMIT:
                        legacy_edge_samples.append(
                            {
                                "source": topic_id,
                                "target": target_id,
                                "field": field,
                                "relation": relation,
                            }
                        )
                if topic_id:
                    outbound[topic_id] = outbound.get(topic_id, 0) + 1
                if target_id in inbound:
                    inbound[target_id] = inbound.get(target_id, 0) + 1
                if relation == "prerequisite" and topic_id:
                    prerequisite_edges.setdefault(topic_id, set()).add(target_id)
                target_subject = topic_subject_by_id.get(target_id)
                if target_subject and target_subject != subject:
                    subject_edges = cross_subject_edge_counts.setdefault(subject, {})
                    subject_edges[target_subject] = subject_edges.get(target_subject, 0) + 1
                    cross_subject_relation_counts[relation] = (
                        cross_subject_relation_counts.get(relation, 0) + 1
                    )
        if local_edges > 24 and topic_id:
            over_connected_topics.append(topic_id)
        standard = SUBJECT_MINIMUM_STANDARDS.get(subject)
        if standard:
            subject_minimum_standard_topic_counts[subject] = (
                subject_minimum_standard_topic_counts.get(subject, 0) + 1
            )
            chapter = str(topic.data.get("chapter") or "").strip() or "<missing>"
            required_relations = set(standard.get("relations", ()))
            required_fields = set(standard.get("fields", ()))
            missing_relations = sorted(required_relations - local_relations)
            missing_fields = sorted(
                field for field in required_fields if not topic.data.get(field)
            )
            if missing_relations or missing_fields:
                chapter_gaps = chapter_gap_counts.setdefault(subject, {})
                chapter_gaps[chapter] = chapter_gaps.get(chapter, 0) + 1
                subject_minimum_standard_gap_counts[subject] = (
                    subject_minimum_standard_gap_counts.get(subject, 0) + 1
                )
                relation_gaps = subject_minimum_standard_relation_gap_counts.setdefault(
                    subject, {}
                )
                for relation in missing_relations:
                    relation_gaps[relation] = relation_gaps.get(relation, 0) + 1
                field_gaps = subject_minimum_standard_field_gap_counts.setdefault(
                    subject, {}
                )
                for field in missing_fields:
                    field_gaps[field] = field_gaps.get(field, 0) + 1
                samples = subject_minimum_standard_gap_samples.setdefault(subject, [])
                if len(samples) < SUBJECT_MINIMUM_GAP_SAMPLE_LIMIT:
                    samples.append(
                        {
                            "id": topic_id,
                            "missing_relations": missing_relations,
                            "missing_fields": missing_fields,
                        }
                    )
                minimum_gap_candidates.setdefault(subject, []).append(
                    {
                        "id": topic_id,
                        "chapter": chapter,
                        "unit": str(topic.data.get("unit") or "").strip(),
                        "missing_relations": missing_relations,
                        "missing_fields": missing_fields,
                        "missing_count": len(missing_relations) + len(missing_fields),
                        "edge_count": local_edges,
                    }
                )
            else:
                chapter_ready = chapter_ready_counts.setdefault(subject, {})
                chapter_ready[chapter] = chapter_ready.get(chapter, 0) + 1
                subject_minimum_standard_ready_counts[subject] = (
                    subject_minimum_standard_ready_counts.get(subject, 0) + 1
                )
        else:
            subject_minimum_standard_uncovered_subject_counts[subject] = (
                subject_minimum_standard_uncovered_subject_counts.get(subject, 0) + 1
            )

    isolated_topics = sorted(
        topic_id
        for topic_id in topic_ids
        if inbound.get(topic_id, 0) == 0 and outbound.get(topic_id, 0) == 0
    )
    duplicate_names = sum(1 for count in duplicate_name_keys.values() if count > 1)

    cycle_nodes = _find_prerequisite_cycle_nodes(prerequisite_edges)
    standard_subjects = set(SUBJECT_MINIMUM_STANDARDS)
    for subject in standard_subjects:
        subject_minimum_standard_topic_counts.setdefault(subject, 0)
        subject_minimum_standard_ready_counts.setdefault(subject, 0)
        subject_minimum_standard_gap_counts.setdefault(subject, 0)
    subject_minimum_standard_ready_rates = {
        subject: (
            subject_minimum_standard_ready_counts.get(subject, 0)
            / subject_minimum_standard_topic_counts[subject]
            if subject_minimum_standard_topic_counts[subject]
            else 0.0
        )
        for subject in standard_subjects
    }
    subject_minimum_standard_gap_rates = {
        subject: (
            subject_minimum_standard_gap_counts.get(subject, 0)
            / subject_minimum_standard_topic_counts[subject]
            if subject_minimum_standard_topic_counts[subject]
            else 0.0
        )
        for subject in standard_subjects
    }
    top_missing_relation_by_subject = {
        subject: [
            {"relation": relation, "count": count}
            for relation, count in sorted(
                counts.items(), key=lambda item: (-item[1], item[0])
            )
        ]
        for subject, counts in sorted(
            subject_minimum_standard_relation_gap_counts.items()
        )
    }
    top_gap_topics_by_subject = {
        subject: sorted(
            candidates,
            key=lambda item: (
                -int(item["missing_count"]),
                int(item["edge_count"]),
                str(item["chapter"]),
                str(item["id"]),
            ),
        )[:QUALITY_ACTION_LIST_LIMIT]
        for subject, candidates in sorted(minimum_gap_candidates.items())
    }
    recommended_next_batch = {
        subject: [str(item["id"]) for item in items[:QUALITY_ACTION_LIST_LIMIT]]
        for subject, items in top_gap_topics_by_subject.items()
    }

    return {
        "topic_count": len(topics),
        "edge_count": typed_edges + legacy_edges,
        "typed_edges": typed_edges,
        "legacy_edges": legacy_edges,
        "missing_stage": missing_stage,
        "schema_ready_topics": schema_ready_topics,
        "missing_college_course_family": len(missing_college_course_family),
        "isolated_nodes": len(isolated_topics),
        "over_connected_nodes": len(over_connected_topics),
        "duplicate_name_keys": duplicate_names,
        "cycles_in_prerequisites": len(cycle_nodes),
        "stage_counts": dict(sorted(stage_counts.items())),
        "subject_counts": dict(sorted(subject_counts.items())),
        "subject_schema_ready_counts": dict(sorted(subject_schema_ready_counts.items())),
        "subject_minimum_standards": {
            subject: {
                "relations": list(standard.get("relations", ())),
                "fields": list(standard.get("fields", ())),
            }
            for subject, standard in sorted(SUBJECT_MINIMUM_STANDARDS.items())
        },
        "subject_minimum_standard_topic_counts": dict(
            sorted(subject_minimum_standard_topic_counts.items())
        ),
        "subject_minimum_standard_ready_counts": dict(
            sorted(subject_minimum_standard_ready_counts.items())
        ),
        "subject_minimum_standard_gap_counts": dict(
            sorted(subject_minimum_standard_gap_counts.items())
        ),
        "subject_minimum_standard_ready_rates": dict(
            sorted(subject_minimum_standard_ready_rates.items())
        ),
        "subject_minimum_standard_gap_rates": dict(
            sorted(subject_minimum_standard_gap_rates.items())
        ),
        "subject_minimum_standard_relation_gap_counts": {
            subject: dict(sorted(counts.items()))
            for subject, counts in sorted(
                subject_minimum_standard_relation_gap_counts.items()
            )
        },
        "subject_minimum_standard_field_gap_counts": {
            subject: dict(sorted(counts.items()))
            for subject, counts in sorted(subject_minimum_standard_field_gap_counts.items())
        },
        "subject_minimum_standard_uncovered_subject_counts": dict(
            sorted(subject_minimum_standard_uncovered_subject_counts.items())
        ),
        "top_gap_topics_by_subject": top_gap_topics_by_subject,
        "top_missing_relation_by_subject": top_missing_relation_by_subject,
        "chapter_ready_counts": {
            subject: dict(sorted(counts.items()))
            for subject, counts in sorted(chapter_ready_counts.items())
        },
        "chapter_gap_counts": {
            subject: dict(sorted(counts.items()))
            for subject, counts in sorted(chapter_gap_counts.items())
        },
        "cross_subject_edge_counts": {
            subject: dict(sorted(counts.items()))
            for subject, counts in sorted(cross_subject_edge_counts.items())
        },
        "cross_subject_relation_counts": dict(
            sorted(cross_subject_relation_counts.items())
        ),
        "legacy_edge_samples": legacy_edge_samples,
        "recommended_next_batch": recommended_next_batch,
        "relation_counts": dict(sorted(edge_counts.items())),
        "sample_isolated_nodes": isolated_topics[:10],
        "sample_missing_college_course_family": sorted(missing_college_course_family)[:10],
        "sample_over_connected_nodes": sorted(over_connected_topics)[:10],
        "sample_prerequisite_cycle_nodes": sorted(cycle_nodes)[:10],
        "sample_subject_minimum_standard_gaps": {
            subject: samples
            for subject, samples in sorted(subject_minimum_standard_gap_samples.items())
        },
    }


def _validate_graph_quality(
    topics: tuple[KnowledgeSeedTopic, ...],
    report: dict[str, Any],
    issues: list[KnowledgeSeedIssue],
) -> None:
    if not report.get("cycles_in_prerequisites"):
        return
    topic_by_id = {
        str(topic.data.get("id") or "").strip(): topic
        for topic in topics
        if str(topic.data.get("id") or "").strip()
    }
    for topic_id in report.get("sample_prerequisite_cycle_nodes", []):
        topic = topic_by_id.get(str(topic_id))
        issues.append(
            KnowledgeSeedIssue(
                "prerequisite_cycle",
                "prerequisites must not form a cycle",
                str(topic.path if topic else ""),
                str(topic_id),
            )
        )


def validate_knowledge_seed_manifest(path: Path | str) -> KnowledgeSeedValidationResult:
    manifest_path = Path(path).resolve()
    issues: list[KnowledgeSeedIssue] = []
    manifest_payload = _read_json(manifest_path, issues)
    if manifest_payload is None:
        return KnowledgeSeedValidationResult((), tuple(issues), _build_quality_report(()))
    taxonomy = _load_taxonomy(manifest_path, issues)

    topics: list[KnowledgeSeedTopic] = []
    manifest_seed_paths: dict[Path, set[Path]] = {}
    for seed_path in _iter_seed_files(
        manifest_path,
        manifest_payload,
        issues,
        manifest_seed_paths=manifest_seed_paths,
    ):
        payload = _read_json(seed_path, issues)
        if payload is None:
            continue
        raw_topics = payload.get("topics")
        if not isinstance(raw_topics, list):
            issues.append(
                KnowledgeSeedIssue(
                    "invalid_topics",
                    "seed file must include a topics list",
                    str(seed_path),
                )
            )
            continue
        for raw_topic in raw_topics:
            if not isinstance(raw_topic, dict):
                issues.append(
                    KnowledgeSeedIssue(
                        "invalid_topic",
                        "topic entry must be an object",
                        str(seed_path),
                    )
                )
                continue
            topic = _normalize_topic(seed_path, payload, raw_topic)
            _validate_topic_fields(topic, issues, taxonomy)
            _validate_examples(topic, issues)
            topics.append(topic)

    topic_ids: set[str] = set()
    for topic in topics:
        topic_id = str(topic.data.get("id") or "").strip()
        if not topic_id:
            continue
        if topic_id in topic_ids:
            issues.append(
                KnowledgeSeedIssue(
                    "duplicate_topic_id",
                    f"duplicate topic id: {topic_id}",
                    str(topic.path),
                    topic_id,
                )
            )
            continue
        topic_ids.add(topic_id)
    _validate_references(topics, topic_ids, issues)
    _validate_taxonomy_coverage(topics, issues)
    _validate_stage_specific_context(topics, issues)

    if isinstance(manifest_payload.get("files"), list):
        for item in manifest_payload["files"]:
            if not isinstance(item, dict) or "topic_count" not in item:
                continue
            raw_path = str(item.get("path") or item.get("file") or "").strip()
            seed_path = Path(raw_path)
            if not seed_path.is_absolute():
                seed_path = manifest_path.parent / seed_path
            expected = item.get("topic_count")
            resolved_seed_path = seed_path.resolve()
            counted_paths = manifest_seed_paths.get(
                resolved_seed_path, {resolved_seed_path}
            )
            actual = sum(1 for topic in topics if topic.path.resolve() in counted_paths)
            if expected != actual:
                issues.append(
                    KnowledgeSeedIssue(
                        "manifest_topic_count_mismatch",
                        f"manifest topic_count for {raw_path} is {expected}, actual {actual}",
                        str(manifest_path),
                    )
                )

    topic_tuple = tuple(topics)
    report = _build_quality_report(topic_tuple)
    _validate_graph_quality(topic_tuple, report, issues)
    return KnowledgeSeedValidationResult(
        topic_tuple,
        tuple(issues),
        report,
    )


def _format_quality_report(report: dict[str, Any] | None) -> list[str]:
    if not report:
        return []
    lines = ["Knowledge Seed Quality Report"]
    for key in (
        "topic_count",
        "edge_count",
        "typed_edges",
        "legacy_edges",
        "missing_stage",
        "schema_ready_topics",
        "missing_college_course_family",
        "isolated_nodes",
        "over_connected_nodes",
        "duplicate_name_keys",
        "cycles_in_prerequisites",
    ):
        lines.append(f"{key}: {report.get(key, 0)}")
    relation_counts = report.get("relation_counts")
    if isinstance(relation_counts, dict) and relation_counts:
        lines.append("relation_counts:")
        for relation, count in relation_counts.items():
            lines.append(f"  {relation}: {count}")
    topic_counts = report.get("subject_minimum_standard_topic_counts")
    ready_counts = report.get("subject_minimum_standard_ready_counts")
    gap_counts = report.get("subject_minimum_standard_gap_counts")
    ready_rates = report.get("subject_minimum_standard_ready_rates")
    if isinstance(topic_counts, dict) and topic_counts:
        lines.append("subject_minimum_standard_ready_counts:")
        for subject, total in topic_counts.items():
            count = 0
            if isinstance(ready_counts, dict):
                count = int(ready_counts.get(subject, 0) or 0)
            gap_count = 0
            if isinstance(gap_counts, dict):
                gap_count = int(gap_counts.get(subject, 0) or 0)
            rate = 0.0
            if isinstance(ready_rates, dict):
                rate = float(ready_rates.get(subject, 0.0) or 0.0)
            lines.append(
                f"  {subject}: {count}/{int(total)} ready, {gap_count} gaps, {rate:.2%}"
            )
    top_missing = report.get("top_missing_relation_by_subject")
    if isinstance(top_missing, dict) and top_missing:
        lines.append("top_missing_relation_by_subject:")
        for subject, entries in top_missing.items():
            if not isinstance(entries, list) or not entries:
                continue
            summary = ", ".join(
                f"{entry.get('relation')}: {entry.get('count')}"
                for entry in entries[:5]
                if isinstance(entry, dict)
            )
            if summary:
                lines.append(f"  {subject}: {summary}")
    next_batch = report.get("recommended_next_batch")
    if isinstance(next_batch, dict) and next_batch:
        lines.append("recommended_next_batch:")
        for subject, topic_ids in next_batch.items():
            if not isinstance(topic_ids, list) or not topic_ids:
                continue
            preview = ", ".join(str(topic_id) for topic_id in topic_ids[:5])
            lines.append(f"  {subject}: {preview}")
    cross_subject = report.get("cross_subject_edge_counts")
    if isinstance(cross_subject, dict) and cross_subject:
        lines.append("cross_subject_edge_counts:")
        for subject, counts in cross_subject.items():
            if not isinstance(counts, dict) or not counts:
                continue
            summary = ", ".join(
                f"{target}: {count}" for target, count in list(counts.items())[:5]
            )
            lines.append(f"  {subject}: {summary}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Study Companion knowledge seeds.")
    parser.add_argument(
        "path",
        nargs="?",
        default=Path(__file__).resolve().parent / "static" / "knowledge_graph_seed.json",
        help="Path to knowledge_graph_seed.json or a legacy seed file.",
    )
    args = parser.parse_args(argv)
    result = validate_knowledge_seed_manifest(Path(args.path))
    for line in _format_quality_report(result.report):
        print(line)
    if result.is_valid:
        print(f"validated {len(result.topics)} knowledge seed topics")
        return 0
    for issue in result.issues:
        location = f"{issue.path}"
        if issue.topic_id:
            location += f" topic={issue.topic_id}"
        print(f"{issue.code}: {location}: {issue.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
