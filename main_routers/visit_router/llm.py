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

"""One-shot LLM calls of the visit endpoints (no session, no history, no tools).

Used for the visit persona and its private-section scan. Every call is
bounded by an output token budget and a timeout; the caller bounds the input
(``truncate_to_tokens``) before building the prompt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

_ACLOSE_TIMEOUT_S = 5.0
"""Upper bound on closing the client after a call (it may run while that call is being cancelled)."""

OneShotLLM = Callable[[str], Awaitable[str]]
"""Prompt in, reply text out; raises on any failure (no client, timeout, empty reply)."""


class VisitLLMUnavailable(RuntimeError):
    """No model is configured, the call failed, or it returned nothing."""


async def one_shot(prompt: str, *, max_tokens: int, timeout: float, model_type: str = "summary") -> str:
    """Send ``prompt`` as one user message to the ``model_type`` model and return the reply text."""
    from utils.config_manager import get_config_manager
    from utils.llm_client import HumanMessage, create_chat_llm_async

    try:
        api_cfg = await get_config_manager().aget_model_api_config(model_type)
    except Exception as exc:  # noqa: BLE001 - 配置读不出与没配同样处理
        raise VisitLLMUnavailable(f"model config unavailable: {type(exc).__name__}") from None
    model = (api_cfg or {}).get("model")
    if not model:
        raise VisitLLMUnavailable("model not configured")
    llm = await create_chat_llm_async(
        model,
        (api_cfg or {}).get("base_url"),
        (api_cfg or {}).get("api_key"),
        provider_type=(api_cfg or {}).get("provider_type"),
        max_completion_tokens=max_tokens,
        timeout=timeout,
        max_retries=1,
    )
    try:
        # 调用方已按 token 预算截断输入（角色卡 / 记录块）
        resp = await llm.ainvoke([HumanMessage(content=prompt)])  # noqa: LLM_INPUT_BUDGET
    except Exception as exc:  # noqa: BLE001
        logger.warning("visit llm: call failed: %s", type(exc).__name__)
        raise VisitLLMUnavailable("call failed") from None
    finally:
        try:
            # 限时：调用超时被取消后还卡在关闭上，外层的 wait_for 会一直等这段清理
            await asyncio.wait_for(llm.aclose(), _ACLOSE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - 关闭失败 / 超时不影响已拿到的结果
            logger.debug("visit llm: aclose failed: %s", type(exc).__name__)
    content = getattr(resp, "content", None)
    text = content if isinstance(content, str) else ""
    if not text.strip():
        raise VisitLLMUnavailable("empty reply")
    return text


def one_shot_llm(*, max_tokens: int, timeout: float, model_type: str = "summary") -> OneShotLLM:
    """A :data:`OneShotLLM` bound to one budget and timeout."""

    async def call(prompt: str) -> str:
        return await one_shot(prompt, max_tokens=max_tokens, timeout=timeout, model_type=model_type)

    return call
