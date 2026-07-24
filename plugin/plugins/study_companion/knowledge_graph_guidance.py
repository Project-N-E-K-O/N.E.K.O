from __future__ import annotations

from collections import deque
from typing import Any

from ._graph_utils import (
    dedupe_edges as _dedupe_edges,
    normalized_relation as _normalized_relation,
    text as _text,
    topic_id as _topic_id,
    topic_label as _topic_label,
)


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
    "\u600e\u4e48\u505a",
    "\u600e\u4e48\u6c42",
    "\u600e\u4e48\u533a\u5206",
    "\u4e48\u5b66",
    "\u5982\u4f55",
    "\u5b66\u4e60",
    "\u4ec0\u4e48",
    "\u533a\u522b",
    "\u5173\u7cfb",
    "\u4e3a\u4ec0\u4e48",
    "\u7528\u6765",
    "\u4e00\u5b9a",
    "\u4e0d\u4e00\u5b9a",
}
SUBJECT_QUERY_HINTS = {
    "math": {
        "\u6570\u5b66",
        "\u51fd\u6570",
        "\u65b9\u7a0b",
        "\u51e0\u4f55",
        "\u6982\u7387",
        "\u4ee3\u6570",
        "math",
        "mathematics",
    },
    "physics": {
        "\u725b\u987f",
        "\u53d7\u529b",
        "\u901f\u5ea6",
        "\u52a0\u901f\u5ea6",
        "\u529f",
        "\u80fd\u91cf",
        "\u7535\u573a",
        "\u7535\u52bf",
    },
    "chemistry": {
        "\u5316\u5b66",
        "\u6c27\u5316",
        "\u8fd8\u539f",
        "\u914d\u5e73",
        "\u5e73\u8861",
        "ph",
        "\u7535\u79bb",
    },
    "biology": {
        "\u57fa\u56e0",
        "\u9057\u4f20",
        "\u8868\u73b0\u578b",
        "\u51cf\u6570\u5206\u88c2",
        "\u6709\u4e1d\u5206\u88c2",
    },
    "english": {
        "\u9605\u8bfb\u7406\u89e3",
        "\u4e3b\u65e8",
        "\u5b8c\u5f62",
        "\u957f\u96be\u53e5",
        "\u63a8\u65ad\u9898",
        "\u7ec6\u8282\u9898",
    },
    "computer_science": {
        "\u6570\u7ec4",
        "\u94fe\u8868",
        "\u6700\u77ed\u8def",
        "bfs",
        "dfs",
        "\u904d\u5386",
    },
    "politics": {
        "\u653f\u6cbb",
        "\u6cd5\u6cbb",
        "\u516c\u6c11",
        "\u6c11\u4e3b",
        "\u54f2\u5b66",
        "politics",
        "civics",
        "government",
    },
    "chinese": {
        "\u8bed\u6587",
        "\u4e2d\u6587",
        "\u9605\u8bfb",
        "\u4f5c\u6587",
        "\u6587\u8a00\u6587",
        "\u8bd7\u6b4c",
        "chinese",
        "literature",
    },
    "history": {
        "\u5386\u53f2",
        "\u671d\u4ee3",
        "\u9769\u547d",
        "\u6218\u4e89",
        "\u6587\u660e",
        "history",
        "historical",
    },
    "geography": {
        "\u5730\u7406",
        "\u6c14\u5019",
        "\u5730\u5f62",
        "\u7ecf\u7eac\u5ea6",
        "\u533a\u57df",
        "geography",
        "climate",
    },
    "economics": {
        "\u7ecf\u6d4e",
        "\u4f9b\u7ed9",
        "\u9700\u6c42",
        "\u5e02\u573a",
        "\u901a\u8d27\u81a8\u80c0",
        "economics",
        "economy",
        "market",
    },
}
RELATION_GROUP_TITLES = {
    "prerequisite": "\u5148\u8865\u4ec0\u4e48",
    "confusable": "\u5bb9\u6613\u6df7\u5728\u54ea\u91cc",
    "procedure_step": "\u89e3\u9898\u6d41\u7a0b\u4e0b\u4e00\u6b65",
    "application": "\u5178\u578b\u7528\u9014",
    "extends": "\u540e\u7eed\u62d3\u5c55",
    "co_occurs": "\u4e00\u8d77\u590d\u4e60",
}
RELATION_GROUP_ORDER = tuple(RELATION_GROUP_TITLES)


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
        relation = _normalized_relation(value.get("relation"))
        if relation:
            return relation
    return "prerequisite" if field == "prerequisites" else "co_occurs"


