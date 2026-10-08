# -*- coding: utf-8 -*-
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

"""Shared output budget for memory calls that deliberately keep thinking on.

Reasoning tokens and the JSON answer share one output cap. The shared runaway
guard (``LLM_OUTPUT_GUARD_MAX_TOKENS``) is exhausted by thinking models before
the JSON is written, so these calls ask for ``MEMORY_THINKING_OUTPUT_MAX_TOKENS``
and fall back to the shared guard once when an endpoint rejects the larger cap
(a 400 naming the token field, raised before anything is generated).

The helper never changes a call site's control flow: it returns the provider
response unchanged and only logs when the output looks exhausted, so a parse
failure downstream is no longer silent about its cause.
"""

from __future__ import annotations

from typing import Any

from config import LLM_OUTPUT_GUARD_MAX_TOKENS, MEMORY_THINKING_OUTPUT_MAX_TOKENS
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")

_TOKEN_FIELDS = ('max_tokens', 'max_completion_tokens', 'max_output_tokens')


def output_cap_rejected(exc: BaseException) -> bool:
    """True when a 400 says the requested output cap exceeds the model's limit."""
    if getattr(exc, 'status_code', None) != 400:
        return False
    text = str(exc).lower()
    return any(key in text for key in _TOKEN_FIELDS)


def _metadata(response: Any) -> dict:
    metadata = getattr(response, 'response_metadata', None)
    return metadata if isinstance(metadata, dict) else {}


def _completion_tokens(metadata: dict) -> Any:
    usage = metadata.get('token_usage') or {}
    if not isinstance(usage, dict):
        return None
    output_tokens = usage.get('completion_tokens')
    if output_tokens is None:
        output_tokens = usage.get('output_tokens')
    return output_tokens


def response_hit_output_limit(response: Any, output_cap: int) -> bool:
    """Classify only strong evidence that the provider exhausted output tokens.

    Either the provider says so (``finish_reason`` length / max_tokens), or the
    visible answer is empty while the output tokens reached the requested cap.
    """
    metadata = _metadata(response)
    finish_reason = str(metadata.get('finish_reason') or '').strip().lower()
    if finish_reason in {'length', 'max_tokens'}:
        return True

    content = str(getattr(response, 'content', '') or '').strip()
    if content:
        return False
    try:
        return int(_completion_tokens(metadata) or 0) >= output_cap
    except (TypeError, ValueError):
        return False


def describe_output(response: Any) -> str:
    """One-line ``finish_reason`` / output-token summary for failure logs."""
    metadata = _metadata(response)
    content = str(getattr(response, 'content', '') or '')
    return (
        f"finish_reason={metadata.get('finish_reason')!r} "
        f"completion_tokens={_completion_tokens(metadata)!r} "
        f"content_chars={len(content.strip())}"
    )


async def _acreate(api_config: dict, *, timeout: float, output_cap: int):
    from config.providers import memory_thinking_extra_body
    from utils.llm_client import create_chat_llm_async

    model = api_config['model']
    return await create_chat_llm_async(
        model,
        api_config['base_url'], api_config['api_key'],
        timeout=timeout, max_retries=0,
        max_completion_tokens=output_cap,  # thinking shares this cap with the JSON answer
        # None = no extra_body = the model's native thinking; a few models get
        # an explicit lower-effort form because their native thinking is unbounded.
        extra_body=memory_thinking_extra_body(model),
        provider_type=api_config.get('provider_type'),
    )


async def aclose_quietly(llm, call_label: str) -> None:
    # Closing is cleanup: a close failure must not mask the call outcome (a
    # raise here would replace a valid response or the original error).
    try:
        await llm.aclose()
    except Exception as exc:
        logger.warning(f"[MemoryThinking] {call_label}: LLM 关闭失败: {exc}")


async def ainvoke_thinking(
    api_config: dict, prompt: Any, *, timeout: float, call_label: str,
) -> tuple[Any, int]:
    """Run one thinking-on memory call; return ``(response, output_cap_used)``.

    ``prompt`` must already be input-budgeted by the caller. Exceptions other
    than the cap rejection propagate unchanged, as they did at each call site.
    """
    output_cap = MEMORY_THINKING_OUTPUT_MAX_TOKENS
    llm = await _acreate(api_config, timeout=timeout, output_cap=output_cap)
    try:
        try:
            response = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # caller passes an already budgeted prompt.
        except Exception as cap_error:
            if not output_cap_rejected(cap_error):
                raise
            logger.info(
                f"[MemoryThinking] {call_label}: 模型拒绝 {output_cap} 输出额度，"
                f"改用 {LLM_OUTPUT_GUARD_MAX_TOKENS} 重试"
            )
            await aclose_quietly(llm, call_label)
            output_cap = LLM_OUTPUT_GUARD_MAX_TOKENS
            llm = await _acreate(api_config, timeout=timeout, output_cap=output_cap)
            response = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # same budgeted prompt, lower output cap.
    finally:
        await aclose_quietly(llm, call_label)

    if response_hit_output_limit(response, output_cap):
        logger.warning(
            f"[MemoryThinking] {call_label}: 输出额度 {output_cap} 耗尽 "
            f"({describe_output(response)})"
        )
    return response, output_cap
