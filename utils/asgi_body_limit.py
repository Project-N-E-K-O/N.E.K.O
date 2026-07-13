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

This rejects oversized request bodies before they are fully buffered by the
application layer (a router's ``request.json()`` / ``request.form()`` parse),
uniformly across every router, and stays orthogonal to each router's
business-level validation (e.g. ``memory_router.validate_chat_payload``).

Design (see issue #1586, raised from the PR #1585 discussion):

- Ordinary request bodies are capped by a small desktop-backend guard. Known
  multipart upload endpoints (Live2D/VRM/MMD models, jukebox music,
  character-card zips, ...) get a larger but still finite cap so legitimate
  large uploads survive without letting arbitrary routers spoof multipart and
  bypass the guard.
- ``Content-Length`` is inspected first so obviously oversized requests can be
  rejected before parsing. Bodies are also counted while the app reads from ASGI
  ``receive`` so chunked / unknown-length requests cannot bypass the cap and
  reach ``request.json()`` / ``request.form()`` unbounded.
- Only the ``http`` scope is handled; ``websocket`` / ``lifespan`` scopes are
  forwarded untouched (the Pet realtime WebSocket endpoints must not be
  affected).

This app is a loopback-only desktop backend with no external trust boundary, so
this guard is a memory / data-shape safeguard, not a security perimeter. The
cap's job is to stop bare body-parsing endpoints from reading anomalously large
bodies into memory before validating their shape.
"""
from __future__ import annotations

import json
import re

# 16 MiB. Comfortably above every non-multipart endpoint's legitimate body
# (the largest is the recent-chat payload's 2 MB business cap from PR #1585),
# while still stopping an anomalous JSON/urlencoded body from being buffered
# into memory before it is parsed.
DEFAULT_MAX_INBOUND_BODY_BYTES = 16 * 1024 * 1024

# The largest current upload feature is jukebox import at 10 GiB. Add the normal
# JSON cap as multipart envelope slack so that an exactly-at-limit file is not
# rejected before the route's own validation sees it.
DEFAULT_TRUSTED_MULTIPART_BODY_BYTES = 10 * 1024 * 1024 * 1024 + DEFAULT_MAX_INBOUND_BODY_BYTES

_TRUSTED_MULTIPART_UPLOAD_EXACT_PATHS = frozenset(
    {
        "/api/avatar-drop/parse-document",
        "/api/characters/audio/analyze_silence",
        "/api/characters/audio/trim_silence",
        "/api/characters/import-card",
        "/api/characters/voice_clone",
        "/api/jukebox/actions",
        "/api/jukebox/import",
        "/api/jukebox/pack-folder",
        "/api/jukebox/songs",
        "/api/live2d/upload_model",
        "/api/model/mmd/upload",
        "/api/model/mmd/upload_animation",
        "/api/model/mmd/upload_zip",
        "/api/model/pngtuber/upload_model",
        "/api/model/vrm/upload",
        "/api/model/vrm/upload_animation",
        "/api/steam/workshop/upload-reference-audio",
    }
)
_TRUSTED_MULTIPART_UPLOAD_PREFIXES = (
    "/api/live2d/upload_file/",
)
_TRUSTED_MULTIPART_UPLOAD_PATTERNS = (
    re.compile(r"^/api/characters/catgirl/[^/]+/(?:card-face|export-with-portrait)$"),
)


class _InboundBodyTooLarge(BaseException):
    """Internal control flow for streamed body overflow."""

    def __init__(self, max_body_bytes: int):
        super().__init__(max_body_bytes)
        self.max_body_bytes = max_body_bytes


class InboundBodySizeLimitMiddleware:
    """Reject oversized request bodies before full buffering."""

    def __init__(
        self,
        app,
        max_body_bytes: int = DEFAULT_MAX_INBOUND_BODY_BYTES,
        trusted_multipart_body_bytes: int = DEFAULT_TRUSTED_MULTIPART_BODY_BYTES,
    ):
        self.app = app
        self.max_body_bytes = int(max_body_bytes)
        self.trusted_multipart_body_bytes = int(trusted_multipart_body_bytes)

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

        request_limit = self._request_limit_bytes(scope, self._is_multipart(content_type))
        if self._exceeds_content_length_limit(content_length, request_limit):
            await self._reject(send, request_limit)
            return

        wrapped_receive = self._limited_receive(receive, request_limit)
        try:
            await self.app(scope, wrapped_receive, send)
        except _InboundBodyTooLarge as exc:
            await self._reject(send, exc.max_body_bytes)

    def _is_multipart(self, content_type: bytes) -> bool:
        return content_type.strip().lower().startswith(b"multipart/")

    def _request_limit_bytes(self, scope, is_multipart: bool) -> int:
        if is_multipart and self._is_trusted_multipart_upload_scope(scope):
            return self.trusted_multipart_body_bytes
        return self.max_body_bytes

    def _is_trusted_multipart_upload_scope(self, scope) -> bool:
        path = str(scope.get("path") or "")
        if path in _TRUSTED_MULTIPART_UPLOAD_EXACT_PATHS:
            return True
        if any(path.startswith(prefix) for prefix in _TRUSTED_MULTIPART_UPLOAD_PREFIXES):
            return True
        return any(pattern.match(path) for pattern in _TRUSTED_MULTIPART_UPLOAD_PATTERNS)

    def _exceeds_content_length_limit(self, content_length: bytes | None, max_body_bytes: int) -> bool:
        if content_length is None:
            return False
        try:
            length = int(content_length)
        except (TypeError, ValueError):
            # Malformed Content-Length: let the server / downstream handle it
            # instead of guessing here.
            return False
        return length > max_body_bytes

    def _limited_receive(self, receive, max_body_bytes: int):
        seen = 0

        async def wrapped_receive():
            nonlocal seen
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body") or b"")
                if seen > max_body_bytes:
                    raise _InboundBodyTooLarge(max_body_bytes)
            return message

        return wrapped_receive

    async def _reject(self, send, max_body_bytes: int) -> None:
        body = json.dumps(
            {
                "ok": False,
                "error_code": "payload_too_large",
                "max_bytes": max_body_bytes,
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
