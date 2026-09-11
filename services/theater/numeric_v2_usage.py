"""Observe usage for one theater HTTP request without writing story state, archives or the global token ledger."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
import json

from starlette.responses import JSONResponse


# 请求间隔离，子任务共享本请求容器；普通聊天未进入作用域时完全不参与统计。
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


async def invoke_with_usage(client: Any, messages: list[Any], *, stage: str):
    """Read only reported client usage; timeout or missing provider data stays unknown rather than becoming an estimate presented as actual consumption."""
    # Actor/Evaluator 在调用前已按会话档位检查完整 messages；这里仅观察，不二次裁剪或改写证据。
    calls = _usage_calls.get()
    if calls is None:
        return await client.ainvoke(messages)  # noqa: LLM_INPUT_BUDGET
    row = {"stage": stage, "input_tokens": None, "output_tokens": None}
    calls.append(row)
    response = await client.ainvoke(messages)  # noqa: LLM_INPUT_BUDGET
    usage = (getattr(response, "response_metadata", None) or {}).get("token_usage") or {}
    # 客户端对 Anthropic 也提供 OpenAI 别名；兼容原生字段时输入包含缓存创建和读取。
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
        # 沿用原错误码和安全头，仅重算附加用量后的正文长度。
        body = json.loads(response.body)
        return JSONResponse({**body, "token_usage": usage}, status_code=response.status_code,
                            headers={k: v for k, v in response.headers.items() if k != "content-length"},
                            background=response.background)
    return {**response, "token_usage": usage}
