from __future__ import annotations

from ._common import *  # noqa: F401, F403





class _TutorExplainEntriesMixin:



    @plugin_entry(
        id="study_explain_text",
        name=tr("entries.explain_text.name", default="Explain Study Text"),
        description=tr(
            "entries.explain_text.description",
            default="Explain a concept from supplied text, or use the latest OCR text if text is omitted.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "default": ""},
            },
        },
        timeout=45.0,
        llm_result_fields=["summary", "reply", "diagnostic"],
    )
    async def study_explain_text(self, text: str = "", **_):
        if self._agent is None:
            return Err(SdkError("study tutor agent is not initialized"))
        raw_text = str(text or "").strip()
        # Phase 1: detect an explicit mode intent and switch first when present.
        intent = (
            handle_user_intent(raw_text, language=self._cfg.language)
            if raw_text
            else {
                "matched": False,
                "pure_switch": False,
                "mode": "",
                "remaining_text": "",
            }
        )
        async with self._lock:
            active_mode = self._state.active_mode
        mode_switch: dict[str, Any] = {}
        if intent.get("matched") and intent.get("kind") == "mode_switch":
            try:
                mode_switch = await self._apply_mode_switch(
                    str(intent.get("mode") or MODE_COMPANION),
                    f"intent:{intent.get('keyword') or 'text'}",
                    language=self._cfg.language,
                )
                active_mode = str(mode_switch.get("new_mode") or active_mode)
            except ValueError as exc:
                return Err(SdkError(str(exc)))
            if intent.get("pure_switch"):
                transition_phrase = str(
                    mode_switch.get("transition_phrase")
                    or intent.get("transition_phrase")
                    or ""
                )
                return Ok(
                    {
                        **mode_switch,
                        "reply": transition_phrase,
                        "summary": transition_phrase,
                        "operation": MODE_CONCEPT_EXPLAIN,
                        "input_text": raw_text,
                        "degraded": False,
                    }
                )
        # Phase 2: resolve the text to explain.
        intent_kind = str(intent.get("kind") or "")
        source_text = str(intent.get("remaining_text") or "").strip()
        if not source_text and intent_kind != "concept_explain":
            source_text = raw_text
        used_ocr_fallback = False
        if not source_text:
            async with self._lock:
                source_text = self._state.last_ocr_text
            used_ocr_fallback = bool(source_text.strip())
        # Phase 3: explain with the active mode selected above.
        tutor_context = await self._build_learning_context(
            LLM_OPERATION_CONCEPT_EXPLAIN,
            input_text=source_text,
            extra={
                "source": "ocr_snapshot"
                if used_ocr_fallback or not raw_text
                else "manual",
                "mode": active_mode,
                "mode_switch": bool(mode_switch.get("changed")),
                "source_text": source_text,
            },
        )
        reply = await self._agent.concept_explain(
            source_text,
            mode=active_mode,
            context=tutor_context,
        )
        payload = await self._finalize_tutor_call(
            LLM_OPERATION_CONCEPT_EXPLAIN,
            reply,
            history_kind=MODE_CONCEPT_EXPLAIN,
            metadata={
                "degraded": reply.degraded,
                "diagnostic": reply.diagnostic,
                "mode": active_mode,
                "mode_switch": mode_switch,
                "intent": intent,
                "screen_classification": tutor_context.get("screen_classification")
                or {},
            },
            extra_context=tutor_context,
        )
        if mode_switch:
            payload["mode_switch"] = mode_switch
        if intent.get("matched"):
            payload["intent"] = intent
            if intent.get("pure_switch"):
                payload["transition_phrase"] = str(
                    mode_switch.get("transition_phrase")
                    or intent.get("transition_phrase")
                    or ""
                )
        return Ok(payload)
