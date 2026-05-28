from __future__ import annotations

from .entry_common import *  # noqa: F401, F403





class _TutorQuestionEntriesMixin:



    @plugin_entry(
        id="study_generate_question",
        name=tr("entries.generate_question.name", default="Generate Study Question"),
        description=tr(
            "entries.generate_question.description",
            default="Generate one study question from supplied text or the latest OCR text.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "default": ""},
                "topic": {"type": "string", "default": ""},
            },
        },
        timeout=60.0,
        llm_result_fields=[
            "summary",
            "question",
            "answer",
            "hint",
            "difficulty",
            "topic",
        ],
    )
    async def study_generate_question(self, text: str = "", topic: str = "", **_):
        if self._agent is None:
            return Err(SdkError("study tutor agent is not initialized"))
        source_text = str(text or "").strip()
        used_ocr_fallback = False
        if not source_text:
            async with self._lock:
                source_text = self._state.last_ocr_text
            used_ocr_fallback = bool(source_text.strip())
        async with self._lock:
            active_mode = self._state.active_mode
        tutor_context = await self._build_learning_context(
            LLM_OPERATION_QUESTION_GENERATE,
            input_text=source_text,
            extra={
                "source": "ocr_snapshot" if used_ocr_fallback or not text else "manual",
                "source_text": source_text,
                "topic_hint": str(topic or "").strip(),
                "mode": active_mode,
            },
        )
        reply = await self._agent.question_generate(
            source_text, mode=active_mode, context=tutor_context
        )
        payload = await self._finalize_tutor_call(
            LLM_OPERATION_QUESTION_GENERATE,
            reply,
            history_kind=LLM_OPERATION_QUESTION_GENERATE,
            metadata={
                "degraded": reply.degraded,
                "diagnostic": reply.diagnostic,
                "payload": reply.payload,
                "screen_classification": tutor_context.get("screen_classification")
                or {},
            },
            extra_context=tutor_context,
        )
        payload["screen_classification"] = (
            tutor_context.get("screen_classification") or {}
        )
        return Ok(payload)