def _edge_reason(value: Any) -> str:
    return _text(value.get("reason")) if isinstance(value, dict) else ""


def _edge_use_cases(value: Any) -> list[str]:
    if not isinstance(value, dict) or not isinstance(value.get("use_cases"), list):
        return []
    return [_text(item) for item in value["use_cases"] if _text(item)]


def _edge_priority_value(relation: str, ref: Any) -> str:
    if isinstance(ref, dict):
        priority = _text(ref.get("priority"))
        if priority in {"core", "useful", "optional"}:
            return priority
    if relation in {"prerequisite", "procedure_step", "confusable"}:
        return "core"
    if relation in {"application", "supports", "extends"}:
        return "useful"
    return "optional"


def _edge_context_value(relation: str, use_cases: list[str], ref: Any) -> str:
    if isinstance(ref, dict):
        context = _text(ref.get("context"))
        if context in {"diagnosis", "explanation", "practice", "review"}:
            return context
    if relation == "confusable":
        return "diagnosis"
    if relation in {"procedure_step", "application"}:
        return "practice"
    if relation in {"extends", "co_occurs"} or "review" in use_cases:
        return "review"
    return "explanation"


def _edge_confidence_value(ref: Any, *, reason: str, use_cases: list[str]) -> float:
    if isinstance(ref, dict):
        try:
            confidence = float(ref.get("confidence"))
        except (TypeError, ValueError):
            confidence = -1.0
        if 0.0 <= confidence <= 1.0:
            return confidence
    if reason and use_cases:
        return 0.95
    if reason or use_cases:
        return 0.85
    return 0.7


