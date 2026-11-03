"""Observe usage for one theater HTTP request without writing story state, archives or the global token ledger."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
import json

from starlette.responses import JSONResponse

from .numeric_v2_trace import invoke_with_trace


# Isolate requests; subtasks share their request container. Ordinary chat outside the scope is excluded entirely.
_usage_calls: ContextVar[list[dict[str, Any]] | None] = ContextVar("numeric_v2_usage_calls", default=None)


@contextmanager
def numeric_v2_usage_scope():
    """Reset context at request completion, including cancellation and errors, so usage cannot leak into the next turn."""
    calls: list[dict[str, Any]] = []
    token = _usage_calls.set(calls)
    try:
        yield calls
    finally:
        _usage_calls.reset(token)


async def invoke_with_usage(client: Any, messages: list[Any], *, stage: str,
                            response_format: dict[str, Any] | None = None):
    """Read only reported client usage; timeout or missing provider data stays unknown rather than becoming an estimate presented as actual consumption."""
    # Actor/Evaluator already validate full messages against session tiers before calling; observe only, without trimming or rewriting evidence.
    calls = _usage_calls.get()
    if calls is None:
        return await invoke_with_trace(client, messages, stage=stage, response_format=response_format)
    row = {"stage": stage, "input_tokens": None, "output_tokens": None}
    calls.append(row)
    response = await invoke_with_trace(client, messages, stage=stage, response_format=response_format)
    usage = (getattr(response, "response_metadata", None) or {}).get("token_usage") or {}
    # The Anthropic client also provides OpenAI aliases; native input fields include cache creation and reads.
    prompt = usage.get("prompt_tokens")
    if prompt is None and isinstance(usage.get("input_tokens"), int):
        prompt = sum(usage.get(key) or 0 for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    for key, value in (("input_tokens", prompt), ("output_tokens", completion)):
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            row[key] = value
    return response


def with_numeric_v2_usage(response: Any, calls: list[dict[str, Any]]):
    """Report successful and mapped failed calls; idempotent replays with no model request report zero calls without charging twice."""
    usage = {
        "input_tokens": sum(row["input_tokens"] or 0 for row in calls),
        "output_tokens": sum(row["output_tokens"] or 0 for row in calls),
        "complete": all(row["input_tokens"] is not None and row["output_tokens"] is not None for row in calls),
        "calls": calls,
    }
    if isinstance(response, JSONResponse):
        # Preserve existing error codes and security headers; recalculate only body length after adding usage.
        body = json.loads(response.body)
        return JSONResponse({**body, "token_usage": usage}, status_code=response.status_code,
                            headers={k: v for k, v in response.headers.items() if k != "content-length"},
                            background=response.background)
    return {**response, "token_usage": usage}
