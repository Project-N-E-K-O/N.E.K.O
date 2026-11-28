"""Internal model execution against one already-resolved plugin slot.

The authenticated gateway resolves a binding before invoking this service.
Execution policy owns total deadlines, fallback and accounting around this
single-attempt boundary; observations only retain the latest upstream counters.
"""
from __future__ import annotations

import asyncio
import json
import weakref
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import anyio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

from plugin.server.domain.model_config import ModelSlot
from plugin.server.infrastructure.host_model_slots import adapt_host_follow_request, client_headers
from plugin.server.model_gateway import anthropic, openai
from plugin.server.model_gateway.errors import ModelGatewayError, upstream_error
from plugin.server.model_gateway.observation import AttemptObservation
from plugin.server.model_gateway.request import prepare_chat_request
from plugin.server.model_gateway.transport import decode_object, encode_sse, iter_sse_data, read_json_response
from utils.http_client import ensure_user_agent
from plugin.utils.http_imports import ensure_httpx, load_httpx


MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_ERROR_USAGE_BYTES = 64 * 1024


def _endpoint(slot: ModelSlot) -> str:
    base = slot.base_url.rstrip("/")
    if slot.protocol == "openai_chat":
        return base + "/chat/completions"
    # Anthropic's SDK uses /v1/messages; accept a base with or without /v1.
    return base + ("/messages" if base.endswith("/v1") else "/v1/messages")


def _headers(slot: ModelSlot) -> dict[str, str]:
    try:
        slot.api_key.encode("ascii")
    except UnicodeError as exc:
        raise ModelGatewayError("invalid_model_configuration", "Configured API key is not a valid HTTP credential", 500) from exc
    headers = ensure_user_agent({"Accept": "application/json, text/event-stream", "Content-Type": "application/json"})
    if slot.protocol == "anthropic_messages":
        headers["anthropic-version"] = "2023-06-01"
        headers.update(client_headers(slot.protocol, slot.base_url))
        if slot.api_key:
            headers["x-api-key"] = slot.api_key
    elif slot.api_key:
        headers["Authorization"] = "Bearer " + slot.api_key
    return headers


def _check_status(response: httpx.Response) -> None:
    if not response.is_success:
        raise upstream_error(response.status_code)


async def _observe_error_usage(response: httpx.Response, slot: ModelSlot, observation: AttemptObservation) -> None:
    """Best-effort diagnostics must not replace the original HTTP status error."""
    httpx = await ensure_httpx()
    try:
        # Error bodies are optional diagnostics: bound both their bytes and the
        # wait, while preserving cancellation from the enclosing request policy.
        with anyio.move_on_after(1):
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > MAX_ERROR_USAGE_BYTES:
                    return
                data.extend(chunk)
            body = decode_object(bytes(data))
            observation.observe(body.get("usage"), protocol=slot.protocol, reported=True)
    except (httpx.HTTPError, ModelGatewayError, ValueError):
        # Missing or unreadable usage diagnostics must preserve the upstream
        # HTTP status error that the caller will report.
        pass


def _observe_stream_usage(converter, observation: AttemptObservation | None, *, reported: bool = False) -> None:
    if observation is None:
        return
    try:
        observation.observe(converter.usage, reported=reported)
    except ModelGatewayError:
        # Anthropic validates its cumulative usage on access. An invalid update
        # must not erase the last known counters or hide the original failure.
        pass