def _relation_priority(edge: dict[str, Any]) -> int:
    return RELATION_PRIORITY.get(_normalized_relation(edge.get("relation")), 99)


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
    payload["priority"] = _edge_priority_value(relation, ref)
    payload["context"] = _edge_context_value(relation, use_cases, ref)
    payload["confidence"] = _edge_confidence_value(
        ref,
        reason=reason,
        use_cases=use_cases,
    )
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
            relation = _edge_relation("related", ref)
            if relation == "prerequisite":
                edges.append(
                    _edge_payload(
                        source=by_id.get(related_id),
                        target=topic,
                        source_id=related_id,
                        target_id=target_id,
                        relation=relation,
                        ref=ref,
                    )
                )
                continue
            edges.append(
                _edge_payload(
                    source=topic,
                    target=by_id.get(related_id),
                    source_id=target_id,
                    target_id=related_id,
                    relation=relation,
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
        for stopword in sorted(GENERIC_QUERY_TERMS, key=len, reverse=True):
            cjk = cjk.replace(stopword, " ")
        cjk = cjk.replace("\u6709", " ")
        for connector in ("\u548c", "\u4e0e", "\u8ddf", "\u53ca", "\u3001"):
            cjk = cjk.replace(connector, " ")
        for item in cjk.split():
            if item:
                terms.add(item)
        for size in (2, 3, 4):
            compact_cjk = cjk.replace(" ", "")
            for index in range(0, max(0, len(compact_cjk) - size + 1)):
                terms.add(compact_cjk[index : index + size])
    return sorted(
        (term for term in terms if term not in GENERIC_QUERY_TERMS),
        key=lambda item: (-len(item), item),
    )


def _subject_hints(query: str) -> set[str]:
    normalized = _text(query).lower()
    if not normalized:
        return set()
    hints: set[str] = set()
    for subject, tokens in SUBJECT_QUERY_HINTS.items():
        if any(token in normalized for token in tokens):
            hints.add(subject)
    return hints


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
    query_text = query or topic_id
    terms = _query_terms(query_text)
    subject_hints = _subject_hints(query_text)
    if not terms:
        return []
    scored: list[dict[str, Any]] = []
    for topic in topics:
        current_id = _topic_id(topic)
        if not current_id:
            continue
        label = _topic_label(topic, current_id)
        label_lower = label.lower()
        aliases = [alias.lower() for alias in _topic_aliases(topic)]
        haystack = _topic_search_text(topic)
        score = 0
        matched_terms: list[str] = []
        if label_lower and label_lower in terms:
            score += 40
            matched_terms.append(label_lower)
        elif len(label_lower) >= 2 and label_lower in " ".join(terms):
            score += 24
            matched_terms.append(label_lower)
        for alias in aliases:
            if alias and alias in terms:
                score += 36
                matched_terms.append(alias)
            elif len(alias) >= 2 and alias in " ".join(terms):
                score += 20
                matched_terms.append(alias)
        for term in terms:
            if not term:
                continue
            if term == current_id.lower() or term == label.lower():
                score += 20
            elif term in aliases:
                score += 18
            elif any(term in alias for alias in aliases):
                score += 8 if len(term) >= 3 else 5
            elif label.lower().startswith(term):
                score += 18
            elif term in label.lower():
                score += 10
            elif term in haystack:
                score += 3
            else:
                continue
            matched_terms.append(term)
        subject = _text(topic.get("subject"))
        if score and subject_hints:
            if subject in subject_hints:
                score += 18
            elif subject:
                score -= 10
        if score:
            scored.append(
                {
                    "id": current_id,
                    "label": label,
                    "score": score,
                    "match": "query",
                    "matched_terms": list(dict.fromkeys(matched_terms))[:6],
                }
            )
    return sorted(
        scored,
        key=lambda item: (
            -int(item["score"]),
            len(_text(item["label"])),
            item["label"],
        ),
    )[: max(1, int(limit or 5))]


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


def _sort_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        _dedupe_edges(edges),
        key=lambda edge: (
            _relation_priority(edge),
            _text(edge.get("from_label")),
            _text(edge.get("to_label")),
            _text(edge.get("from")),
            _text(edge.get("to")),
        ),
    )


def _build_relation_groups(
    *,
    learning_path: list[dict[str, Any]],
    applications: list[dict[str, Any]],
    confusions: list[dict[str, Any]],
    next_practice: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    candidates = _dedupe_edges(
        [*learning_path, *applications, *confusions, *next_practice]
    )
    grouped: dict[str, list[dict[str, Any]]] = {
        relation: [] for relation in RELATION_GROUP_ORDER
    }
    for edge in candidates:
        relation = _normalized_relation(edge.get("relation"))
        if relation in grouped:
            normalized_edge = dict(edge)
            normalized_edge["relation"] = relation
            grouped[relation].append(normalized_edge)
    return {
        relation: {
            "relation": relation,
            "title": RELATION_GROUP_TITLES[relation],
            "items": _sort_edges(items),
        }
        for relation, items in grouped.items()
    }


def _build_guidance_sections(
    relation_groups: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    return [
        {
            "relation": relation,
            "title": group["title"],
            "items": group["items"],
        }
        for relation, group in relation_groups.items()
    ]


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
        relation = _normalized_relation(edge.get("relation"))
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
        if len(
            [item for item in questions if item["kind"] == "prerequisite_probe"]
        ) >= 3:
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
        relation = _normalized_relation(edge.get("relation"))
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
    from .knowledge_graph_index import (  # lazy import avoids a module import cycle
        KnowledgeGraphIndex,
        SubgraphBudget,
        build_relevant_subgraph,
        compress_subgraph_payload,
    )
    graph_index = KnowledgeGraphIndex(topic_items)

    subgraph_budget = SubgraphBudget(
        focus_topics=max(1, min(3, int(match_limit or 3))),
        max_depth=max(1, min(2, int(max_depth or 2))),
        max_nodes=24,
    )
    relevant_subgraph = build_relevant_subgraph(
        graph_index,
        topic_id=topic_id,
        query=query,
        budget=subgraph_budget,
    )
    model_context = compress_subgraph_payload(relevant_subgraph, mode="guidance")
    by_id = graph_index.by_id
    edges = graph_index.edges
    incoming = graph_index.incoming_edges
    outgoing = graph_index.outgoing_edges

    matches = graph_index.match(topic_id=topic_id, query=query, limit=match_limit)
    selected_id = _text(matches[0]["id"]) if matches else _text(topic_id)
    selected_topic = by_id.get(selected_id)
    if not selected_topic:
        relation_groups = _build_relation_groups(
            learning_path=[],
            applications=[],
            confusions=[],
            next_practice=[],
        )
        return {
            "topic": {},
            "matches": matches,
            "learning_path": [],
            "applications": [],
            "confusions": [],
            "next_practice_topics": [],
            "relation_groups": relation_groups,
            "guidance_sections": _build_guidance_sections(relation_groups),
            "diagnosis_questions": [],
            "relevant_subgraph": relevant_subgraph,
            "model_context": model_context,
            "summary": {
                "matched": False,
                "topic_count": len(topic_items),
                "edge_count": len(edges),
                "active_relation_group_count": 0,
                "diagnosis_question_count": 0,
                "subgraph_node_count": relevant_subgraph["summary"]["node_count"],
                "subgraph_edge_count": relevant_subgraph["summary"]["edge_count"],
                "raw_seed_included": False,
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
        edge for edge in outgoing_edges if _normalized_relation(edge.get("relation")) in APPLICATION_RELATIONS
    ]
    incoming_edges = incoming.get(selected_id, [])
    confusions = _dedupe_edges(
        [
            edge
            for edge in [*outgoing_edges, *incoming_edges]
            if _normalized_relation(edge.get("relation")) in CONFUSION_RELATIONS
        ]
    )
    next_practice = [
        edge
        for edge in outgoing_edges
        if _normalized_relation(edge.get("relation")) in NEXT_PRACTICE_RELATIONS
    ]
    diagnosis_questions = _build_diagnosis_questions(
        selected_id=selected_id,
        selected_label=_topic_label(selected_topic, selected_id),
        learning_path=learning_path,
        confusions=confusions,
        next_practice=next_practice,
    )
    relation_groups = _build_relation_groups(
        learning_path=learning_path,
        applications=applications,
        confusions=confusions,
        next_practice=next_practice,
    )
    active_relation_group_count = sum(
        1 for group in relation_groups.values() if group["items"]
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
        "relation_groups": relation_groups,
        "guidance_sections": _build_guidance_sections(relation_groups),
        "diagnosis_questions": diagnosis_questions,
        "relevant_subgraph": relevant_subgraph,
        "model_context": model_context,
        "summary": {
            "matched": True,
            "topic_count": len(topic_items),
            "edge_count": len(edges),
            "learning_path_count": len(learning_path),
            "application_count": len(applications),
            "confusion_count": len(confusions),
            "next_practice_count": len(next_practice),
            "active_relation_group_count": active_relation_group_count,
            "diagnosis_question_count": len(diagnosis_questions),
            "subgraph_node_count": relevant_subgraph["summary"]["node_count"],
            "subgraph_edge_count": relevant_subgraph["summary"]["edge_count"],
            "raw_seed_included": False,
        },
    }
