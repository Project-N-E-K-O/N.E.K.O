"""Injected synchronous model calls; no provider client or configuration at import."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .json_response import JSONResponseParser


class LLMCallFailure(str):
    """Keep the generator's technical-failure contract, without exposing secrets."""

    def __new__(cls, message: str, *, error_code: str, exception_type: str,
                status_code: int | None = None, provider_code: str = ""):
        value = super().__new__(cls, message)
        value.error_code = error_code
        value.exception_type = exception_type
        value.status_code = status_code
        value.provider_code = provider_code
        return value

    def diagnostic(self) -> dict[str, Any]:
        result = {"exception_type": self.exception_type}
        if self.status_code is not None:
            result["upstream_status_code"] = self.status_code
        if self.provider_code:
            result["provider_code"] = self.provider_code
        return result


@dataclass(frozen=True)
class ModelReply:
    content: str
    model: str
    usage: Mapping[str, Any] | None = None


ModelCall = Callable[..., ModelReply | str]
_usage: ContextVar[list | None] = ContextVar("theater_workshop_usage", default=None)


@contextmanager
def capture_usage():
    records: list[dict] = []
    token = _usage.set(records)
    try:
        yield records
    finally:
        _usage.reset(token)


class ModelAgent(JSONResponseParser):
    def __init__(self, name: str, model_call: ModelCall | None):
        self.name = name
        self._model_call = model_call

    def call_llm(self, messages, **options) -> str:
        if self._model_call is None:
            raise RuntimeError("workshop_model_required")
        # The host performs exactly the requested number of attempts. There is
        # no second retry loop in this adapter or in the generator.
        reply = self._model_call(messages, **options)
        content = reply.content if isinstance(reply, ModelReply) else reply
        if not isinstance(content, str):
            raise TypeError("workshop_model_response_invalid")
        records = _usage.get()
        if records is not None:
            usage = (reply.usage or {}) if isinstance(reply, ModelReply) else {}
            def count(key):
                value = usage.get(key)
                return max(0, value) if isinstance(value, int) and not isinstance(value, bool) else 0
            prompt, completion = count("prompt_tokens"), count("completion_tokens")
            records.append({
                "agent": self.name, "operation": options.get("operation"),
                "model": reply.model if isinstance(reply, ModelReply) else "injected",
                "prompt_tokens": prompt, "completion_tokens": completion,
                "total_tokens": count("total_tokens") if "total_tokens" in usage else prompt + completion,
                "usage_reported": isinstance(reply, ModelReply) and reply.usage is not None,
            })
        return content
