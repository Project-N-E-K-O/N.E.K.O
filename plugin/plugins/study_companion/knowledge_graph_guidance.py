from __future__ import annotations

from collections import deque
from typing import Any


APPLICATION_RELATIONS = {"application", "procedure_step", "extends", "supports"}
CONFUSION_RELATIONS = {"confusable"}
NEXT_PRACTICE_RELATIONS = {"application", "procedure_step", "extends", "co_occurs", "next"}
RELATION_PRIORITY = {
    "prerequisite": 0,
    "procedure_step": 1,
    "application": 2,
    "supports": 3,
    "extends": 4,
    "co_occurs": 5,
    "next": 6,
    "confusable": 7,
}
GENERIC_QUERY_TERMS = {
    "\u4e0d\u4f1a",
    "\u4e0d\u61c2",
    "\u600e\u4e48",
    "\u600e\u4e48\u5b66",
    "\u4e48\u5b66",
    "\u5982\u4f55",
    "\u5b66\u4e60",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _topic_id(topic: dict[str, Any]) -> str:
    return _text(topic.get("id") or topic.get("topic_id"))


def _topic_label(topic: dict[str, Any] | None, fallback: str = "") -> str:
    if not topic:
        return fallback
    return _text(topic.get("name") or topic.get("label") or _topic_id(topic) or fallback)


def _topic_aliases(topic: dict[str, Any]) -> list[str]:
    value = topic.get("aliases")
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]


def _ref_id(value: Any) -> str:
    if isinstance(value, dict):
        return _text(value.get("id") or value.get("topic_id"))
    return _text(value)


def _edge_relation(field: str, value: Any) -> str:
    if isinstance(value, dict):
        relation = _text(value.get("relation"))
        if relation:
            return relation
    return "prerequisite" if field == "prerequisites" else "co_occurs"


def _edge_reason(value: Any) -> str:
    return _text(value.get("reason")) if isinstance(value, dict) else ""


def _edge_use_cases(value: Any) -> list[str]:
    if not isinstance(value, dict) or not isinstance(value.get("use_cases"), list):
        return []
    return [_text(item) for item in value["use_cases"] if _text(item)]


def _relation_priority(edge: dict[str, Any]) -> int:
    return RELATION_PRIORITY.get(_text(edge.get("relation")), 99)


def _edge_payload(
    *,
    source: dict[str, Any] | None,
    target: dict[str, Any] | None,
    source_id: str,
    target_id: str,
    relation: str,
    ref: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "from": source_id,
        "to": target_id,
        "from_label": _topic_label(source, source_id),
        "to_label": _topic_label(target, target_id),
        "relation": relation,
    }
    reason = _edge_reason(ref)
    if reason:
        payload["reason"] = reason
    use_cases = _edge_use_cases(ref)
    if use_cases:
        payload["use_cases"] = use_cases
    if isinstance(ref, dict) and ref.get("required_mastery") is not None:
        payload["required_mastery"] = ref.get("required_mastery")
    return payload


