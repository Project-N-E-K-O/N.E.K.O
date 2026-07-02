from __future__ import annotations

from typing import Any, Iterable


RELATION_ALIASES = {
    "supports": "prerequisite",
    "next": "extends",
    "nearby": "co_occurs",
}


def text(value: Any) -> str:
    return str(value or "").strip()


def topic_id(topic: dict[str, Any]) -> str:
    for candidate in (topic.get("id"), topic.get("topic_id")):
        value = text(candidate)
        if value:
            return value
    return ""


def topic_label(topic: dict[str, Any] | None, fallback: str = "") -> str:
    if not topic:
        return text(fallback)
    for candidate in (topic.get("name"), topic.get("label"), topic_id(topic), fallback):
        value = text(candidate)
        if value:
            return value
    return ""


def normalized_relation(relation: Any) -> str:
    value = text(relation)
    return RELATION_ALIASES.get(value, value)


def dedupe_edges(edges: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for edge in edges:
        relation = normalized_relation(edge.get("relation"))
        key = (text(edge.get("from")), text(edge.get("to")), relation)
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        payload = dict(edge)
        payload["relation"] = relation
        unique.append(payload)
    return unique
