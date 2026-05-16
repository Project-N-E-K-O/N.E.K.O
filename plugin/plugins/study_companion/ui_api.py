from __future__ import annotations

from typing import Any


def _topic_ref_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("topic_id") or "").strip()
    return str(value or "").strip()


def build_open_ui_payload(*, plugin_id: str, available: bool) -> dict[str, Any]:
    path = f"/plugin/{plugin_id}/ui/" if available else ""
    message_key = "ui.open.available" if available else "ui.open.unavailable"
    return {
        "available": available,
        "path": path,
        "message_key": message_key,
    }


def build_knowledge_map_payload(
    *,
    topics: list[dict[str, Any]] | None = None,
    mastery_overview: list[dict[str, Any]] | None = None,
    weak_topics: list[dict[str, Any]] | None = None,
    wrong_questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    topic_items = list(topics or [])
    mastery_items = list(mastery_overview or [])
    weak_items = list(weak_topics or [])
    wrong_items = list(wrong_questions or [])
    mastery_by_topic = {str(item.get("topic_id") or ""): item for item in mastery_items}
    weak_topic_ids = {str(item.get("topic_id") or "") for item in weak_items}
    nodes = []
    edges = []
    weak_node_count = 0
    for topic in topic_items:
        topic_id = str(topic.get("id") or "").strip()
        if not topic_id:
            continue
        mastery = mastery_by_topic.get(topic_id) or {}
        weak = topic_id in weak_topic_ids
        if weak:
            weak_node_count += 1
        nodes.append(
            {
                "id": topic_id,
                "label": str(topic.get("name") or topic_id),
                "subject": str(topic.get("subject") or ""),
                "chapter": str(topic.get("chapter") or ""),
                "mastery": float(mastery.get("mastery") or 0.0),
                "level": str(mastery.get("level") or ""),
                "weak": weak,
            }
        )
        for prereq in topic.get("prerequisites") or []:
            prereq_id = _topic_ref_id(prereq)
            if prereq_id:
                edge = {"from": prereq_id, "to": topic_id, "relation": "prerequisite"}
                if isinstance(prereq, dict) and prereq.get("required_mastery") is not None:
                    edge["required_mastery"] = prereq.get("required_mastery")
                edges.append(edge)
        for related in topic.get("related") or []:
            related_id = _topic_ref_id(related)
            if related_id:
                relation = str(related.get("relation") or "related") if isinstance(related, dict) else "related"
                edges.append({"from": topic_id, "to": related_id, "relation": relation})
    return {
        "nodes": nodes,
        "edges": edges,
        "mastery_overview": mastery_items,
        "weak_topics": weak_items,
        "wrong_questions": wrong_items,
        "summary": {
            "topic_count": len(nodes),
            "edge_count": len(edges),
            "weak_topic_count": weak_node_count,
            "wrong_question_count": len(wrong_items),
        },
    }


def build_contribution_settings_payload(*, opt_in: bool, preview: dict[str, Any] | None = None) -> dict[str, Any]:
    preview_payload = dict(preview or {})
    preview_payload["opt_in"] = bool(opt_in)
    return {
        "opt_in": bool(opt_in),
        "preview": preview_payload,
        "summary": preview_payload.get("summary") or {},
        "queue": preview_payload.get("queue") or [],
    }
