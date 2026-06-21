from __future__ import annotations

from .entry_common import (
    asyncio,
    Err,
    Ok,
    SdkError,
    _entry_exception_error,
    _validate_optional_vision_image_payload,
    plugin_entry,
    tr,
    ui,
    LLM_OPERATION_ANSWER_EVALUATE,
)
from .models import public_current_question_payload


class _TutorAnswerEntriesMixin:
    @ui.action()
    @plugin_entry(
        id="study_evaluate_answer",
        name=tr("entries.evaluate_answer.name", default="Evaluate Study Answer"),
        description=tr(
            "entries.evaluate_answer.description",
            default="Evaluate an answer against the current generated question or a supplied question.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "answer": {"type": "string", "default": ""},
                "question": {"type": "string", "default": ""},
                "expected_answer": {"type": "string", "default": ""},
                "question_id": {"type": "string", "default": ""},
                "attempt_id": {"type": "string", "default": ""},
                "selected_topic_id": {"type": "string", "default": ""},
                "vision_image_base64": {"type": "string", "default": ""},
            },
        },
        timeout=310.0,
        llm_result_fields=[
            "summary",
            "verdict",
            "score",
            "error_type",
            "feedback",
            "next_action",
        ],
    )
    async def study_evaluate_answer(
        self, answer: str = "", question: str = "", expected_answer: str = "", **kwargs
    ):
        if self._agent is None:
            return Err(SdkError("study tutor agent is not initialized"))
        async with self._lock:
            current_question = dict(self._state.current_question)
            active_mode = self._state.active_mode
        supplied_question = str(question or "").strip()
        supplied_expected = str(expected_answer or "").strip()
        state_question = str(current_question.get("question") or "").strip()
        state_expected = str(current_question.get("answer") or "").strip()
        supplied_question_id = str(kwargs.get("question_id") or "").strip()
        supplied_attempt_id = str(kwargs.get("attempt_id") or "").strip()
        state_question_id = str(current_question.get("question_id") or "").strip()
        state_attempt_id = str(current_question.get("attempt_id") or "").strip()
        current_question_requires_identity = bool(state_question_id or state_attempt_id)
        using_current_question = (
            not supplied_question or supplied_question == state_question
        )
        if current_question_requires_identity and using_current_question:
            if (
                not supplied_question_id
                or not supplied_attempt_id
                or supplied_question_id != state_question_id
                or supplied_attempt_id != state_attempt_id
            ):
                return Err(
                    SdkError(
                        "current question identity does not match",
                        code="QUESTION_MISMATCH",
                    )
                )
            if current_question.get("attempt_evaluated"):
                return Err(
                    SdkError(
                        "attempt has already been evaluated",
                        code="ATTEMPT_ALREADY_EVALUATED",
                    )
                )
        resolved_question = supplied_question or state_question
        if not resolved_question:
            return Err(SdkError("study tutor requires a question to evaluate against"))
        vision_image_payload = str(kwargs.get("vision_image_base64") or "").strip()
        validated_vision_image = _validate_optional_vision_image_payload(
            self, vision_image_payload, operation="study_evaluate_answer"
        )
        if isinstance(validated_vision_image, Err):
            return validated_vision_image
        vision_image_payload = validated_vision_image
        resolved_expected = supplied_expected
        if not resolved_expected and (
            not supplied_question or supplied_question == state_question
        ):
            resolved_expected = state_expected
        answer_text = str(answer or "").strip()
        question_payload = dict(current_question) if using_current_question else {}
        question_payload.update(
            {
                "question": resolved_question,
                "answer": resolved_expected,
            }
        )
        selected_topic_id = str(
            kwargs.get("selected_topic_id")
            or question_payload.get("selected_topic_id")
            or question_payload.get("topic_id")
            or ""
        ).strip()
        if selected_topic_id:
            question_payload["selected_topic_id"] = selected_topic_id
        reserved_attempt = False
        if using_current_question and state_attempt_id:
            async with self._lock:
                live_question = self._state.current_question
                if str(live_question.get("attempt_id") or "") != state_attempt_id:
                    return Err(
                        SdkError(
                            "current question identity does not match",
                            code="QUESTION_MISMATCH",
                        )
                    )
                if live_question.get("attempt_evaluated") or live_question.get(
                    "attempt_evaluation_pending"
                ):
                    return Err(
                        SdkError(
                            "attempt has already been evaluated",
                            code="ATTEMPT_ALREADY_EVALUATED",
                        )
                    )
                live_question["attempt_evaluation_pending"] = True
                reserved_attempt = True
        run_id = self._resolve_current_run_id(kwargs)
        session_id = str(kwargs.get("session_id") or "").strip()
        try:
            tutor_context = await self._build_learning_context(
                LLM_OPERATION_ANSWER_EVALUATE,
                input_text=answer_text,
                extra={
                    "question": resolved_question,
                    "expected_answer": resolved_expected,
                    "answer": answer_text,
                    "current_question": current_question
                    if using_current_question
                    else {},
                    "public_current_question": public_current_question_payload(
                        current_question
                    )
                    if using_current_question
                    else {},
                    "question_payload": question_payload,
                    "question_source": "current_question"
                    if using_current_question
                    else "supplied",
                    "run_id": run_id,
                    "session_id": session_id,
                    "question_id": supplied_question_id or state_question_id,
                    "attempt_id": supplied_attempt_id or state_attempt_id,
                    "selected_topic_id": selected_topic_id,
                    "mode": active_mode,
                    **(
                        {
                            "vision_enabled": True,
                            "vision_image_base64": vision_image_payload,
                        }
                        if vision_image_payload
                        else {}
                    ),
                },
            )
            reply = await self._agent.answer_evaluate(
                question=resolved_question,
                answer=answer_text,
                expected_answer=resolved_expected,
                mode=active_mode,
                context=tutor_context,
            )
            payload = await self._finalize_tutor_call(
                LLM_OPERATION_ANSWER_EVALUATE,
                reply,
                history_kind=LLM_OPERATION_ANSWER_EVALUATE,
                metadata={
                    "question": resolved_question,
                    "question_id": supplied_question_id or state_question_id,
                    "attempt_id": supplied_attempt_id or state_attempt_id,
                    "degraded": reply.degraded,
                    "diagnostic": reply.diagnostic,
                    "payload": reply.payload,
                    "screen_classification": tutor_context.get(
                        "screen_classification"
                    )
                    or {},
                },
                extra_context=tutor_context,
            )
            payload["question"] = resolved_question
            if supplied_question_id or state_question_id:
                payload["question_id"] = supplied_question_id or state_question_id
            if supplied_attempt_id or state_attempt_id:
                payload["attempt_id"] = supplied_attempt_id or state_attempt_id
            if selected_topic_id:
                payload["selected_topic_id"] = selected_topic_id
            payload["screen_classification"] = (
                tutor_context.get("screen_classification") or {}
            )
            topic = str(
                payload.get("topic")
                or payload.get("selected_topic_id")
                or question_payload.get("topic")
                or question_payload.get("selected_topic_id")
                or tutor_context.get("topic")
                or ""
            ).strip()
            try:
                mastery_after = (
                    await asyncio.to_thread(self._knowledge_tracker.get_mastery, topic)
                    if topic
                    else -1.0
                )
            except Exception as exc:
                self.logger.warning("study answer mastery enrichment failed: {}", exc)
                mastery_after = -1.0
            await self._emit_answer_evaluated_event(
                verdict=str(payload.get("verdict") or ""),
                score=payload.get("score", 0.0),
                question_summary=resolved_question,
                user_answer_summary=answer_text,
                correction_hint=str(
                    payload.get("correction_hint")
                    or payload.get("feedback")
                    or payload.get("next_action")
                    or ""
                ),
                topic=topic,
                mastery_after=mastery_after,
            )
            if using_current_question and state_attempt_id:
                public_eval_cache = {
                    key: value
                    for key, value in payload.items()
                    if key
                    not in {
                        "answer",
                        "accepted_answers",
                        "key_points",
                        "rubric",
                        "solution_steps",
                        "internal_private_payload",
                        "current_question_private",
                    }
                }
                async with self._lock:
                    if (
                        str(self._state.current_question.get("attempt_id") or "")
                        == state_attempt_id
                    ):
                        self._state.current_question.pop(
                            "attempt_evaluation_pending", None
                        )
                        self._state.current_question["attempt_evaluated"] = True
                        self._state.current_question["answer_evaluation_cache"] = (
                            public_eval_cache
                        )
                await self._persist_state()
            return Ok(payload)
        except Exception as exc:
            if reserved_attempt:
                async with self._lock:
                    if (
                        str(self._state.current_question.get("attempt_id") or "")
                        == state_attempt_id
                    ):
                        self._state.current_question.pop(
                            "attempt_evaluation_pending", None
                        )
            return _entry_exception_error(
                self, exc, operation="study_evaluate_answer"
            )
