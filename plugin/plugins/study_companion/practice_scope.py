from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from .models import PracticeScopeV1, utc_now_iso


PRACTICE_SCOPE_SCHEMA_VERSION = 1
PRACTICE_SCOPE_MODES = frozenset({"explicit_scope", "explicit_topic"})
_MAX_SCOPE_VALUE_LENGTH = 160
_SERVER_OWNED_SCOPE_FIELDS = frozenset(
    {"scope_key", "scope_revision", "display_path", "eligible_topic_ids", "set_at", "source"}
)


class PracticeScopeError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _scope_text(value: object, *, machine_key: bool = False) -> str:
    text = str(value or "").strip()
    if len(text) > _MAX_SCOPE_VALUE_LENGTH:
        raise PracticeScopeError(
            "practice scope value is too long", code="INVALID_PRACTICE_SCOPE"
        )
    if machine_key:
        return text.lower().replace("-", "_").replace(" ", "_")
    return text


def _topic_value(topic: Mapping[str, Any], key: str) -> str:
    machine_key = key in {"stage", "subject", "course_family"}
    return _scope_text(topic.get(key), machine_key=machine_key)


def _requested_scope_fields(requested_scope: Mapping[str, Any]) -> dict[str, str]:
    forbidden = _SERVER_OWNED_SCOPE_FIELDS.intersection(requested_scope)
    if forbidden:
        raise PracticeScopeError(
            "practice scope contains server-owned fields",
            code="INVALID_PRACTICE_SCOPE",
        )
    try:
        schema_version = int(requested_scope.get("schema_version") or 1)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PracticeScopeError(
            "unsupported practice scope schema", code="INVALID_PRACTICE_SCOPE"
        ) from exc
    if schema_version != PRACTICE_SCOPE_SCHEMA_VERSION:
        raise PracticeScopeError(
            "unsupported practice scope schema", code="INVALID_PRACTICE_SCOPE"
        )
    mode = _scope_text(requested_scope.get("mode") or "explicit_scope", machine_key=True)
    if mode not in PRACTICE_SCOPE_MODES:
        raise PracticeScopeError(
            "unsupported practice scope mode", code="INVALID_PRACTICE_SCOPE"
        )
    fields = {
        "mode": mode,
        "stage": _scope_text(requested_scope.get("stage"), machine_key=True),
        "subject": _scope_text(requested_scope.get("subject"), machine_key=True),
        "course_family": _scope_text(
            requested_scope.get("course_family"), machine_key=True
        ),
        "chapter": _scope_text(requested_scope.get("chapter")),
        "unit": _scope_text(requested_scope.get("unit")),
        "topic_id": _scope_text(requested_scope.get("topic_id")),
    }
    if not fields["stage"] or not fields["subject"]:
        raise PracticeScopeError(
            "practice scope requires stage and subject",
            code="INVALID_PRACTICE_SCOPE",
        )
    if fields["stage"] == "college" and not fields["course_family"]:
        raise PracticeScopeError(
            "college practice scope requires course_family",
            code="INVALID_PRACTICE_SCOPE",
        )
    if fields["unit"] and not fields["chapter"]:
        raise PracticeScopeError(
            "unit practice scope requires chapter", code="INVALID_PRACTICE_SCOPE"
        )
    if mode == "explicit_topic" and not fields["topic_id"]:
        raise PracticeScopeError(
            "explicit topic scope requires topic_id",
            code="INVALID_PRACTICE_SCOPE",
        )
    if mode == "explicit_scope" and fields["topic_id"]:
        raise PracticeScopeError(
            "topic_id requires explicit_topic mode",
            code="INVALID_PRACTICE_SCOPE",
        )
    return fields


def _fields_match_topic(fields: Mapping[str, str], topic: Mapping[str, Any]) -> bool:
    for key in ("stage", "subject", "course_family", "chapter", "unit", "topic_id"):
        expected = str(fields.get(key) or "")
        if not expected:
            continue
        topic_key = "id" if key == "topic_id" else key
        if _topic_value(topic, topic_key) != expected:
            return False
    return True


