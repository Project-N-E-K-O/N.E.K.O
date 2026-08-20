from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from .tutor_llm_agent_common import (
    Any,
    asyncio,
    re,
    STUDY_EMPTY_INPUT_DEFAULT,
    STUDY_FALLBACK_EXPLANATION_DEFAULT,
    STUDY_FALLBACK_FEEDBACK,
    STUDY_FALLBACK_NEXT_ACTION,
    STUDY_MARKDOWN_SECTION_EMPTY_ITEM,
    SdkError,
    LLM_OPERATION_ANSWER_EVALUATE,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    LLM_OPERATION_KNOWLEDGE_TRACK,
    LLM_OPERATION_QUESTION_GENERATE,
    LLM_OPERATION_SUMMARIZE_SESSION,
    build_operation_messages,
    study_i18n_t,
    StudyConfig,
    TutorReply,
    utc_now_iso,
    _as_str,
    _as_dict,
    diagnostic_code_for_exception,
)
from .qwen_native_client import (
    QwenNativeResult,
    messages_have_image,
    new_operation_deadline,
)
from .study_model_gateway import (
    AgentQuotaReservation,
    StudyModelGateway,
    StudyModelResult,
    StudyModelRuntimeSnapshot,
)
from .tutor_llm_agent_json_corrector import _JSONCorrector


_bound_model_runtime: ContextVar[StudyModelRuntimeSnapshot | None] = ContextVar(
    "study_companion_model_runtime", default=None
)