def _prepare(
    slot: ModelSlot, body: object, *, streaming: bool, inject_stream_usage: bool = True,
) -> tuple[str, bytes, dict, bool]:
    """Validate, convert and encode once, off the HTTP event loop."""
    httpx = load_httpx()
    request = prepare_chat_request(slot, body)
    # Host-follow slots send what the main app sends for the same model.
    request, provider_headers = adapt_host_follow_request(slot, request)
    try:
        httpx.URL(_endpoint(slot))
    except (httpx.InvalidURL, ValueError, UnicodeError) as exc:
        raise ModelGatewayError("invalid_model_configuration", "Configured model endpoint is not a valid HTTP URL", 500) from exc
    if bool(request.get("stream")) != streaming:
        raise ModelGatewayError("invalid_request", "Request stream flag does not match the execution path", param="stream")
    include_usage = request.get("stream_options", {}).get("include_usage", False)
    if slot.protocol == "anthropic_messages":
        payload = anthropic.prepare_request(request)
    elif streaming and (inject_stream_usage or include_usage):
        payload = {**request, "stream_options": {"include_usage": True}}
    elif streaming:
        payload = {key: value for key, value in request.items() if key != "stream_options"}
    else:
        payload = request
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ModelGatewayError("invalid_request", "Request is not valid UTF-8 JSON") from exc
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ModelGatewayError("request_too_large", "Model request exceeded the size limit", 413)
    headers = _headers(slot)
    for name, value in provider_headers.items():
        # Provider headers never replace credentials or the content headers.
        if name.lower() not in {key.lower() for key in headers}:
            headers[name] = value
    return body["model"], encoded, headers, include_usage


class _PooledClientEntry:
    """Reference-counted pooled client so evicted clients drain and close cleanly."""

    __slots__ = ("client", "in_flight", "evicted")

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.in_flight = 0
        self.evicted = False