def _scope_key(fields: Mapping[str, str]) -> str:
    identity = {
        "schema_version": PRACTICE_SCOPE_SCHEMA_VERSION,
        **{
            key: str(fields.get(key) or "")
            for key in (
                "mode",
                "stage",
                "subject",
                "course_family",
                "chapter",
                "unit",
                "topic_id",
            )
        },
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"ps1_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"


def build_practice_scope(
    requested_scope: Mapping[str, Any],
    topics: Iterable[Mapping[str, Any]],
    *,
    revision: int,
    source: str = "knowledge_map",
) -> PracticeScopeV1:
    if not isinstance(requested_scope, Mapping):
        raise PracticeScopeError(
            "practice scope must be an object", code="INVALID_PRACTICE_SCOPE"
        )
    fields = _requested_scope_fields(requested_scope)
    matching_topics = [dict(topic) for topic in topics if _fields_match_topic(fields, topic)]
    if not matching_topics:
        raise PracticeScopeError(
            "practice scope has no topics", code="NO_TOPICS_IN_SCOPE"
        )
    matching_topics.sort(key=lambda topic: str(topic.get("id") or ""))
    display_path = [fields["stage"], fields["subject"]]
    if fields["course_family"] and fields["course_family"] != fields["subject"]:
        display_path.append(fields["course_family"])
    if fields["chapter"]:
        display_path.append(fields["chapter"])
    if fields["unit"] and fields["unit"] != fields["chapter"]:
        display_path.append(fields["unit"])
    if fields["topic_id"]:
        selected = matching_topics[0]
        display_path.append(str(selected.get("name") or fields["topic_id"]))
    return PracticeScopeV1(
        schema_version=PRACTICE_SCOPE_SCHEMA_VERSION,
        mode=fields["mode"],
        stage=fields["stage"],
        subject=fields["subject"],
        course_family=fields["course_family"],
        chapter=fields["chapter"],
        unit=fields["unit"],
        topic_id=fields["topic_id"],
        scope_key=_scope_key(fields),
        scope_revision=max(1, int(revision)),
        display_path=display_path,
        source=_scope_text(source) or "knowledge_map",
        set_at=utc_now_iso(),
        eligible_topic_ids=[str(topic.get("id") or "") for topic in matching_topics],
    )


def practice_scope_matches_topic(
    scope: PracticeScopeV1 | Mapping[str, Any], topic: Mapping[str, Any]
) -> bool:
    fields = scope.to_public_dict() if isinstance(scope, PracticeScopeV1) else dict(scope)
    normalized = {
        "stage": _scope_text(fields.get("stage"), machine_key=True),
        "subject": _scope_text(fields.get("subject"), machine_key=True),
        "course_family": _scope_text(fields.get("course_family"), machine_key=True),
        "chapter": _scope_text(fields.get("chapter")),
        "unit": _scope_text(fields.get("unit")),
        "topic_id": _scope_text(fields.get("topic_id")),
    }
    return _fields_match_topic(normalized, topic)


def _candidate_topic_id(item: Mapping[str, Any] | None) -> str:
    value = dict(item or {})
    nested_topic = value.get("topic") if isinstance(value.get("topic"), Mapping) else {}
    payload = value.get("payload") if isinstance(value.get("payload"), Mapping) else {}
    payload_summary = (
        value.get("payload_summary")
        if isinstance(value.get("payload_summary"), Mapping)
        else {}
    )
    return str(
        value.get("topic_id")
        or nested_topic.get("id")
        or payload.get("topic_id")
        or payload.get("id")
        or payload_summary.get("topic_id")
        or payload_summary.get("id")
        or value.get("id")
        or ""
    ).strip()


def filter_question_params_to_scope(
    params: Mapping[str, Any], eligible_topic_ids: set[str] | frozenset[str]
) -> dict[str, Any]:
    eligible = {str(topic_id).strip() for topic_id in eligible_topic_ids if str(topic_id).strip()}
    filtered = deepcopy(dict(params or {}))
    target_id = str(filtered.get("target_topic_id") or "").strip()
    if target_id not in eligible:
        filtered["target_topic_id"] = ""
        filtered["target_topic"] = {}

    retry_candidates = [
        dict(item)
        for item in filtered.get("retry_wrong_questions") or []
        if isinstance(item, Mapping) and _candidate_topic_id(item) in eligible
    ]
    existing_retry = filtered.get("retry_wrong_question")
    if (
        isinstance(existing_retry, Mapping)
        and _candidate_topic_id(existing_retry) in eligible
        and dict(existing_retry) not in retry_candidates
    ):
        retry_candidates.insert(0, dict(existing_retry))
    filtered["retry_wrong_questions"] = retry_candidates
    filtered["retry_wrong_question"] = retry_candidates[0] if retry_candidates else {}

    for key in ("due_reviews", "weak_topics", "candidate_evidence"):
        filtered[key] = [
            dict(item)
            for item in filtered.get(key) or []
            if isinstance(item, Mapping) and _candidate_topic_id(item) in eligible
        ]
    return filtered


def ordered_scope_topics(
    topics: Iterable[Mapping[str, Any]], *, attempted_topic_ids: set[str] | frozenset[str]
) -> list[dict[str, Any]]:
    attempted = {str(topic_id).strip() for topic_id in attempted_topic_ids}

    def safe_number(value: object, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return number if math.isfinite(number) else default

    return sorted(
        (dict(topic) for topic in topics),
        key=lambda topic: (
            str(topic.get("id") or "") in attempted,
            safe_number(topic.get("depth"), 1.0),
            safe_number(topic.get("difficulty"), 0.5),
            str(topic.get("id") or ""),
        ),
    )
