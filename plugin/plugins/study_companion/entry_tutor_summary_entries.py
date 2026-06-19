from __future__ import annotations

from .entry_common import (
    asyncio,
    Err,
    Ok,
    SdkError,
    plugin_entry,
    tr,
    ui,
    LLM_OPERATION_SUMMARIZE_SESSION,
)


class _TutorSummaryEntriesMixin:
    @ui.action()
    @plugin_entry(
        id="study_summarize_session",
        name=tr("entries.summarize_session.name", default="Summarize Study Session"),
        description=tr(
            "entries.summarize_session.description",
            default="Summarize recent study interactions into compact study notes.",
        ),
        input_schema={
            "type": "object",
            "properties": {"focus": {"type": "string", "default": ""}},
        },
        timeout=75.0,
        llm_result_fields=[
            "summary",
            "markdown",
            "highlights",
            "weak_points",
            "next_actions",
        ],
    )
    async def study_summarize_session(self, focus: str = "", **_):
        if self._agent is None:
            return Err(SdkError("study tutor agent is not initialized"))
        async with self._lock:
            active_mode = self._state.active_mode
        history = await asyncio.to_thread(
            self._store.list_interactions, max(5, min(30, self._cfg.history_limit))
        )
        tutor_context = await self._build_learning_context(
            LLM_OPERATION_SUMMARIZE_SESSION,
            input_text="session",
            extra={
                "focus": str(focus or "").strip(),
                "history": history,
                "mode": active_mode,
            },
        )
        reply = await self._agent.summarize_session(
            history, mode=active_mode, context=tutor_context
        )
        payload = await self._finalize_tutor_call(
            LLM_OPERATION_SUMMARIZE_SESSION,
            reply,
            history_kind=LLM_OPERATION_SUMMARIZE_SESSION,
            metadata={
                "degraded": reply.degraded,
                "diagnostic": reply.diagnostic,
                "payload": reply.payload,
                "screen_classification": tutor_context.get("screen_classification")
                or {},
            },
        )
        payload["screen_classification"] = (
            tutor_context.get("screen_classification") or {}
        )
        try:
            await self._emit_session_summarized_event(payload)
        except Exception as exc:
            self.logger.warning("study session summarized event degraded: {}", exc)
        return Ok(payload)
