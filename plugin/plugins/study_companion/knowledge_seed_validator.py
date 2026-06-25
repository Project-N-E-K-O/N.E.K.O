from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REQUIRED_SCALAR_FIELDS = ("id", "name", "subject", "stage", "chapter", "unit")
REQUIRED_LIST_FIELDS = (
    "prerequisites",
    "related",
    "skills",
    "question_types",
    "examples",
    "typical_misconceptions",
)
NON_EMPTY_LIST_FIELDS = (
    "skills",
    "question_types",
    "examples",
    "typical_misconceptions",
)
RESERVED_CONTEXT_FIELDS = ("curriculum_version", "exam_region", "exam_type")
TAXONOMY_FILE_NAME = "knowledge_seed_taxonomy.json"


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
) -> Iterable[Path]:
    files = payload.get("files")
    if not isinstance(files, list):
        yield manifest_path
        return
    seen_paths: set[Path] = set()
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
        if child_path in seen_paths:
            issues.append(
                KnowledgeSeedIssue(
                    "duplicate_manifest_file",
                    f"manifest references duplicate seed file: {child_name}",
                    str(manifest_path),
                )
            )
            continue
        seen_paths.add(child_path)
        if not child_path.is_file():
            issues.append(
                KnowledgeSeedIssue(
                    "missing_manifest_file",
                    f"manifest seed file does not exist: {child_name}",
                    str(manifest_path),
                )
            )
            continue
        yield child_path


def _normalize_topic(
    path: Path,
    payload: dict[str, Any],
    topic: dict[str, Any],
) -> KnowledgeSeedTopic:
    subject = str(topic.get("subject") or payload.get("subject") or "").strip()
    stage = str(
        topic.get("stage")
        or topic.get("grade_level")
        or topic.get("education_level")
        or topic.get("course_level")
        or _default_stage(payload)
    ).strip()
    return KnowledgeSeedTopic(path=path, data=topic, subject=subject, stage=stage)


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
        "unit": str(data.get("unit") or "").strip(),
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
        if field in NON_EMPTY_LIST_FIELDS and not value:
            issues.append(
                KnowledgeSeedIssue(
                    "empty_required_list",
                    f"topic field must not be empty: {field}",
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
    for topic in topics:
        data = topic.data
        topic_id = str(data.get("id") or "").strip()
        if topic.stage in {"primary", "junior_high", "senior_high", "college"}:
            missing = [
                field
                for field in RESERVED_CONTEXT_FIELDS
                if field not in data or not data.get(field)
            ]
            if missing:
                issues.append(
                    KnowledgeSeedIssue(
                        "missing_curriculum_context",
                        "topic missing curriculum context fields: "
                        + ",".join(missing),
                        str(topic.path),
                        topic_id,
                    )
                )


def _validate_stage_specific_context(
    topics: Iterable[KnowledgeSeedTopic],
    issues: list[KnowledgeSeedIssue],
) -> None:
    for topic in topics:
        data = topic.data
        topic_id = str(data.get("id") or "").strip()
        regions = data.get("exam_region")
        region_values = regions if isinstance(regions, list) else [regions]
        region_set = {str(item).strip() for item in region_values if str(item).strip()}
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
        return str(value.get("id") or "").strip()
    return str(value or "").strip()


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


def validate_knowledge_seed_manifest(path: Path | str) -> KnowledgeSeedValidationResult:
    manifest_path = Path(path).resolve()
    issues: list[KnowledgeSeedIssue] = []
    manifest_payload = _read_json(manifest_path, issues)
    if manifest_payload is None:
        return KnowledgeSeedValidationResult((), tuple(issues))
    taxonomy = _load_taxonomy(manifest_path, issues)

    topics: list[KnowledgeSeedTopic] = []
    for seed_path in _iter_seed_files(manifest_path, manifest_payload, issues):
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
            actual = sum(1 for topic in topics if topic.path.resolve() == seed_path.resolve())
            if expected != actual:
                issues.append(
                    KnowledgeSeedIssue(
                        "manifest_topic_count_mismatch",
                        f"manifest topic_count for {raw_path} is {expected}, actual {actual}",
                        str(manifest_path),
                    )
                )

    return KnowledgeSeedValidationResult(tuple(topics), tuple(issues))


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
