from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http import HTTPStatus
import time
from typing import Any, NoReturn
from urllib.parse import urlsplit

from .constants import (
    LLM_OPERATION_ANSWER_EVALUATE,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    LLM_OPERATION_DOCUMENT_ANALYZE,
    LLM_OPERATION_DOCUMENT_CHUNK_ANALYZE,
    LLM_OPERATION_DOCUMENT_MERGE,
    LLM_OPERATION_EXPAND_NOTE,
    LLM_OPERATION_KNOWLEDGE_TRACK,
    LLM_OPERATION_QUESTION_GENERATE,
    LLM_OPERATION_SUMMARIZE_SESSION,
    LLM_OPERATION_SUMMARIZE_TO_NOTE,
)
from .qwen_compatible_transport import (
    QwenCompatibleTransport,
    QwenCompatibleTransportError,
)

try:
    from dashscope import AioGeneration, AioMultiModalConversation
except Exception as exc:  # pragma: no cover - guarded host dependency.
    AioGeneration = None  # type: ignore[assignment]
    AioMultiModalConversation = None  # type: ignore[assignment]
    _DASHSCOPE_IMPORT_ERROR = exc
else:
    _DASHSCOPE_IMPORT_ERROR = None

try:
    import utils.config_manager as _config_manager_module
except Exception as exc:  # pragma: no cover - guarded host dependency.
    _config_manager_module = None  # type: ignore[assignment]
    _CONFIG_MANAGER_IMPORT_ERROR = exc
else:
    _CONFIG_MANAGER_IMPORT_ERROR = None

try:
    import utils.token_tracker as _token_tracker_module
except Exception as exc:  # pragma: no cover - guarded host dependency.
    _token_tracker_module = None  # type: ignore[assignment]
    _TOKEN_TRACKER_IMPORT_ERROR = exc
else:
    _TOKEN_TRACKER_IMPORT_ERROR = None

try:
    from utils.dashscope_region import dashscope_http_url_from_base
except Exception as exc:  # pragma: no cover - guarded host dependency.
    dashscope_http_url_from_base = None  # type: ignore[assignment]
    _DASHSCOPE_REGION_IMPORT_ERROR = exc
else:
    _DASHSCOPE_REGION_IMPORT_ERROR = None


_SESSION_CACHE_HEADERS = {"x-dashscope-session-cache": "enable"}
_DASHSCOPE_TEXT_HOSTS = frozenset(
    {
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
        "dashscope-us.aliyuncs.com",
    }
)
_TEXT_TIMEOUT_SECONDS = 45.0
_VISION_TIMEOUT_SECONDS = 60.0
_LONG_FORM_TIMEOUT_SECONDS = 75.0
_DOCUMENT_CHUNK_TIMEOUT_SECONDS = 110.0
_DOCUMENT_MERGE_TIMEOUT_SECONDS = 120.0
_OUTPUT_TOKEN_BUDGETS = {
    LLM_OPERATION_CONCEPT_EXPLAIN: 3072,
    LLM_OPERATION_QUESTION_GENERATE: 1024,
    LLM_OPERATION_ANSWER_EVALUATE: 1536,
    LLM_OPERATION_KNOWLEDGE_TRACK: 768,
    LLM_OPERATION_SUMMARIZE_SESSION: 3072,
    LLM_OPERATION_EXPAND_NOTE: 3072,
    LLM_OPERATION_SUMMARIZE_TO_NOTE: 3072,
    LLM_OPERATION_DOCUMENT_ANALYZE: 3072,
    LLM_OPERATION_DOCUMENT_CHUNK_ANALYZE: 1200,
    LLM_OPERATION_DOCUMENT_MERGE: 4096,
    "json_correction": 1536,
    "knowledge_semantic_route": 512,
    "solution_structure_repair": 1536,
}


class QwenNativeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        diagnostic: str,
        status_code: int = 0,
        request_id: str = "",
        provider_code: str = "",
        operation: str = "",
    ) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic
        self.status_code = status_code
        self.request_id = request_id
        self.provider_code = provider_code
        self.operation = operation


@dataclass(frozen=True)
class QwenNativeResult:
    text: str
    model: str
    model_group: str
    request_id: str
    input_tokens: int
    output_tokens: int
    finish_reason: str = ""
    max_output_tokens: int = 0
    output_limit_reached: bool = False
    reasoning_tokens: int = 0
    text_tokens: int = 0
    termination_unknown: bool = False


def _get(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _message_has_image(message: dict[str, Any]) -> bool:
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") == "image_url"
        for block in content
    )


def messages_have_image(messages: list[dict[str, Any]]) -> bool:
    return any(_message_has_image(message) for message in messages)


