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
from collections.abc import Iterable
import logging
import os
import threading
import weakref
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_clients_by_loop: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = weakref.WeakKeyDictionary()
_fallback_client: Optional[httpx.AsyncClient] = None
_clients_lock = threading.RLock()

# Default timeout matches the most permissive of the three historical call sites (5s). Callers may
# override individual requests using client.get(url, timeout=...).
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


class InternalServiceSigningError(httpx.RequestError):
    """Raised when signing an internal loopback service request fails before transmission."""


def _httpx_signing_path(request: httpx.Request) -> str:
    """Return the raw path component (excluding query string) from an httpx.Request."""
    raw = request.url.raw_path
    if isinstance(raw, (bytes, bytearray)):
        path = bytes(raw).split(b"?", 1)[0].decode("latin-1")
    else:
        path = str(raw).split("?", 1)[0]
    return path or (request.url.path or "/")


def _httpx_signing_port(request: httpx.Request) -> int | None:
    """Return the target TCP port for an httpx.Request."""
    if request.url.port is not None:
        return int(request.url.port)
    scheme = (request.url.scheme or "").lower()
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _trusted_internal_service_ports() -> set[int]:
    """Return configured and launcher-allocated loopback ports for N.E.K.O internal services.

    Note: only ports visible via shared config constants, NEKO_USER_PLUGIN_SERVER_PORT,
    or USER_PLUGIN_BASE are trusted here; a standalone service re-binding to an ad-hoc
    fallback port inside its own process only updates that process and cannot be observed
    by peer processes without launcher coordination.
    """
    from urllib.parse import urlsplit
    import config

    ports: set[int] = set()
    for attr in (
        "MAIN_SERVER_PORT",
        "MEMORY_SERVER_PORT",
        "TOOL_SERVER_PORT",
        "USER_PLUGIN_SERVER_PORT",
    ):
        val = getattr(config, attr, None)
        if isinstance(val, int) and not isinstance(val, bool) and 0 < val <= 65535:
            ports.add(val)

    raw_plugin_port = os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "").strip()
    if raw_plugin_port:
        try:
            parsed_port = int(raw_plugin_port)
            if 0 < parsed_port <= 65535:
                ports.add(parsed_port)
        except ValueError:
            pass

    base_url = str(getattr(config, "USER_PLUGIN_BASE", "") or "").strip()
    if base_url:
        try:
            parsed_base = urlsplit(base_url)
            if parsed_base.port is not None and 0 < parsed_base.port <= 65535:
                ports.add(int(parsed_base.port))
        except ValueError:
            pass
    return ports


def _normalize_extra_trusted_ports(extra_trusted_ports: Iterable[int] | None) -> frozenset[int]:
    """Validate optional per-client trusted loopback ports into a ``frozenset[int]``."""
    if extra_trusted_ports is None:
        return frozenset()
    return frozenset(
        port
        for port in extra_trusted_ports
        if isinstance(port, int) and not isinstance(port, bool) and 0 < port <= 65535
    )


def _should_attach_internal_service_proof(
    request: httpx.Request,
    *,
    extra_trusted_ports: frozenset[int] = frozenset(),
) -> bool:
    """Return True when a request targets a trusted loopback internal service under proxy/remote mode."""
    from utils.deployment import is_behind_proxy, is_remote_backend_deployment
    from utils.instance_access import INTERNAL_SERVICE_HEADER

    if not (is_behind_proxy() or is_remote_backend_deployment()):
        return False
    if INTERNAL_SERVICE_HEADER in request.headers:
        return False
    if (request.url.scheme or "").lower() not in {"http", "https"}:
        return False
    host = (request.url.host or "").strip().strip("[]").lower()
    if not host:
        return False
    if host != "localhost":
        import ipaddress

        try:
            ip = ipaddress.ip_address(host)
            if not bool((getattr(ip, "ipv4_mapped", None) or ip).is_loopback):
                return False
        except ValueError:
            return False
    port = _httpx_signing_port(request)
    return port is not None and (port in _trusted_internal_service_ports() or port in extra_trusted_ports)


