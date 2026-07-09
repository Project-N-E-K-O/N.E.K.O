from __future__ import annotations

from typing import Any
from urllib.parse import quote


STUDY_PANEL_SURFACE_ID = "study-panel"


def _topic_ref_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("topic_id") or "").strip()
    return str(value or "").strip()


def build_open_ui_payload(*, plugin_id: str, available: bool) -> dict[str, Any]:
    path = (
        f"/plugin/{quote(plugin_id, safe='')}/ui/"
        if available
        else ""
    )
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
    stage_counts: dict[str, int] = {}
    for topic in topic_items:
        topic_id = str(topic.get("id") or "").strip()
        if not topic_id:
            continue
        stage = str(
            topic.get("stage")
            or topic.get("grade_level")
            or topic.get("education_level")
            or topic.get("course_level")
            or ""
        )
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
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
                "stage": stage,
                "grade_level": stage,  # backward-compat alias for older consumers
                "mastery": float(mastery.get("mastery") or 0.0),
                "level": str(mastery.get("level") or ""),
                "weak": weak,
            }
        )
        for prereq in topic.get("prerequisites") or []:
            prereq_id = _topic_ref_id(prereq)
            if prereq_id:
                edge = {"from": prereq_id, "to": topic_id, "relation": "prerequisite"}
                if (
                    isinstance(prereq, dict)
                    and prereq.get("required_mastery") is not None
                ):
                    edge["required_mastery"] = prereq.get("required_mastery")
                edges.append(edge)
        for related in topic.get("related") or []:
            related_id = _topic_ref_id(related)
            if related_id:
                relation = (
                    str(related.get("relation") or "related")
                    if isinstance(related, dict)
                    else "related"
                )
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
            "stage_counts": stage_counts,
        },
    }


def build_contribution_settings_payload(
    *, opt_in: bool, preview: dict[str, Any] | None = None
) -> dict[str, Any]:
    preview_payload = dict(preview or {})
    preview_payload["opt_in"] = bool(opt_in)
    return {
        "opt_in": bool(opt_in),
        "preview": preview_payload,
        "summary": preview_payload.get("summary") or {},
        "queue": preview_payload.get("queue") or [],
    }


def build_pomodoro_status_payload(
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(status or {})
    return {
        "state": str(payload.get("state") or "idle"),
        "mode": str(payload.get("mode") or "focus"),
        "remaining_seconds": max(0, int(payload.get("remaining_seconds") or 0)),
        "session_count": max(0, int(payload.get("session_count") or 0)),
        "goal_id": str(payload.get("goal_id") or ""),
        "date": str(payload.get("date") or ""),
        "pause_count": max(0, int(payload.get("pause_count") or 0)),
        "interrupt_count": max(0, int(payload.get("interrupt_count") or 0)),
        "current_focus_session": dict(payload.get("current_focus_session") or {}),
        "config": dict(payload.get("config") or {}),
    }


def build_habit_dashboard_payload(
    *,
    goals: list[dict[str, Any]] | None = None,
    checkin: dict[str, Any] | None = None,
    pomodoro: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    supervision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    goal_items = list(goals or [])
    completed = [
        item for item in goal_items if str(item.get("status") or "") == "completed"
    ]
    summary_payload = dict(summary or {})
    summary_payload.setdefault("completed_goal_count", len(completed))
    summary_payload.setdefault("goal_count", len(goal_items))
    return {
        "goals": goal_items,
        "checkin": dict(checkin or {}),
        "pomodoro": build_pomodoro_status_payload(pomodoro or {}),
        "summary": summary_payload,
        "supervision": dict(supervision or {}),
    }