def operation_timeout_seconds(
    operation: str,
    *,
    has_image: bool,
    configured_timeout_seconds: float,
) -> float:
    if has_image:
        operation_limit = _VISION_TIMEOUT_SECONDS
    elif operation == LLM_OPERATION_DOCUMENT_CHUNK_ANALYZE:
        operation_limit = _DOCUMENT_CHUNK_TIMEOUT_SECONDS
    elif operation == LLM_OPERATION_DOCUMENT_MERGE:
        operation_limit = _DOCUMENT_MERGE_TIMEOUT_SECONDS
    elif operation in {
        LLM_OPERATION_SUMMARIZE_SESSION,
        LLM_OPERATION_EXPAND_NOTE,
        LLM_OPERATION_SUMMARIZE_TO_NOTE,
        LLM_OPERATION_DOCUMENT_ANALYZE,
    }:
        operation_limit = _LONG_FORM_TIMEOUT_SECONDS
    else:
        operation_limit = _TEXT_TIMEOUT_SECONDS
    try:
        configured = float(configured_timeout_seconds)
    except (TypeError, ValueError, OverflowError):
        configured = operation_limit
    return max(1.0, min(operation_limit, configured))


def new_operation_deadline(
    operation: str,
    *,
    has_image: bool,
    configured_timeout_seconds: float,
) -> float:
    return time.monotonic() + operation_timeout_seconds(
        operation,
        has_image=has_image,
        configured_timeout_seconds=configured_timeout_seconds,
    )