async def attach_internal_service_proof(
    request: httpx.Request,
    *,
    extra_trusted_ports: frozenset[int] = frozenset(),
) -> None:
    """Attach port-bound signed internal-service header on loopback hops when proxy/remote mode is active."""
    if not _should_attach_internal_service_proof(request, extra_trusted_ports=extra_trusted_ports):
        return
    from filelock import Timeout as FileLockTimeout
    from utils.instance_access import INTERNAL_SERVICE_HEADER, instance_key, internal_service_proof

    try:
        key = await asyncio.to_thread(instance_key)
        port = _httpx_signing_port(request)
        if port is None:
            raise ValueError("missing internal service target port")
        request.headers[INTERNAL_SERVICE_HEADER] = internal_service_proof(
            key, request.method, _httpx_signing_path(request), port=port
        )
    except (OSError, ValueError, FileLockTimeout) as exc:
        raise InternalServiceSigningError(
            f"failed to sign internal service request: {type(exc).__name__}",
            request=request,
        ) from exc


_attach_internal_service_proof = attach_internal_service_proof


def attach_internal_service_proof_sync(
    request: httpx.Request,
    *,
    extra_trusted_ports: frozenset[int] = frozenset(),
) -> None:
    """Sync variant of attach_internal_service_proof for blocking httpx.Client callers."""
    if not _should_attach_internal_service_proof(request, extra_trusted_ports=extra_trusted_ports):
        return
    from filelock import Timeout as FileLockTimeout
    from utils.instance_access import INTERNAL_SERVICE_HEADER, instance_key, internal_service_proof

    try:
        key = instance_key()
        port = _httpx_signing_port(request)
        if port is None:
            raise ValueError("missing internal service target port")
        request.headers[INTERNAL_SERVICE_HEADER] = internal_service_proof(
            key, request.method, _httpx_signing_path(request), port=port
        )
    except (OSError, ValueError, FileLockTimeout) as exc:
        raise InternalServiceSigningError(
            f"failed to sign internal service request: {type(exc).__name__}",
            request=request,
        ) from exc


def _build_internal_service_request_hook(*, sync: bool, extra_trusted_ports: frozenset[int]):
    if not extra_trusted_ports:
        return attach_internal_service_proof_sync if sync else attach_internal_service_proof
    if sync:
        def _sync_hook(request: httpx.Request) -> None:
            attach_internal_service_proof_sync(request, extra_trusted_ports=extra_trusted_ports)

        return _sync_hook

    async def _async_hook(request: httpx.Request) -> None:
        await attach_internal_service_proof(request, extra_trusted_ports=extra_trusted_ports)

    return _async_hook


def internal_service_event_hooks(
    *,
    sync: bool = False,
    extra_trusted_ports: Iterable[int] | None = None,
) -> dict[str, list]:
    """Return an httpx event_hooks mapping that signs loopback internal requests."""
    normalized_extra = _normalize_extra_trusted_ports(extra_trusted_ports)
    hook = _build_internal_service_request_hook(sync=sync, extra_trusted_ports=normalized_extra)
    return {"request": [hook]}


def merge_internal_service_event_hooks(
    event_hooks: dict | None = None,
    *,
    sync: bool = False,
    extra_trusted_ports: Iterable[int] | None = None,
) -> dict[str, list]:
    """Return a copy of event_hooks with the internal-service request hook appended."""
    normalized_extra = _normalize_extra_trusted_ports(extra_trusted_ports)
    hook = _build_internal_service_request_hook(sync=sync, extra_trusted_ports=normalized_extra)
    merged: dict[str, list] = {
        key: list(value) for key, value in (event_hooks or {}).items()
    }
    request_hooks = merged.setdefault("request", [])
    if hook not in request_hooks:
        request_hooks.append(hook)
    return merged


def _create_internal_http_client() -> httpx.AsyncClient:
    """Create a client dedicated to internal 127.0.0.1 services."""
    # verify=False skips SSLContext initialization entirely: we access only
    # internal 127.0.0.1 services over plain HTTP, without TLS.
    # trust_env=False ignores HTTP_PROXY/NO_PROXY and similar environment variables.
    transport = httpx.AsyncHTTPTransport(verify=False, retries=0)
    return httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT,
        proxy=None,
        trust_env=False,
        transport=transport,
        event_hooks=internal_service_event_hooks(),
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
