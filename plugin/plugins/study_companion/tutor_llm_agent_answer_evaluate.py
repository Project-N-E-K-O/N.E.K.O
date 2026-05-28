from __future__ import annotations

from .tutor_llm_agent_common import *  # noqa: F401, F403



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
        "expected_answer": str(expected_answer or current_context.get("expected_answer") or "").strip(),
        "language": self._config.language,
        "mode": normalize_mode(mode),
    }
    return await self._invoke_structured_operation(LLM_OPERATION_ANSWER_EVALUATE, operation_context)

def _normalize_evaluation(self, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    score = _clamp_int(raw.get("score"), 0, 100, 0)
    verdict = _as_str(raw.get("verdict")).strip().lower()
    if verdict not in _ANSWER_VERDICTS:
        verdict = self._verdict_from_score(score, answer=_as_str(context.get("answer")).strip())
    feedback = _as_str(raw.get("feedback")).strip()
    if not feedback:
        feedback = self._fallback_feedback(verdict, context)
    error_type = _as_str(raw.get("error_type")).strip() or ("none" if verdict == "correct" else "unsupported")
    next_action = _as_str(raw.get("next_action")).strip() or self._fallback_next_action(verdict)
    return {
        "verdict": verdict,
        "score": score,
        "error_type": error_type,
        "feedback": feedback,
        "next_action": next_action,
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