def _native_messages(
    messages: list[dict[str, Any]], *, multimodal: bool
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if not multimodal:
            if isinstance(content, list):
                text_parts = [
                    str(block.get("text") or "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                content = "\n".join(part for part in text_parts if part)
            converted.append({"role": role, "content": str(content or "")})
            continue

        blocks: list[dict[str, str]] = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = str(block.get("text") or "")
                    if text:
                        blocks.append({"text": text})
                elif block.get("type") == "image_url":
                    image_url = block.get("image_url")
                    url = (
                        str(image_url.get("url") or "")
                        if isinstance(image_url, dict)
                        else str(image_url or "")
                    )
                    if url:
                        blocks.append({"image": url})
        else:
            text = str(content or "")
            if text:
                blocks.append({"text": text})
        converted.append({"role": role, "content": blocks})
    return converted


def _extract_text(response: object) -> str:
    output = _get(response, "output")
    direct_text = str(_get(output, "text", "") or "").strip()
    if direct_text:
        return direct_text
    choices = _get(output, "choices", [])
    if not isinstance(choices, list) or not choices:
        return ""
    message = _get(choices[0], "message")
    content = _get(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(_get(block, "text", "") or "").strip()
            for block in content
            if str(_get(block, "text", "") or "").strip()
        ).strip()
    return str(content or "").strip()


def _extract_finish_reason(response: object) -> str:
    output = _get(response, "output")
    candidates = [
        _get(response, "finish_reason"),
        _get(output, "finish_reason"),
    ]
    choices = _get(output, "choices", [])
    if isinstance(choices, (list, tuple)) and choices:
        first_choice = choices[0]
        candidates.extend(
            (
                _get(first_choice, "finish_reason"),
                _get(_get(first_choice, "message"), "finish_reason"),
            )
        )
    for candidate in candidates:
        normalized = str(candidate or "").strip().lower()
        if normalized:
            return normalized
    return ""


def _extract_output_token_details(usage: object) -> tuple[int, int]:
    details = _get(usage, "output_tokens_details")
    if details is None:
        details = _get(usage, "completion_tokens_details")
    reasoning_tokens = _as_nonnegative_int(
        _get(details, "reasoning_tokens", _get(usage, "reasoning_tokens"))
    )
    text_tokens = _as_nonnegative_int(
        _get(details, "text_tokens", _get(usage, "text_tokens"))
    )
    return reasoning_tokens, text_tokens


def _log_request_diagnostic(
    logger: Any,
    *,
    operation: str,
    transport: str,
    thinking_enabled: bool,
    elapsed_ms: int,
    timeout_seconds: float,
    request_id: str,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    text_tokens: int,
    finish_reason: str,
    termination_unknown: bool,
    success: bool,
) -> None:
    log_info = getattr(logger, "info", None)
    if not callable(log_info):
        return
    log_info(
        "study Qwen request diagnostic: operation={} transport={} thinking={} "
        "elapsed_ms={} timeout_seconds={} request_id={} prompt_tokens={} "
        "completion_tokens={} reasoning_tokens={} text_tokens={} "
        "finish_reason={} termination_unknown={} success={}",
        operation,
        transport,
        thinking_enabled,
        elapsed_ms,
        round(timeout_seconds, 3),
        request_id,
        input_tokens,
        output_tokens,
        reasoning_tokens,
        text_tokens,
        finish_reason,
        termination_unknown,
        success,
    )


def _diagnostic_for_response(response: object) -> str:
    status_code = _as_nonnegative_int(_get(response, "status_code"))
    code = str(_get(response, "code", "") or "").lower()
    message = str(_get(response, "message", "") or "").lower()
    combined = f"{code} {message}"
    normalized_code = "".join(character for character in code if character.isalnum())
    if status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        return "authentication_failed"
    if status_code == HTTPStatus.TOO_MANY_REQUESTS or "rate" in combined:
        return "rate_limited"
    if any(
        marker in combined
        for marker in (
            "context length",
            "context_length",
            "context window",
            "maximum context",
            "prompt is too long",
            "too many tokens",
        )
    ):
        return "context_limit_exceeded"
    if "image" in combined or "multimodal" in combined:
        if any(
            marker in combined
            for marker in (
                "not supported",
                "does not support",
                "unsupported",
            )
        ):
            return "vision_not_supported"
        return "invalid_image"
    if normalized_code in {
        "invalidmodel",
        "modelaccessdenied",
        "modelnotexist",
        "modelnotfound",
        "modelnotsupported",
    }:
        return "model_not_supported"
    if status_code == HTTPStatus.NOT_FOUND:
        return "invalid_endpoint"
    if status_code == HTTPStatus.BAD_REQUEST:
        if "url error" in message or "endpoint" in message:
            return "invalid_endpoint"
        return "invalid_request"
    if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return "provider_unavailable"
    return "llm_call_failed"


def _text_transport_kind(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url.strip())
        port = parsed.port
    except ValueError as exc:
        raise QwenNativeError(
            "configured agent endpoint is invalid",
            diagnostic="invalid_endpoint",
        ) from exc
    host = str(parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.rstrip("/")
    if (
        parsed.scheme.lower() != "https"
        or host not in _DASHSCOPE_TEXT_HOSTS
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise QwenNativeError(
            "configured agent endpoint is not an official DashScope endpoint",
            diagnostic="invalid_endpoint",
        )
    if path == "/compatible-mode/v1":
        return "compatible"
    if path == "/api/v1":
        return "native"
    raise QwenNativeError(
        "configured agent endpoint uses an unsupported DashScope path",
        diagnostic="invalid_endpoint",
    )


class QwenNativeClient:
    def __init__(self, *, logger: Any) -> None:
        self._logger = logger
        self._compatible_transport = QwenCompatibleTransport()

    async def call(
        self,
        messages: list[dict[str, Any]],
        *,
        operation: str,
        deadline: float,
        api_config: dict[str, Any] | None = None,
    ) -> QwenNativeResult:
        get_config_manager = getattr(_config_manager_module, "get_config_manager", None)
        if not callable(get_config_manager):
            raise QwenNativeError(
                "configuration manager is unavailable",
                diagnostic="provider_unavailable",
            )
        if not callable(dashscope_http_url_from_base):
            raise QwenNativeError(
                "DashScope region support is unavailable",
                diagnostic="provider_unavailable",
            )
        has_image = messages_have_image(messages)
        model_group = "vision" if has_image else "agent"
        if api_config is None:
            config_manager = get_config_manager()
            async_get_config = getattr(config_manager, "aget_model_api_config", None)
            if callable(async_get_config):
                api_config = await async_get_config(model_group)
            else:
                api_config = await asyncio.to_thread(
                    config_manager.get_model_api_config, model_group
                )
        else:
            api_config = dict(api_config)
        base_url = str(api_config.get("base_url") or "").strip()
        model = str(api_config.get("model") or "").strip()
        api_key = str(api_config.get("api_key") or "").strip()
        if not model or "qwen" not in model.lower():
            await self._raise_preflight_error(
                QwenNativeError(
                    f"configured {model_group} model is not a Qwen model",
                    diagnostic="model_not_supported",
                    operation=operation,
                ),
                model=model,
                model_group=model_group,
                operation=operation,
            )
        if not api_key:
            await self._raise_preflight_error(
                QwenNativeError(
                    f"configured {model_group} API key is missing",
                    diagnostic="authentication_failed",
                    operation=operation,
                ),
                model=model,
                model_group=model_group,
                operation=operation,
            )
        try:
            text_transport = "native" if has_image else _text_transport_kind(base_url)
        except QwenNativeError as exc:
            exc.operation = operation
            await self._raise_preflight_error(
                exc,
                model=model,
                model_group=model_group,
                operation=operation,
            )
        base_address = str(dashscope_http_url_from_base(base_url, "") or "").strip()
        if has_image and not base_address:
            await self._raise_preflight_error(
                QwenNativeError(
                    f"configured {model_group} endpoint is not a DashScope endpoint",
                    diagnostic="invalid_endpoint",
                    operation=operation,
                ),
                model=model,
                model_group=model_group,
                operation=operation,
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            await self._raise_preflight_error(
                QwenNativeError(
                    "study tutor Qwen deadline exhausted",
                    diagnostic="timeout",
                    operation=operation,
                ),
                model=model,
                model_group=model_group,
                operation=operation,
            )

        input_tokens = 0
        output_tokens = 0
        reasoning_tokens = 0
        text_tokens = 0
        request_id = ""
        finish_reason = ""
        termination_unknown = True
        success = False
        request_started = time.monotonic()
        request_timeout_seconds = remaining
        transport = (
            "native_vision"
            if has_image
            else "compatible"
            if text_transport == "compatible"
            else "native_text"
        )
        thinking_enabled = False
        try:
            max_output_tokens = _OUTPUT_TOKEN_BUDGETS.get(operation, 3072)
            if has_image:
                if AioMultiModalConversation is None:
                    raise QwenNativeError(
                        "DashScope multimodal client is unavailable",
                        diagnostic="provider_unavailable",
                    )
                awaitable = AioMultiModalConversation.call(
                    model=model,
                    messages=_native_messages(messages, multimodal=True),
                    api_key=api_key,
                    result_format="message",
                    max_tokens=max_output_tokens,
                    enable_thinking=False,
                    headers=dict(_SESSION_CACHE_HEADERS),
                    base_address=base_address,
                    request_timeout=remaining,
                )
            elif text_transport == "compatible":
                compatible_result = await self._compatible_transport.chat_completions(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    messages=_native_messages(messages, multimodal=False),
                    max_tokens=_OUTPUT_TOKEN_BUDGETS.get(operation, 3072),
                    timeout_seconds=remaining,
                )
                request_id = compatible_result.request_id
                input_tokens = compatible_result.input_tokens
                output_tokens = compatible_result.output_tokens
                finish_reason = compatible_result.finish_reason
                reasoning_tokens = compatible_result.reasoning_tokens
                text_tokens = compatible_result.text_tokens
                termination_unknown = (
                    compatible_result.termination_unknown
                    or finish_reason not in {"length", "stop"}
                )
                output_limit_reached = finish_reason == "length"
                if output_limit_reached:
                    self._logger.warning(
                        "study Qwen output limit reached: diagnostic=output_truncated "
                        "operation={} model_group={} "
                        "finish_reason={} output_tokens={} max_output_tokens={}",
                        operation,
                        model_group,
                        finish_reason,
                        output_tokens,
                        max_output_tokens,
                    )
                success = True
                return QwenNativeResult(
                    text=compatible_result.text,
                    model=model,
                    model_group=model_group,
                    request_id=request_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    finish_reason=finish_reason,
                    max_output_tokens=max_output_tokens,
                    output_limit_reached=output_limit_reached,
                    reasoning_tokens=reasoning_tokens,
                    text_tokens=text_tokens,
                    termination_unknown=termination_unknown,
                )
            else:
                if AioGeneration is None:
                    raise QwenNativeError(
                        "DashScope text client is unavailable",
                        diagnostic="provider_unavailable",
                    )
                awaitable = AioGeneration.call(
                    model=model,
                    messages=_native_messages(messages, multimodal=False),
                    api_key=api_key,
                    result_format="message",
                    max_tokens=max_output_tokens,
                    enable_thinking=False,
                    headers=dict(_SESSION_CACHE_HEADERS),
                    base_address=base_address,
                    request_timeout=remaining,
                )
            response = await asyncio.wait_for(awaitable, timeout=remaining)
            request_id = str(_get(response, "request_id", "") or "")
            usage = _get(response, "usage")
            input_tokens = _as_nonnegative_int(_get(usage, "input_tokens"))
            output_tokens = _as_nonnegative_int(_get(usage, "output_tokens"))
            reasoning_tokens, text_tokens = _extract_output_token_details(usage)
            finish_reason = _extract_finish_reason(response)
            termination_unknown = finish_reason not in {"length", "stop"}
            output_limit_reached = finish_reason == "length"
            status_code = _as_nonnegative_int(_get(response, "status_code"))
            if status_code != HTTPStatus.OK:
                raise QwenNativeError(
                    "DashScope native request was rejected",
                    diagnostic=_diagnostic_for_response(response),
                    status_code=status_code,
                    request_id=request_id,
                    provider_code=str(_get(response, "code", "") or ""),
                    operation=operation,
                )
            if output_limit_reached:
                self._logger.warning(
                    "study Qwen output limit reached: diagnostic=output_truncated "
                    "operation={} model_group={} "
                    "finish_reason={} output_tokens={} max_output_tokens={}",
                    operation,
                    model_group,
                    finish_reason,
                    output_tokens,
                    max_output_tokens,
                )
            text = _extract_text(response)
            if not text:
                raise QwenNativeError(
                    "DashScope returned an empty response",
                    diagnostic="provider_unavailable",
                    request_id=request_id,
                )
            success = True
            return QwenNativeResult(
                text=text,
                model=model,
                model_group=model_group,
                request_id=request_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finish_reason=finish_reason,
                max_output_tokens=max_output_tokens,
                output_limit_reached=output_limit_reached,
                reasoning_tokens=reasoning_tokens,
                text_tokens=text_tokens,
                termination_unknown=termination_unknown,
            )
        except QwenCompatibleTransportError as exc:
            request_id = exc.request_id
            error = QwenNativeError(
                "DashScope compatible request failed",
                diagnostic=exc.diagnostic,
                status_code=exc.status_code,
                request_id=exc.request_id,
                provider_code=exc.provider_code,
                operation=operation,
            )
            self._logger.warning(
                "study Qwen request failed: operation={} status_code={} "
                "provider_code={} request_id={} diagnostic={}",
                operation,
                error.status_code,
                error.provider_code,
                error.request_id,
                error.diagnostic,
            )
            raise error from exc
        except (TimeoutError, OSError) as exc:
            diagnostic = "timeout" if isinstance(exc, TimeoutError) else "provider_unavailable"
            error = QwenNativeError(
                "DashScope native request failed",
                diagnostic=diagnostic,
                request_id=request_id,
                operation=operation,
            )
            self._logger.warning(
                "study Qwen request failed: operation={} status_code={} "
                "provider_code={} request_id={} diagnostic={}",
                operation,
                error.status_code,
                error.provider_code,
                error.request_id,
                error.diagnostic,
            )
            raise error from exc
        except QwenNativeError as exc:
            self._logger.warning(
                "study Qwen request failed: operation={} status_code={} "
                "provider_code={} request_id={} diagnostic={}",
                exc.operation or operation,
                exc.status_code,
                exc.provider_code,
                exc.request_id,
                exc.diagnostic,
            )
            raise
        finally:
            await self._record_usage(
                model=model,
                model_group=model_group,
                operation=operation,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                success=success,
            )
            _log_request_diagnostic(
                self._logger,
                operation=operation,
                transport=transport,
                thinking_enabled=thinking_enabled,
                elapsed_ms=max(0, round((time.monotonic() - request_started) * 1000)),
                timeout_seconds=request_timeout_seconds,
                request_id=request_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                text_tokens=text_tokens,
                finish_reason=finish_reason,
                termination_unknown=termination_unknown,
                success=success,
            )

    async def _raise_preflight_error(
        self,
        error: QwenNativeError,
        *,
        model: str,
        model_group: str,
        operation: str,
    ) -> NoReturn:
        self._logger.warning(
            "study Qwen request failed: operation={} status_code={} "
            "provider_code={} request_id={} diagnostic={}",
            operation,
            error.status_code,
            error.provider_code,
            error.request_id,
            error.diagnostic,
        )
        await self._record_usage(
            model=model,
            model_group=model_group,
            operation=operation,
            input_tokens=0,
            output_tokens=0,
            success=False,
        )
        raise error

    async def _record_usage(
        self,
        *,
        model: str,
        model_group: str,
        operation: str,
        input_tokens: int,
        output_tokens: int,
        success: bool,
    ) -> None:
        tracker_type = getattr(_token_tracker_module, "TokenTracker", None)
        get_instance = getattr(tracker_type, "get_instance", None)
        if not callable(get_instance):
            return

        def _record() -> None:
            tracker = get_instance()
            tracker.record(
                model=model,
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                call_type=model_group,
                source=f"study_companion:{operation}",
                success=success,
            )

        try:
            await asyncio.to_thread(_record)
        except Exception as exc:  # Token telemetry must not fail the tutor call.
            self._logger.warning("study Qwen token tracking failed: {}", exc)


__all__ = [
    "QwenNativeClient",
    "QwenNativeError",
    "QwenNativeResult",
    "messages_have_image",
    "new_operation_deadline",
    "operation_timeout_seconds",
]
