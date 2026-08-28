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
"""ASGI middleware: global inbound request-body size guard.

This rejects oversized request bodies *before* they reach the application
layer (a router's ``request.json()`` / ``request.form()`` parse), uniformly
across every router, and stays orthogonal to each router's business-level
validation (e.g. ``memory_router.validate_chat_payload``).

Design (see issue #1586, raised from the PR #1585 discussion):

- Only non-multipart requests are capped. ``multipart/form-data`` is the file
  upload path (Live2D/VRM/MMD models, jukebox music, character-card zips, ...)
  whose legitimate bodies routinely run to hundreds of MB or GB. Those upload
  routers already enforce their own 1 MB-chunk streaming guards (read-and-tally,
  stop on overflow, never buffering the whole body in memory), so this guard
  passes multipart straight through to avoid killing legitimate large uploads.
- Only the ``Content-Length`` header is inspected; the body itself is never
  read. That is what makes the rejection happen "before parsing". When
  ``Content-Length`` is absent (e.g. chunked transfer encoding) the request is
  passed through — we would rather under-guard than reject a valid request.
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

# 16 MiB. Comfortably above every non-multipart endpoint's legitimate body
# (the largest is the recent-chat payload's 2 MB business cap from PR #1585),
# while still stopping an anomalous JSON/urlencoded body from being buffered
# into memory before it is parsed.
DEFAULT_MAX_INBOUND_BODY_BYTES = 16 * 1024 * 1024


class InboundBodySizeLimitMiddleware:
    """Reject oversized request bodies before routers parse them.

    Multipart uploads remain globally exempt, except for exact paths supplied
    through ``streamed_path_limits``. Those paths are read into a bounded
    spooled file and replayed to the downstream application only after the
    actual byte count has passed validation. This closes the gap where an
    absent or dishonest ``Content-Length`` could otherwise make FastAPI spool
    an unbounded multipart body before a route-level guard runs.
    """

    def __init__(
        self,
        app,
        max_body_bytes: int = DEFAULT_MAX_INBOUND_BODY_BYTES,
        streamed_path_limits: Mapping[str, int] | None = None,
    ):
        self.app = app
        self.max_body_bytes = int(max_body_bytes)
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
            if self._declared_length_exceeds(content_length, streamed_limit):
                await self._reject(
                    send,
                    max_bytes=streamed_limit,
                    error_code="knowledge_request_too_large",
                )
                return
            spool, exceeded, disconnected = await self._spool_bounded_body(
                receive,
                max_bytes=streamed_limit,
            )
            if exceeded:
                await asyncio.to_thread(spool.close)
                await self._reject(
                    send,
                    max_bytes=streamed_limit,
                    error_code="knowledge_request_too_large",
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
            return

        if self._exceeds_limit(content_length, content_type):
            await self._reject(send, max_bytes=self.max_body_bytes)
            return

        await self.app(scope, receive, send)

    def _exceeds_limit(self, content_length: bytes | None, content_type: bytes) -> bool:
        if content_length is None:
            # No Content-Length (chunked / unknown): pass through rather than
            # risk rejecting a valid streaming request.
            return False
        # Multipart uploads are exempt — the upload routers guard them with
        # their own streaming, much-larger caps.
        if content_type.strip().lower().startswith(b"multipart/"):
            return False
        return self._declared_length_exceeds(content_length, self.max_body_bytes)

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
            return False
        return length > max_bytes

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
            if disconnected:
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": b"", "more_body": False}

        return replay

    async def _reject(
        self,
        send,
        *,
        max_bytes: int,
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
