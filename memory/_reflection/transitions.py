"""Pure mutation helpers for reflection lifecycle transitions."""

from __future__ import annotations

from datetime import datetime


def find_reflection(reflections: list[dict], reflection_id: str) -> dict | None:
    for reflection in reflections:
        if isinstance(reflection, dict) and reflection.get("id") == reflection_id:
            return reflection
    return None


def apply_promotion_status(
    reflections: list[dict],
    reflection_id: str,
    status: str,
    *,
    now: datetime,
) -> str | None:
    for reflection in reflections:
        if reflection.get("id") == reflection_id:
            reflection["status"] = status
            reflection[f"{status}_at"] = now.isoformat()
            return reflection.get("text", "")
    return None


def apply_mark_surfaced_handled(
    surfaced: list[dict],
    reflection_id: str,
    feedback: str,
    *,
    now: datetime,
) -> bool:
    changed = False
    for item in surfaced:
        if item.get("reflection_id") == reflection_id and item.get("feedback") is None:
            item["feedback"] = feedback
            item["feedback_at"] = now.isoformat()
            changed = True
    return changed

def compute_merged_evidence(target: dict, reflection: dict) -> tuple[float, float]:
    """Merge evidence conservatively without double-counting witnesses."""
    return (
        max(
            float(target.get("reinforcement", 0.0) or 0.0),
            float(reflection.get("reinforcement", 0.0) or 0.0),
        ),
        max(
            float(target.get("disputation", 0.0) or 0.0),
            float(reflection.get("disputation", 0.0) or 0.0),
        ),
    )


def apply_batch_mark(
    surfaced: list[dict],
    reflection_ids: list[str],
    feedback: str,
    *,
    upgradable_feedback: set[str | None],
    now: datetime,
) -> bool:
    ids = set(reflection_ids)
    changed = False
    for item in surfaced:
        if (
            item.get("reflection_id") in ids
            and item.get("feedback") in upgradable_feedback
        ):
            item["feedback"] = feedback
            item["feedback_at"] = now.isoformat()
            changed = True
    return changed
