from __future__ import annotations

from .tutor_llm_agent_common import (
    Any,
    Callable,
    asyncio,
    hashlib,
    inspect,
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
    _config_manager_module,
    _llm_client_module,
    _token_tracker_module,
    _LLM_CALL_TIMEOUT_GRACE_SECONDS,
    _as_str,
    _as_dict,
    diagnostic_code_for_exception,
)
from .tutor_llm_agent_json_corrector import _JSONCorrector


class _LLMClientCache:
    def __init__(self, *, logger: Any) -> None:
        self._logger = logger
        self._cache: dict[tuple[Any, ...], Any] = {}
        self._locks: dict[tuple[Any, ...], asyncio.Lock] = {}

    def get(self, key: tuple[Any, ...]) -> Any | None:
        return self._cache.get(key)

    async def get_or_create(
        self,
        key: tuple[Any, ...],
        factory: Callable[[], Any],
    ) -> Any:
        llm = self._cache.get(key)
        if llm is not None:
            return llm
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            llm = self._cache.get(key)
            if llm is None:
                llm = factory()
                self._cache[key] = llm
        return self._cache[key]

    def close_all(self) -> None:
        clients = list(self._cache.values())
        self._cache.clear()
        self._locks.clear()
        for llm in clients:
            self._close_cached_llm(llm)

    async def close_all_async(self) -> None:
        clients = list(self._cache.values())
        self._cache.clear()
        self._locks.clear()
        for llm in clients:
            await self._close_cached_llm_async(llm)

    def _close_cached_llm(self, llm: Any) -> None:
        found_close = False
        for method_name in ("shutdown", "aclose"):
            close = getattr(llm, method_name, None)
            if not callable(close):
                continue
            found_close = True
            try:
                result = close()
            except Exception as exc:
                self._logger.warning(
                    "study tutor llm close via {} failed: {}", method_name, exc
                )
                continue
            if inspect.isawaitable(result):
                self._finalize_async_close(result, method_name=method_name)
            return
        if not found_close:
            self._logger.warning(
                "study tutor llm has no shutdown or aclose method: {}",
                type(llm).__name__,
            )

    async def _close_cached_llm_async(self, llm: Any) -> None:
        found_close = False
        for method_name in ("shutdown", "aclose"):
            close = getattr(llm, method_name, None)
            if not callable(close):
                continue
            found_close = True
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning(
                    "study tutor llm async close via {} failed: {}", method_name, exc
                )
                continue
            return
        if not found_close:
            self._logger.warning(
                "study tutor llm has no shutdown or aclose method: {}",
                type(llm).__name__,
            )

    def _finalize_async_close(self, close_result: Any, *, method_name: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(close_result)
            except Exception as exc:
                self._logger.warning(
                    "study tutor llm async close via {} failed without running loop: {}",
                    method_name,
                    exc,
                )
            return
        try:
            task = loop.create_task(close_result)
        except Exception as exc:
            self._logger.warning(
                "study tutor llm async close via {} could not be scheduled: {}",
                method_name,
                exc,
            )
            return
        task.add_done_callback(self._consume_close_exception)

    def _consume_close_exception(self, task: asyncio.Task[Any]) -> None:
        try:
            exc = task.exception()
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            return
        if exc is not None:
            self._logger.warning("study tutor llm close task failed: {}", exc)


class TutorLLMAgent:
    def __init__(self, *, logger: Any, config: StudyConfig) -> None:
        self._logger = logger
        self._config = config
        self._client_cache = _LLMClientCache(logger=logger)
        self._json_corrector = _JSONCorrector(logger=logger)

    def update_config(self, config: StudyConfig) -> None:
        self._client_cache.close_all()
        self._config = config

    async def shutdown(self) -> None:
        await self._client_cache.close_all_async()

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
            raw_text = await self._json_corrector.invoke_with_correction(
                operation=operation,
                messages=messages,
                call_model=self._call_model,
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
    def _model_supports_vision(model: str) -> bool:
        normalized = str(model or "").strip().lower()
        if not normalized:
            return False
        if normalized.startswith("glm-") and re.search(
            r"(?:^|[-_.])\d+(?:\.\d+)?v(?:[-_.]|$)",
            normalized,
        ):
            return True
        return any(
            marker in normalized
            for marker in (
                "gpt-4o",
                "gpt-4.1",
                "gpt-4.5",
                "gpt-5",
                "vision",
                "vl",
                "qwen2.5-vl",
                "qwen-vl",
                "gemini",
                "claude-3",
                "claude-4",
            )
        )

    @staticmethod
    def _message_has_image_content(message: dict[str, Any]) -> bool:
        content = message.get("content")
        if not isinstance(content, list):
            return False
        return any(
            isinstance(block, dict) and block.get("type") == "image_url"
            for block in content
        )

    @staticmethod
    def _strip_image_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                result.append(dict(message))
                continue
            text_parts = [
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            next_message = dict(message)
            next_message["content"] = "\n".join(part for part in text_parts if part)
            result.append(next_message)
        return result

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
    ) -> str:
        get_config_manager = getattr(_config_manager_module, "get_config_manager", None)
        create_chat_llm = getattr(_llm_client_module, "create_chat_llm", None)
        set_call_type = getattr(_token_tracker_module, "set_call_type", None)
        missing_runtime_deps = [
            name
            for name, dep in (
                ("utils.config_manager.get_config_manager", get_config_manager),
                ("utils.llm_client.create_chat_llm", create_chat_llm),
                ("utils.token_tracker.set_call_type", set_call_type),
            )
            if not callable(dep)
        ]
        if missing_runtime_deps:
            details = ", ".join(missing_runtime_deps)
            raise SdkError(f"missing runtime dependency: {details}")

        config_manager = get_config_manager()
        has_image = any(
            self._message_has_image_content(message) for message in messages
        )
        if has_image:
            vision_config = config_manager.get_model_api_config("vision")
            vision_base_url = str(vision_config.get("base_url") or "").strip()
            vision_model = str(vision_config.get("model") or "").strip()
            if vision_base_url and vision_model:
                api_config = vision_config
                model_group = "vision"
            else:
                api_config = config_manager.get_model_api_config("agent")
                model_group = "agent"
        else:
            api_config = config_manager.get_model_api_config("agent")
            model_group = "agent"
        base_url = str(api_config.get("base_url") or "").strip()
        model = str(api_config.get("model") or "").strip()
        api_key = str(api_config.get("api_key") or "").strip()
        if not base_url or not model:
            raise SdkError(f"missing configured {model_group} model")
        if (
            has_image
            and model_group != "vision"
            and not self._model_supports_vision(model)
        ):
            self._logger.warning(
                "vision stripped: model {} not in vision allowlist", model
            )
            messages = self._strip_image_content(messages)
        key = (
            model_group,
            operation,
            base_url,
            model,
            self._api_key_cache_fingerprint(api_key),
        )
        timeout_seconds = (
            float(self._config.llm_call_timeout_seconds)
            + _LLM_CALL_TIMEOUT_GRACE_SECONDS
        )
        llm = await self._client_cache.get_or_create(
            key,
            lambda: create_chat_llm(
                model=model,
                base_url=base_url,
                api_key=api_key,
                timeout=timeout_seconds,
            ),
        )
        if llm is None:
            raise SdkError("failed to initialize agent model")
        set_call_type(model_group)
        ainvoke = getattr(llm, "ainvoke", None)
        if callable(ainvoke):
            response = await asyncio.wait_for(
                ainvoke(messages), timeout=timeout_seconds
            )
        else:
            response = await asyncio.wait_for(
                asyncio.to_thread(llm.invoke, messages), timeout=timeout_seconds
            )
        return str(getattr(response, "content", "") or response)

    @staticmethod
    def _api_key_cache_fingerprint(api_key: str) -> tuple[str, str]:
        if not api_key:
            return ("empty", "")
        digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        return ("sha256", digest)


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
