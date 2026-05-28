from __future__ import annotations

from ._common import *  # noqa: F401, F403





class _TutorLearningSupportMixin:



    async def _track_learning(
        self,
        operation: str,
        reply: TutorReply,
        *,
        extra_context: dict[str, Any] | None = None,
    ) -> None:
        if self._agent is None or not hasattr(self._agent, "knowledge_track"):
            return
        try:
            track_context = await self._build_learning_context(
                LLM_OPERATION_KNOWLEDGE_TRACK,
                input_text=reply.input_text,
                extra={
                    "operation": operation,
                    "result": reply.payload or {"reply": reply.reply},
                    "reply": reply.reply,
                    "degraded": reply.degraded,
                    "diagnostic": reply.diagnostic,
                    **(extra_context or {}),
                },
            )
            track_reply = await self._agent.knowledge_track(
                mode=self._state.active_mode, context=track_context
            )
        except Exception as exc:
            self.logger.warning("study knowledge track failed: {}", exc)
            track_reply = TutorReply(
                operation=LLM_OPERATION_KNOWLEDGE_TRACK,
                input_text=reply.input_text,
                reply="knowledge track updated",
                payload={
                    "topic": self._guess_track_topic(reply),
                    "mastery_delta": 0.0,
                    "confidence": 0.35,
                    "weak_points": [],
                    "next_steps": [],
                    "screen_type": self._screen_classification_context().get(
                        "screen_type"
                    )
                    or "",
                },
                degraded=True,
                diagnostic=diagnostic_code_for_exception(exc),
                created_at=utc_now_iso(),
            )
        self._record_tutor_result(LLM_OPERATION_KNOWLEDGE_TRACK, track_reply)
        if operation == LLM_OPERATION_ANSWER_EVALUATE:
            await self._record_answer_knowledge(
                reply, track_reply, extra_context=extra_context
            )

    async def _record_answer_knowledge(
        self,
        eval_reply: TutorReply,
        track_reply: TutorReply,
        *,
        extra_context: dict[str, Any] | None = None,
    ) -> None:
        context = dict(extra_context or {})
        track_payload = dict(track_reply.payload or {})
        eval_payload = dict(eval_reply.payload or {})
        current_question = dict(context.get("current_question") or {})
        question_payload = dict(context.get("question_payload") or current_question)
        question_text = str(
            context.get("question")
            or question_payload.get("question")
            or current_question.get("question")
            or ""
        ).strip()
        question_payload["question"] = question_text
        question_payload["answer"] = str(
            context.get("expected_answer")
            or question_payload.get("answer")
            or current_question.get("answer")
            or ""
        )
        topic = str(
            question_payload.get("topic")
            or track_payload.get("topic")
            or eval_payload.get("topic")
            or self._guess_track_topic(track_reply)
        ).strip()
        if topic:
            question_payload.setdefault("topic", topic)
        eval_result = {
            **eval_payload,
            "topic": topic,
            "track": track_payload,
        }
        session_id = (
            str(
                context.get("session_id")
                or context.get("run_id")
                or getattr(self._state, "run_id", "")
                or getattr(self.ctx, "run_id", "")
                or "default"
            ).strip()
            or "default"
        )
        mastery_before: float | None = 0.0
        if topic:
            try:
                mastery_before = await asyncio.to_thread(
                    self._knowledge_tracker.get_mastery, topic
                )
            except Exception as exc:
                self.logger.warning(
                    "study knowledge tracker mastery-before read failed: {}", exc
                )
                mastery_before = None
        try:
            tracking_result = await asyncio.to_thread(
                self._knowledge_tracker.on_answer,
                topic_id=topic,
                question=question_payload,
                user_answer=str(context.get("answer") or eval_reply.input_text or ""),
                eval_result=eval_result,
                mode=str(context.get("mode") or self._state.active_mode),
                session_id=session_id,
            )
        except Exception as exc:
            self.logger.warning("study knowledge tracker persistence failed: {}", exc)
            return
        tracked_topic = str(tracking_result.get("topic_id") or topic).strip()
        mastery_after: float | None = None
        if tracked_topic:
            try:
                mastery_after = await asyncio.to_thread(
                    self._knowledge_tracker.get_mastery, tracked_topic
                )
            except Exception as exc:
                self.logger.warning(
                    "study knowledge tracker mastery-after read failed: {}", exc
                )
        crossed = (
            _detect_mastery_threshold_crossed(mastery_before, mastery_after)
            if mastery_before is not None and mastery_after is not None
            else None
        )
        if (
            self._event_bus is not None
            and crossed is not None
            and mastery_before is not None
            and mastery_after is not None
        ):
            self._event_bus.schedule_emit(
                StudyEvent(
                    name="mastery_updated",
                    payload={
                        "topic": tracked_topic,
                        "mastery": mastery_after,
                        "mastery_before": mastery_before,
                        "direction": "up"
                        if mastery_after > mastery_before
                        else "down",
                        "crossed_threshold": crossed,
                        "evidence_count": 1,
                    },
                )
            )

    @staticmethod
    def _guess_track_topic(reply: TutorReply) -> str:
        payload = dict(reply.payload or {})
        topic = str(payload.get("topic") or "").strip()
        if topic:
            return topic
        text = str(reply.input_text or "").strip()
        first_line = next(
            (line.strip() for line in text.splitlines() if line.strip()), ""
        )
        return first_line[:48] or "general"
