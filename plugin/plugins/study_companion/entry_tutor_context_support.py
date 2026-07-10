from __future__ import annotations

from .entry_common import (
    Any,
    asyncio,
    time,
    LLM_OPERATION_ANSWER_EVALUATE,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    LLM_OPERATION_KNOWLEDGE_TRACK,
    LLM_OPERATION_QUESTION_GENERATE,
    LLM_OPERATION_SUMMARIZE_SESSION,
    StudyEvent,
    TutorReply,
    utc_now_iso,
    build_tutor_payload,
    diagnostic_code_for_exception,
    _detect_mastery_threshold_crossed,
    _plugin_lock,
)
from .knowledge_graph_guidance import build_knowledge_guidance_payload
from .models import public_current_question_payload


def _warn(logger: Any, message: str, *args: Any) -> None:
    warning = getattr(logger, "warning", None)
    if callable(warning):
        warning(message, *args)


class _LearningContext(dict[str, Any]):
    def __init__(
        self,
        *args: Any,
        public_knowledge_guidance: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.public_knowledge_guidance = public_knowledge_guidance


class _TutorContextSupportMixin:
    def _invalidate_knowledge_guidance_cache(self) -> None:
        cache = getattr(self, "_knowledge_guidance_topics_cache", None)
        if isinstance(cache, dict):
            cache.clear()

    async def _build_knowledge_guidance_context(
        self,
        operation: str,
        *,
        input_text: str = "",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if operation in {
            LLM_OPERATION_KNOWLEDGE_TRACK,
            LLM_OPERATION_SUMMARIZE_SESSION,
        }:
            return {}
        seed = dict(context or {})
        query = str(
            seed.get("source_text")
            or seed.get("question")
            or seed.get("topic_hint")
            or seed.get("topic")
            or input_text
            or ""
        ).strip()
        topic_id = str(
            seed.get("selected_topic_id")
            or seed.get("topic_id")
            or seed.get("target_topic_id")
            or ""
        ).strip()
        if not query and not topic_id:
            return {}
        try:
            cache_key = "all:5000"
            cache = getattr(self, "_knowledge_guidance_topics_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                setattr(self, "_knowledge_guidance_topics_cache", cache)
            topics = cache.get(cache_key)
            if topics is None:
                topics = await asyncio.to_thread(self._store.list_topics, 5000, None, None)
                cache[cache_key] = list(topics or [])
            return build_knowledge_guidance_payload(
                topics=list(topics or []),
                topic_id=topic_id,
                query=query,
                max_depth=3,
                match_limit=5,
            )
        except Exception as exc:
            _warn(
                getattr(self, "logger", None),
                "study knowledge graph guidance failed: {}",
                exc,
            )
            return {}

    def _merge_session_summary_seed(
        self,
        operation: str,
        *,
        payload: dict[str, Any] | None = None,
        seed: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = dict(seed or {})
        payload = dict(payload or {})
        current["event_count"] = int(current.get("event_count") or 0) + 1
        current["last_operation"] = operation
        current["last_updated_at"] = utc_now_iso()
        screen_type = str(
            payload.get("screen_type") or current.get("last_screen_type") or ""
        ).strip()
        if screen_type:
            current["last_screen_type"] = screen_type
        if operation == LLM_OPERATION_QUESTION_GENERATE:
            current["question_count"] = int(current.get("question_count") or 0) + 1
        elif operation == LLM_OPERATION_ANSWER_EVALUATE:
            current["answer_count"] = int(current.get("answer_count") or 0) + 1
            verdict = str(payload.get("verdict") or "").strip()
            if verdict:
                verdict_counts = dict(current.get("verdict_counts") or {})
                verdict_counts[verdict] = int(verdict_counts.get(verdict) or 0) + 1
                current["verdict_counts"] = verdict_counts
            weak_points = [
                item for item in payload.get("weak_points") or [] if str(item).strip()
            ]
            if weak_points:
                current["weak_points"] = weak_points[:6]
        elif operation == LLM_OPERATION_CONCEPT_EXPLAIN:
            current["explain_count"] = int(current.get("explain_count") or 0) + 1
        elif operation == LLM_OPERATION_KNOWLEDGE_TRACK:
            current["track_count"] = int(current.get("track_count") or 0) + 1
        elif operation == LLM_OPERATION_SUMMARIZE_SESSION:
            current["summary_count"] = int(current.get("summary_count") or 0) + 1
        topic = str(payload.get("topic") or "").strip()
        if topic:
            current["last_topic"] = topic
        weak_points = [
            item for item in payload.get("weak_points") or [] if str(item).strip()
        ]
        if weak_points:
            current["weak_points"] = weak_points[:6]
        return current

    async def _build_learning_context(
        self,
        operation: str,
        *,
        input_text: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = self._state_snapshot()
        history_limit = max(5, min(12, int(self._cfg.history_limit or 10)))
        history = await asyncio.to_thread(self._store.list_interactions, history_limit)
        current_question = snapshot.get("current_question") or {}
        public_current_question = public_current_question_payload(current_question)
        context = _LearningContext(
            {
                "operation": operation,
                "input_text": input_text,
                "language": self._cfg.language,
                "mode": snapshot.get("active_mode") or self._cfg.mode,
                "screen_classification": snapshot.get("last_screen_classification")
                or {},
                "recent_screen_classifications": snapshot.get(
                    "recent_screen_classifications"
                )
                or [],
                "current_question": public_current_question,
                "public_current_question": public_current_question,
                "last_answer_evaluation": snapshot.get("last_answer_evaluation")
                or {},
                "session_summary_seed": snapshot.get("session_summary_seed") or {},
                "recent_learning_events": (
                    snapshot.get("recent_learning_events") or []
                )[-8:],
                "last_ocr_text": snapshot.get("last_ocr_text") or "",
                "last_ocr_at": snapshot.get("last_ocr_at") or "",
                "history": history,
            }
        )
        if operation == LLM_OPERATION_QUESTION_GENERATE:
            hint = ""
            if extra:
                hint = str(extra.get("topic_hint") or extra.get("topic") or "").strip()
            supplied_params = (
                extra.get("knowledge_question_params")
                if isinstance(extra, dict)
                else None
            )
            if isinstance(supplied_params, dict):
                context["knowledge_question_params"] = dict(supplied_params)
            else:
                context["knowledge_question_params"] = await asyncio.to_thread(
                    self._knowledge_tracker.get_next_question_params,
                    hint,
                )
        elif operation == LLM_OPERATION_SUMMARIZE_SESSION:
            context["knowledge_session_summary"] = await asyncio.to_thread(
                self._knowledge_tracker.get_session_summary
            )
        else:
            context["knowledge_summary"] = await asyncio.to_thread(
                self._knowledge_tracker.get_status_summary,
                limit=5,
            )
        if bool(self._cfg.llm_vision_enabled):
            user_image = ""
            async with _plugin_lock(self._lock):
                user_image = str(self._state.last_vision_image_base64 or "").strip()
            if user_image:
                context["vision_enabled"] = True
                context["vision_image_base64"] = user_image
            elif self._ocr_pipeline is not None:
                vision_snapshot = self._ocr_pipeline.latest_vision_snapshot()
                if vision_snapshot:
                    context["vision_enabled"] = True
                    context["vision_image_base64"] = str(
                        vision_snapshot.get("vision_image_base64") or ""
                    )
                    context["vision_snapshot"] = {
                        key: value
                        for key, value in vision_snapshot.items()
                        if key != "vision_image_base64"
                    }
        if extra:
            context.update(extra)
        guidance = await self._build_knowledge_guidance_context(
            operation,
            input_text=input_text,
            context=context,
        )
        if guidance.get("summary", {}).get("matched"):
            context.public_knowledge_guidance = guidance
            if operation == LLM_OPERATION_CONCEPT_EXPLAIN:
                context["knowledge_guidance"] = guidance
            else:
                model_context = guidance.get("model_context")
                context["knowledge_guidance"] = (
                    dict(model_context) if isinstance(model_context, dict) else guidance
                )
        return context

    async def _record_tutor_result(
        self, operation: str, reply: TutorReply, *, extra: dict[str, Any] | None = None
    ) -> None:
        payload = dict(reply.payload or {})
        summary = str(reply.reply or "").strip()
        async with _plugin_lock(self._lock):
            event = {
                "operation": operation,
                "kind": operation,
                "input_text": reply.input_text,
                "summary": summary,
                "degraded": bool(reply.degraded),
                "diagnostic": reply.diagnostic,
                "at": time.time(),
                "created_at": reply.created_at or utc_now_iso(),
                "screen_type": str(
                    payload.get("screen_type")
                    or (extra or {}).get("screen_type")
                    or self._state.last_screen_classification.get("screen_type")
                    or ""
                ),
            }
            seed = self._merge_session_summary_seed(
                operation, payload=payload, seed=self._state.session_summary_seed
            )
            self._state.session_summary_seed = seed
            self._state.recent_learning_events = (
                self._state.recent_learning_events + [event]
            )[-16:]
            if operation != LLM_OPERATION_KNOWLEDGE_TRACK:
                if operation == LLM_OPERATION_QUESTION_GENERATE:
                    if str(payload.get("question") or "").strip():
                        self._state.current_question = dict(payload)
                        self._state.last_question_at = reply.created_at or utc_now_iso()
                elif operation == LLM_OPERATION_ANSWER_EVALUATE:
                    self._state.last_answer_evaluation = dict(payload)
                    self._state.last_answer_evaluated_at = (
                        reply.created_at or utc_now_iso()
                    )
                elif operation == LLM_OPERATION_SUMMARIZE_SESSION:
                    self._state.last_session_summary = str(
                        payload.get("summary") or ""
                    ).strip()
                    self._state.last_session_summary_at = (
                        reply.created_at or utc_now_iso()
                    )

    async def _finalize_tutor_call(
        self,
        operation: str,
        reply: TutorReply,
        *,
        history_kind: str,
        metadata: dict[str, Any],
        extra_context: dict[str, Any] | None = None,
        public_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self._record_tutor_result(operation, reply, extra=extra_context)
        diagnostic = str(reply.diagnostic or "")
        if diagnostic and reply.degraded:
            async with _plugin_lock(self._lock):
                self._state.last_error = diagnostic
        await asyncio.to_thread(
            self._store.append_interaction,
            kind=history_kind,
            input_text=reply.input_text,
            output_text=reply.reply,
            metadata=metadata,
            history_limit=self._cfg.history_limit,
        )
        tracking_enrichment: dict[str, Any] = {}
        if operation != LLM_OPERATION_SUMMARIZE_SESSION:
            tracking_enrichment = await self._track_learning(
                operation,
                reply,
                extra_context=extra_context,
                public_payload=public_payload,
            )
        await self._persist_state()
        if public_payload is not None:
            payload = {
                "operation": reply.operation,
                "input_text": reply.input_text,
                "reply": reply.reply,
                "degraded": reply.degraded,
                "diagnostic": reply.diagnostic,
                "created_at": reply.created_at or utc_now_iso(),
                **public_payload,
            }
            payload.setdefault("summary", reply.reply)
        else:
            payload = build_tutor_payload(reply)
        guidance = getattr(extra_context, "public_knowledge_guidance", None)
        if not isinstance(guidance, dict):
            guidance = (extra_context or {}).get("knowledge_guidance")
        if isinstance(guidance, dict):
            summary = guidance.get("summary")
            if isinstance(summary, dict) and summary.get("matched"):
                payload["knowledge_guidance"] = guidance
                diagnosis_questions = guidance.get("diagnosis_questions")
                payload["diagnosis_questions"] = (
                    list(diagnosis_questions)
                    if isinstance(diagnosis_questions, list)
                    else []
                )
        payload.update(tracking_enrichment)
        return payload

    async def _track_learning(
        self,
        operation: str,
        reply: TutorReply,
        *,
        extra_context: dict[str, Any] | None = None,
        public_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._agent is None or not hasattr(self._agent, "knowledge_track"):
            return {}
        try:
            track_context = await self._build_learning_context(
                LLM_OPERATION_KNOWLEDGE_TRACK,
                input_text=reply.input_text,
                extra={
                    "operation": operation,
                    "result": public_payload or reply.payload or {"reply": reply.reply},
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
        await self._record_tutor_result(LLM_OPERATION_KNOWLEDGE_TRACK, track_reply)
        if operation == LLM_OPERATION_ANSWER_EVALUATE:
            return await self._record_answer_knowledge(
                reply, track_reply, extra_context=extra_context
            )
        return {}

    async def _record_answer_knowledge(
        self,
        eval_reply: TutorReply,
        track_reply: TutorReply,
        *,
        extra_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
            return {}
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
                        "direction": "up" if mastery_after > mastery_before else "down",
                        "crossed_threshold": crossed,
                        "evidence_count": 1,
                    },
                )
            )
        if mastery_before is None or mastery_after is None or not tracked_topic:
            return {}
        return {
            "selected_topic_id": tracked_topic,
            "mastery_before": mastery_before,
            "mastery_after": mastery_after,
            "mastery_delta": round(mastery_after - mastery_before, 4),
        }

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
