from __future__ import annotations

from dataclasses import dataclass, field
import threading

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
from .knowledge_graph_guidance import match_topics
from .models import public_current_question_payload
from ._semantic_routing import (
    StudyInputSemantics,
    build_semantic_routing_messages,
    parse_study_input_semantics,
)


_SEMANTIC_ROUTE_OPERATION = "knowledge_semantic_route"
_SEMANTIC_ROUTE_MIN_CONFIDENCE = 0.6
_SEMANTIC_ROUTE_TIMEOUT_SECONDS = 12.0
_CANCEL_DRAIN_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True)
class _TutorFinalizeProgress:
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    worker_started: threading.Event = field(default_factory=threading.Event)
    commit_started: threading.Event = field(default_factory=threading.Event)
    history_persisted: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)


async def _append_interaction_cancel_safe(
    store: Any,
    *,
    progress: _TutorFinalizeProgress,
    kind: str,
    input_text: str,
    output_text: str,
    metadata: dict[str, Any],
    history_limit: int,
) -> None:
    try:
        persisted = await asyncio.to_thread(
            store.append_interaction,
            kind=kind,
            input_text=input_text,
            output_text=output_text,
            metadata=metadata,
            history_limit=history_limit,
            cancel_event=progress.cancel_requested,
            worker_started_event=progress.worker_started,
            commit_started_event=progress.commit_started,
            committed_event=progress.history_persisted,
            finished_event=progress.finished,
        )
    except asyncio.CancelledError:
        progress.cancel_requested.set()
        if progress.commit_started.is_set():
            finished = await asyncio.shield(
                asyncio.to_thread(
                    progress.finished.wait, _CANCEL_DRAIN_TIMEOUT_SECONDS
                )
            )
            if not finished:
                _warn(
                    getattr(store, "_logger", None),
                    "study interaction cancellation drain timed out",
                )
        raise
    if persisted is False:
        raise asyncio.CancelledError


