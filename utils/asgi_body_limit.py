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
"""ASGI middleware: inbound request-body size guard.

This rejects oversized request bodies *before* they reach the application
layer (a router's ``request.json()`` / ``request.form()`` parse), uniformly
across every router, and stays orthogonal to each router's business-level
validation (e.g. ``memory_router.validate_chat_payload``).

Design (see issue #1586, raised from the PR #1585 discussion):

- Non-multipart requests are capped globally. ``multipart/form-data`` is the file
  upload path (Live2D/VRM/MMD models, jukebox music, character-card zips, ...)
  whose legitimate bodies routinely run to hundreds of MB or GB, so multipart
  remains exempt unless the application explicitly configures a bounded route.
- A configured multipart route is authorized and size-checked before FastAPI
  calls ``request.form()``. The receive wrapper also enforces the limit when
  ``Content-Length`` is absent or incorrect.
- For the global non-multipart cap, only ``Content-Length`` is inspected. The
  bounded multipart route additionally counts ASGI chunks as the parser reads
  them, without buffering another copy in this middleware.
- Exact paths supplied through ``streamed_path_limits`` are read into a bounded
  spooled file and replayed downstream only after the actual byte count has
  passed validation, whatever their content type. An absent or dishonest
  ``Content-Length`` therefore cannot make FastAPI spool an unbounded body
  before a route-level guard runs.
- Only the ``http`` scope is handled; ``websocket`` / ``lifespan`` scopes are
  forwarded untouched (the Pet realtime WebSocket endpoints must not be
  affected).

This app is a loopback-only desktop backend with no external trust boundary, so
this guard is a memory / data-shape safeguard, not a security perimeter. A
client can trivially bypass the cap by labelling its request ``multipart/*`` —
but that only routes it back into the upload routers' own streaming guards, so
an oversized body still never gets buffered whole. The cap's job is the bare
``request.json()`` endpoints that would otherwise read an arbitrarily large
body into memory before validating its shape.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Mapping

from fastapi import HTTPException

# 16 MiB. Comfortably above every non-multipart endpoint's legitimate body
# (the largest is the recent-chat payload's 2 MB business cap from PR #1585),
# while still stopping an anomalous JSON/urlencoded body from being buffered
# into memory before it is parsed.
DEFAULT_MAX_INBOUND_BODY_BYTES = 16 * 1024 * 1024


class InboundBodySizeLimitMiddleware:
    """Reject oversized request bodies before routers parse them."""

    def __init__(
        self,
        app,
        max_body_bytes: int = DEFAULT_MAX_INBOUND_BODY_BYTES,
        *,
        multipart_path_prefix: str | None = None,
        multipart_methods: tuple[str, ...] = (),
        max_multipart_body_bytes: int | None = None,
        multipart_preflight=None,
        streamed_path_limits: Mapping[str, int] | None = None,
    ):
        self.app = app
        self.max_body_bytes = int(max_body_bytes)
        self.multipart_path_prefix = (multipart_path_prefix or "").rstrip("/")
        self.multipart_methods = frozenset(method.upper() for method in multipart_methods)
        self.max_multipart_body_bytes = (
            int(max_multipart_body_bytes)
            if max_multipart_body_bytes is not None
            else None
        )
        self.multipart_preflight = multipart_preflight
        self.streamed_path_limits = {
            str(path): int(limit)
            for path, limit in (streamed_path_limits or {}).items()
        }

    async def __call__(self, scope, receive, send):
        # websocket / lifespan scopes carry no Content-Length body to cap.
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        content_length: bytes | None = None
        content_type: bytes = b""
        for key, value in scope.get("headers") or ():
            lowered = key.lower()
            if lowered == b"content-length":
                content_length = value
            elif lowered == b"content-type":
                content_type = value

        streamed_limit = self.streamed_path_limits.get(str(scope.get("path") or ""))
        if streamed_limit is not None:
            await self._serve_streamed_path(
                scope, receive, send, content_length, streamed_limit
            )
            return

        configured_route = self._matches_configured_route(scope)
        bounded_multipart = configured_route and self._is_bounded_multipart(content_type)
        # preflight 按路由触发，不按 content-type：这些路由的 handler 声明了
        # Form/File 参数，FastAPI 会在进入 handler（也就是路由内部那套同样的
        # 本地访问 / CSRF 校验）之前就把 body 解析掉。只对 multipart 跑 preflight
        # 的话，跨域客户端换个 content-type 就能反复让服务器解析大 body 再被拒。
        if configured_route and self.multipart_preflight is not None:
            rejected = self.multipart_preflight(scope)
            if rejected is not None:
                await rejected(scope, receive, send)
                return

        maximum = self.max_multipart_body_bytes if bounded_multipart else self.max_body_bytes
        if self._exceeds_limit(content_length, content_type, bounded_multipart=bounded_multipart):
            await self._reject(send, maximum)
            return

        if not bounded_multipart:
            await self.app(scope, receive, send)
            return

        consumed = 0
        overflowed = False

        async def limited_receive():
            nonlocal consumed, overflowed
            message = await receive()
            if message.get("type") == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > maximum:
                    overflowed = True
                    raise _InboundBodyTooLarge(maximum)
            return message

        async def limited_send(message):
            if not overflowed:
                await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except _InboundBodyTooLarge:
            pass
        if overflowed:
            await self._reject(send, maximum)

    def _matches_configured_route(self, scope) -> bool:
        """Path/method match for the guarded routes, independent of content type."""
        if (
            not self.multipart_path_prefix
            or scope.get("method", "").upper() not in self.multipart_methods
        ):
            return False
        path = str(scope.get("path") or "").rstrip("/")
        return path == self.multipart_path_prefix or path.startswith(
            f"{self.multipart_path_prefix}/"
        )

    def _is_bounded_multipart(self, content_type: bytes) -> bool:
        """Whether the multipart-specific size policy applies to this body."""
        return (
            self.max_multipart_body_bytes is not None
            and content_type.strip().lower().startswith(b"multipart/")
        )

    def _exceeds_limit(
        self,
        content_length: bytes | None,
        content_type: bytes,
        *,
        bounded_multipart: bool = False,
    ) -> bool:
        if content_length is None:
            # No Content-Length (chunked / unknown): pass through rather than
            # risk rejecting a valid streaming request.
            return False
        # Multipart uploads are exempt — the upload routers guard them with
        # their own streaming, much-larger caps.
        if content_type.strip().lower().startswith(b"multipart/") and not bounded_multipart:
            return False
        maximum = self.max_multipart_body_bytes if bounded_multipart else self.max_body_bytes
        return self._declared_length_exceeds(content_length, maximum)

    @staticmethod
    def _declared_length_exceeds(
        content_length: bytes | None,
        max_bytes: int,
    ) -> bool:
        if content_length is None:
            return False
        try:
            length = int(content_length)
        except (TypeError, ValueError):
            # Malformed Content-Length: let the server / downstream handle it
            # instead of guessing here.
            return False
        return length > max_bytes

    async def _serve_streamed_path(
        self,
        scope,
        receive,
        send,
        content_length: bytes | None,
        max_bytes: int,
    ) -> None:
        if self._declared_length_exceeds(content_length, max_bytes):
            await self._reject(
                send, max_bytes, error_code="knowledge_request_too_large"
            )
            return
        spool, exceeded, disconnected = await self._spool_bounded_body(
            receive,
            max_bytes=max_bytes,
        )
        if exceeded:
            await asyncio.to_thread(spool.close)
            await self._reject(
                send, max_bytes, error_code="knowledge_request_too_large"
            )
            return
        try:
            await self.app(
                scope,
                self._replay_receive(spool, disconnected=disconnected),
                send,
            )
        finally:
            await asyncio.to_thread(spool.close)

    @staticmethod
    async def _spool_bounded_body(receive, *, max_bytes: int):
        spool = tempfile.SpooledTemporaryFile(max_size=min(max_bytes, 1024 * 1024))
        size = 0
        disconnected = False
        try:
            while True:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    disconnected = True
                    break
                if message.get("type") != "http.request":
                    continue
                body = message.get("body", b"")
                size += len(body)
                if size > max_bytes:
                    return spool, True, disconnected
                await asyncio.to_thread(spool.write, body)
                if not message.get("more_body", False):
                    break
            await asyncio.to_thread(spool.seek, 0)
            return spool, False, disconnected
        except BaseException:
            try:
                await asyncio.shield(asyncio.to_thread(spool.close))
            except BaseException:
                pass
            raise

    @staticmethod
    def _replay_receive(spool, *, disconnected: bool):
        finished = False

        async def replay():
            nonlocal finished
            if finished:
                return {"type": "http.disconnect"}
            if disconnected:
                # The client went away before the body was complete, so what was
                # spooled is a prefix. Replaying it would hand the application a
                # truncated request that still parses as a whole one; the only
                # honest thing left to report is the disconnect.
                finished = True
                return {"type": "http.disconnect"}
            chunk = await asyncio.to_thread(spool.read, 64 * 1024)
            if chunk:
                more_body = len(chunk) == 64 * 1024
                if not more_body:
                    finished = True
                return {
                    "type": "http.request",
                    "body": chunk,
                    "more_body": more_body,
                }
            finished = True
            return {"type": "http.request", "body": b"", "more_body": False}

        return replay

    async def _reject(
        self,
        send,
        max_bytes: int,
        *,
        error_code: str = "payload_too_large",
    ) -> None:
        body = json.dumps(
            {
                "ok": False,
                "error_code": error_code,
                "max_bytes": max_bytes,
                "error": "请求体超过允许的体积上限。",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    # Don't try to keep-alive a connection whose (unread) request
                    # body may still be streaming in.
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})


class _InboundBodyTooLarge(HTTPException):
    def __init__(self, maximum: int):
        super().__init__(
            status_code=413,
            detail={
                "ok": False,
                "error_code": "payload_too_large",
                "max_bytes": maximum,
            },
        )
