from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

from ._graph_utils import (
    dedupe_edges as _dedupe_edges,
    normalized_relation,
    text as _text,
    topic_id as _topic_id,
    topic_label as _topic_label,
)
from .knowledge_graph_guidance import build_topic_edges, match_topics


CORE_RELATION_ORDER = (
    "prerequisite",
    "procedure_step",
    "confusable",
    "application",
    "extends",
    "co_occurs",
)
RELATION_SCORE = {relation: index for index, relation in enumerate(CORE_RELATION_ORDER)}
PRIORITY_SCORE = {"core": 0, "useful": 1, "optional": 2}


def _string_list(value: Any, *, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)][:limit]


def _aliases(topic: dict[str, Any]) -> list[str]:
    return _string_list(topic.get("aliases"), limit=12)


def _edge_relation(edge: dict[str, Any]) -> str:
    return normalized_relation(edge.get("relation"))


def _edge_confidence(edge: dict[str, Any]) -> float:
    try:
        confidence = float(edge.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    return max(0.0, min(1.0, confidence))


def _query_overlap_score(query: str, edge: dict[str, Any]) -> int:
    compact_query = _text(query).lower()
    if not compact_query:
        return 0
    haystack = " ".join(
        _text(edge.get(field)).lower()
        for field in ("from_label", "to_label", "reason")
    )
    score = 0
    for token in compact_query.split():
        if token and token in haystack:
            score += 2
    for size in (2, 3, 4):
        for index in range(0, max(0, len(compact_query) - size + 1)):
            fragment = compact_query[index : index + size]
            if fragment and fragment in haystack:
                score += 1
                break
    return score


def _edge_sort_key(query: str, edge: dict[str, Any]) -> tuple[int, int, float, int, str, str]:
    relation = _edge_relation(edge)
    priority = _text(edge.get("priority")) or "optional"
    return (
        RELATION_SCORE.get(relation, 99),
        PRIORITY_SCORE.get(priority, 2),
        -_edge_confidence(edge),
        -_query_overlap_score(query, edge),
        _text(edge.get("from_label")),
        _text(edge.get("to_label")),
    )


def _topic_summary(topic: dict[str, Any] | None, topic_id: str = "") -> dict[str, Any]:
    resolved_id = _topic_id(topic or {}) or topic_id
    return {
        "id": resolved_id,
        "label": _topic_label(topic, resolved_id),
        "subject": _text((topic or {}).get("subject")),
        "stage": _text((topic or {}).get("stage")),
        "unit": _text((topic or {}).get("unit")),
        "skills": _string_list((topic or {}).get("skills"), limit=4),
        "question_types": _string_list((topic or {}).get("question_types"), limit=4),
        "typical_misconceptions": _string_list(
            (topic or {}).get("typical_misconceptions"), limit=4
        ),
    }


@dataclass(frozen=True)
class SubgraphBudget:
    focus_topics: int = 3
    max_depth: int = 2
    max_nodes: int = 20
    max_edges: int = 30
    relation_limits: dict[str, int] = field(
        default_factory=lambda: {
            "prerequisite": 5,
            "procedure_step": 6,
            "confusable": 4,
            "application": 4,
            "extends": 3,
            "co_occurs": 3,
        }
    )


@dataclass
class KnowledgeGraphIndex:
    topics: list[dict[str, Any]]

    def __post_init__(self) -> None:
        self.by_id: dict[str, dict[str, Any]] = {
            _topic_id(topic): topic for topic in self.topics if _topic_id(topic)
        }
        self.name_to_ids: dict[str, list[str]] = {}
        self.alias_to_ids: dict[str, list[str]] = {}
        self.subject_to_ids: dict[str, list[str]] = {}
        self.stage_to_ids: dict[str, list[str]] = {}
        for topic_id, topic in self.by_id.items():
            name = _topic_label(topic, topic_id).lower()
            if name:
                self.name_to_ids.setdefault(name, []).append(topic_id)
            for alias in _aliases(topic):
                self.alias_to_ids.setdefault(alias.lower(), []).append(topic_id)
            subject = _text(topic.get("subject"))
            stage = _text(topic.get("stage"))
            if subject:
                self.subject_to_ids.setdefault(subject, []).append(topic_id)
            if stage:
                self.stage_to_ids.setdefault(stage, []).append(topic_id)

        self.edges: list[dict[str, Any]] = build_topic_edges(self.topics)
        self.incoming_edges: dict[str, list[dict[str, Any]]] = {}
        self.outgoing_edges: dict[str, list[dict[str, Any]]] = {}
        self.relation_counts: dict[str, int] = {}
        for edge in self.edges:
            from_id = _text(edge.get("from"))
            to_id = _text(edge.get("to"))
            relation = _edge_relation(edge)
            if from_id:
                self.outgoing_edges.setdefault(from_id, []).append(edge)
            if to_id:
                self.incoming_edges.setdefault(to_id, []).append(edge)
            if relation:
                self.relation_counts[relation] = self.relation_counts.get(relation, 0) + 1

    def topic(self, topic_id: str) -> dict[str, Any] | None:
        return self.by_id.get(_text(topic_id))

    def match(self, *, query: str = "", topic_id: str = "", limit: int = 5) -> list[dict[str, Any]]:
        return match_topics(self.topics, query=query, topic_id=topic_id, limit=limit)

    def related_edges(self, topic_id: str) -> list[dict[str, Any]]:
        key = _text(topic_id)
        return [*self.incoming_edges.get(key, []), *self.outgoing_edges.get(key, [])]


def _collect_candidate_edges(
    index: KnowledgeGraphIndex,
    *,
    focus_ids: list[str],
    query: str,
    budget: SubgraphBudget,
) -> list[dict[str, Any]]:
    queue: deque[tuple[str, int]] = deque((topic_id, 0) for topic_id in focus_ids)
    visited: set[str] = set(focus_ids)
    edges: list[dict[str, Any]] = []
    while queue:
        current_id, depth = queue.popleft()
        if depth >= max(1, budget.max_depth):
            continue
        current_edges = sorted(
            index.related_edges(current_id),
            key=lambda edge: _edge_sort_key(query, edge),
        )
        for edge in current_edges:
            relation = _edge_relation(edge)
            if relation not in budget.relation_limits:
                continue
            edges.append(edge)
            other_id = _text(edge.get("from"))
            if other_id == current_id:
                other_id = _text(edge.get("to"))
            if other_id and other_id not in visited and other_id in index.by_id:
                visited.add(other_id)
                queue.append((other_id, depth + 1))
    return _dedupe_edges(edges)


def build_relevant_subgraph(
    topics: list[dict[str, Any]] | KnowledgeGraphIndex,
    *,
    query: str = "",
    topic_id: str = "",
    budget: SubgraphBudget | None = None,
) -> dict[str, Any]:
    index = topics if isinstance(topics, KnowledgeGraphIndex) else KnowledgeGraphIndex(list(topics or []))
    active_budget = budget or SubgraphBudget()
    matches = index.match(
        query=query,
        topic_id=topic_id,
        limit=max(1, active_budget.focus_topics),
    )
    focus_ids = [
        _text(match.get("id"))
        for match in matches[: max(1, active_budget.focus_topics)]
        if _text(match.get("id")) in index.by_id
    ]
    if not focus_ids and _text(topic_id) in index.by_id:
        focus_ids = [_text(topic_id)]

    candidate_edges = _collect_candidate_edges(
        index,
        focus_ids=focus_ids,
        query=query,
        budget=active_budget,
    )
    relation_groups: dict[str, list[dict[str, Any]]] = {
        relation: [] for relation in active_budget.relation_limits
    }
    selected_edges: list[dict[str, Any]] = []
    selected_node_ids: set[str] = set(focus_ids)
    for relation in active_budget.relation_limits:
        relation_edges = [
            edge for edge in candidate_edges if _edge_relation(edge) == relation
        ]
        for edge in sorted(relation_edges, key=lambda item: _edge_sort_key(query, item)):
            if len(selected_edges) >= active_budget.max_edges:
                break
            if len(relation_groups[relation]) >= active_budget.relation_limits[relation]:
                break
            edge_node_ids = {_text(edge.get("from")), _text(edge.get("to"))}
            next_nodes = selected_node_ids | edge_node_ids
            if len(next_nodes) > active_budget.max_nodes:
                continue
            selected_node_ids = next_nodes
            relation_groups[relation].append(edge)
            selected_edges.append(edge)

    nodes = [
        _topic_summary(index.topic(node_id), node_id)
        for node_id in sorted(
            selected_node_ids,
            key=lambda item: (0 if item in focus_ids else 1, item),
        )
    ]
    focus_topics = [
        _topic_summary(index.topic(topic_id), topic_id) for topic_id in focus_ids
    ]
    return {
        "query": query,
        "matches": matches,
        "focus_topics": focus_topics,
        "nodes": nodes,
        "edges": selected_edges,
        "relation_groups": {
            relation: {"relation": relation, "items": items}
            for relation, items in relation_groups.items()
        },
        "summary": {
            "matched": bool(focus_topics),
            "node_count": len(nodes),
            "edge_count": len(selected_edges),
            "max_nodes": active_budget.max_nodes,
            "max_edges": active_budget.max_edges,
            "max_depth": active_budget.max_depth,
            "raw_seed_included": False,
        },
    }


def _labels_for_edges(edges: Iterable[dict[str, Any]], *, relation: str) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for edge in edges:
        label = _text(edge.get("from_label"))
        if relation in {"application", "procedure_step", "confusable", "extends", "co_occurs"}:
            label = _text(edge.get("to_label")) or label
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def compress_subgraph_payload(
    subgraph: dict[str, Any],
    *,
    mode: str = "guidance",
) -> dict[str, Any]:
    groups = subgraph.get("relation_groups") if isinstance(subgraph, dict) else {}
    if not isinstance(groups, dict):
        groups = {}
    focus_topics = subgraph.get("focus_topics") if isinstance(subgraph, dict) else []
    focus = focus_topics[0] if isinstance(focus_topics, list) and focus_topics else {}

    def group_items(relation: str) -> list[dict[str, Any]]:
        group = groups.get(relation)
        if not isinstance(group, dict) or not isinstance(group.get("items"), list):
            return []
        return [item for item in group["items"] if isinstance(item, dict)]

    procedure_edges = group_items("procedure_step")
    application_edges = group_items("application")
    return {
        "mode": mode,
        "query": _text(subgraph.get("query") if isinstance(subgraph, dict) else ""),
        "focus": {
            "id": _text(focus.get("id")) if isinstance(focus, dict) else "",
            "label": _text(focus.get("label")) if isinstance(focus, dict) else "",
        },
        "prerequisites": _labels_for_edges(group_items("prerequisite"), relation="prerequisite"),
        "procedure": _labels_for_edges(procedure_edges, relation="procedure_step"),
        "confusions": _labels_for_edges(group_items("confusable"), relation="confusable"),
        "applications": _labels_for_edges(application_edges, relation="application"),
        "extensions": _labels_for_edges(group_items("extends"), relation="extends"),
        "review_with": _labels_for_edges(group_items("co_occurs"), relation="co_occurs"),
        "practice_suggestions": _labels_for_edges(
            [*procedure_edges, *application_edges],
            relation="application",
        )[:6],
        "summary": {
            "node_count": int((subgraph.get("summary") or {}).get("node_count") or 0)
            if isinstance(subgraph, dict)
            else 0,
            "edge_count": int((subgraph.get("summary") or {}).get("edge_count") or 0)
            if isinstance(subgraph, dict)
            else 0,
            "raw_seed_included": False,
        },
    }