class TutorLLMAgent:
    def __init__(self, *, logger: Any, config: StudyConfig) -> None:
        self._logger = logger
        self._config = config
        self._model_gateway = StudyModelGateway(logger=logger)
        # Compatibility seam for focused legacy tests and private embedders.
        self._qwen_client = self._model_gateway.native_client
        self._json_corrector = _JSONCorrector(logger=logger)

    def update_config(self, config: StudyConfig) -> None:
        self._config = config

    async def shutdown(self) -> None:
        return None

    async def resolve_model_runtime(
        self, model_group: str = "agent"
    ) -> StudyModelRuntimeSnapshot:
        return await self._model_gateway.resolve_runtime(model_group)

    async def describe_model_runtimes(self) -> dict[str, dict[str, object]]:
        return await self._model_gateway.describe_runtimes()

    async def reserve_optional_agent_call(
        self, operation: str
    ) -> tuple[bool, AgentQuotaReservation | None]:
        return await self._model_gateway.reserve_optional_agent_call(operation)

    @contextmanager
    def bind_model_runtime(self, runtime: StudyModelRuntimeSnapshot):
        """Keep a long-running operation on one immutable host-model snapshot."""
        token = _bound_model_runtime.set(runtime)
        try:
            yield runtime
        finally:
            _bound_model_runtime.reset(token)

    def _localize_reply(self, language: str | None, key: str, **values: Any) -> str:
        if key == "empty_input":
            return study_i18n_t(
                language,
                "reply.empty_input",
                default=str(values.get("default") or STUDY_EMPTY_INPUT_DEFAULT),
            )
        if key == "fallback_explanation":
            first_line = str(values.get("first_line") or "").strip()
            return study_i18n_t(
                language,
                "reply.fallback_explanation",
                default=str(
                    values.get("default") or STUDY_FALLBACK_EXPLANATION_DEFAULT
                ),
                first_line=first_line,
            )
        return str(values.get("default") or "")

    async def _invoke_structured_operation(
        self, operation: str, context: dict[str, Any]
    ) -> TutorReply:
        try:
            messages = build_operation_messages(operation, context)
            vision_image_base64 = str(context.get("vision_image_base64") or "")
            if vision_image_base64:
                messages = self._attach_vision_image(messages, vision_image_base64)
            deadline = self._new_operation_deadline(operation, messages)
            raw_text = await self._json_corrector.invoke_with_correction(
                operation=operation,
                messages=messages,
                call_model=self._call_model,
                deadline=deadline,
            )
            parsed = self._json_corrector.parse_json_object(raw_text)
            payload = self._normalize_result(operation, parsed, context)
            return TutorReply(
                operation=operation,
                input_text=self._input_text_for_operation(operation, context),
                reply=self._reply_from_payload(operation, payload),
                payload=payload,
                degraded=False,
                created_at=utc_now_iso(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.warning("study {} degraded: {}", operation, exc)
            return self._fallback_structured_reply(
                operation, context, diagnostic=diagnostic_code_for_exception(exc)
            )

    def _normalize_result(
        self, operation: str, raw: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        if operation == LLM_OPERATION_QUESTION_GENERATE:
            return self._normalize_question(raw, context)
        if operation == LLM_OPERATION_ANSWER_EVALUATE:
            return self._normalize_evaluation(raw, context)
        if operation == LLM_OPERATION_KNOWLEDGE_TRACK:
            return self._normalize_track(raw, context)
        if operation == LLM_OPERATION_SUMMARIZE_SESSION:
            return self._normalize_summary(raw, context)
        reply = _as_str(raw.get("reply")).strip()
        if not reply:
            raise SdkError("missing reply")
        return {"reply": reply}

    def _fallback_structured_reply(
        self, operation: str, context: dict[str, Any], *, diagnostic: str
    ) -> TutorReply:
        if operation == LLM_OPERATION_QUESTION_GENERATE:
            payload = self._fallback_question(context)
        elif operation == LLM_OPERATION_ANSWER_EVALUATE:
            payload = self._fallback_evaluation(context)
        elif operation == LLM_OPERATION_KNOWLEDGE_TRACK:
            payload = self._fallback_track(context)
        elif operation == LLM_OPERATION_SUMMARIZE_SESSION:
            payload = self._fallback_summary(context)
        else:
            payload = {
                "reply": self._localize_reply(self._config.language, "empty_input")
            }
        return TutorReply(
            operation=operation,
            input_text=self._input_text_for_operation(operation, context),
            reply=self._reply_from_payload(operation, payload),
            payload=payload,
            degraded=True,
            diagnostic=diagnostic,
            created_at=utc_now_iso(),
        )

    @staticmethod
    def _heuristic_verdict(answer: str, expected: str) -> tuple[str, int, str]:
        normalized_answer = re.sub(r"\s+", " ", answer.strip().lower())
        normalized_expected = re.sub(r"\s+", " ", expected.strip().lower())
        if not normalized_expected:
            return ("partial", 50, "needs_reference")
        if normalized_expected and normalized_expected in normalized_answer:
            return ("correct", 90, "none")
        expected_tokens = {
            token for token in re.split(r"\W+", normalized_expected) if len(token) > 2
        }
        answer_tokens = {
            token for token in re.split(r"\W+", normalized_answer) if len(token) > 2
        }
        if expected_tokens:
            overlap = len(expected_tokens & answer_tokens) / max(
                1, len(expected_tokens)
            )
            if overlap >= 0.65:
                return ("correct", 82, "none")
            if overlap >= 0.3:
                return ("partial", 55, "incomplete")
        return ("wrong", 20, "misconception")

    @staticmethod
    def _verdict_from_score(score: int, *, answer: str) -> str:
        if not answer:
            return "dont_know"
        if score >= 80:
            return "correct"
        if score >= 40:
            return "partial"
        return "wrong"

    @staticmethod
    def _fallback_feedback(verdict: str, context: dict[str, Any]) -> str:
        return STUDY_FALLBACK_FEEDBACK.get(verdict, STUDY_FALLBACK_FEEDBACK["wrong"])

    @staticmethod
    def _fallback_next_action(verdict: str) -> str:
        return STUDY_FALLBACK_NEXT_ACTION.get(
            verdict, STUDY_FALLBACK_NEXT_ACTION["wrong"]
        )

    @staticmethod
    def _markdown_from_summary(
        summary: str,
        highlights: list[str],
        weak_points: list[str],
        next_actions: list[str],
    ) -> str:
        def _section(title: str, items: list[str]) -> str:
            if not items:
                return f"## {title}\n\n- {STUDY_MARKDOWN_SECTION_EMPTY_ITEM}"
            return f"## {title}\n\n" + "\n".join(f"- {item}" for item in items)

        return "\n\n".join(
            [
                "## Summary\n\n" + summary,
                _section("Highlights", highlights),
                _section("Weak Points", weak_points),
                _section("Next Actions", next_actions),
            ]
        )

    @staticmethod
    def _reply_from_payload(operation: str, payload: dict[str, Any]) -> str:
        if operation == LLM_OPERATION_QUESTION_GENERATE:
            return _as_str(payload.get("question")).strip()
        if operation == LLM_OPERATION_ANSWER_EVALUATE:
            return _as_str(payload.get("feedback")).strip()
        if operation == LLM_OPERATION_KNOWLEDGE_TRACK:
            return _as_str(payload.get("topic")).strip() or "knowledge updated"
        if operation == LLM_OPERATION_SUMMARIZE_SESSION:
            return (
                _as_str(payload.get("markdown")).strip()
                or _as_str(payload.get("summary")).strip()
            )
        return _as_str(payload.get("reply")).strip()

    @staticmethod
    def _input_text_for_operation(operation: str, context: dict[str, Any]) -> str:
        if operation == LLM_OPERATION_ANSWER_EVALUATE:
            return _as_str(context.get("answer")).strip()
        if operation == LLM_OPERATION_SUMMARIZE_SESSION:
            return "session"
        return _as_str(
            context.get("source_text") or context.get("text") or context.get("question")
        ).strip()

    @staticmethod
    def _screen_type_from_context(context: dict[str, Any]) -> str:
        screen = _as_dict(context.get("screen_classification"))
        return (
            _as_str(screen.get("screen_type")).strip()
            or _as_str(context.get("screen_type")).strip()
        )

    @staticmethod
    def _guess_topic(context: dict[str, Any]) -> str:
        question = _as_dict(
            context.get("current_question") or context.get("question_payload")
        )
        topic = _as_str(question.get("topic")).strip()
        if topic:
            return topic
        text = _as_str(
            context.get("source_text") or context.get("text") or context.get("question")
        ).strip()
        first_line = next(
            (line.strip() for line in text.splitlines() if line.strip()), ""
        )
        if not first_line:
            return "general"
        return first_line[:48]

    @staticmethod
    def _attach_vision_image(
        messages: list[dict[str, Any]],
        image_base64: str,
        *,
        detail: str = "auto",
    ) -> list[dict[str, Any]]:
        if not image_base64:
            return messages
        if image_base64.lower().startswith("data:"):
            if not image_base64.lower().startswith(
                ("data:image/jpeg;base64,", "data:image/png;base64,")
            ):
                return messages
            image_url = image_base64
        else:
            image_url = f"data:image/jpeg;base64,{image_base64}"
        if detail not in {"low", "high", "auto"}:
            detail = "auto"
        result = [dict(message) for message in messages]
        for index in range(len(result) - 1, -1, -1):
            if str(result[index].get("role") or "") != "user":
                continue
            content = result[index].get("content")
            if isinstance(content, list):
                text = "\n".join(
                    str(block.get("text") or "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            else:
                text = str(content or "")
            result[index]["content"] = [
                {"type": "text", "text": text},
                {
                    "type": "image_url",
                    "image_url": {"url": image_url, "detail": detail},
                },
            ]
            return result
        return result

    async def _call_model(
        self,
        messages: list[dict[str, Any]],
        *,
        operation: str = LLM_OPERATION_CONCEPT_EXPLAIN,
        deadline: float | None = None,
        quota_reservation: AgentQuotaReservation | None = None,
    ) -> str:
        call_kwargs: dict[str, Any] = {
            "operation": operation,
            "deadline": deadline,
        }
        if quota_reservation is not None:
            call_kwargs["quota_reservation"] = quota_reservation
        result = await self._call_model_result(messages, **call_kwargs)
        return result.text

    async def _call_model_result(
        self,
        messages: list[dict[str, Any]],
        *,
        operation: str = LLM_OPERATION_CONCEPT_EXPLAIN,
        deadline: float | None = None,
        runtime: StudyModelRuntimeSnapshot | None = None,
        quota_reservation: AgentQuotaReservation | None = None,
    ) -> StudyModelResult | QwenNativeResult:
        effective_deadline = deadline or self._new_operation_deadline(
            operation, messages
        )
        # Preserve instance-level replacement used by older integrations without
        # making the production router depend on a Qwen-specific client.
        if self._qwen_client is not self._model_gateway.native_client:
            return await self._qwen_client.call(
                messages,
                operation=operation,
                deadline=effective_deadline,
            )
        runtime = runtime or _bound_model_runtime.get()
        call_kwargs: dict[str, Any] = {
            "operation": operation,
            "deadline": effective_deadline,
            "runtime": runtime,
        }
        if quota_reservation is not None:
            call_kwargs["quota_reservation"] = quota_reservation
        return await self._model_gateway.call(messages, **call_kwargs)

    def _new_operation_deadline(
        self, operation: str, messages: list[dict[str, Any]]
    ) -> float:
        return new_operation_deadline(
            operation,
            has_image=messages_have_image(messages),
            configured_timeout_seconds=self._config.llm_call_timeout_seconds,
        )


from .tutor_llm_agent_concept_explain import concept_explain
from .tutor_llm_agent_question_generate import (
    _fallback_question,
    _normalize_question,
    question_generate,
)
from .tutor_llm_agent_answer_evaluate import (
    _fallback_evaluation,
    _normalize_evaluation,
    answer_evaluate,
)
from .tutor_llm_agent_knowledge_track import (
    _fallback_track,
    _normalize_track,
    knowledge_track,
)
from .tutor_llm_agent_summarize_session import (
    _fallback_summary,
    _normalize_summary,
    summarize_session,
)

TutorLLMAgent.concept_explain = concept_explain  # type: ignore[method-assign]
TutorLLMAgent.question_generate = question_generate  # type: ignore[method-assign]
TutorLLMAgent._normalize_question = _normalize_question  # type: ignore[method-assign]
TutorLLMAgent._fallback_question = _fallback_question  # type: ignore[method-assign]
TutorLLMAgent.answer_evaluate = answer_evaluate  # type: ignore[method-assign]
TutorLLMAgent._normalize_evaluation = _normalize_evaluation  # type: ignore[method-assign]
TutorLLMAgent._fallback_evaluation = _fallback_evaluation  # type: ignore[method-assign]
TutorLLMAgent.knowledge_track = knowledge_track  # type: ignore[method-assign]
TutorLLMAgent._normalize_track = _normalize_track  # type: ignore[method-assign]
TutorLLMAgent._fallback_track = _fallback_track  # type: ignore[method-assign]
TutorLLMAgent.summarize_session = summarize_session  # type: ignore[method-assign]
TutorLLMAgent._normalize_summary = _normalize_summary  # type: ignore[method-assign]
TutorLLMAgent._fallback_summary = _fallback_summary  # type: ignore[method-assign]

from .tutor_llm_agent_notebook import expand_note, summarize_to_note

TutorLLMAgent.expand_note = expand_note  # type: ignore[method-assign]
TutorLLMAgent.summarize_to_note = summarize_to_note  # type: ignore[method-assign]

from .tutor_llm_agent_document import (
    analyze_document_chunk,
    build_document_merge_messages,
    document_analyze,
    merge_document_chunks,
)

TutorLLMAgent.document_analyze = document_analyze  # type: ignore[attr-defined]
TutorLLMAgent.analyze_document_chunk = analyze_document_chunk  # type: ignore[attr-defined]
TutorLLMAgent.merge_document_chunks = merge_document_chunks  # type: ignore[attr-defined]
TutorLLMAgent.build_document_merge_messages = staticmethod(build_document_merge_messages)  # type: ignore[attr-defined]
