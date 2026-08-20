from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from http import HTTPStatus
import time
from typing import Any
from urllib.parse import urlsplit

from .qwen_native_client import (
    QwenNativeClient,
    QwenNativeError,
    QwenNativeResult,
    _OUTPUT_TOKEN_BUDGETS,
    messages_have_image,
)

try:
    import utils.config_manager as _config_manager_module
except Exception as exc:  # pragma: no cover - guarded host dependency.
    _config_manager_module = None  # type: ignore[assignment]
    _CONFIG_MANAGER_IMPORT_ERROR = exc
else:
    _CONFIG_MANAGER_IMPORT_ERROR = None

try:
    from utils.llm_client import create_chat_llm_async
except Exception as exc:  # pragma: no cover - guarded host dependency.
    create_chat_llm_async = None  # type: ignore[assignment]
    _LLM_CLIENT_IMPORT_ERROR = exc
else:
    _LLM_CLIENT_IMPORT_ERROR = None

try:
    from utils.token_tracker import llm_call_context
except Exception:  # pragma: no cover - telemetry is optional at import time.
    llm_call_context = None  # type: ignore[assignment]


_DASHSCOPE_HOSTS = frozenset(
    {
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
        "dashscope-us.aliyuncs.com",
    }
)
_SUPPORTED_PROVIDER_TYPES = frozenset({"openai_compatible", "anthropic"})


@dataclass(frozen=True, slots=True)
class StudyModelRuntimeSnapshot:
    model_group: str
    model: str
    provider_type: str
    transport: str
    api_key: str = field(repr=False)
    base_url: str = field(repr=False)

    def safe_description(self) -> dict[str, object]:
        configured = bool(self.model and self.base_url)
        return {
            "group": self.model_group,
            "model": self.model,
            "provider_type": self.provider_type,
            "configured": configured,
            "credential_configured": bool(self.api_key),
            "transport": self.transport,
            "transport_supported": self.transport != "unsupported",
            "vision_capability": (
                "unknown" if self.model_group == "vision" else "not_applicable"
            ),
        }


@dataclass(frozen=True, slots=True)
class StudyModelResult:
    text: str
    model: str
    model_group: str
    request_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""
    max_output_tokens: int = 0
    output_limit_reached: bool = False
    reasoning_tokens: int = 0
    text_tokens: int = 0
    termination_unknown: bool = True


@dataclass(slots=True)
class AgentQuotaReservation:
    remaining_calls: int

    def consume(self) -> bool:
        if self.remaining_calls <= 0:
            return False
        self.remaining_calls -= 1
        return True


class StudyModelError(RuntimeError):
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


def _runtime_transport(model: str, base_url: str, provider_type: str) -> str:
    if provider_type not in _SUPPORTED_PROVIDER_TYPES:
        return "unsupported"
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError:
        return "unsupported"
    host = str(parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.rstrip("/")
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        return "unsupported"
    if (
        provider_type == "openai_compatible"
        and "qwen" in model.lower()
        and parsed.scheme.lower() == "https"
        and host in _DASHSCOPE_HOSTS
        and path == "/api/v1"
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    ):
        return "dashscope_native"
    return (
        "anthropic"
        if provider_type == "anthropic"
        else "openai_compatible"
    )


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _generic_error_details(exc: BaseException) -> tuple[int, str, str]:
    status_code = _nonnegative_int(getattr(exc, "status_code", 0))
    response = getattr(exc, "response", None)
    if not status_code:
        status_code = _nonnegative_int(getattr(response, "status_code", 0))
    body = getattr(exc, "body", None)
    provider_code = ""
    request_id = str(getattr(exc, "request_id", "") or "")
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            provider_code = str(error.get("code") or error.get("type") or "")
            request_id = request_id or str(error.get("request_id") or "")
    headers = getattr(response, "headers", None)
    if not request_id and headers is not None:
        try:
            request_id = str(
                headers.get("x-request-id") or headers.get("request-id") or ""
            )
        except Exception:
            request_id = ""
    return status_code, provider_code, request_id


def _diagnostic_for_generic_error(exc: BaseException, *, has_image: bool) -> str:
    status_code, provider_code, _ = _generic_error_details(exc)
    name = exc.__class__.__name__.lower()
    combined = f"{provider_code} {exc}".lower()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in name:
        return "timeout"
    if status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN} or any(
        marker in name for marker in ("authentication", "permissiondenied")
    ):
        return "authentication_failed"
    if status_code == HTTPStatus.TOO_MANY_REQUESTS or "ratelimit" in name:
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
    if has_image and any(
        marker in combined
        for marker in (
            "vision is not supported",
            "image input is not supported",
            "does not support image",
            "multimodal is not supported",
            "unsupported image",
        )
    ):
        return "vision_not_supported"
    normalized_code = "".join(ch for ch in provider_code.lower() if ch.isalnum())
    if normalized_code in {
        "invalidmodel",
        "modelaccessdenied",
        "modelnotexist",
        "modelnotfound",
        "modelnotsupported",
    }:
        return "model_not_supported"
    if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR or any(
        marker in name for marker in ("connection", "internalserver")
    ):
        return "provider_unavailable"
    if status_code == HTTPStatus.NOT_FOUND:
        return "invalid_endpoint"
    if status_code == HTTPStatus.BAD_REQUEST:
        return "invalid_request"
    return "llm_call_failed"


