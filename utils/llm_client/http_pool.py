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

"""Loop-scoped shared httpx.AsyncClient pool for one-shot async LLM clients.

One-shot callers throughout the codebase construct a chat client via
``create_chat_llm_async(...)`` and call ``await llm.aclose()`` right after a
single request. OpenAI and Anthropic SDK clients resolve request URLs, headers,
credentials, and timeouts per request without mutating their underlying
``httpx.AsyncClient``, so borrowing a shared ``httpx.AsyncClient`` keyed by
``(event_loop, family, origin, credential_fingerprint, proxy_env, ssl_context)``
reuses warm TCP + TLS connections transparently across calls while keeping
cookies, credentials, and proxy settings strictly isolated.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
import weakref
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any

from utils.http.pool_utils import (
    DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS,
    credential_fingerprint,
    http_origin_key,
    install_stale_connection_retry,
    proxy_env_fingerprint,
)
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

SHARED_LLM_HTTP_POOL_KEEPALIVE_SECONDS: float = DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS
SHARED_LLM_HTTP_POOL_MAX_CLIENTS: int = 16

_SHARED_HTTP_POOL_LOOP: contextvars.ContextVar[asyncio.AbstractEventLoop | None] = (
    contextvars.ContextVar("_neko_llm_shared_http_pool_loop", default=None)
)

_POOL_LOCK = threading.Lock()
_http_pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, OrderedDict[tuple, Any]]" = (
    weakref.WeakKeyDictionary()
)
_PENDING_CLOSE_TASKS: set[asyncio.Task[None]] = set()


def get_active_shared_http_pool_loop() -> asyncio.AbstractEventLoop | None:
    """Return the event loop armed by ``create_chat_llm_async``, or ``None``."""
    return _SHARED_HTTP_POOL_LOOP.get()


def set_active_shared_http_pool_loop(
    loop: asyncio.AbstractEventLoop | None,
) -> contextvars.Token[asyncio.AbstractEventLoop | None]:
    """Arm *loop* on the current context so worker-thread client builds borrow its HTTP pool."""
    return _SHARED_HTTP_POOL_LOOP.set(loop)


def reset_active_shared_http_pool_loop(
    token: contextvars.Token[asyncio.AbstractEventLoop | None],
) -> None:
    """Restore the previous shared HTTP pool loop context."""
    _SHARED_HTTP_POOL_LOOP.reset(token)


def _credential_header_values(default_headers: Mapping[str, Any] | None) -> tuple[str, str]:
    if not isinstance(default_headers, Mapping):
        return ("", "")
    auth = ""
    api_key_hdr = ""
    for key, value in default_headers.items():
        lowered = str(key).lower()
        if lowered == "authorization":
            auth = str(value or "")
        elif lowered == "x-api-key":
            api_key_hdr = str(value or "")
    return (auth, api_key_hdr)


def _http_pool_key(
    *,
    family: str,
    base_url: str | None,
    api_key: str | None,
    default_headers: Mapping[str, Any] | None,
    ssl_context: Any,
) -> tuple:
    auth_hdr, api_key_hdr = _credential_header_values(default_headers)
    return (
        str(family or ""),
        http_origin_key(base_url),
        credential_fingerprint(api_key, auth_hdr, api_key_hdr),
        proxy_env_fingerprint(),
        id(ssl_context),
    )


async def _close_quietly(client: Any) -> None:
    close = getattr(client, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:
        logger.debug("[llm_http_pool] closing shared HTTP client failed: %s", exc)


def _schedule_close_on_loop(loop: asyncio.AbstractEventLoop, client: Any) -> None:
    if loop.is_closed():
        return

    def _spawn() -> None:
        if loop.is_closed():
            return
        coro = _close_quietly(client)
        try:
            task = loop.create_task(coro)
        except Exception:
            coro.close()
        else:
            _PENDING_CLOSE_TASKS.add(task)
            task.add_done_callback(_PENDING_CLOSE_TASKS.discard)

    try:
        loop.call_soon_threadsafe(_spawn)
    except RuntimeError:
        # Loop closed between check and callback scheduling; nothing to drain.
        pass


def get_shared_async_http_client(
    loop: asyncio.AbstractEventLoop,
    *,
    family: str,
    base_url: str | None,
    api_key: str | None,
    default_headers: Mapping[str, Any] | None,
    ssl_context: Any,
    make_client: Callable[[], Any],
    label: str = "LLM HTTP",
) -> Any | None:
    """Return a pooled ``httpx.AsyncClient`` for *loop*, creating one if needed.

    Safe to call from worker threads (such as ``asyncio.to_thread`` inside
    ``create_chat_llm_async``). Returns ``None`` if *loop* is already closed.
    """
    if loop.is_closed():
        return None

    key = _http_pool_key(
        family=family,
        base_url=base_url,
        api_key=api_key,
        default_headers=default_headers,
        ssl_context=ssl_context,
    )

    with _POOL_LOCK:
        pool = _http_pools.get(loop)
        if pool is not None:
            existing = pool.get(key)
            if existing is not None:
                if not getattr(existing, "is_closed", False):
                    pool.move_to_end(key)
                    return existing
                pool.pop(key, None)

    made = install_stale_connection_retry(make_client(), label=label)

    loser = None
    with _POOL_LOCK:
        if loop.is_closed():
            return None
        pool = _http_pools.get(loop)
        if pool is None:
            pool = OrderedDict()
            _http_pools[loop] = pool
        raced = pool.get(key)
        if raced is not None and not getattr(raced, "is_closed", False):
            pool.move_to_end(key)
            loser = made
            chosen = raced
        else:
            pool[key] = made
            while len(pool) > SHARED_LLM_HTTP_POOL_MAX_CLIENTS:
                # Dropped without force-closing: an in-flight call may still use it.
                pool.popitem(last=False)
            chosen = made

    if loser is not None:
        _schedule_close_on_loop(loop, loser)
    return chosen


async def aclose_shared_llm_http_clients() -> None:
    """Concurrently close and forget every shared LLM HTTP client on the running loop."""
    loop = asyncio.get_running_loop()
    with _POOL_LOCK:
        pool = _http_pools.pop(loop, None)
    clients = list((pool or {}).values())
    if not clients:
        return
    await asyncio.gather(*(_close_quietly(client) for client in clients), return_exceptions=True)
