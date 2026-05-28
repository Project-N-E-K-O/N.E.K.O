from __future__ import annotations

from ._common import *  # noqa: F401, F403



async def question_generate(
    self,
    text: str,
    *,
    mode: str = MODE_COMPANION,
    context: dict[str, Any] | None = None,
) -> TutorReply:
    normalized = str(text or "").strip()
    operation_context = {
        **dict(context or {}),
        "text": normalized,
        "source_text": normalized,
        "language": self._config.language,
        "mode": normalize_mode(mode),
    }
    if not normalized:
        return self._fallback_structured_reply(
            LLM_OPERATION_QUESTION_GENERATE,
            operation_context,
            diagnostic="empty_input",
        )
    return await self._invoke_structured_operation(LLM_OPERATION_QUESTION_GENERATE, operation_context)

def _normalize_question(self, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    question = _as_str(raw.get("question")).strip() or _as_str(raw.get("prompt")).strip()
    if not question:
        raise SdkError("missing question")
    topic = _as_str(raw.get("topic")).strip() or self._guess_topic(context)
    return {
        "question": question,
        "answer": _as_str(raw.get("answer")).strip() or _as_str(raw.get("reference_answer")).strip(),
        "hint": _as_str(raw.get("hint")).strip(),
        "difficulty": _clamp_int(raw.get("difficulty"), 1, 5, 3),
        "topic": topic,
        "screen_type": self._screen_type_from_context(context),
    }

def _fallback_question(self, context: dict[str, Any]) -> dict[str, Any]:
    text = _as_str(context.get("source_text") or context.get("text")).strip()
    if not text:
        return {
            **STUDY_FALLBACK_QUESTION_EMPTY,
            "hint": self._localize_reply(self._config.language, "empty_input"),
            "screen_type": self._screen_type_from_context(context),
        }
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), text[:120])
    return {
        "question": STUDY_FALLBACK_QUESTION_TEMPLATE["question"],
        "answer": first_line[:200],
        "hint": STUDY_FALLBACK_QUESTION_TEMPLATE["hint"],
        "difficulty": STUDY_FALLBACK_QUESTION_TEMPLATE["difficulty"],
        "topic": self._guess_topic(context),
        "screen_type": self._screen_type_from_context(context),
    }