def _usage_value(usage: dict[str, Any], *names: str) -> int:
    for name in names:
        if name in usage:
            return _nonnegative_int(usage.get(name))
    return 0


class StudyModelGateway:
    def __init__(self, *, logger: Any) -> None:
        self._logger = logger
        self.native_client = QwenNativeClient(logger=logger)

    async def resolve_runtime(
        self, model_group: str
    ) -> StudyModelRuntimeSnapshot:
        group = str(model_group or "").strip().lower()
        if group not in {"agent", "vision"}:
            raise StudyModelError(
                "unsupported study model group", diagnostic="unsupported_provider"
            )
        get_config_manager = getattr(_config_manager_module, "get_config_manager", None)
        if not callable(get_config_manager):
            raise StudyModelError(
                "configuration manager is unavailable",
                diagnostic="provider_unavailable",
            )
        manager = get_config_manager()
        async_get_config = getattr(manager, "aget_model_api_config", None)
        if callable(async_get_config):
            config = await async_get_config(group)
        else:
            config = await asyncio.to_thread(manager.get_model_api_config, group)
        model = str(config.get("model") or "").strip()
        base_url = str(config.get("base_url") or "").strip()
        api_key = str(config.get("api_key") or "").strip()
        provider_type = str(
            config.get("provider_type") or "openai_compatible"
        ).strip().lower()
        return StudyModelRuntimeSnapshot(
            model_group=group,
            model=model,
            provider_type=provider_type,
            transport=_runtime_transport(model, base_url, provider_type),
            api_key=api_key,
            base_url=base_url,
        )

    async def describe_runtime(self, model_group: str) -> dict[str, object]:
        return (await self.resolve_runtime(model_group)).safe_description()

    async def describe_runtimes(self) -> dict[str, dict[str, object]]:
        text, vision = await asyncio.gather(
            self.describe_runtime("agent"), self.describe_runtime("vision")
        )
        return {"text": text, "vision": vision}

    async def call(
        self,
        messages: list[dict[str, Any]],
        *,
        operation: str,
        deadline: float,
        runtime: StudyModelRuntimeSnapshot | None = None,
        quota_reservation: AgentQuotaReservation | None = None,
    ) -> StudyModelResult:
        has_image = messages_have_image(messages)
        expected_group = "vision" if has_image else "agent"
        runtime = runtime or await self.resolve_runtime(expected_group)
        if runtime.model_group != expected_group:
            raise StudyModelError(
                "runtime model group does not match request content",
                diagnostic="invalid_request",
                operation=operation,
            )
        if not runtime.model or not runtime.base_url:
            raise StudyModelError(
                f"configured {expected_group} model is incomplete",
                diagnostic="model_unavailable",
                operation=operation,
            )
        if not runtime.api_key:
            raise StudyModelError(
                f"configured {expected_group} credential is missing",
                diagnostic="authentication_failed",
                operation=operation,
            )
        if runtime.transport == "unsupported":
            raise StudyModelError(
                f"configured {expected_group} provider protocol is unsupported",
                diagnostic="unsupported_provider",
                operation=operation,
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StudyModelError(
                "study model deadline exhausted",
                diagnostic="timeout",
                operation=operation,
            )
        if expected_group == "agent":
            await self._reserve_agent_quota(operation, quota_reservation)
        if runtime.transport == "dashscope_native":
            return await self._call_native(
                messages, operation=operation, deadline=deadline, runtime=runtime
            )
        return await self._call_generic(
            messages, operation=operation, deadline=deadline, runtime=runtime
        )

    async def reserve_optional_agent_call(
        self, operation: str
    ) -> tuple[bool, AgentQuotaReservation | None]:
        runtime = await self.resolve_runtime("agent")
        if not runtime.model or not runtime.base_url:
            raise StudyModelError(
                "configured agent model is incomplete",
                diagnostic="model_unavailable",
                operation=operation,
            )
        if not runtime.api_key:
            raise StudyModelError(
                "configured agent credential is missing",
                diagnostic="authentication_failed",
                operation=operation,
            )
        if runtime.transport == "unsupported":
            raise StudyModelError(
                "configured agent provider protocol is unsupported",
                diagnostic="unsupported_provider",
                operation=operation,
            )
        get_config_manager = getattr(_config_manager_module, "get_config_manager", None)
        if not callable(get_config_manager):
            raise StudyModelError(
                "configuration manager is unavailable",
                diagnostic="provider_unavailable",
                operation=operation,
            )
        manager = get_config_manager()
        reserve = getattr(manager, "areserve_agent_daily_quota", None)
        if callable(reserve):
            reserved, _info = await reserve(
                source=f"study_companion:{operation}",
                units=2,
                minimum_units=1,
            )
        else:
            reserved, _info = await asyncio.to_thread(
                manager.reserve_agent_daily_quota,
                f"study_companion:{operation}",
                2,
                1,
            )
        count = max(0, int(reserved or 0))
        reservation = AgentQuotaReservation(count) if count else None
        return count >= 2, reservation

    async def _reserve_agent_quota(
        self,
        operation: str,
        quota_reservation: AgentQuotaReservation | None = None,
    ) -> None:
        if quota_reservation is not None and quota_reservation.consume():
            return
        get_config_manager = getattr(_config_manager_module, "get_config_manager", None)
        if not callable(get_config_manager):
            raise StudyModelError(
                "configuration manager is unavailable",
                diagnostic="provider_unavailable",
                operation=operation,
            )
        manager = get_config_manager()
        consume = getattr(manager, "aconsume_agent_daily_quota", None)
        if callable(consume):
            ok, _info = await consume(
                source=f"study_companion:{operation}", units=1
            )
        else:
            ok, _info = await asyncio.to_thread(
                manager.consume_agent_daily_quota,
                f"study_companion:{operation}",
                1,
            )
        if not ok:
            raise StudyModelError(
                "free Agent daily quota is exhausted",
                diagnostic="agent_quota_exceeded",
                operation=operation,
            )

    async def _call_native(
        self,
        messages: list[dict[str, Any]],
        *,
        operation: str,
        deadline: float,
        runtime: StudyModelRuntimeSnapshot,
    ) -> StudyModelResult:
        try:
            result = await self.native_client.call(
                messages,
                operation=operation,
                deadline=deadline,
                api_config={
                    "model": runtime.model,
                    "base_url": runtime.base_url,
                    "api_key": runtime.api_key,
                    "provider_type": runtime.provider_type,
                },
            )
        except QwenNativeError as exc:
            raise StudyModelError(
                "DashScope native model request failed",
                diagnostic=exc.diagnostic,
                status_code=exc.status_code,
                request_id=exc.request_id,
                provider_code=exc.provider_code,
                operation=exc.operation or operation,
            ) from exc
        return StudyModelResult(
            text=result.text,
            model=result.model,
            model_group=result.model_group,
            request_id=result.request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            finish_reason=result.finish_reason,
            max_output_tokens=result.max_output_tokens,
            output_limit_reached=result.output_limit_reached,
            reasoning_tokens=result.reasoning_tokens,
            text_tokens=result.text_tokens,
            termination_unknown=result.termination_unknown,
        )

    async def _call_generic(
        self,
        messages: list[dict[str, Any]],
        *,
        operation: str,
        deadline: float,
        runtime: StudyModelRuntimeSnapshot,
    ) -> StudyModelResult:
        if not callable(create_chat_llm_async):
            raise StudyModelError(
                "host LLM client factory is unavailable",
                diagnostic="provider_unavailable",
                operation=operation,
            )
        remaining = deadline - time.monotonic()
        max_output_tokens = _OUTPUT_TOKEN_BUDGETS.get(operation, 3072)
        client: Any = None
        has_image = messages_have_image(messages)
        try:
            client = await create_chat_llm_async(
                model=runtime.model,
                base_url=runtime.base_url,
                api_key=runtime.api_key,
                provider_type=runtime.provider_type,
                max_retries=0,
                max_completion_tokens=max_output_tokens,
                timeout=remaining,
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            if callable(llm_call_context):
                with llm_call_context(runtime.model_group):
                    response = await asyncio.wait_for(
                        client.ainvoke(messages), timeout=remaining
                    )
            else:
                response = await asyncio.wait_for(
                    client.ainvoke(messages), timeout=remaining
                )
            metadata = dict(getattr(response, "response_metadata", {}) or {})
            usage = dict(metadata.get("token_usage") or {})
            finish_reason = str(metadata.get("finish_reason") or "").lower()
            output_tokens = _usage_value(
                usage, "completion_tokens", "output_tokens"
            )
            input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
            details = usage.get("completion_tokens_details") or usage.get(
                "output_tokens_details"
            )
            details = dict(details) if isinstance(details, dict) else {}
            reasoning_tokens = _usage_value(details, "reasoning_tokens")
            text_tokens = _usage_value(details, "text_tokens")
            text = str(getattr(response, "content", "") or "").strip()
            if not text:
                raise StudyModelError(
                    "model provider returned an empty response",
                    diagnostic="provider_unavailable",
                    operation=operation,
                )
            return StudyModelResult(
                text=text,
                model=runtime.model,
                model_group=runtime.model_group,
                request_id=str(metadata.get("request_id") or ""),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finish_reason=finish_reason,
                max_output_tokens=max_output_tokens,
                output_limit_reached=finish_reason == "length",
                reasoning_tokens=reasoning_tokens,
                text_tokens=text_tokens,
                termination_unknown=finish_reason not in {"length", "stop"},
            )
        except asyncio.CancelledError:
            raise
        except StudyModelError:
            raise
        except Exception as exc:
            status_code, provider_code, request_id = _generic_error_details(exc)
            diagnostic = _diagnostic_for_generic_error(exc, has_image=has_image)
            self._logger.warning(
                "study model request failed: operation={} model_group={} "
                "status_code={} provider_code={} request_id={} diagnostic={}",
                operation,
                runtime.model_group,
                status_code,
                provider_code,
                request_id,
                diagnostic,
            )
            raise StudyModelError(
                "model provider request failed",
                diagnostic=diagnostic,
                status_code=status_code,
                request_id=request_id,
                provider_code=provider_code,
                operation=operation,
            ) from exc
        finally:
            if client is not None:
                close = getattr(client, "aclose", None)
                if callable(close):
                    try:
                        await close()
                    except Exception as exc:
                        self._logger.warning(
                            "study model client close failed: operation={} error_type={}",
                            operation,
                            exc.__class__.__name__,
                        )


__all__ = [
    "AgentQuotaReservation",
    "StudyModelError",
    "StudyModelGateway",
    "StudyModelResult",
    "StudyModelRuntimeSnapshot",
]
