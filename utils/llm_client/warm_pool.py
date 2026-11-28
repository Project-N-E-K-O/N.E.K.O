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

"""Chat clients kept open between calls, one set per event loop.

Most callers build a chat client per call and close it afterwards, so every
call pays a new TLS handshake. Short, frequent calls whose latency the user
sees (a one-letter answer read from the first token's probabilities) keep a
client here instead: on Doubao a warm connection answered in about 300 ms
against 400-580 ms on a new one (measured 2026-11-27).

A client is bound to the event loop that made it, and the servers run their
own loops, so the pool is kept per loop. Each pool holds at most
:data:`WARM_POOL_MAX_CLIENTS` clients; the least recently used one is dropped
when a new one would exceed that.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import weakref
from collections import OrderedDict
from typing import Any

from utils.http.pool_utils import DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

# Seconds an idle connection of a pooled client stays open (httpx default: 5).
# Aligned with DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS (55s), staying below typical
# 60-75s upstream gateway and NAT idle limits while stale-retry handles early drops.
WARM_POOL_KEEPALIVE_SECONDS = DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS
WARM_POOL_MAX_CLIENTS = 8

_pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, OrderedDict[tuple, Any]]" = (
    weakref.WeakKeyDictionary()
)


def _pool_key(model: str, base_url: str, api_key: str, provider_type: str | None, kwargs: dict) -> tuple:
    key_print = hashlib.sha256(str(api_key or "").encode("utf-8")).hexdigest()[:16]
    return (
        str(model or ""),
        str(base_url or ""),
        key_print,
        str(provider_type or ""),
        json.dumps(kwargs, sort_keys=True, default=str),
    )


async def _close_quietly(llm: Any) -> None:
    close = getattr(llm, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:
        logger.debug("[warm_pool] closing an evicted client failed: %s", exc)


async def get_warm_chat_llm(
    model: str,
    base_url: str,
    api_key: str,
    *,
    provider_type: str | None = None,
    **kwargs: Any,
) -> Any:
    """Return a pooled chat client for these settings, making one if needed.

    *kwargs* go to :func:`utils.llm_client.create_chat_llm_async` and are part
    of the pool key. Callers must not close the client; per-call settings
    (``max_completion_tokens``, ``timeout``, ...) go to the invoke call instead.
    """
    from utils import llm_client

    loop = asyncio.get_running_loop()
    pool = _pools.get(loop)
    if pool is None:
        pool = OrderedDict()
        _pools[loop] = pool
    key = _pool_key(model, base_url, api_key, provider_type, kwargs)
    llm = pool.get(key)
    if llm is not None:
        if not getattr(llm, "is_closed", False):
            pool.move_to_end(key)
            return llm
        pool.pop(key, None)

    made = await llm_client.create_chat_llm_async(  # noqa: LLM_OUTPUT_BUDGET  # shared client; callers set the budget and timeout per call.
        model,
        base_url,
        api_key,
        provider_type=provider_type,
        keepalive_expiry=WARM_POOL_KEEPALIVE_SECONDS,
        **kwargs,
    )
    raced = pool.get(key)
    if raced is not None and not getattr(raced, "is_closed", False):
        # Another caller made the same client while this one was being made.
        # Reorder before awaiting: the key may be dropped while the close runs.
        pool.move_to_end(key)
        await _close_quietly(made)
        return raced
    pool[key] = made
    while len(pool) > WARM_POOL_MAX_CLIENTS:
        # Dropped, not closed: a call may still be using it. The client closes
        # its connections when the last reference goes.
        pool.popitem(last=False)
    return made


async def close_warm_chat_llms() -> None:
    """Close and forget every pooled chat and shared LLM HTTP client of the running loop."""
    from .http_pool import aclose_shared_llm_http_clients

    pool = _pools.pop(asyncio.get_running_loop(), None)
    clients = list((pool or {}).values())
    await asyncio.gather(
        *(_close_quietly(llm) for llm in clients),
        aclose_shared_llm_http_clients(),
        return_exceptions=True,
    )
