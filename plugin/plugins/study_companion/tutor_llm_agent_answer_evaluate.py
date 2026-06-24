from __future__ import annotations

from .tutor_llm_agent_common import (
    Any,
    LLM_OPERATION_ANSWER_EVALUATE,
    MODE_COMPANION,
    normalize_mode,
    TutorReply,
    _ANSWER_VERDICTS,
    _as_list,
    _as_str,
    _clamp_int,
)


async def answer_evaluate(
    self,
    question: str = "",
    answer: str = "",
    *,
    expected_answer: str = "",
    mode: str = MODE_COMPANION,
    context: dict[str, Any] | None = None,
) -> TutorReply:
    current_context = dict(context or {})
    operation_context = {
        **current_context,
        "question": str(question or current_context.get("question") or "").strip(),
        "answer": str(answer or "").strip(),
        "expected_answer": str(
            expected_answer or current_context.get("expected_answer") or ""
        ).strip(),
        "language": self._config.language,
        "mode": normalize_mode(mode),
    }
    return await self._invoke_structured_operation(
        LLM_OPERATION_ANSWER_EVALUATE, operation_context
    )


def _normalize_evaluation(
    self, raw: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    score = _clamp_int(raw.get("score"), 0, 100, 0)
    verdict = _as_str(raw.get("verdict")).strip().lower()
    if verdict not in _ANSWER_VERDICTS:
        verdict = self._verdict_from_score(
            score, answer=_as_str(context.get("answer")).strip()
        )
    feedback = _as_str(raw.get("feedback")).strip()
    if not feedback:
        feedback = self._fallback_feedback(verdict, context)
    error_type = _as_str(raw.get("error_type")).strip() or (
        "none" if verdict == "correct" else "unsupported"
    )
    next_action = _as_str(raw.get("next_action")).strip() or self._fallback_next_action(
        verdict
    )
    covered_points = [
        _as_str(item, str(item)).strip()
        for item in _as_list(raw.get("covered_points"))
        if _as_str(item, str(item)).strip()
    ]
    missing_points = [
        _as_str(item, str(item)).strip()
        for item in _as_list(raw.get("missing_points"))
        if _as_str(item, str(item)).strip()
    ]
    misconceptions = [
        _as_str(item, str(item)).strip()
        for item in _as_list(raw.get("misconceptions"))
        if _as_str(item, str(item)).strip()
    ]
    step_feedback = [
        _as_str(item, str(item)).strip()
        for item in _as_list(raw.get("step_feedback"))
        if _as_str(item, str(item)).strip()
    ]
    reference_answer = _as_str(raw.get("reference_answer")).strip() or _as_str(
        context.get("expected_answer")
    ).strip()
    return {
        "verdict": verdict,
        "score": score,
        "error_type": error_type,
        "feedback": feedback,
        "next_action": next_action,
        "final_answer_correct": raw.get("final_answer_correct")
        if isinstance(raw.get("final_answer_correct"), bool)
        else verdict == "correct",
        "covered_points": covered_points,
        "missing_points": missing_points,
        "misconceptions": misconceptions,
        "step_feedback": step_feedback,
        "reference_answer": reference_answer,
        "related_topics": [
            _as_str(item, str(item)).strip()
            for item in _as_list(raw.get("related_topics"))
            if _as_str(item, str(item)).strip()
        ],
        "math_equivalence_engine": {"enabled": False},
        "screen_type": self._screen_type_from_context(context),
    }


def _fallback_evaluation(self, context: dict[str, Any]) -> dict[str, Any]:
    answer = _as_str(context.get("answer")).strip()
    expected = _as_str(context.get("expected_answer")).strip()
    if not answer:
        verdict, score, error_type = "dont_know", 0, "empty_answer"
    else:
        verdict, score, error_type = self._heuristic_verdict(answer, expected)
    return {
        "verdict": verdict,
        "score": score,
        "error_type": error_type,
        "feedback": self._fallback_feedback(verdict, context),
        "next_action": self._fallback_next_action(verdict),
        "screen_type": self._screen_type_from_context(context),
    }
