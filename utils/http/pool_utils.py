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

"""Shared HTTP keep-alive, stale-connection retry, and split-timeout helpers.

Keeping idle connections open longer than httpx's 5s default saves TCP + TLS
handshakes between turns, while staying below typical 60-75s upstream gateway
and NAT idle thresholds. When a peer closes a pooled socket right as a new
request is written, httpcore surfaces ``RemoteProtocolError``, ``ReadError``,
or ``WriteError`` instead of retrying. :func:`install_stale_connection_retry`
retries such a request once, only when the request body is replayable and no
response headers have arrived yet.

Passing a bare numeric ``timeout=T`` to httpx or the OpenAI/Anthropic SDKs
sets ``connect``, ``read``, ``write``, and ``pool`` all to ``T``.
:func:`build_split_http_timeout` keeps ``connect`` and ``pool`` short while
preserving the caller's ``read`` ceiling.
"""

from __future__ import annotations

from typing import Any, TypeVar

import httpx

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

# Below the 60-75s idle timeouts common at upstream API gateways, reverse
# proxies, and NAT tables, so clients retire idle sockets cleanly before peers.
DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS: float = 55.0

DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS: float = 10.0
DEFAULT_HTTP_WRITE_TIMEOUT_SECONDS: float = 30.0
DEFAULT_HTTP_POOL_TIMEOUT_SECONDS: float = 10.0

# Errors that indicate a pooled connection died before any response arrived.
STALE_CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
)

_ClientT = TypeVar("_ClientT")


def build_split_http_timeout(
    timeout: float | int | httpx.Timeout | None,
    *,
    connect: float = DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
    write: float = DEFAULT_HTTP_WRITE_TIMEOUT_SECONDS,
    pool: float = DEFAULT_HTTP_POOL_TIMEOUT_SECONDS,
) -> httpx.Timeout | Any | None:
    """Convert a bare numeric timeout into a split ``httpx.Timeout``.

    ``None`` and existing ``httpx.Timeout`` instances pass through unchanged.
    Non-numeric SDK sentinels also pass through unchanged. Numeric values cap
    ``connect``, ``write``, and ``pool`` while keeping ``read`` at the caller's
    configured upper bound (never enlarging any short timeout).
    """
    if timeout is None or isinstance(timeout, httpx.Timeout):
        return timeout
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        return timeout
    read_s = float(timeout)
    if read_s <= 0:
        return httpx.Timeout(read_s)
    return httpx.Timeout(
        connect=min(float(connect), read_s),
        read=read_s,
        write=min(float(write), read_s),
        pool=min(float(pool), read_s),
    )


def _wrap_async_transport(transport: Any, *, label: str) -> None:
    original = getattr(transport, "handle_async_request", None)
    if original is None or getattr(original, "_neko_stale_retry", False):
        return

    async def handle_async_request(request: httpx.Request) -> httpx.Response:
        # Decide before sending: reading a streamed body may swap it for a
        # buffered copy, but what went out once cannot be generated again.
        replayable = isinstance(request.stream, httpx.ByteStream)
        # Returns once the response headers are in; the body streams later,
        # so nothing below retries after any response data arrived.
        try:
            return await original(request)
        except STALE_CONNECTION_ERRORS as exc:
            if not replayable:
                raise
            logger.info(
                "%s 请求所用连接已被对端断开（%s），重试一次: host=%s",
                label,
                type(exc).__name__,
                request.url.host,
            )
            return await original(request)

    handle_async_request._neko_stale_retry = True  # type: ignore[attr-defined]
    transport.handle_async_request = handle_async_request


def _wrap_sync_transport(transport: Any, *, label: str) -> None:
    original = getattr(transport, "handle_request", None)
    if original is None or getattr(original, "_neko_stale_retry", False):
        return

    def handle_request(request: httpx.Request) -> httpx.Response:
        replayable = isinstance(request.stream, httpx.ByteStream)
        try:
            return original(request)
        except STALE_CONNECTION_ERRORS as exc:
            if not replayable:
                raise
            logger.info(
                "%s 请求所用连接已被对端断开（%s），重试一次: host=%s",
                label,
                type(exc).__name__,
                request.url.host,
            )
            return original(request)

    handle_request._neko_stale_retry = True  # type: ignore[attr-defined]
    transport.handle_request = handle_request


def install_stale_connection_retry(client: _ClientT, *, label: str = "HTTP") -> _ClientT:
    """Retry a replayable request once when its pooled connection is dead.

    Wraps the client's default transport and any proxy mounts (both async and
    sync handlers when present), below event hooks, auth, and redirects, so a
    retry resends the exact same request. Safe to call on test fakes lacking
    ``_transport`` or ``_mounts`` and idempotent across repeated calls.
    """
    transports = [getattr(client, "_transport", None)]
    transports.extend((getattr(client, "_mounts", None) or {}).values())
    for transport in transports:
        if transport is not None:
            _wrap_async_transport(transport, label=label)
            _wrap_sync_transport(transport, label=label)
    return client
