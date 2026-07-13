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

"""
Shared httpx.AsyncClient singleton dedicated to internal 127.0.0.1 services.

Why it is needed:
  Every `async with httpx.AsyncClient(...)` construction makes httpx eagerly
  initialize an SSLContext (reading certifi / the Windows system trust store);
  even for plain http://127.0.0.1 requests this initialization still runs.
  Measured at up to 1.1 s/call on cold start under event-loop pressure, blowing
  the 2-second timeout of `/new_dialog` and showing up as "memory server
  response timeout" (the server actually responded in ~25ms).

Coverage:
  All internal http://127.0.0.1 services — memory_server / agent_server
  (tool_server) / user_plugin_server etc. `httpx.AsyncClient` itself is safe to
  reuse across hosts; the pool buckets keep-alive connections by
  (scheme, host, port) and concurrent use doesn't interfere.

Solution:
  Reuse one AsyncClient per process, with SSL verification explicitly disabled
  (plain http to 127.0.0.1 doesn't need it); the pool reuses TCP connections
  automatically. Subsequent requests only pay the actual network cost.

Usage:
    from utils.internal_http_client import get_internal_http_client
    client = get_internal_http_client()
    resp = await client.get(f"http://127.0.0.1:{PORT}/new_dialog/{name}")

Call `aclose_internal_http_client()` at process shutdown to release the pool.

⚠️ Never use this for external HTTPS: `verify=False` lets a man-in-the-middle
   forge certificates without errors. For external HTTPS use `utils/external_http_client.py`.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import weakref
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_clients_by_loop: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = weakref.WeakKeyDictionary()
_fallback_client: Optional[httpx.AsyncClient] = None
_clients_lock = threading.RLock()

# 默认超时：与历史三个调用点中最宽松的对齐（5s）。调用方可以用
# `client.get(url, timeout=...)` 针对单次请求覆盖。
_DEFAULT_TIMEOUT = 5.0


def get_internal_http_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient dedicated to the current event loop. Lazily initialized on first call.

    `httpx.AsyncClient`'s transport binds to the event loop on first request. The main
    service and sync connector threads each hold their own loop, so clients are
    isolated per loop here, avoiding reuse of one pool across threads/loops.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        global _fallback_client
        with _clients_lock:
            if _fallback_client is None or _fallback_client.is_closed:
                _fallback_client = _create_internal_http_client()
                logger.debug("[internal_http_client] initialized fallback AsyncClient (verify=False)")
            return _fallback_client

    with _clients_lock:
        client = _clients_by_loop.get(loop)
        if client is None or client.is_closed:
            client = _create_internal_http_client()
            _clients_by_loop[loop] = client
            logger.debug("[internal_http_client] initialized loop-local AsyncClient (verify=False)")
        return client


def _create_internal_http_client() -> httpx.AsyncClient:
    """Create a client dedicated to internal 127.0.0.1 services."""
    # verify=False 彻底跳过 SSLContext 初始化 —— 我们只用来访问
    # 127.0.0.1 的内部服务，纯 http，不经过 TLS。
    # trust_env=False 不读 HTTP_PROXY/NO_PROXY 等环境变量。
    transport = httpx.AsyncHTTPTransport(verify=False, retries=0)
    return httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT,
        proxy=None,
        trust_env=False,
        transport=transport,
    )


async def _close_client(client: httpx.AsyncClient, *, context: str) -> None:
    if client.is_closed:
        return
    try:
        await client.aclose()
        logger.debug("[internal_http_client] %s AsyncClient closed", context)
    except Exception as e:
        logger.debug("[internal_http_client] close failed (%s): %s", context, e)


async def aclose_internal_http_client_current_loop() -> None:
    """Close the internal client bound to the current event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    with _clients_lock:
        client = _clients_by_loop.pop(loop, None)
    if client is not None:
        await _close_client(client, context="loop-local")


async def aclose_internal_http_client() -> None:
    """Call from the FastAPI shutdown hook to release the connection pool."""
    global _fallback_client
    with _clients_lock:
        clients = list(_clients_by_loop.items())
        _clients_by_loop.clear()
        fallback_client = _fallback_client
        _fallback_client = None
    for _loop, client in clients:
        await _close_client(client, context="loop-local")

    if fallback_client is not None:
        await _close_client(fallback_client, context="fallback")
