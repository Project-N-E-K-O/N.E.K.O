from __future__ import annotations

from typing import Any
from urllib.parse import quote


STUDY_PANEL_SURFACE_ID = "study-panel"

CORE_EDGE_RELATIONS = {"prerequisite", "procedure_step", "confusable"}
USEFUL_EDGE_RELATIONS = {"application", "extends", "supports"}
EDGE_CONTEXT_VALUES = {"diagnosis", "explanation", "practice", "review"}
EDGE_PRIORITY_VALUES = {"core", "useful", "optional"}


def _topic_ref_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("topic_id") or "").strip()
    return str(value or "").strip()


def _knowledge_edge_priority(relation: str, ref: dict[str, Any]) -> str:
    priority = str(ref.get("priority") or "").strip()
    if priority in EDGE_PRIORITY_VALUES:
        return priority
    if relation in CORE_EDGE_RELATIONS:
        return "core"
    if relation in USEFUL_EDGE_RELATIONS:
        return "useful"
    return "optional"


def _knowledge_edge_context(relation: str, ref: dict[str, Any]) -> str:
    context = str(ref.get("context") or "").strip()
    if context in EDGE_CONTEXT_VALUES:
        return context
    if relation in {"prerequisite", "confusable"}:
        return "diagnosis"
    if relation in {"procedure_step", "application"}:
        return "practice"
    if relation in {"extends", "co_occurs", "nearby", "next"}:
        return "review"
    return "explanation"


def _knowledge_edge_confidence(ref: dict[str, Any], *, reason: str, use_cases: list[str]) -> float:
    try:
        confidence = float(ref.get("confidence"))
    except (TypeError, ValueError):
        confidence = -1.0
    if 0.0 <= confidence <= 1.0:
        return round(confidence, 3)
    if reason and use_cases:
        return 0.9
    if reason or use_cases:
        return 0.8
    return 0.65


def _knowledge_edge_payload(
    *,
    source_id: str,
    target_id: str,
    relation: str,
    ref: Any,
) -> dict[str, Any]:
    ref_payload = ref if isinstance(ref, dict) else {}
    edge: dict[str, Any] = {
        "from": source_id,
        "to": target_id,
        "relation": relation,
    }
    typed_relation = str(ref_payload.get("relation") or "").strip()
    if typed_relation:
        edge["relation"] = typed_relation
    relation = str(edge["relation"])
    reason = str(ref_payload.get("reason") or "").strip()
    if reason:
        edge["reason"] = reason
    use_case_items: list[str] = []
    use_cases = ref_payload.get("use_cases")
    if isinstance(use_cases, list):
        use_case_items = [str(item).strip() for item in use_cases if str(item).strip()]
        edge["use_cases"] = use_case_items
    if ref_payload.get("required_mastery") is not None:
        edge["required_mastery"] = ref_payload.get("required_mastery")
    edge["priority"] = _knowledge_edge_priority(relation, ref_payload)
    edge["context"] = _knowledge_edge_context(relation, ref_payload)
    edge["confidence"] = _knowledge_edge_confidence(
        ref_payload,
        reason=reason,
        use_cases=use_case_items,
    )
    return edge


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
    subject_counts: dict[str, int] = {}
    chapter_counts: dict[str, int] = {}
    unit_counts: dict[str, int] = {}
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
        subject = str(topic.get("subject") or "")
        chapter = str(topic.get("chapter") or "")
        unit = str(topic.get("unit") or chapter)
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        subject_counts[subject] = subject_counts.get(subject, 0) + 1
        chapter_counts[chapter] = chapter_counts.get(chapter, 0) + 1
        unit_counts[unit] = unit_counts.get(unit, 0) + 1
        mastery = mastery_by_topic.get(topic_id) or {}
        weak = topic_id in weak_topic_ids
        if weak:
            weak_node_count += 1
        nodes.append(
            {
                "id": topic_id,
                "label": str(topic.get("name") or topic_id),
                "subject": subject,
                "chapter": chapter,
                "unit": unit,
                "stage": stage,
                "grade_level": stage,  # backward-compat alias for older consumers
                "skills": list(topic.get("skills") or []),
                "question_types": list(topic.get("question_types") or []),
                "examples": list(topic.get("examples") or []),
                "typical_misconceptions": list(
                    topic.get("typical_misconceptions")
                    or topic.get("misconceptions")
                    or []
                ),
                "mastery": float(mastery.get("mastery") or 0.0),
                "level": str(mastery.get("level") or ""),
                "weak": weak,
            }
        )
        for prereq in topic.get("prerequisites") or []:
            prereq_id = _topic_ref_id(prereq)
            if prereq_id:
                edges.append(
                    _knowledge_edge_payload(
                        source_id=prereq_id,
                        target_id=topic_id,
                        relation="prerequisite",
                        ref=prereq,
                    )
                )
        for related in topic.get("related") or []:
            related_id = _topic_ref_id(related)
            if related_id:
                edges.append(
                    _knowledge_edge_payload(
                        source_id=topic_id,
                        target_id=related_id,
                        relation="co_occurs",
                        ref=related,
                    )
                )
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
            "subject_counts": subject_counts,
            "chapter_counts": chapter_counts,
            "unit_counts": unit_counts,
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
