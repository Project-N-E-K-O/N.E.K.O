from __future__ import annotations

from .tutor_llm_agent_common import (
    Any,
    asyncio,
    STUDY_FALLBACK_EXPLANATION_DEFAULT,
    SdkError,
    MODE_COMPANION,
    MODE_TEACHING,
    build_concept_explain_messages,
    build_transition_phrase,
    normalize_mode,
    MODE_CONCEPT_EXPLAIN,
    TutorReply,
    utc_now_iso,
    diagnostic_code_for_exception,
)


async def concept_explain(
    self,
    text: str,
    *,
    mode: str = MODE_COMPANION,
    context: dict[str, Any] | None = None,
) -> TutorReply:
    normalized = str(text or "").strip()
    if not normalized:
        return TutorReply(
            operation=MODE_CONCEPT_EXPLAIN,
            input_text="",
            reply=self._localize_reply(self._config.language, "empty_input"),
            degraded=True,
            diagnostic="empty_input",
            created_at=utc_now_iso(),
        )
    selected_mode = normalize_mode(mode)
    teaching_prefix = (
        build_transition_phrase(
            MODE_TEACHING, language=self._config.language, outcome="changed"
        )
        if selected_mode == MODE_TEACHING
        else ""
    )
    messages = build_concept_explain_messages(
        text=normalized,
        language=self._config.language,
        mode=selected_mode,
        context=context,
    )
    try:
        content = await self._call_model(messages)
        reply = content.strip()
        if not reply:
            raise SdkError("empty model response")
        if teaching_prefix and not reply.startswith(teaching_prefix):
            reply = f"{teaching_prefix}\n\n{reply}"
        return TutorReply(
            operation=MODE_CONCEPT_EXPLAIN,
            input_text=normalized,
            reply=reply,
            degraded=False,
            created_at=utc_now_iso(),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        self._logger.warning("study concept_explain degraded: {}", exc)
        fallback_reply = self._localize_reply(
            self._config.language,
            "fallback_explanation",
            default=STUDY_FALLBACK_EXPLANATION_DEFAULT,
            first_line=next(
                (line.strip() for line in normalized.splitlines() if line.strip()),
                normalized[:120],
            ),
        )
        if teaching_prefix and not fallback_reply.startswith(teaching_prefix):
            fallback_reply = f"{teaching_prefix}\n\n{fallback_reply}"
        return TutorReply(
            operation=MODE_CONCEPT_EXPLAIN,
            input_text=normalized,
            reply=fallback_reply,
            degraded=True,
            diagnostic=diagnostic_code_for_exception(exc),
            created_at=utc_now_iso(),
        )
