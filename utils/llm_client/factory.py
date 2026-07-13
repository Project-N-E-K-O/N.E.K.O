# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Provider-aware synchronous and asynchronous chat-client factories."""

from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from .anthropic_client import (
    ChatAnthropic, _SENTINEL, _is_anthropic_endpoint,
    _is_kimi_code_anthropic_base_url,
)
from .openai_client import ChatOpenAI

def create_chat_llm(
    model: str,
    base_url: str | None,
    api_key: str | None,
    *,
    temperature: float | None = None,
    streaming: bool = False,
    max_retries: int = 2,
    max_completion_tokens: int | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    extra_body: Any = _SENTINEL,
    model_kwargs: dict | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    provider_type: str | None = None,
    **kw: Any,
) -> "ChatOpenAI | ChatAnthropic":
    """Create a chat client with automatic provider-specific configuration.

    Returns either a :class:`ChatOpenAI` (OpenAI-compatible endpoints) or a
    :class:`ChatAnthropic` (Anthropic Messages API endpoints such as Kimi Code).

    Provider cache headers and extra_body (thinking-disable etc.) are resolved
    automatically from ``config.providers``.  Pass ``extra_body=None`` to
    explicitly skip the auto-resolved extra_body (e.g. when thinking should
    remain enabled).

    Args:
        model: Model name (e.g. "qwen-flash", "gpt-4.1-mini", "kimi-for-coding").
        base_url: Provider API base URL.
        api_key: API key.
        provider_type: Optional provider type hint ("anthropic" selects the
            Anthropic Messages API path).
        extra_body: Override auto-resolved extra_body.  ``_SENTINEL`` (default)
            means "auto-resolve from model name"; ``None`` means "no extra_body".
        **kw: Forwarded to the selected client class.
    """
    from config.providers import get_cache_kwargs, get_extra_body

    is_anthropic = _is_anthropic_endpoint(base_url, provider_type)

    cache_kw = get_cache_kwargs(base_url)

    if extra_body is _SENTINEL:
        resolved = get_extra_body(model)
        extra_body = resolved or None

    if is_anthropic:
        # Kimi Code 要求 User-Agent 为 claude-code/0.1.0，参考 AstrBot / LingChat 实现。
        cache_default_headers = cache_kw.pop("default_headers", None) or {}
        default_headers = dict(cache_default_headers)
        default_headers.update(dict(kw.pop("default_headers", {}) or {}))
        if _is_kimi_code_anthropic_base_url(base_url):
            default_headers.setdefault("User-Agent", "claude-code/0.1.0")
        return ChatAnthropic(
            model=model,
            base_url=base_url,
            api_key=api_key,
            streaming=streaming,
            max_retries=max_retries,
            max_completion_tokens=max_completion_tokens,
            max_tokens=max_tokens,
            timeout=timeout,
            extra_body=extra_body,
            model_kwargs=model_kwargs,
            tools=tools,
            tool_choice=tool_choice,
            default_headers=default_headers or None,
            **cache_kw,
            **kw,
        )

    # Anthropic API 使用 x-api-key 而非 Bearer token，需要注入专用 headers
    _api_key = api_key
    if base_url and "api.anthropic.com" in base_url:
        anthropic_headers = {
            "x-api-key": api_key or "",
            "anthropic-version": "2023-06-01",
        }
        # 合并 cache_kw / kw / anthropic 的 default_headers，避免重复关键字
        merged_headers = {
            **cache_kw.pop("default_headers", {}),
            **kw.pop("default_headers", {}),
            **anthropic_headers,
        }
        kw["default_headers"] = merged_headers
        # OpenAI SDK 要求 api_key 非空，给占位值（实际鉴权走 x-api-key header）
        _api_key = "anthropic-via-header"

    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=_api_key,
        streaming=streaming,
        max_retries=max_retries,
        max_completion_tokens=max_completion_tokens,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_body=extra_body,
        model_kwargs=model_kwargs,
        tools=tools,
        tool_choice=tool_choice,
        **cache_kw,
        **kw,
    )


# Keep a marker on the original implementation so the async bridge can honor
# either patch point: the consumer module used by focused unit tests, or the
# historical package facade used throughout the repository.
create_chat_llm._original_impl = create_chat_llm


async def create_chat_llm_async(*args: Any, **kwargs: Any) -> "ChatOpenAI | ChatAnthropic":
    """Create a chat client without blocking the running event loop."""
    from utils import llm_client as _facade

    loop = asyncio.get_running_loop()
    factory = create_chat_llm
    facade_factory = _facade.create_chat_llm
    original = getattr(facade_factory, "__dict__", {}).get("_original_impl")
    if original is None or factory is original:
        factory = facade_factory
    task = asyncio.create_task(asyncio.to_thread(factory, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(
            lambda done: loop.create_task(_close_cancelled_chat_llm_result(done))
        )
        raise

async def _close_cancelled_chat_llm_result(task: asyncio.Task["ChatOpenAI | ChatAnthropic"]) -> None:
    try:
        llm = task.result()
    except Exception:
        return
    try:
        await llm.aclose()
    except Exception:
        return
