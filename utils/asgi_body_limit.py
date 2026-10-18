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

- Non-multipart requests are capped globally; ``json_path_limits`` can raise
  the cap for an exact POST path whose JSON body legitimately carries a large
  payload (still a bounded ``Content-Length`` check, never unlimited).
  ``multipart/form-data`` is the file
  upload path (Live2D/VRM/MMD models, jukebox music, character-card zips, ...)
  whose legitimate bodies routinely run to hundreds of MB or GB, so multipart
  remains exempt unless the application explicitly configures a bounded route.
- A configured multipart route is authorized and size-checked before FastAPI
  calls ``request.form()``. The receive wrapper also enforces the limit when
  ``Content-Length`` is absent or incorrect.
- For the global non-multipart cap, only ``Content-Length`` is inspected. The
  bounded multipart route additionally counts ASGI chunks as the parser reads
  them, without buffering another copy in this middleware.
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

import json

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
        json_path_limits: dict[str, int] | None = None,
    ):
        self.app = app
        self.max_body_bytes = int(max_body_bytes)
        self.json_path_limits = {
            str(path).rstrip("/"): int(limit)
            for path, limit in (json_path_limits or {}).items()
        }
        self.multipart_path_prefix = (multipart_path_prefix or "").rstrip("/")
        self.multipart_methods = frozenset(method.upper() for method in multipart_methods)
        self.max_multipart_body_bytes = (
            int(max_multipart_body_bytes)
            if max_multipart_body_bytes is not None
            else None
        )
        self.multipart_preflight = multipart_preflight

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

        configured_route = self._matches_configured_route(scope)
        bounded_multipart = configured_route and self._is_bounded_multipart(content_type)
        # Trigger preflight by route, not content-type: these route handlers declare
        # Form/File parameters, so FastAPI parses the body before entering the handler and its
        # equivalent local-access/CSRF checks. Running preflight only for multipart lets cross-origin
        # clients change content-type and repeatedly force large-body parsing before rejection.
        if configured_route and self.multipart_preflight is not None:
            rejected = self.multipart_preflight(scope)
            if rejected is not None:
                await rejected(scope, receive, send)
                return

        maximum = (
            self.max_multipart_body_bytes if bounded_multipart else self._non_multipart_limit(scope)
        )
        if self._exceeds_limit(
            content_length, content_type, bounded_multipart=bounded_multipart, maximum=maximum,
        ):
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

    def _non_multipart_limit(self, scope) -> int:
        """Global cap, or the exact-path POST override from ``json_path_limits``."""
        if self.json_path_limits and scope.get("method", "").upper() == "POST":
            path = str(scope.get("path") or "").rstrip("/")
            override = self.json_path_limits.get(path)
            if override is not None:
                return override
        return self.max_body_bytes

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
        maximum: int | None = None,
    ) -> bool:
        if content_length is None:
            # No Content-Length (chunked / unknown): pass through rather than
            # risk rejecting a valid streaming request.
            return False
        # Multipart uploads are exempt — the upload routers guard them with
        # their own streaming, much-larger caps.
        if content_type.strip().lower().startswith(b"multipart/") and not bounded_multipart:
            return False
        try:
            length = int(content_length)
        except (TypeError, ValueError):
            # Malformed Content-Length: let the server / downstream handle it
            # instead of guessing here.
            return False
        if maximum is None:
            maximum = self.max_multipart_body_bytes if bounded_multipart else self.max_body_bytes
        return length > maximum

    async def _reject(self, send, maximum: int) -> None:
        body = json.dumps(
            {
                "ok": False,
                "error_code": "payload_too_large",
                "max_bytes": maximum,
                "error": "请求体超过全局体积上限。",
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
