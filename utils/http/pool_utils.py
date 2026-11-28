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

import asyncio
import hashlib
import http.cookiejar
import inspect
import ipaddress
import os
import ssl
import time
import urllib.request
import weakref
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx

from utils.http import PROXY_ENV_LOCK
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

# Event loops owned by a multi-service host (such as run_merged_servers) where
# per-service shutdown hooks must leave shared pool cleanup to the loop owner.
_DEFERRED_POOL_CLEANUP_LOOPS: "weakref.WeakSet[asyncio.AbstractEventLoop]" = weakref.WeakSet()


def defer_loop_pool_cleanup(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Mark *loop* so per-service shutdown hooks defer shared pool closing to the loop owner."""
    target = loop if loop is not None else asyncio.get_running_loop()
    _DEFERRED_POOL_CLEANUP_LOOPS.add(target)


def is_loop_pool_cleanup_deferred(loop: asyncio.AbstractEventLoop | None = None) -> bool:
    """Return True when a shared loop owner is responsible for closing pooled HTTP clients."""
    target = loop if loop is not None else asyncio.get_running_loop()
    return target in _DEFERRED_POOL_CLEANUP_LOOPS


def release_deferred_loop_pool_cleanup(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Clear the deferred-cleanup marker for *loop* before its owner closes shared pools."""
    target = loop if loop is not None else asyncio.get_running_loop()
    _DEFERRED_POOL_CLEANUP_LOOPS.discard(target)

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

# Maximum elapsed time after sending a request for a transport disconnect to be
# treated as a dead idle keep-alive socket rather than an in-flight upstream
# drop while the server is already processing / billing a long LLM request.
STALE_RETRY_MAX_ELAPSED_SECONDS: float = 2.0

# httpcore trace events emitted whenever a request opens a brand-new TCP/UDS/TLS
# connection rather than reusing an idle pooled keep-alive socket.
_FRESH_CONNECTION_TRACE_SUFFIXES: tuple[str, ...] = (
    ".connect_tcp.started",
    ".connect_unix_socket.started",
    ".start_tls.started",
)
SEND_REQUEST_HEADERS_TRACE_SUFFIX: str = ".send_request_headers.started"
_SEND_REQUEST_HEADERS_TRACE_SUFFIX: str = SEND_REQUEST_HEADERS_TRACE_SUFFIX


def is_fresh_connection_trace(event_name: str) -> bool:
    """Return True when an httpcore trace event marks a brand-new connection."""
    return event_name.endswith(_FRESH_CONNECTION_TRACE_SUFFIXES)


_is_fresh_connection_trace = is_fresh_connection_trace


_ClientT = TypeVar("_ClientT")


def build_split_http_timeout(
    timeout: float | int | httpx.Timeout | None,
    *,
    connect: float = DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
    write: float = DEFAULT_HTTP_WRITE_TIMEOUT_SECONDS,
    pool: float = DEFAULT_HTTP_POOL_TIMEOUT_SECONDS,
) -> httpx.Timeout | Any | None:
    """Convert a bare numeric timeout into a split ``httpx.Timeout``.

    ``None``, ``False``, non-positive numbers, and existing ``httpx.Timeout``
    instances are handled safely: ``None``, ``False``, and numeric values
    ``<= 0`` return ``None`` (allowing fallback to ``request_timeout`` or the
    SDK default), existing ``httpx.Timeout`` and non-numeric SDK sentinels pass
    through unchanged, and positive numbers cap ``connect``, ``write``, and
    ``pool`` while keeping ``read`` at the caller's configured upper bound.
    """
    if timeout is None or timeout is False:
        return None
    if isinstance(timeout, httpx.Timeout):
        return timeout
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        return timeout
    read_s = float(timeout)
    if read_s <= 0:
        return None
    return httpx.Timeout(
        connect=min(float(connect), read_s),
        read=read_s,
        write=min(float(write), read_s),
        pool=min(float(pool), read_s),
    )


def _wrap_async_transport(
    transport: Any,
    *,
    label: str,
    max_elapsed_seconds: float = STALE_RETRY_MAX_ELAPSED_SECONDS,
) -> None:
    original = getattr(transport, "handle_async_request", None)
    if original is None or getattr(original, "_neko_stale_retry", False):
        return

    async def handle_async_request(request: httpx.Request) -> httpx.Response:
        # Decide before sending: reading a streamed body may swap it for a
        # buffered copy, but what went out once cannot be generated again.
        replayable = isinstance(request.stream, httpx.ByteStream)
        orig_extensions = getattr(request, "extensions", None)
        track_extensions = isinstance(orig_extensions, dict)
        prev_trace = orig_extensions.get("trace") if track_extensions else None
        opened_fresh_connection = False
        send_started: float | None = None

        if track_extensions:
            async def _track_trace(event_name: str, info: dict[str, Any]) -> None:
                nonlocal opened_fresh_connection, send_started
                if _is_fresh_connection_trace(event_name):
                    opened_fresh_connection = True
                elif send_started is None and event_name.endswith(_SEND_REQUEST_HEADERS_TRACE_SUFFIX):
                    send_started = time.monotonic()
                if prev_trace is not None:
                    res = prev_trace(event_name, info)
                    if inspect.isawaitable(res):
                        await res

            request.extensions = {**orig_extensions, "trace": _track_trace}

        started = time.monotonic()
        # Returns once the response headers are in; the body streams later,
        # so nothing below retries after any response data arrived.
        try:
            return await original(request)
        except STALE_CONNECTION_ERRORS as exc:
            elapsed = time.monotonic() - (send_started if send_started is not None else started)
            if (
                not replayable
                or opened_fresh_connection
                or elapsed > max_elapsed_seconds
            ):
                raise
            if track_extensions:
                request.extensions = orig_extensions
            logger.info(
                "%s 请求所用连接已被对端断开（%s，耗时 %.3fs），重试一次: host=%s",
                label,
                type(exc).__name__,
                elapsed,
                request.url.host,
            )
            return await original(request)
        finally:
            if track_extensions:
                request.extensions = orig_extensions

    handle_async_request._neko_stale_retry = True  # type: ignore[attr-defined]
    transport.handle_async_request = handle_async_request


def _wrap_sync_transport(
    transport: Any,
    *,
    label: str,
    max_elapsed_seconds: float = STALE_RETRY_MAX_ELAPSED_SECONDS,
) -> None:
    original = getattr(transport, "handle_request", None)
    if original is None or getattr(original, "_neko_stale_retry", False):
        return

    def handle_request(request: httpx.Request) -> httpx.Response:
        replayable = isinstance(request.stream, httpx.ByteStream)
        orig_extensions = getattr(request, "extensions", None)
        track_extensions = isinstance(orig_extensions, dict)
        prev_trace = orig_extensions.get("trace") if track_extensions else None
        opened_fresh_connection = False
        send_started: float | None = None

        if track_extensions:
            def _track_trace(event_name: str, info: dict[str, Any]) -> None:
                nonlocal opened_fresh_connection, send_started
                if _is_fresh_connection_trace(event_name):
                    opened_fresh_connection = True
                elif send_started is None and event_name.endswith(_SEND_REQUEST_HEADERS_TRACE_SUFFIX):
                    send_started = time.monotonic()
                if prev_trace is not None:
                    prev_trace(event_name, info)

            request.extensions = {**orig_extensions, "trace": _track_trace}

        started = time.monotonic()
        try:
            return original(request)
        except STALE_CONNECTION_ERRORS as exc:
            elapsed = time.monotonic() - (send_started if send_started is not None else started)
            if (
                not replayable
                or opened_fresh_connection
                or elapsed > max_elapsed_seconds
            ):
                raise
            if track_extensions:
                request.extensions = orig_extensions
            logger.info(
                "%s 请求所用连接已被对端断开（%s，耗时 %.3fs），重试一次: host=%s",
                label,
                type(exc).__name__,
                elapsed,
                request.url.host,
            )
            return original(request)
        finally:
            if track_extensions:
                request.extensions = orig_extensions

    handle_request._neko_stale_retry = True  # type: ignore[attr-defined]
    transport.handle_request = handle_request


def install_stale_connection_retry(
    client: _ClientT,
    *,
    label: str = "HTTP",
    max_elapsed_seconds: float = STALE_RETRY_MAX_ELAPSED_SECONDS,
) -> _ClientT:
    """Retry a replayable request once when a reused pooled connection is dead.

    Wraps the client's default transport and any proxy mounts (both async and
    sync handlers when present), below event hooks, auth, and redirects, so a
    retry resends the exact same request. Retries are skipped whenever httpcore
    trace events show that a fresh TCP/UDS/TLS connection was opened for this
    request or when the failure occurs more than *max_elapsed_seconds* after
    sending, preventing duplicate retries on fresh connections or mid-generation
    drops. Safe to call on test fakes lacking ``_transport`` or ``_mounts`` and
    idempotent across repeated calls.
    """
    transports = [getattr(client, "_transport", None)]
    transports.extend((getattr(client, "_mounts", None) or {}).values())
    for transport in transports:
        if transport is not None:
            _wrap_async_transport(
                transport,
                label=label,
                max_elapsed_seconds=max_elapsed_seconds,
            )
            _wrap_sync_transport(
                transport,
                label=label,
                max_elapsed_seconds=max_elapsed_seconds,
            )
    return client


_PROXY_ENV_KEYS: tuple[str, ...] = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def http_origin_key(url: str | None) -> tuple[str, str, int | None]:
    """Extract a normalized ``(scheme, host, port)`` tuple for HTTP pool keying."""
    raw = str(url or "").strip()
    if not raw:
        return ("", "", None)
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is None:
        if scheme == "https":
            port = 443
        elif scheme == "http":
            port = 80
    return (scheme, host, port)


_PROXY_ENV_NAMES_LOWER: frozenset[str] = frozenset(
    {
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
)


def proxy_env_fingerprint() -> tuple[str | None, ...]:
    """Snapshot proxy environment variables, preserving unset (None) vs empty string."""
    with PROXY_ENV_LOCK:
        return tuple(os.environ.get(name) for name in _PROXY_ENV_KEYS)


class _RejectAllCookiePolicy(http.cookiejar.DefaultCookiePolicy):
    """Reject storing and sending cookies so pooled HTTP clients stay stateless."""

    def set_ok(self, cookie: Any, request: Any) -> bool:
        return False

    def return_ok(self, cookie: Any, request: Any) -> bool:
        return False


def make_stateless_cookie_jar() -> http.cookiejar.CookieJar:
    """Return a CookieJar that neither stores Set-Cookie headers nor sends cookies."""
    return http.cookiejar.CookieJar(policy=_RejectAllCookiePolicy())


def create_default_ssl_context() -> ssl.SSLContext:
    """Create a default TLS context honoring SSL_CERT_FILE / SSL_CERT_DIR / certifi."""
    import certifi

    if os.environ.get("SSL_CERT_FILE"):
        return ssl.create_default_context(cafile=os.environ["SSL_CERT_FILE"])
    if os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context(capath=os.environ["SSL_CERT_DIR"])
    return ssl.create_default_context(cafile=certifi.where())


def _proxies_from_env_items(
    env_items: list[tuple[str, str]],
    *,
    has_request_method: bool = False,
) -> dict[str, str]:
    """Replicate urllib.request.getproxies_environment() on an atomic env snapshot."""
    proxies: dict[str, str] = {}
    for name, value in env_items:
        lowered = name.lower()
        if value and lowered.endswith("_proxy"):
            proxies[lowered[:-6]] = value
    if has_request_method:
        proxies.pop("http", None)
    for name, value in env_items:
        if name.islower() and name.endswith("_proxy"):
            scheme = name[:-6]
            if value:
                proxies[scheme] = value
            else:
                proxies.pop(scheme, None)
    return proxies


def _proxy_mount_map_from_proxies(proxy_info: dict[str, str]) -> dict[str, str | None]:
    """Convert a scheme->proxy dict into httpx-compatible mount pattern -> proxy URL map."""
    mounts: dict[str, str | None] = {}
    for scheme in ("http", "https", "all"):
        raw = proxy_info.get(scheme)
        if raw:
            mounts[f"{scheme}://"] = raw if "://" in raw else f"http://{raw}"
    no_proxy_hosts = [host.strip() for host in proxy_info.get("no", "").split(",")]
    for hostname in no_proxy_hosts:
        if hostname == "*":
            return {}
        if not hostname:
            continue
        if "://" in hostname:
            mounts[hostname] = None
        else:
            try:
                addr = ipaddress.ip_address(hostname.strip("[]"))
            except ValueError:
                if hostname.lower() == "localhost":
                    mounts[f"all://{hostname}"] = None
                else:
                    mounts[f"all://*{hostname}"] = None
            else:
                if addr.version == 6:
                    mounts[f"all://[{addr}]"] = None
                else:
                    mounts[f"all://{addr}"] = None
    return mounts


def snapshot_http_proxy_config() -> tuple[tuple[Any, ...], dict[str, str | None]]:
    """Atomically capture proxy pool-key fingerprint and httpx mount map from one snapshot."""
    with PROXY_ENV_LOCK:
        env_items = sorted(
            (name, value)
            for name, value in os.environ.items()
            if name.lower() in _PROXY_ENV_NAMES_LOWER
        )
        has_request_method = "REQUEST_METHOD" in os.environ
    proxy_info = _proxies_from_env_items(env_items, has_request_method=has_request_method)
    if not proxy_info:
        fallback = (
            getattr(urllib.request, "getproxies_registry", None)
            or getattr(urllib.request, "getproxies_macosx_sysconf", None)
        )
        if callable(fallback):
            try:
                proxy_info = dict(fallback())
            except Exception:
                # Best-effort OS proxy lookup; fall back to direct connections.
                proxy_info = {}
    mount_map = _proxy_mount_map_from_proxies(proxy_info)
    fingerprint = (tuple(env_items), tuple(sorted(mount_map.items())))
    return fingerprint, mount_map


def build_async_proxy_mounts(
    mount_map: dict[str, str | None],
    *,
    verify: Any = True,
    limits: httpx.Limits | None = None,
) -> dict[str, httpx.AsyncHTTPTransport | None]:
    """Build explicit httpx AsyncHTTPTransport mounts from a snapshotted mount map."""
    mounts: dict[str, httpx.AsyncHTTPTransport | None] = {}
    for pattern, proxy_url in mount_map.items():
        if proxy_url is None:
            mounts[pattern] = None
        else:
            transport_kw: dict[str, Any] = {
                "verify": verify,
                "proxy": httpx.Proxy(url=proxy_url),
                "trust_env": False,
            }
            if limits is not None:
                transport_kw["limits"] = limits
            mounts[pattern] = httpx.AsyncHTTPTransport(**transport_kw)
    return mounts


def credential_fingerprint(*parts: str | None) -> str:
    """Hash credential components for pool isolation without retaining raw secrets."""
    joined = "\x00".join(str(part or "") for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
