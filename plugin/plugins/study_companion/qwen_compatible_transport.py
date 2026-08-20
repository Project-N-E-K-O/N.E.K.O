from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


_ALLOWED_HOSTS = frozenset(
    {
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
        "dashscope-us.aliyuncs.com",
    }
)
_COMPATIBLE_PATH = "/compatible-mode/v1"
_CONTEXT_LIMIT_MARKERS = (
    "context length",
    "context_length",
    "context window",
    "maximum context",
    "prompt is too long",
    "too many tokens",
)


class QwenCompatibleTransportError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        diagnostic: str,
        status_code: int = 0,
        request_id: str = "",
        provider_code: str = "",
    ) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic
        self.status_code = status_code
        self.request_id = request_id
        self.provider_code = provider_code


@dataclass(frozen=True, slots=True)
class QwenCompatibleResult:
    text: str
    model: str
    request_id: str
    input_tokens: int
    output_tokens: int
    finish_reason: str = ""
    reasoning_tokens: int = 0
    text_tokens: int = 0
    termination_unknown: bool = False


def compatible_chat_completions_url(base_url: object) -> str:
    raw = str(base_url or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise QwenCompatibleTransportError(
            "invalid DashScope compatible endpoint",
            diagnostic="invalid_endpoint",
        ) from exc
    host = str(parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or host not in _ALLOWED_HOSTS
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path.rstrip("/") != _COMPATIBLE_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise QwenCompatibleTransportError(
            "endpoint must be an official DashScope compatible-mode URL",
            diagnostic="invalid_endpoint",
        )
    base = urlunsplit(("https", host, _COMPATIBLE_PATH, "", ""))
    return f"{base}/chat/completions"


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _response_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "").strip()
            for block in content
            if isinstance(block, dict) and str(block.get("text") or "").strip()
        ).strip()
    return ""


def _response_finish_reason(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    return str(choices[0].get("finish_reason") or "").strip().lower()


def _error_diagnostic(status_code: int, payload: object) -> str:
    data = payload if isinstance(payload, dict) else {}
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    code = str(error.get("code") or error.get("type") or "").lower()
    message = str(error.get("message") or "").lower()
    combined = f"{code} {message}"
    normalized_code = "".join(character for character in code if character.isalnum())
    if status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        return "authentication_failed"
    if status_code == HTTPStatus.TOO_MANY_REQUESTS or "rate" in combined:
        return "rate_limited"
    if any(marker in combined for marker in _CONTEXT_LIMIT_MARKERS):
        return "context_limit_exceeded"
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


class QwenCompatibleTransport:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def chat_completions(
        self,
        *,
        base_url: object,
        api_key: object,
        model: object,
        messages: list[dict[str, Any]],
        max_tokens: int,
        timeout_seconds: float,
    ) -> QwenCompatibleResult:
        url = compatible_chat_completions_url(base_url)
        key = str(api_key or "").strip()
        model_name = str(model or "").strip()
        if not key:
            raise QwenCompatibleTransportError(
                "DashScope API key is missing", diagnostic="authentication_failed"
            )
        if not model_name or "qwen" not in model_name.lower():
            raise QwenCompatibleTransportError(
                "configured model is not a Qwen model",
                diagnostic="model_not_supported",
            )
        try:
            output_budget = int(max_tokens)
            timeout = float(timeout_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("max_tokens and timeout_seconds must be numeric") from exc
        if output_budget <= 0 or timeout <= 0:
            raise ValueError("max_tokens and timeout_seconds must be positive")

        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(timeout),
            ) as client:
                async with asyncio.timeout(timeout):
                    response = await client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model_name,
                            "messages": messages,
                            "max_tokens": output_budget,
                            "enable_thinking": False,
                            "stream": False,
                        },
                    )
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise QwenCompatibleTransportError(
                "DashScope compatible request timed out", diagnostic="timeout"
            ) from exc
        except httpx.TimeoutException as exc:
            raise QwenCompatibleTransportError(
                "DashScope compatible request timed out", diagnostic="timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise QwenCompatibleTransportError(
                "DashScope compatible request failed",
                diagnostic="provider_unavailable",
            ) from exc

        request_id = str(
            response.headers.get("x-request-id")
            or response.headers.get("x-dashscope-request-id")
            or ""
        )
        try:
            payload = response.json()
        except ValueError as exc:
            diagnostic = (
                "provider_unavailable"
                if response.status_code == HTTPStatus.OK
                else _error_diagnostic(response.status_code, {})
            )
            raise QwenCompatibleTransportError(
                "DashScope returned invalid JSON",
                diagnostic=diagnostic,
                status_code=response.status_code,
                request_id=request_id,
            ) from exc
        if response.status_code != HTTPStatus.OK:
            error = payload.get("error") if isinstance(payload, dict) else {}
            error = error if isinstance(error, dict) else {}
            raise QwenCompatibleTransportError(
                "DashScope compatible request was rejected",
                diagnostic=_error_diagnostic(response.status_code, payload),
                status_code=response.status_code,
                request_id=request_id,
                provider_code=str(error.get("code") or error.get("type") or ""),
            )
        text = _response_text(payload)
        if not text:
            raise QwenCompatibleTransportError(
                "DashScope returned an empty completion",
                diagnostic="provider_unavailable",
                status_code=response.status_code,
                request_id=request_id,
            )
        usage = payload.get("usage") if isinstance(payload, dict) else {}
        usage = usage if isinstance(usage, dict) else {}
        token_details = usage.get("completion_tokens_details")
        token_details = token_details if isinstance(token_details, dict) else {}
        finish_reason = _response_finish_reason(payload)
        return QwenCompatibleResult(
            text=text,
            model=str(payload.get("model") or model_name),
            request_id=request_id or str(payload.get("request_id") or ""),
            input_tokens=_nonnegative_int(usage.get("prompt_tokens")),
            output_tokens=_nonnegative_int(usage.get("completion_tokens")),
            finish_reason=finish_reason,
            reasoning_tokens=_nonnegative_int(token_details.get("reasoning_tokens")),
            text_tokens=_nonnegative_int(token_details.get("text_tokens")),
            termination_unknown=finish_reason not in {"length", "stop"},
        )


__all__ = [
    "QwenCompatibleResult",
    "QwenCompatibleTransport",
    "QwenCompatibleTransportError",
    "compatible_chat_completions_url",
]