def build_topic_edges(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {_topic_id(topic): topic for topic in topics if _topic_id(topic)}
    edges: list[dict[str, Any]] = []
    for topic in topics:
        target_id = _topic_id(topic)
        if not target_id:
            continue
        for ref in topic.get("prerequisites") or []:
            source_id = _ref_id(ref)
            if not source_id:
                continue
            edges.append(
                _edge_payload(
                    source=by_id.get(source_id),
                    target=topic,
                    source_id=source_id,
                    target_id=target_id,
                    relation=_edge_relation("prerequisites", ref),
                    ref=ref,
                )
            )
        for ref in topic.get("related") or []:
            related_id = _ref_id(ref)
            if not related_id:
                continue
            edges.append(
                _edge_payload(
                    source=topic,
                    target=by_id.get(related_id),
                    source_id=target_id,
                    target_id=related_id,
                    relation=_edge_relation("related", ref),
                    ref=ref,
                )
            )
    return edges


def _topic_search_text(topic: dict[str, Any]) -> str:
    parts: list[str] = [
        _topic_id(topic),
        _topic_label(topic),
        _text(topic.get("subject")),
        _text(topic.get("chapter")),
        _text(topic.get("unit")),
        _text(topic.get("course_family")),
    ]
    parts.extend(_topic_aliases(topic))
    for field in ("skills", "question_types", "typical_misconceptions"):
        value = topic.get(field)
        if isinstance(value, list):
            parts.extend(_text(item) for item in value if _text(item))
    for example in topic.get("examples") or []:
        if isinstance(example, dict):
            parts.append(_text(example.get("prompt")))
    return " ".join(part for part in parts if part).lower()


def _query_terms(query: str) -> list[str]:
    normalized = _text(query).lower()
    if not normalized:
        return []
    terms = {normalized}
    for raw_part in normalized.replace("，", " ").replace("。", " ").split():
        if raw_part:
            terms.add(raw_part)
        cjk_chars = [char for char in raw_part if "\u4e00" <= char <= "\u9fff"]
        cjk = "".join(cjk_chars)
        for size in (2, 3, 4):
            for index in range(0, max(0, len(cjk) - size + 1)):
                terms.add(cjk[index : index + size])
    return sorted(
        (term for term in terms if term not in GENERIC_QUERY_TERMS),
        key=lambda item: (-len(item), item),
    )


def match_topics(
    topics: list[dict[str, Any]],
    *,
    topic_id: str = "",
    query: str = "",
    limit: int = 5,
) -> list[dict[str, Any]]:
    by_id = {_topic_id(topic): topic for topic in topics if _topic_id(topic)}
    topic_key = _text(topic_id)
    if topic_key and topic_key in by_id:
        return [
            {
                "id": topic_key,
                "label": _topic_label(by_id[topic_key], topic_key),
                "score": 100,
                "match": "topic_id",
            }
        ]
    terms = _query_terms(query or topic_id)
    if not terms:
        return []
    scored: list[dict[str, Any]] = []
    for topic in topics:
        current_id = _topic_id(topic)
        if not current_id:
            continue
        label = _topic_label(topic, current_id)
        aliases = [alias.lower() for alias in _topic_aliases(topic)]
        haystack = _topic_search_text(topic)
        score = 0
        matched_terms: list[str] = []
        for term in terms:
            if not term:
                continue
            if term == current_id.lower() or term == label.lower():
                score += 20
            elif term in aliases:
                score += 18
            elif any(term in alias for alias in aliases):
                score += 10
            elif term in label.lower():
                score += 8
            elif term in haystack:
                score += 3
            else:
                continue
            matched_terms.append(term)
        if score:
            scored.append(
                {
                    "id": current_id,
                    "label": label,
                    "score": score,
                    "match": "query",
                    "matched_terms": matched_terms[:6],
                }
            )
    return sorted(scored, key=lambda item: (-int(item["score"]), item["label"]))[
        : max(1, int(limit or 5))
    ]


def _learning_path_for_topic(
    *,
    topic_id: str,
    by_id: dict[str, dict[str, Any]],
    incoming: dict[str, list[dict[str, Any]]],
    max_depth: int,
) -> list[dict[str, Any]]:
    queue: deque[tuple[str, int]] = deque([(topic_id, 0)])
    seen = {topic_id}
    path: list[dict[str, Any]] = []
    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for edge in sorted(incoming.get(current_id, []), key=_relation_priority):
            parent_id = _text(edge.get("from"))
            if not parent_id or parent_id in seen:
                continue
            seen.add(parent_id)
            item = dict(edge)
            item["depth"] = depth + 1
            item["topic"] = by_id.get(parent_id, {})
            path.append(item)
            queue.append((parent_id, depth + 1))
    return path


def _dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for edge in edges:
        key = (
            _text(edge.get("from")),
            _text(edge.get("to")),
            _text(edge.get("relation")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(edge)
    return unique


def _other_topic_for_edge(edge: dict[str, Any], selected_id: str) -> tuple[str, str]:
    if _text(edge.get("from")) == selected_id:
        return _text(edge.get("to")), _text(edge.get("to_label"))
    return _text(edge.get("from")), _text(edge.get("from_label"))


def _question_payload(
    *,
    kind: str,
    topic_id: str,
    topic_label: str,
    question: str,
    edge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": kind,
        "topic_id": topic_id,
        "topic_label": topic_label or topic_id,
        "question": question,
    }
    if edge:
        payload["relation"] = _text(edge.get("relation"))
        reason = _text(edge.get("reason"))
        if reason:
            payload["reason"] = reason
    return payload


def _build_diagnosis_questions(
    *,
    selected_id: str,
    selected_label: str,
    learning_path: list[dict[str, Any]],
    confusions: list[dict[str, Any]],
    next_practice: list[dict[str, Any]],
    limit: int = 8,
) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(payload: dict[str, Any]) -> None:
        key = (_text(payload.get("kind")), _text(payload.get("topic_id")))
        if not key[0] or not key[1] or key in seen:
            return
        seen.add(key)
        questions.append(payload)

    for edge in sorted(learning_path, key=lambda item: (_relation_priority(item), int(item.get("depth") or 0))):
        relation = _text(edge.get("relation"))
        if relation not in {"prerequisite", "application", "procedure_step"}:
            continue
        topic_id, topic_label = _other_topic_for_edge(edge, selected_id)
        if not topic_id or topic_id == selected_id:
            continue
        if relation == "procedure_step":
            add(
                _question_payload(
                    kind="procedure_probe",
                    topic_id=topic_id,
                    topic_label=topic_label,
                    question=(
                        f"你是卡在“{topic_label or topic_id}”这一步，"
                        f"还是不知道它在“{selected_label}”里该放到哪里？"
                    ),
                    edge=edge,
                )
            )
            continue
        if relation == "application":
            add(
                _question_payload(
                    kind="application_practice",
                    topic_id=topic_id,
                    topic_label=topic_label,
                    question=(
                        f"要不要用“{topic_label or topic_id}”做一道典型题，"
                        f"看看“{selected_label}”怎样落到题目里？"
                    ),
                    edge=edge,
                )
            )
            continue
        add(
            _question_payload(
                kind="prerequisite_probe",
                topic_id=topic_id,
                topic_label=topic_label,
                question=(
                    f"你是卡在“{topic_label or topic_id}”，"
                    f"还是不知道它怎样用于“{selected_label}”？"
                ),
                edge=edge,
            )
        )
        if len([item for item in questions if item["kind"] == "prerequisite_probe"]) >= 3:
            break

    for edge in confusions:
        topic_id, topic_label = _other_topic_for_edge(edge, selected_id)
        if not topic_id or topic_id == selected_id:
            continue
        add(
            _question_payload(
                kind="confusion_check",
                topic_id=topic_id,
                topic_label=topic_label,
                question=f"你是不是把“{selected_label}”和“{topic_label or topic_id}”混在一起了？",
                edge=edge,
            )
        )
        if len([item for item in questions if item["kind"] == "confusion_check"]) >= 2:
            break

    for edge in next_practice:
        relation = _text(edge.get("relation"))
        topic_id, topic_label = _other_topic_for_edge(edge, selected_id)
        if not topic_id or topic_id == selected_id:
            continue
        if relation == "application":
            add(
                _question_payload(
                    kind="application_practice",
                    topic_id=topic_id,
                    topic_label=topic_label,
                    question=(
                        f"要不要用“{topic_label or topic_id}”做一道典型题，"
                        f"把“{selected_label}”用到具体场景里？"
                    ),
                    edge=edge,
                )
            )
        elif relation == "procedure_step":
            add(
                _question_payload(
                    kind="procedure_probe",
                    topic_id=topic_id,
                    topic_label=topic_label,
                    question=(
                        f"你是卡在“{topic_label or topic_id}”这一步，"
                        f"还是不知道它怎样推进“{selected_label}”？"
                    ),
                    edge=edge,
                )
            )
        elif relation == "extends":
            add(
                _question_payload(
                    kind="extension_suggestion",
                    topic_id=topic_id,
                    topic_label=topic_label,
                    question=(
                        f"如果基础判断已经会了，要不要进阶到“{topic_label or topic_id}”？"
                    ),
                    edge=edge,
                )
            )
        elif relation == "co_occurs":
            add(
                _question_payload(
                    kind="related_review",
                    topic_id=topic_id,
                    topic_label=topic_label,
                    question=(
                        f"要不要顺手复习“{topic_label or topic_id}”，"
                        f"它经常和“{selected_label}”一起出现？"
                    ),
                    edge=edge,
                )
            )
        else:
            add(
                _question_payload(
                    kind="next_step",
                    topic_id=topic_id,
                    topic_label=topic_label,
                    question=(
                        f"要不要下一步练“{topic_label or topic_id}”，"
                        f"把“{selected_label}”用到具体题里？"
                    ),
                    edge=edge,
                )
            )
        if len(questions) >= limit:
            break

    return questions[:limit]


def build_knowledge_guidance_payload(
    *,
    topics: list[dict[str, Any]],
    topic_id: str = "",
    query: str = "",
    max_depth: int = 3,
    match_limit: int = 5,
) -> dict[str, Any]:
    topic_items = list(topics or [])
    by_id = {_topic_id(topic): topic for topic in topic_items if _topic_id(topic)}
    edges = build_topic_edges(topic_items)
    incoming: dict[str, list[dict[str, Any]]] = {}
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        incoming.setdefault(_text(edge.get("to")), []).append(edge)
        outgoing.setdefault(_text(edge.get("from")), []).append(edge)

    matches = match_topics(
        topic_items,
        topic_id=topic_id,
        query=query,
        limit=match_limit,
    )
    selected_id = _text(matches[0]["id"]) if matches else _text(topic_id)
    selected_topic = by_id.get(selected_id)
    if not selected_topic:
        return {
            "topic": {},
            "matches": matches,
            "learning_path": [],
            "applications": [],
            "confusions": [],
            "next_practice_topics": [],
            "diagnosis_questions": [],
            "summary": {
                "matched": False,
                "topic_count": len(topic_items),
                "edge_count": len(edges),
                "diagnosis_question_count": 0,
            },
        }

    learning_path = _learning_path_for_topic(
        topic_id=selected_id,
        by_id=by_id,
        incoming=incoming,
        max_depth=max(1, int(max_depth or 3)),
    )
    outgoing_edges = outgoing.get(selected_id, [])
    applications = [
        edge for edge in outgoing_edges if _text(edge.get("relation")) in APPLICATION_RELATIONS
    ]
    incoming_edges = incoming.get(selected_id, [])
    confusions = _dedupe_edges(
        [
            edge
            for edge in [*outgoing_edges, *incoming_edges]
            if _text(edge.get("relation")) in CONFUSION_RELATIONS
        ]
    )
    next_practice = [
        edge
        for edge in outgoing_edges
        if _text(edge.get("relation")) in NEXT_PRACTICE_RELATIONS
    ]
    diagnosis_questions = _build_diagnosis_questions(
        selected_id=selected_id,
        selected_label=_topic_label(selected_topic, selected_id),
        learning_path=learning_path,
        confusions=confusions,
        next_practice=next_practice,
    )
    return {
        "topic": {
            "id": selected_id,
            "label": _topic_label(selected_topic, selected_id),
            "subject": _text(selected_topic.get("subject")),
            "stage": _text(selected_topic.get("stage")),
            "chapter": _text(selected_topic.get("chapter")),
            "unit": _text(selected_topic.get("unit")),
            "course_family": _text(selected_topic.get("course_family")),
            "aliases": _topic_aliases(selected_topic),
            "typical_misconceptions": list(
                selected_topic.get("typical_misconceptions") or []
            ),
        },
        "matches": matches,
        "learning_path": learning_path,
        "applications": applications,
        "confusions": confusions,
        "next_practice_topics": next_practice,
        "diagnosis_questions": diagnosis_questions,
        "summary": {
            "matched": True,
            "topic_count": len(topic_items),
            "edge_count": len(edges),
            "learning_path_count": len(learning_path),
            "application_count": len(applications),
            "confusion_count": len(confusions),
            "next_practice_count": len(next_practice),
            "diagnosis_question_count": len(diagnosis_questions),
        },
    }