def _consume_background_task(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except BaseException:
        pass


async def _await_completion_on_cancel(
    awaitable: Any,
    *,
    timeout_seconds: float = _CANCEL_DRAIN_TIMEOUT_SECONDS,
    logger: Any = None,
) -> Any:
    task = asyncio.create_task(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=max(0.0, float(timeout_seconds))
            )
        except asyncio.TimeoutError:
            _warn(logger, "study state persistence cancellation drain timed out")
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        if not task.done():
            task.add_done_callback(_consume_background_task)
        raise


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


def _knowledge_guidance_outcome(
    *,
    status: str,
    semantics: StudyInputSemantics | None = None,
    guidance: dict[str, Any] | None = None,
    source: str = "semantic_route",
    semantic_status: str = "not_applicable",
    semantic_reason: str = "",
    response_mode: str | None = None,
) -> dict[str, Any]:
    topic = guidance.get("topic") if isinstance(guidance, dict) else None
    topic = topic if isinstance(topic, dict) else {}
    related: list[dict[str, str]] = []
    subgraph = guidance.get("relevant_subgraph") if isinstance(guidance, dict) else None
    nodes = subgraph.get("nodes") if isinstance(subgraph, dict) else None
    if isinstance(nodes, list):
        focus_id = str(topic.get("id") or "").strip()
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id") or "").strip()
            label = str(node.get("label") or "").strip()
            if node_id and label and node_id != focus_id:
                related.append({"id": node_id, "label": label})
            if len(related) >= 4:
                break
    applied = status == "applied" and bool(topic.get("id"))
    subject = semantics.subject if semantics else str(topic.get("subject") or "unknown")
    return {
        "knowledge_guidance_applied": applied,
        "knowledge_guidance_status": "applied" if applied else status,
        "knowledge_guidance_subject": subject,
        "knowledge_guidance_content_type": semantics.content_type if semantics else "",
        "knowledge_guidance_entity": semantics.entity if semantics else "",
        "knowledge_guidance_focus_topic": (
            {
                "id": str(topic.get("id") or ""),
                "label": str(topic.get("label") or ""),
            }
            if applied
            else {}
        ),
        "knowledge_guidance_related_topics": related if applied else [],
        "knowledge_guidance_source": source,
        "study_semantic_status": semantic_status,
        "study_semantic_reason": semantic_reason,
        "study_response_mode": (
            response_mode
            if response_mode is not None
            else semantics.response_mode if semantics else "unknown"
        ),
        "study_semantic_subject": semantics.subject if semantics else "unknown",
        "study_semantic_content_type": semantics.content_type if semantics else "unknown",
        "study_semantic_intent": semantics.intent if semantics else "unknown",
        "study_semantic_entity": semantics.entity if semantics else "",
    }


def _topic_reference_ids(topic: dict[str, Any]) -> list[str]:
    reference_ids: list[str] = []
    for field in ("prerequisites", "related"):
        refs = topic.get(field)
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if isinstance(ref, dict):
                ref_id = str(ref.get("id") or ref.get("topic_id") or "").strip()
            else:
                ref_id = str(ref or "").strip()
            if ref_id:
                reference_ids.append(ref_id)
    return reference_ids


def _load_explicit_guidance_topics(
    store: Any,
    topic_id: str,
    *,
    max_depth: int = 2,
) -> list[dict[str, Any]]:
    topics: list[dict[str, Any]] = []
    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(topic_id, 0)]
    while queue and len(topics) < 24:
        current_id, depth = queue.pop(0)
        if not current_id or current_id in seen:
            continue
        seen.add(current_id)
        topic = store.get_topic(current_id)
        if not isinstance(topic, dict):
            continue
        topics.append(topic)
        if depth >= max_depth:
            continue
        queue.extend(
            (reference_id, depth + 1)
            for reference_id in _topic_reference_ids(topic)
            if reference_id not in seen
        )
    return topics


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
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if operation in {
            LLM_OPERATION_KNOWLEDGE_TRACK,
            LLM_OPERATION_SUMMARIZE_SESSION,
        }:
            return {}, _knowledge_guidance_outcome(status="not_applicable")
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
            return {}, _knowledge_guidance_outcome(status="not_applicable")
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
            topic_items = list(topics or [])
            if topic_id:
                explicit = match_topics(topic_items, topic_id=topic_id, limit=1)
                if not explicit:
                    explicit_topics = await asyncio.to_thread(
                        _load_explicit_guidance_topics,
                        self._store,
                        topic_id,
                    )
                    topics_by_id = {
                        str(topic.get("id") or ""): topic
                        for topic in topic_items
                        if isinstance(topic, dict) and topic.get("id")
                    }
                    topics_by_id.update(
                        {
                            str(topic.get("id") or ""): topic
                            for topic in explicit_topics or []
                            if isinstance(topic, dict) and topic.get("id")
                        }
                    )
                    topic_items = list(topics_by_id.values())
                    explicit = match_topics(topic_items, topic_id=topic_id, limit=1)
                if not explicit:
                    return {}, _knowledge_guidance_outcome(
                        status="not_matched", source="selected_topic"
                    )
                explicit_topic = explicit[0]
                explicit_response_mode = (
                    "general_explanation"
                    if operation == LLM_OPERATION_CONCEPT_EXPLAIN
                    else "problem_solving"
                )
                semantics = StudyInputSemantics(
                    subject=str(explicit_topic.get("subject") or "unknown"),
                    content_type="selected_topic",
                    intent="explicit_topic",
                    response_mode=explicit_response_mode,
                    entity=str(explicit_topic.get("label") or ""),
                    retrieval_concepts=(str(explicit_topic.get("label") or ""),),
                    confidence=1.0,
                )
                guidance = build_knowledge_guidance_payload(
                    topics=topic_items,
                    topic_id=topic_id,
                    query=query,
                    response_mode=explicit_response_mode,
                    max_depth=3,
                    match_limit=5,
                )
                return guidance, _knowledge_guidance_outcome(
                    status="applied",
                    semantics=semantics,
                    guidance=guidance,
                    source="selected_topic",
                    semantic_status="available",
                    response_mode=explicit_response_mode,
                )
            if operation != LLM_OPERATION_CONCEPT_EXPLAIN:
                guidance = build_knowledge_guidance_payload(
                    topics=topic_items,
                    query=query,
                    max_depth=3,
                    match_limit=5,
                )
                status = (
                    "applied"
                    if guidance.get("summary", {}).get("matched")
                    else "not_matched"
                )
                return guidance, _knowledge_guidance_outcome(
                    status=status, guidance=guidance, source="query_match"
                )

            semantics, route_status, route_reason = await self._route_study_input_semantics(
                query, context=seed
            )
            if "_agent_quota_reservation" in seed and context is not None:
                context["_agent_quota_reservation"] = seed[
                    "_agent_quota_reservation"
                ]
            if semantics is None:
                return {}, _knowledge_guidance_outcome(
                    status=route_status,
                    semantic_status=route_status,
                    semantic_reason=route_reason,
                )
            if semantics.confidence < _SEMANTIC_ROUTE_MIN_CONFIDENCE:
                return {}, _knowledge_guidance_outcome(
                    status="low_confidence", semantics=semantics,
                    semantic_status="low_confidence", semantic_reason="low_confidence",
                    response_mode="unknown",
                )
            if semantics.subject == "unknown":
                return {}, _knowledge_guidance_outcome(
                    status="not_matched", semantics=semantics,
                    semantic_status="available",
                )
            semantic_query = " ".join(
                part
                for part in (
                    semantics.entity,
                    semantics.content_type,
                    semantics.intent,
                    *semantics.retrieval_concepts,
                )
                if part
            )
            matches = match_topics(
                topic_items,
                query=semantic_query,
                subject=semantics.subject,
                limit=5,
            )
            if not matches or int(matches[0].get("score") or 0) < 10:
                return {}, _knowledge_guidance_outcome(
                    status="not_matched", semantics=semantics,
                    semantic_status="available",
                )
            subject_topics = [
                topic
                for topic in topic_items
                if str(topic.get("subject") or "").strip().lower()
                == semantics.subject
            ]
            guidance = build_knowledge_guidance_payload(
                topics=subject_topics,
                topic_id=str(matches[0].get("id") or ""),
                query=semantic_query,
                response_mode=semantics.response_mode,
                max_depth=3,
                match_limit=5,
            )
            if not guidance.get("summary", {}).get("matched"):
                return {}, _knowledge_guidance_outcome(
                    status="not_matched", semantics=semantics,
                    semantic_status="available",
                )
            return guidance, _knowledge_guidance_outcome(
                status="applied", semantics=semantics, guidance=guidance,
                semantic_status="available",
            )
        except Exception as exc:
            _warn(
                getattr(self, "logger", None),
                "study knowledge graph guidance failed: {}",
                exc,
            )
            return {}, _knowledge_guidance_outcome(
                status="routing_unavailable",
                semantic_status="routing_unavailable",
                semantic_reason="call_failed",
            )

    async def _route_study_input_semantics(
        self, input_text: str, *, context: dict[str, Any]
    ) -> tuple[StudyInputSemantics | None, str, str]:
        agent = getattr(self, "_agent", None)
        call_model = getattr(agent, "_call_model", None)
        if not callable(call_model):
            return None, "routing_unavailable", "model_unavailable"
        messages: list[dict[str, Any]] = build_semantic_routing_messages(
            text=input_text,
            language=self._cfg.language,
            has_images=bool(str(context.get("vision_image_base64") or "").strip()),
        )
        image = str(context.get("vision_image_base64") or "").strip()
        if image:
            attach_image = getattr(agent, "_attach_vision_image", None)
            if not callable(attach_image):
                return None, "routing_unavailable", "model_unavailable"
            messages = attach_image(messages, image)
        request_deadline = context.get("deadline_monotonic")
        parsed_request_deadline = 0.0
        if not isinstance(request_deadline, bool):
            try:
                parsed_request_deadline = float(request_deadline)
            except (TypeError, ValueError):
                pass
        if (
            parsed_request_deadline > 0
            and parsed_request_deadline <= time.monotonic()
        ):
            return None, "routing_unavailable", "timeout"
        quota_reservation = None
        if not image:
            reserve_optional = getattr(agent, "reserve_optional_agent_call", None)
            if callable(reserve_optional):
                try:
                    optional_allowed, quota_reservation = await reserve_optional(
                        _SEMANTIC_ROUTE_OPERATION
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return None, "routing_unavailable", "quota_reservation_failed"
                if quota_reservation is not None:
                    context["_agent_quota_reservation"] = quota_reservation
                if not optional_allowed:
                    return None, "routing_unavailable", "primary_quota_reserved"
        new_deadline = getattr(agent, "_new_operation_deadline", None)
        route_deadline = time.monotonic() + _SEMANTIC_ROUTE_TIMEOUT_SECONDS
        deadline = (
            min(new_deadline(_SEMANTIC_ROUTE_OPERATION, messages), route_deadline)
            if callable(new_deadline)
            else route_deadline
        )
        if parsed_request_deadline > 0:
            deadline = min(deadline, parsed_request_deadline)
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            return None, "routing_unavailable", "timeout"
        try:
            call_kwargs: dict[str, Any] = {
                "operation": _SEMANTIC_ROUTE_OPERATION,
                "deadline": deadline,
            }
            if quota_reservation is not None:
                call_kwargs["quota_reservation"] = quota_reservation
            raw = await asyncio.wait_for(
                call_model(messages, **call_kwargs),
                timeout=remaining_seconds,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return None, "routing_unavailable", "timeout"
        except Exception:
            return None, "routing_unavailable", "call_failed"
        semantics = parse_study_input_semantics(raw)
        if semantics is None:
            return None, "routing_unavailable", "invalid_response"
        return semantics, "available", ""

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
        guidance, guidance_outcome = await self._build_knowledge_guidance_context(
            operation,
            input_text=input_text,
            context=context,
        )
        context.update(guidance_outcome)
        trusted_question = str(
            (context.get("current_question") or {}).get("question")
            if isinstance(context.get("current_question"), dict)
            else ""
        ).strip()
        source_text = str(context.get("source_text") or input_text or "").strip()
        if (
            operation == LLM_OPERATION_CONCEPT_EXPLAIN
            and context.get("study_response_mode") == "unknown"
            and trusted_question
            and source_text == trusted_question
        ):
            context["study_response_mode"] = "problem_solving"
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
        finalize_progress: _TutorFinalizeProgress | None = None,
    ) -> dict[str, Any]:
        diagnostic = str(reply.diagnostic or "")
        if reply.degraded:
            async with _plugin_lock(self._lock):
                if diagnostic:
                    self._state.last_error = diagnostic
            if public_payload is not None:
                payload = {
                    "operation": reply.operation,
                    "input_text": reply.input_text,
                    "reply": reply.reply,
                    "degraded": True,
                    "diagnostic": reply.diagnostic,
                    "created_at": reply.created_at or utc_now_iso(),
                    **public_payload,
                }
                payload.setdefault("summary", reply.reply)
                return payload
            return build_tutor_payload(reply)

        progress = finalize_progress or _TutorFinalizeProgress()
        await _append_interaction_cancel_safe(
            self._store,
            progress=progress,
            kind=history_kind,
            input_text=reply.input_text,
            output_text=reply.reply,
            metadata=metadata,
            history_limit=self._cfg.history_limit,
        )
        await self._record_tutor_result(operation, reply, extra=extra_context)
        tracking_enrichment: dict[str, Any] = {}
        if operation != LLM_OPERATION_SUMMARIZE_SESSION:
            tracking_enrichment = await self._track_learning(
                operation,
                reply,
                extra_context=extra_context,
                public_payload=public_payload,
            )
        await _await_completion_on_cancel(
            self._persist_state(), logger=getattr(self, "logger", None)
        )
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
        for key in (
            "knowledge_guidance_applied",
            "knowledge_guidance_status",
            "knowledge_guidance_subject",
            "knowledge_guidance_content_type",
            "knowledge_guidance_entity",
            "knowledge_guidance_focus_topic",
            "knowledge_guidance_related_topics",
            "knowledge_guidance_source",
            "study_semantic_status",
            "study_semantic_reason",
            "study_response_mode",
            "study_semantic_subject",
            "study_semantic_content_type",
            "study_semantic_intent",
            "study_semantic_entity",
        ):
            if isinstance(extra_context, dict) and key in extra_context:
                payload[key] = extra_context[key]
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
        if operation != LLM_OPERATION_ANSWER_EVALUATE:
            return {}
        eval_payload = dict(public_payload or reply.payload or {})
        context = dict(extra_context or {})
        question_payload = dict(
            context.get("question_payload") or context.get("current_question") or {}
        )
        related_topics = eval_payload.get("related_topics")
        first_related_topic = (
            str(related_topics[0] or "").strip()
            if isinstance(related_topics, list) and related_topics
            else ""
        )
        topic = str(
            context.get("selected_topic_id")
            or question_payload.get("selected_topic_id")
            or question_payload.get("topic_id")
            or question_payload.get("topic")
            or eval_payload.get("topic")
            or first_related_topic
            or context.get("question")
            or "general"
        ).strip()[:160]
        verdict = str(eval_payload.get("verdict") or "").strip().lower()
        score = float(eval_payload.get("score") or 0.0)
        mastery_delta = (
            0.08
            if verdict == "correct"
            else (-0.08 if verdict in {"wrong", "dont_know"} else 0.02)
        )
        weak_points = [
            str(item).strip()
            for key in ("missing_points", "misconceptions")
            for item in (eval_payload.get(key) or [])
            if str(item).strip()
        ]
        error_type = str(eval_payload.get("error_type") or "").strip()
        if error_type and error_type != "none" and error_type not in weak_points:
            weak_points.append(error_type)
        track_reply = TutorReply(
            operation=LLM_OPERATION_KNOWLEDGE_TRACK,
            input_text=reply.input_text,
            reply=topic,
            payload={
                "topic": topic,
                "mastery_delta": mastery_delta,
                "confidence": max(0.0, min(1.0, score / 100.0)),
                "weak_points": weak_points[:6],
                "next_steps": [str(eval_payload.get("next_action") or "").strip()]
                if str(eval_payload.get("next_action") or "").strip()
                else [],
                "screen_type": str(
                    eval_payload.get("screen_type")
                    or self._screen_classification_context().get("screen_type")
                    or ""
                ),
            },
            created_at=reply.created_at or utc_now_iso(),
        )
        return await self._record_answer_knowledge(
            reply, track_reply, extra_context=extra_context
        )

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
        self._invalidate_knowledge_guidance_cache()
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
