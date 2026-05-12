from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
from typing import Any, Awaitable, Callable

from plugin.sdk.plugin import SdkError

from .constants import (
    LLM_OPERATION_ANSWER_EVALUATE,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    LLM_OPERATION_KNOWLEDGE_TRACK,
    LLM_OPERATION_QUESTION_GENERATE,
    LLM_OPERATION_SUMMARIZE_SESSION,
    MODE_COMPANION,
    MODE_TEACHING,
)
from .llm_prompts import build_concept_explain_messages, build_operation_messages
from .mode_manager import build_transition_phrase, normalize_mode, study_i18n_t
from .models import MODE_CONCEPT_EXPLAIN, StudyConfig, TutorReply, utc_now_iso

try:
    from utils.file_utils import robust_json_loads
except Exception:  # pragma: no cover - utility is present in the host app.
    robust_json_loads = None  # type: ignore[assignment]

try:
    import utils.config_manager as _config_manager_module
except Exception as exc:  # pragma: no cover - guarded runtime dependency.
    _config_manager_module = None  # type: ignore[assignment]
    _CONFIG_MANAGER_IMPORT_ERROR = exc
else:
    _CONFIG_MANAGER_IMPORT_ERROR = None

try:
    import utils.llm_client as _llm_client_module
except Exception as exc:  # pragma: no cover - guarded runtime dependency.
    _llm_client_module = None  # type: ignore[assignment]
    _LLM_CLIENT_IMPORT_ERROR = exc
else:
    _LLM_CLIENT_IMPORT_ERROR = None

try:
    import utils.token_tracker as _token_tracker_module
except Exception as exc:  # pragma: no cover - guarded runtime dependency.
    _token_tracker_module = None  # type: ignore[assignment]
    _TOKEN_TRACKER_IMPORT_ERROR = exc
else:
    _TOKEN_TRACKER_IMPORT_ERROR = None


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.DOTALL)
_JSON_CORRECTION_MAX_ATTEMPTS = 1
_JSON_CORRECTION_BAD_OUTPUT_MAX_CHARS = 12000
_JSON_CORRECTION_ERROR_MAX_CHARS = 600
_LLM_CALL_TIMEOUT_GRACE_SECONDS = 0.5
_ANSWER_VERDICTS = frozenset({"correct", "partial", "wrong", "dont_know"})


