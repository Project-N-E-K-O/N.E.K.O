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
Shared httpx.AsyncClient singleton dedicated to external HTTPS.

Why it is needed:
  Every `async with httpx.AsyncClient(...)` pays a very high SSLContext
  initialization cost (~150ms cold on Windows, up to 1.1s under event-loop
  pressure). For modules like web_scraper / meme_fetcher / holiday_cache that
  end up hitting the external network frequently, re-creating the client per
  request is both slow and throws away all connection-pool reuse.

Coverage:
  **External secure HTTPS** (verify=True), allowed to read the HTTP_PROXY /
  HTTPS_PROXY environment variables (trust_env=True), follows 30x redirects by
  default (follow_redirects=True). `httpx.AsyncClient` is safe to reuse across
  hosts; the pool buckets keep-alive connections by (scheme, host, port).

Not applicable for:
  - internal 127.0.0.1 services: use `utils/internal_http_client.py`
  - one-off large user downloads (>30MB or >30s): per-call is clearer and avoids hogging the pool
  - scenarios needing special SSL config / custom verify: per-call
  - long-lived TTS streaming: already has a dedicated per-worker client

Concurrency:
  The shared client **does not block concurrency**. `asyncio.gather(client.get(a), client.get(b))`
  works normally; the pool opens multiple TCP connections automatically (default max_connections=100).

Usage:
    from utils.external_http_client import get_external_http_client
    client = get_external_http_client()
    resp = await client.get("https://example.com/api", timeout=10.0)

Call `aclose_external_http_client()` at process shutdown.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None

# 默认超时：多数 scraper / fetcher 调用在 5-10s 区间。调用方可以用
# `client.get(url, timeout=...)` 覆盖单次请求。
_DEFAULT_TIMEOUT = 10.0


def get_external_http_client() -> httpx.AsyncClient:
    """Return the process-wide shared external HTTPS AsyncClient. Lazily initialized on first call.

    Configuration:
      - verify=True (default): normal TLS certificate validation
      - trust_env=True: reads the HTTP(S)_PROXY / NO_PROXY environment variables
      - follow_redirects=True: follows 30x redirects by default
      - timeout=10.0: default request timeout
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            trust_env=True,
            follow_redirects=True,
        )
        logger.debug("[external_http_client] initialized shared AsyncClient")
    return _client


async def aclose_external_http_client() -> None:
    """Call from the FastAPI shutdown hook to release the connection pool."""
    global _client
    if _client is None:
        return
    if not _client.is_closed:
        try:
            await _client.aclose()
            logger.debug("[external_http_client] shared AsyncClient closed")
        except Exception as e:
            logger.debug(f"[external_http_client] close failed: {e}")
    _client = None
