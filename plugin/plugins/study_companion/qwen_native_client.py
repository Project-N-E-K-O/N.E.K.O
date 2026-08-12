from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http import HTTPStatus
import time
from typing import Any

from .constants import (
    LLM_OPERATION_ANSWER_EVALUATE,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    LLM_OPERATION_EXPAND_NOTE,
    LLM_OPERATION_KNOWLEDGE_TRACK,
    LLM_OPERATION_QUESTION_GENERATE,
    LLM_OPERATION_SUMMARIZE_SESSION,
    LLM_OPERATION_SUMMARIZE_TO_NOTE,
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
_TEXT_TIMEOUT_SECONDS = 45.0
_VISION_TIMEOUT_SECONDS = 60.0
_LONG_FORM_TIMEOUT_SECONDS = 75.0

_OUTPUT_TOKEN_BUDGETS = {
    LLM_OPERATION_CONCEPT_EXPLAIN: 3072,
    LLM_OPERATION_QUESTION_GENERATE: 1024,
    LLM_OPERATION_ANSWER_EVALUATE: 1536,
    LLM_OPERATION_KNOWLEDGE_TRACK: 768,
    LLM_OPERATION_SUMMARIZE_SESSION: 3072,
    LLM_OPERATION_EXPAND_NOTE: 3072,
    LLM_OPERATION_SUMMARIZE_TO_NOTE: 3072,
    "json_correction": 1536,
}


class QwenNativeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        diagnostic: str,
        status_code: int = 0,
        request_id: str = "",
    ) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic
        self.status_code = status_code
        self.request_id = request_id


@dataclass(frozen=True)
class QwenNativeResult:
    text: str
    model: str
    model_group: str
    request_id: str
    input_tokens: int
    output_tokens: int


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
    elif operation in {
        LLM_OPERATION_SUMMARIZE_SESSION,
        LLM_OPERATION_EXPAND_NOTE,
        LLM_OPERATION_SUMMARIZE_TO_NOTE,
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


def _diagnostic_for_response(response: object) -> str:
    status_code = _as_nonnegative_int(_get(response, "status_code"))
    code = str(_get(response, "code", "") or "").lower()
    message = str(_get(response, "message", "") or "").lower()
    combined = f"{code} {message}"
    if status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        return "authentication_failed"
    if status_code == HTTPStatus.TOO_MANY_REQUESTS or "rate" in combined:
        return "rate_limited"
    if "image" in combined or "multimodal" in combined:
        return "invalid_image"
    if status_code in {HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND} and "model" in combined:
        return "model_not_supported"
    if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return "provider_unavailable"
    return "llm_call_failed"


class QwenNativeClient:
    def __init__(self, *, logger: Any) -> None:
        self._logger = logger

    async def call(
        self,
        messages: list[dict[str, Any]],
        *,
        operation: str,
        deadline: float,
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
        api_config = get_config_manager().get_model_api_config(model_group)
        base_url = str(api_config.get("base_url") or "").strip()
        model = str(api_config.get("model") or "").strip()
        api_key = str(api_config.get("api_key") or "").strip()
        if not model or "qwen" not in model.lower():
            raise QwenNativeError(
                f"configured {model_group} model is not a Qwen model",
                diagnostic="model_not_supported",
            )
        if not api_key:
            raise QwenNativeError(
                f"configured {model_group} API key is missing",
                diagnostic="authentication_failed",
            )
        base_address = str(dashscope_http_url_from_base(base_url, "") or "").strip()
        if not base_address:
            raise QwenNativeError(
                f"configured {model_group} endpoint is not a DashScope endpoint",
                diagnostic="model_not_supported",
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError("study tutor Qwen deadline exhausted")

        input_tokens = 0
        output_tokens = 0
        request_id = ""
        success = False
        try:
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
                    max_tokens=_OUTPUT_TOKEN_BUDGETS.get(operation, 3072),
                    enable_thinking=False,
                    headers=dict(_SESSION_CACHE_HEADERS),
                    base_address=base_address,
                    request_timeout=remaining,
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
                    max_tokens=_OUTPUT_TOKEN_BUDGETS.get(operation, 3072),
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
            status_code = _as_nonnegative_int(_get(response, "status_code"))
            if status_code != HTTPStatus.OK:
                raise QwenNativeError(
                    str(_get(response, "message", "") or "DashScope request failed"),
                    diagnostic=_diagnostic_for_response(response),
                    status_code=status_code,
                    request_id=request_id,
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
            )
        finally:
            await self._record_usage(
                model=model,
                model_group=model_group,
                operation=operation,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                success=success,
            )

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