def _as_str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string_list(value: object, *, limit: int = 6) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        text = _as_str(item, str(item)).strip()
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _clamp_float(value: object, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(minimum, min(maximum, number))


def _clamp_int(value: object, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(minimum, min(maximum, number))


def _strip_code_fences(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        return _CODE_FENCE_RE.sub("", text).strip()
    return text


def _bounded_prompt_text(value: object, *, max_chars: int) -> str:
    text = _as_str(value, str(value))
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars]}\n...[truncated {omitted} chars]"


def diagnostic_code_for_exception(exc: BaseException) -> str:
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    if isinstance(exc, asyncio.TimeoutError) or "timeout" in name or "timeout" in message:
        return "timeout"
    if isinstance(exc, SdkError) and (
        "missing configured" in message
        or "failed to initialize" in message
        or "missing runtime dependency" in message
    ):
        return "model_unavailable"
    if "auth" in name or "connection" in name or "unavailable" in name:
        return "model_unavailable"
    return "llm_call_failed"


class _JSONCorrector:
    def __init__(self, *, logger: Any) -> None:
        self._logger = logger

    def parse_json_object(self, raw_text: str) -> dict[str, Any]:
        text = _strip_code_fences(str(raw_text or ""))
        try:
            parsed = robust_json_loads(text) if callable(robust_json_loads) else None
            if parsed is None:
                import json

                parsed = json.loads(text)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                raise SdkError("llm result is not valid json object")
            try:
                parsed = robust_json_loads(match.group(0)) if callable(robust_json_loads) else None
                if parsed is None:
                    import json

                    parsed = json.loads(match.group(0))
            except Exception as exc:
                raise SdkError(f"llm result is not valid json object: {exc}") from exc
        if not isinstance(parsed, dict):
            raise SdkError("llm result must be a json object")
        return dict(parsed)

    async def invoke_with_correction(
        self,
        *,
        operation: str,
        messages: list[dict[str, str]],
        call_model: Callable[..., Awaitable[str]],
    ) -> str:
        raw_text = await call_model(messages, operation=operation)
        last_error: Exception | None = None
        for attempt in range(_JSON_CORRECTION_MAX_ATTEMPTS + 1):
            try:
                self.parse_json_object(raw_text)
                return raw_text
            except SdkError as exc:
                last_error = exc
                if attempt >= _JSON_CORRECTION_MAX_ATTEMPTS:
                    break
            correction_messages = self._build_json_correction_messages(
                operation=operation,
                messages=messages,
                bad_output=raw_text,
                parse_error=last_error,
                attempt=attempt + 1,
                max_attempts=_JSON_CORRECTION_MAX_ATTEMPTS,
            )
            raw_text = await call_model(correction_messages, operation=operation)
        raise SdkError(f"llm result is not valid json object after correction: {last_error}")

    def _build_json_correction_messages(
        self,
        *,
        operation: str,
        messages: list[dict[str, str]],
        bad_output: object,
        parse_error: object,
        attempt: int,
        max_attempts: int,
    ) -> list[dict[str, str]]:
        correction_messages = list(messages)
        correction_messages.append(
            {
                "role": "assistant",
                "content": _bounded_prompt_text(bad_output, max_chars=_JSON_CORRECTION_BAD_OUTPUT_MAX_CHARS),
            }
        )
        correction_messages.append(
            {
                "role": "user",
                "content": (
                    f"JSON correction request {attempt}/{max_attempts}, operation={operation}.\n"
                    f"Parse error: {_bounded_prompt_text(parse_error, max_chars=_JSON_CORRECTION_ERROR_MAX_CHARS)}\n"
                    "Your last response was not a valid JSON object. "
                    "Reply with ONLY one valid JSON object and no markdown."
                ),
            }
        )
        return correction_messages


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
        for method_name in ("shutdown", "aclose"):
            close = getattr(llm, method_name, None)
            if not callable(close):
                continue
            try:
                result = close()
            except Exception as exc:
                self._logger.warning("study tutor llm close via {} failed: {}", method_name, exc)
                continue
            if inspect.isawaitable(result):
                self._finalize_async_close(result, method_name=method_name)
            return
        self._logger.warning("study tutor llm has no shutdown or aclose method: {}", type(llm).__name__)

    async def _close_cached_llm_async(self, llm: Any) -> None:
        for method_name in ("shutdown", "aclose"):
            close = getattr(llm, method_name, None)
            if not callable(close):
                continue
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning("study tutor llm async close via {} failed: {}", method_name, exc)
                continue
            return
        self._logger.warning("study tutor llm has no shutdown or aclose method: {}", type(llm).__name__)

    def _finalize_async_close(self, close_result: Any, *, method_name: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(close_result)
            except Exception as exc:
                self._logger.warning("study tutor llm async close via {} failed without running loop: {}", method_name, exc)
            return
        try:
            task = loop.create_task(close_result)
        except Exception as exc:
            self._logger.warning("study tutor llm async close via {} could not be scheduled: {}", method_name, exc)
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
                default=str(values.get("default") or "Please provide text or capture a readable screen first."),
            )
        if key == "fallback_explanation":
            first_line = str(values.get("first_line") or "").strip()
            return study_i18n_t(
                language,
                "reply.fallback_explanation",
                default=str(values.get("default") or ""),
                first_line=first_line,
            )
        return str(values.get("default") or "")

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
            build_transition_phrase(MODE_TEACHING, language=self._config.language, outcome="changed")
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
                default=(
                    "Key text: {first_line}\n\n"
                    "Explanation: I could not reach the configured model, so this is a local fallback. "
                    "Read the statement once for definitions, then identify the cause, result, and any formula or term that changes the conclusion.\n\n"
                    "Check question: What is the main term or relationship you need to remember from this text?"
                ),
                first_line=next((line.strip() for line in normalized.splitlines() if line.strip()), normalized[:120]),
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

    async def question_generate(
        self,
        text: str,
        *,
        mode: str = MODE_COMPANION,
        context: dict[str, Any] | None = None,
    ) -> TutorReply:
        normalized = str(text or "").strip()
        operation_context = {
            **dict(context or {}),
            "text": normalized,
            "source_text": normalized,
            "language": self._config.language,
            "mode": normalize_mode(mode),
        }
        if not normalized:
            return self._fallback_structured_reply(
                LLM_OPERATION_QUESTION_GENERATE,
                operation_context,
                diagnostic="empty_input",
            )
        return await self._invoke_structured_operation(LLM_OPERATION_QUESTION_GENERATE, operation_context)

    async def answer_evaluate(
        self,
        question: str = "",
        answer: str = "",
        *,
        expected_answer: str = "",
        mode: str = MODE_COMPANION,
        context: dict[str, Any] | None = None,
    ) -> TutorReply:
        current_context = dict(context or {})
        operation_context = {
            **current_context,
            "question": str(question or current_context.get("question") or "").strip(),
            "answer": str(answer or "").strip(),
            "expected_answer": str(expected_answer or current_context.get("expected_answer") or "").strip(),
            "language": self._config.language,
            "mode": normalize_mode(mode),
        }
        return await self._invoke_structured_operation(LLM_OPERATION_ANSWER_EVALUATE, operation_context)

    async def knowledge_track(
        self,
        *,
        mode: str = MODE_COMPANION,
        context: dict[str, Any] | None = None,
    ) -> TutorReply:
        operation_context = {
            **dict(context or {}),
            "language": self._config.language,
            "mode": normalize_mode(mode),
        }
        return await self._invoke_structured_operation(LLM_OPERATION_KNOWLEDGE_TRACK, operation_context)

    async def summarize_session(
        self,
        history: list[dict[str, Any]] | None = None,
        *,
        mode: str = MODE_COMPANION,
        context: dict[str, Any] | None = None,
    ) -> TutorReply:
        operation_context = {
            **dict(context or {}),
            "history": list(history or []),
            "language": self._config.language,
            "mode": normalize_mode(mode),
        }
        return await self._invoke_structured_operation(LLM_OPERATION_SUMMARIZE_SESSION, operation_context)

    async def _invoke_structured_operation(self, operation: str, context: dict[str, Any]) -> TutorReply:
        try:
            messages = build_operation_messages(operation, context)
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
            return self._fallback_structured_reply(operation, context, diagnostic=diagnostic_code_for_exception(exc))

    def _normalize_result(self, operation: str, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
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

    def _normalize_question(self, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        question = _as_str(raw.get("question")).strip() or _as_str(raw.get("prompt")).strip()
        if not question:
            raise SdkError("missing question")
        topic = _as_str(raw.get("topic")).strip() or self._guess_topic(context)
        return {
            "question": question,
            "answer": _as_str(raw.get("answer")).strip() or _as_str(raw.get("reference_answer")).strip(),
            "hint": _as_str(raw.get("hint")).strip(),
            "difficulty": _clamp_int(raw.get("difficulty"), 1, 5, 3),
            "topic": topic,
            "screen_type": self._screen_type_from_context(context),
        }

    def _normalize_evaluation(self, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        score = _clamp_int(raw.get("score"), 0, 100, 0)
        verdict = _as_str(raw.get("verdict")).strip().lower()
        if verdict not in _ANSWER_VERDICTS:
            verdict = self._verdict_from_score(score, answer=_as_str(context.get("answer")).strip())
        feedback = _as_str(raw.get("feedback")).strip()
        if not feedback:
            feedback = self._fallback_feedback(verdict, context)
        error_type = _as_str(raw.get("error_type")).strip() or ("none" if verdict == "correct" else "unsupported")
        next_action = _as_str(raw.get("next_action")).strip() or self._fallback_next_action(verdict)
        return {
            "verdict": verdict,
            "score": score,
            "error_type": error_type,
            "feedback": feedback,
            "next_action": next_action,
            "screen_type": self._screen_type_from_context(context),
        }

    def _normalize_track(self, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        seed = _as_dict(raw.get("session_summary_seed"))
        if not seed:
            seed = _as_dict(context.get("session_summary_seed"))
        return {
            "topic": _as_str(raw.get("topic")).strip() or self._guess_topic(context),
            "mastery_delta": _clamp_float(raw.get("mastery_delta"), -1.0, 1.0, 0.0),
            "confidence": _clamp_float(raw.get("confidence"), 0.0, 1.0, 0.4),
            "weak_points": _string_list(raw.get("weak_points"), limit=6),
            "next_steps": _string_list(raw.get("next_steps"), limit=6),
            "session_summary_seed": seed,
            "screen_type": self._screen_type_from_context(context),
        }

    def _normalize_summary(self, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        summary = _as_str(raw.get("summary")).strip()
        markdown = _as_str(raw.get("markdown")).strip()
        if not summary and markdown:
            summary = next((line.strip("# ").strip() for line in markdown.splitlines() if line.strip()), "")
        if not summary:
            raise SdkError("missing summary")
        if not markdown:
            markdown = self._markdown_from_summary(
                summary,
                _string_list(raw.get("highlights")),
                _string_list(raw.get("weak_points")),
                _string_list(raw.get("next_actions")),
            )
        return {
            "summary": summary,
            "highlights": _string_list(raw.get("highlights")),
            "weak_points": _string_list(raw.get("weak_points")),
            "next_actions": _string_list(raw.get("next_actions")),
            "markdown": markdown,
            "screen_type": self._screen_type_from_context(context),
        }

    def _fallback_structured_reply(self, operation: str, context: dict[str, Any], *, diagnostic: str) -> TutorReply:
        if operation == LLM_OPERATION_QUESTION_GENERATE:
            payload = self._fallback_question(context)
        elif operation == LLM_OPERATION_ANSWER_EVALUATE:
            payload = self._fallback_evaluation(context)
        elif operation == LLM_OPERATION_KNOWLEDGE_TRACK:
            payload = self._fallback_track(context)
        elif operation == LLM_OPERATION_SUMMARIZE_SESSION:
            payload = self._fallback_summary(context)
        else:
            payload = {"reply": self._localize_reply(self._config.language, "empty_input")}
        return TutorReply(
            operation=operation,
            input_text=self._input_text_for_operation(operation, context),
            reply=self._reply_from_payload(operation, payload),
            payload=payload,
            degraded=True,
            diagnostic=diagnostic,
            created_at=utc_now_iso(),
        )

    def _fallback_question(self, context: dict[str, Any]) -> dict[str, Any]:
        text = _as_str(context.get("source_text") or context.get("text")).strip()
        if not text:
            return {
                "question": "",
                "answer": "",
                "hint": self._localize_reply(self._config.language, "empty_input"),
                "difficulty": 1,
                "topic": "general",
                "screen_type": self._screen_type_from_context(context),
            }
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), text[:120])
        return {
            "question": "What is the main idea or rule in this text?",
            "answer": first_line[:200],
            "hint": "Start from the definition, formula, or repeated term in the source text.",
            "difficulty": 2,
            "topic": self._guess_topic(context),
            "screen_type": self._screen_type_from_context(context),
        }

    def _fallback_evaluation(self, context: dict[str, Any]) -> dict[str, Any]:
        answer = _as_str(context.get("answer")).strip()
        expected = _as_str(context.get("expected_answer")).strip()
        if not answer:
            verdict, score, error_type = "dont_know", 0, "empty_answer"
        else:
            verdict, score, error_type = self._heuristic_verdict(answer, expected)
        return {
            "verdict": verdict,
            "score": score,
            "error_type": error_type,
            "feedback": self._fallback_feedback(verdict, context),
            "next_action": self._fallback_next_action(verdict),
            "screen_type": self._screen_type_from_context(context),
        }

    def _fallback_track(self, context: dict[str, Any]) -> dict[str, Any]:
        evaluation = _as_dict(context.get("evaluation") or context.get("last_answer_evaluation"))
        verdict = _as_str(evaluation.get("verdict")).strip()
        delta = 0.08 if verdict == "correct" else (-0.08 if verdict in {"wrong", "dont_know"} else 0.02)
        weak_points = []
        error_type = _as_str(evaluation.get("error_type")).strip()
        if error_type and error_type != "none":
            weak_points.append(error_type)
        return {
            "topic": self._guess_topic(context),
            "mastery_delta": delta,
            "confidence": 0.35,
            "weak_points": weak_points,
            "next_steps": ["Review the latest feedback"] if weak_points else ["Continue with one more practice question"],
            "session_summary_seed": _as_dict(context.get("session_summary_seed")),
            "screen_type": self._screen_type_from_context(context),
        }

    def _fallback_summary(self, context: dict[str, Any]) -> dict[str, Any]:
        history = [item for item in _as_list(context.get("history")) if isinstance(item, dict)]
        highlights = [
            f"{_as_str(item.get('kind'), 'interaction')}: {_as_str(item.get('output_text')).strip()[:80]}"
            for item in history[:4]
            if _as_str(item.get("output_text")).strip()
        ]
        summary = "No study interactions have been recorded yet." if not history else "This session includes recent study interactions and tutor feedback."
        weak_points = _string_list(_as_dict(context.get("session_summary_seed")).get("weak_points"), limit=4)
        next_actions = ["Review the latest feedback", "Try one recall question"]
        markdown = self._markdown_from_summary(summary, highlights, weak_points, next_actions)
        return {
            "summary": summary,
            "highlights": highlights,
            "weak_points": weak_points,
            "next_actions": next_actions,
            "markdown": markdown,
            "screen_type": self._screen_type_from_context(context),
        }

    @staticmethod
    def _heuristic_verdict(answer: str, expected: str) -> tuple[str, int, str]:
        normalized_answer = re.sub(r"\s+", " ", answer.strip().lower())
        normalized_expected = re.sub(r"\s+", " ", expected.strip().lower())
        if not normalized_expected:
            return ("partial", 50, "needs_reference")
        if normalized_expected and normalized_expected in normalized_answer:
            return ("correct", 90, "none")
        expected_tokens = {token for token in re.split(r"\W+", normalized_expected) if len(token) > 2}
        answer_tokens = {token for token in re.split(r"\W+", normalized_answer) if len(token) > 2}
        if expected_tokens:
            overlap = len(expected_tokens & answer_tokens) / max(1, len(expected_tokens))
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
        if verdict == "correct":
            return "This answer matches the core idea."
        if verdict == "partial":
            return "This answer is on the right track, but it needs one more precise step."
        if verdict == "dont_know":
            return "Start with the main definition or rule from the source text."
        return "This answer does not match the expected idea yet."

    @staticmethod
    def _fallback_next_action(verdict: str) -> str:
        if verdict == "correct":
            return "Move to a slightly harder follow-up question."
        if verdict == "partial":
            return "Ask for the missing step and then recheck the answer."
        if verdict == "dont_know":
            return "Give a hint before asking the learner to try again."
        return "Explain the misconception, then ask a simpler recall question."

    @staticmethod
    def _markdown_from_summary(summary: str, highlights: list[str], weak_points: list[str], next_actions: list[str]) -> str:
        def _section(title: str, items: list[str]) -> str:
            if not items:
                return f"## {title}\n\n- None recorded."
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
            return _as_str(payload.get("markdown")).strip() or _as_str(payload.get("summary")).strip()
        return _as_str(payload.get("reply")).strip()

    @staticmethod
    def _input_text_for_operation(operation: str, context: dict[str, Any]) -> str:
        if operation == LLM_OPERATION_ANSWER_EVALUATE:
            return _as_str(context.get("answer")).strip()
        if operation == LLM_OPERATION_SUMMARIZE_SESSION:
            return "session"
        return _as_str(context.get("source_text") or context.get("text") or context.get("question")).strip()

    @staticmethod
    def _screen_type_from_context(context: dict[str, Any]) -> str:
        screen = _as_dict(context.get("screen_classification"))
        return _as_str(screen.get("screen_type")).strip() or _as_str(context.get("screen_type")).strip()

    @staticmethod
    def _guess_topic(context: dict[str, Any]) -> str:
        question = _as_dict(context.get("current_question") or context.get("question_payload"))
        topic = _as_str(question.get("topic")).strip()
        if topic:
            return topic
        text = _as_str(context.get("source_text") or context.get("text") or context.get("question")).strip()
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if not first_line:
            return "general"
        return first_line[:48]

    async def _call_model(
        self,
        messages: list[dict[str, str]],
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

        api_config = get_config_manager().get_model_api_config("summary")
        base_url = str(api_config.get("base_url") or "").strip()
        model = str(api_config.get("model") or "").strip()
        api_key = str(api_config.get("api_key") or "").strip()
        if not base_url or not model:
            raise SdkError("missing configured summary model")
        temperature, max_tokens = self._config.llm_limits_for_operation(operation)
        key = (
            "summary",
            operation,
            base_url,
            model,
            self._api_key_cache_fingerprint(api_key),
            float(temperature),
            int(max_tokens),
        )
        timeout_seconds = float(self._config.llm_call_timeout_seconds) + _LLM_CALL_TIMEOUT_GRACE_SECONDS
        llm = await self._client_cache.get_or_create(
            key,
            lambda: create_chat_llm(
                model=model,
                base_url=base_url,
                api_key=api_key,
                temperature=float(temperature),
                max_completion_tokens=int(max_tokens),
                timeout=timeout_seconds,
            ),
        )
        if llm is None:
            raise SdkError("failed to initialize summary model")
        set_call_type("summary")
        ainvoke = getattr(llm, "ainvoke", None)
        if callable(ainvoke):
            response = await asyncio.wait_for(ainvoke(messages), timeout=timeout_seconds)
        else:
            response = await asyncio.wait_for(asyncio.to_thread(llm.invoke, messages), timeout=timeout_seconds)
        return str(getattr(response, "content", "") or response)

    @staticmethod
    def _api_key_cache_fingerprint(api_key: str) -> tuple[str, str]:
        if not api_key:
            return ("empty", "")
        digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        return ("sha256", digest)