class ModelGatewayService:
    POOL_MAX_CLIENTS = 16

    def __init__(self, client_factory: Callable[[ModelSlot], httpx.AsyncClient] | None = None):
        self._custom_client_factory = client_factory
        self._client_factory = client_factory or self._make_client
        # Loop-scoped warm HTTP clients used when no custom factory is injected.
        self._pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, OrderedDict[tuple, _PooledClientEntry]]" = (
            weakref.WeakKeyDictionary()
        )
        # Evicted entries waiting for active requests or shutdown to finish closing.
        self._draining: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, set[_PooledClientEntry]]" = (
            weakref.WeakKeyDictionary()
        )
        # OpenAI-compatible endpoints that rejected injected stream_options.
        self._stream_options_unsupported: set[str] = set()

    @staticmethod
    def _make_client(slot: ModelSlot) -> httpx.AsyncClient:
        from utils.http.pool_utils import (
            DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS,
            build_split_http_timeout,
        )

        httpx = load_httpx()
        # HTTP-level inactivity guard. A total request deadline is owned by the
        # execution policy, not by httpx's connect/read/write/pool timeouts.
        timeout = build_split_http_timeout(slot.timeout_seconds)
        return httpx.AsyncClient(
            timeout=timeout if timeout is not None else slot.timeout_seconds,
            follow_redirects=False,
            limits=httpx.Limits(keepalive_expiry=DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS),
        )

    @staticmethod
    def _make_pooled_client(proxy_mount_map: dict[str, str | None]) -> httpx.AsyncClient:
        from utils.http.pool_utils import (
            DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS,
            build_async_proxy_mounts,
            create_default_ssl_context,
            make_stateless_cookie_jar,
        )

        httpx = load_httpx()
        verify = create_default_ssl_context()
        limits = httpx.Limits(keepalive_expiry=DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS)
        # Do not install blind transport-level POST retry here: _send_pooled_request
        # coordinates stale-socket recovery with observation.restart and before_send.
        return httpx.AsyncClient(
            follow_redirects=False,
            verify=verify,
            limits=limits,
            cookies=make_stateless_cookie_jar(),
            mounts=build_async_proxy_mounts(proxy_mount_map, verify=verify, limits=limits),
            trust_env=False,
        )

    @staticmethod
    async def _close_client_quietly(client: httpx.AsyncClient) -> None:
        try:
            await client.aclose()
        except Exception:
            # Best-effort teardown of evicted or shutting-down pooled clients.
            pass

    async def _close_draining_entry(
        self,
        loop: asyncio.AbstractEventLoop,
        entry: _PooledClientEntry,
    ) -> None:
        draining = self._draining.get(loop)
        if draining is not None:
            draining.discard(entry)
        await self._close_client_quietly(entry.client)

    async def _release_pooled_client(
        self,
        loop: asyncio.AbstractEventLoop,
        entry: _PooledClientEntry,
    ) -> None:
        entry.in_flight = max(0, entry.in_flight - 1)
        if entry.evicted and entry.in_flight == 0:
            await self._close_draining_entry(loop, entry)

    async def _acquire_pooled_client(
        self,
        slot: ModelSlot,
    ) -> tuple[
        asyncio.AbstractEventLoop,
        _PooledClientEntry,
        list[httpx.AsyncClient],
        list[_PooledClientEntry],
    ]:
        from utils.http.pool_utils import (
            credential_fingerprint,
            http_origin_key,
            snapshot_http_proxy_config,
        )

        loop = asyncio.get_running_loop()
        proxy_fp, proxy_mount_map = snapshot_http_proxy_config()
        key = (
            http_origin_key(slot.base_url),
            slot.protocol,
            credential_fingerprint(slot.api_key),
            proxy_fp,
        )
        pool = self._pools.get(loop)
        if pool is not None:
            existing = pool.get(key)
            if existing is not None:
                if not getattr(existing.client, "is_closed", False):
                    pool.move_to_end(key)
                    existing.in_flight += 1
                    return loop, existing, [], []
                existing.evicted = True
                pool.pop(key, None)

        made = await asyncio.to_thread(self._make_pooled_client, proxy_mount_map)
        pool = self._pools.get(loop)
        if pool is None:
            pool = OrderedDict()
            self._pools[loop] = pool
        raced = pool.get(key)
        if raced is not None and not getattr(raced.client, "is_closed", False):
            pool.move_to_end(key)
            raced.in_flight += 1
            return loop, raced, [made], []
        if raced is not None:
            raced.evicted = True
            pool.pop(key, None)

        entry = _PooledClientEntry(made)
        entry.in_flight = 1
        pool[key] = entry
        idle_evicted: list[_PooledClientEntry] = []
        while len(pool) > self.POOL_MAX_CLIENTS:
            _, evicted = pool.popitem(last=False)
            evicted.evicted = True
            draining = self._draining.get(loop)
            if draining is None:
                draining = set()
                self._draining[loop] = draining
            draining.add(evicted)
            if evicted.in_flight == 0:
                idle_evicted.append(evicted)
        return loop, entry, [], idle_evicted

    async def aclose(self) -> None:
        """Concurrently close every active and draining pooled upstream HTTP client."""
        loop = asyncio.get_running_loop()
        pool = self._pools.pop(loop, None)
        draining = self._draining.pop(loop, None)
        clients: list[httpx.AsyncClient] = []
        seen_ids: set[int] = set()
        if pool:
            for entry in pool.values():
                entry.evicted = True
                cid = id(entry.client)
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    clients.append(entry.client)
        if draining:
            for entry in draining:
                entry.evicted = True
                cid = id(entry.client)
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    clients.append(entry.client)
        if not clients:
            return
        await asyncio.gather(
            *(self._close_client_quietly(client) for client in clients),
            return_exceptions=True,
        )

    async def _send_pooled_request(
        self,
        client: httpx.AsyncClient,
        slot: ModelSlot,
        payload: bytes,
        headers: dict,
        *,
        observation: AttemptObservation | None,
        before_send: Callable[[], Awaitable[None]] | None,
    ) -> httpx.Response:
        import time
        from utils.http.pool_utils import (
            SEND_REQUEST_HEADERS_TRACE_SUFFIX,
            STALE_CONNECTION_ERRORS,
            STALE_RETRY_MAX_ELAPSED_SECONDS,
            build_split_http_timeout,
            is_fresh_connection_trace,
        )

        request_timeout = build_split_http_timeout(slot.timeout_seconds)
        effective_timeout = request_timeout if request_timeout is not None else slot.timeout_seconds
        endpoint = _endpoint(slot)
        request = client.build_request(
            "POST",
            endpoint,
            content=payload,
            headers=headers,
            timeout=effective_timeout,
        )
        opened_fresh_connection = False
        send_started: float | None = None

        async def _track_trace(event_name: str, _info: dict) -> None:
            nonlocal opened_fresh_connection, send_started
            if is_fresh_connection_trace(event_name):
                opened_fresh_connection = True
            elif send_started is None and event_name.endswith(SEND_REQUEST_HEADERS_TRACE_SUFFIX):
                send_started = time.monotonic()

        orig_extensions = dict(request.extensions)
        request.extensions = {**orig_extensions, "trace": _track_trace}
        started = time.monotonic()
        if observation is not None:
            observation.upstream_started = True
        try:
            return await client.send(request, stream=True, follow_redirects=False)
        except STALE_CONNECTION_ERRORS:
            elapsed = time.monotonic() - (send_started if send_started is not None else started)
            if opened_fresh_connection or elapsed > STALE_RETRY_MAX_ELAPSED_SECONDS:
                raise
            # A reused keep-alive socket died before response headers arrived.
            # Account for the failed attempt and reserve quota again before
            # replaying the POST, matching stream_options retry semantics.
            if observation is not None:
                observation.restart("upstream_connection_error")
            if before_send is not None:
                await before_send()
            retry_request = client.build_request(
                "POST",
                endpoint,
                content=payload,
                headers=headers,
                timeout=effective_timeout,
            )
            if observation is not None:
                observation.upstream_started = True
            return await client.send(retry_request, stream=True, follow_redirects=False)

    @asynccontextmanager
    async def _request(
        self,
        slot: ModelSlot,
        payload: bytes,
        headers: dict,
        observation: AttemptObservation | None = None,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ):
        use_pool = (
            self._custom_client_factory is None
            and self._client_factory == self._make_client
        )
        pooled_loop = None
        pooled_entry = None
        raced_to_close: list[httpx.AsyncClient] = []
        idle_evicted: list[_PooledClientEntry] = []
        if use_pool:
            pooled_loop, pooled_entry, raced_to_close, idle_evicted = (
                await self._acquire_pooled_client(slot)
            )
            client = pooled_entry.client
            owns_client = False
        else:
            client = self._client_factory(slot)
            owns_client = True
        response = None
        try:
            if raced_to_close or idle_evicted:
                await asyncio.gather(
                    *(self._close_client_quietly(c) for c in raced_to_close),
                    *(
                        self._close_draining_entry(pooled_loop, evicted)
                        for evicted in idle_evicted
                        if pooled_loop is not None
                    ),
                    return_exceptions=True,
                )
                raced_to_close = []
                idle_evicted = []
            if use_pool:
                response = await self._send_pooled_request(
                    client,
                    slot,
                    payload,
                    headers,
                    observation=observation,
                    before_send=before_send,
                )
            else:
                request = client.build_request("POST", _endpoint(slot), content=payload, headers=headers)
                if observation is not None:
                    observation.upstream_started = True
                response = await client.send(request, stream=True, follow_redirects=False)
            if not response.is_success and observation is not None:
                await _observe_error_usage(response, slot, observation)
            _check_status(response)
            yield response
        finally:
            # StreamingResponse uses an AnyIO cancel scope. Without shielding,
            # repeated cancellation can interrupt async HTTP close halfway.
            with anyio.CancelScope(shield=True):
                try:
                    if response is not None:
                        await response.aclose()
                finally:
                    if raced_to_close or idle_evicted:
                        await asyncio.gather(
                            *(self._close_client_quietly(c) for c in raced_to_close),
                            *(
                                self._close_draining_entry(pooled_loop, evicted)
                                for evicted in idle_evicted
                                if pooled_loop is not None
                            ),
                            return_exceptions=True,
                        )
                    if owns_client:
                        await client.aclose()
                    elif pooled_entry is not None and pooled_loop is not None:
                        await self._release_pooled_client(pooled_loop, pooled_entry)

    async def complete(
        self, slot: ModelSlot, body: object, *, observation: AttemptObservation | None = None,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> dict:
        httpx = await ensure_httpx()
        slot = slot.model_copy(deep=True)
        model_alias, payload, headers, _ = await asyncio.to_thread(_prepare, slot, body, streaming=False)
        if before_send is not None:
            # Only a validated request that is about to be sent reserves quota;
            # locally rejected requests must not consume the shared allowance.
            await before_send()
        try:
            async with self._request(slot, payload, headers, observation, before_send=before_send) as response:
                result = await read_json_response(response)
                if observation is not None:
                    observation.observe(result.get("usage"), protocol=slot.protocol, reported=True)
            adapter = anthropic if slot.protocol == "anthropic_messages" else openai
            return await asyncio.to_thread(adapter.convert_response, result, model_alias=model_alias)
        except httpx.TimeoutException as exc:
            raise ModelGatewayError("upstream_timeout", "Model provider request timed out", 504) from exc
        except httpx.HTTPError as exc:
            raise ModelGatewayError("upstream_connection_error", "Could not complete the model provider request", 502) from exc

    async def stream(
        self, slot: ModelSlot, body: object, *, observation: AttemptObservation | None = None,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> AsyncIterator[bytes]:
        httpx = await ensure_httpx()
        slot = slot.model_copy(deep=True)
        endpoint = _endpoint(slot)
        options_unsupported = endpoint in self._stream_options_unsupported
        model_alias, payload, headers, include_usage = await asyncio.to_thread(
            _prepare, slot, body, streaming=True, inject_stream_usage=not options_unsupported,
        )
        is_anthropic = slot.protocol == "anthropic_messages"
        if is_anthropic:
            converter = anthropic.AnthropicStreamConverter(model_alias, include_usage=include_usage)
        else:
            converter = openai.OpenAIStreamConverter(model_alias, include_usage=include_usage)
        # stream_options is injected only for accounting. If an OpenAI-compatible
        # endpoint rejects it, retry once without it unless the plugin asked.
        can_retry_without_options = not is_anthropic and not include_usage and not options_unsupported
        saw_done = False
        completed = False
        if before_send is not None:
            # Reserve only after validation succeeded, right before the send.
            await before_send()
        try:
            for retrying in (False, True):
                connected = False
                try:
                    async with self._request(slot, payload, headers, observation, before_send=before_send) as response:
                        connected = True
                        if retrying:
                            self._stream_options_unsupported.add(endpoint)
                        async for data in iter_sse_data(response):
                            if data.strip() == "[DONE]":
                                if is_anthropic:
                                    raise ModelGatewayError("invalid_upstream_response", "Unexpected stream terminator", 502)
                                saw_done = True
                                break
                            try:
                                chunks = converter.feed(decode_object(data))
                                # Messages has no separate [DONE] marker: message_stop
                                # makes usage final before its last chunks are yielded.
                                # OpenAI's usage-only chunk is likewise final before [DONE].
                                completed = bool(converter.done if is_anthropic else converter.usage_final)
                            finally:
                                _observe_stream_usage(converter, observation, reported=completed)
                            for chunk in chunks:
                                yield encode_sse(chunk)
                            if is_anthropic and converter.done:
                                break
                        converter.finish()
                        if not is_anthropic and not saw_done:
                            raise ModelGatewayError("incomplete_upstream_stream", "Model stream ended without a terminator", 502)
                        completed = True
                        _observe_stream_usage(converter, observation, reported=True)
                    break
                except ModelGatewayError as exc:
                    if connected or retrying or not can_retry_without_options or exc.code != "upstream_request_rejected":
                        raise
                    if observation is not None:
                        # Preserve this send in the executor's ledger, then
                        # start an independent observation for the retry.
                        observation.restart(exc.code)
                _, payload, headers, _ = await asyncio.to_thread(
                    _prepare, slot, body, streaming=True, inject_stream_usage=False,
                )
                if before_send is not None:
                    # The retry is a second real upstream send with the same
                    # free credentials; it must reserve quota again or fail.
                    await before_send()
            yield b"data: [DONE]\n\n"
        except httpx.TimeoutException as exc:
            raise ModelGatewayError("upstream_timeout", "Model provider stream timed out", 504) from exc
        except httpx.HTTPError as exc:
            raise ModelGatewayError("upstream_connection_error", "Model provider stream disconnected", 502) from exc
        finally:
            _observe_stream_usage(converter, observation, reported=completed)
