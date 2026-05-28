from __future__ import annotations

from .tutor_llm_agent_common import (
    Any,
    STUDY_FALLBACK_TRACK_NEXT_STEPS_DEFAULT,
    STUDY_FALLBACK_TRACK_NEXT_STEPS_WITH_WEAK_POINTS,
    LLM_OPERATION_KNOWLEDGE_TRACK,
    MODE_COMPANION,
    normalize_mode,
    TutorReply,
    _as_str,
    _as_dict,
    _string_list,
    _clamp_float,
)


async def knowledge_track(
    self,
    *,
    mode: str = MODE_COMPANION,
    context: dict[str, Any] | None = None,
) -> TutorReply:
    operation_context = {
        **dict(context or {}),
        "language": self._config.language,
        "mode": normalize_mode(mode),
    }
    return await self._invoke_structured_operation(
        LLM_OPERATION_KNOWLEDGE_TRACK, operation_context
    )


def _normalize_track(
    self, raw: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    seed = _as_dict(raw.get("session_summary_seed"))
    if not seed:
        seed = _as_dict(context.get("session_summary_seed"))
    return {
        "topic": _as_str(raw.get("topic")).strip() or self._guess_topic(context),
        "mastery_delta": _clamp_float(raw.get("mastery_delta"), -1.0, 1.0, 0.0),
        "confidence": _clamp_float(raw.get("confidence"), 0.0, 1.0, 0.4),
        "weak_points": _string_list(raw.get("weak_points"), limit=6),
        "next_steps": _string_list(raw.get("next_steps"), limit=6),
        "session_summary_seed": seed,
        "screen_type": self._screen_type_from_context(context),
    }


def _fallback_track(self, context: dict[str, Any]) -> dict[str, Any]:
    evaluation = _as_dict(
        context.get("evaluation") or context.get("last_answer_evaluation")
    )
    verdict = _as_str(evaluation.get("verdict")).strip()
    delta = (
        0.08
        if verdict == "correct"
        else (-0.08 if verdict in {"wrong", "dont_know"} else 0.02)
    )
    weak_points = []
    error_type = _as_str(evaluation.get("error_type")).strip()
    if error_type and error_type != "none":
        weak_points.append(error_type)
    return {
        "topic": self._guess_topic(context),
        "mastery_delta": delta,
        "confidence": 0.35,
        "weak_points": weak_points,
        "next_steps": (
            list(STUDY_FALLBACK_TRACK_NEXT_STEPS_WITH_WEAK_POINTS)
            if weak_points
            else list(STUDY_FALLBACK_TRACK_NEXT_STEPS_DEFAULT)
        ),
        "session_summary_seed": _as_dict(context.get("session_summary_seed")),
        "screen_type": self._screen_type_from_context(context),
    }
