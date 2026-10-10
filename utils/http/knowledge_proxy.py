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

"""What the public-knowledge proxies may forward, and how much of it.

Both browser-facing hops share these rules: Main's ``/api/public-knowledge/*``
proxy and the plugin server's ``/market/knowledge/*`` bridge in front of it.
Keeping one copy means a new endpoint cannot be allowed on one hop and
forgotten on the other. Neither hop parses bodies; they stream them through
with a size cap.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol

JSON_BODY_MAX_BYTES = 64 * 1024
# Kept equal to the schema-v1 pack limit (knowledge.models.MAX_PACK_BYTES,
# which this layer cannot import) plus room for multipart framing.
PACK_BODY_MAX_BYTES = 10 * 1024 * 1024 + 64 * 1024

READ_PATHS = frozenset(
    {"status", "entries", "entry", "packs", "packs/jobs", "diagnostics/recent"}
)
WRITE_PATHS: dict[str, int] = {
    "settings": JSON_BODY_MAX_BYTES,
    "entry/disabled": JSON_BODY_MAX_BYTES,
    "packs/import": PACK_BODY_MAX_BYTES,
    "packs/jobs/cancel": JSON_BODY_MAX_BYTES,
    "packs/jobs/discard": JSON_BODY_MAX_BYTES,
    "packs/auto-context": JSON_BODY_MAX_BYTES,
    "packs/index-policy": JSON_BODY_MAX_BYTES,
    "packs/material-type": JSON_BODY_MAX_BYTES,
    "packs/remove": JSON_BODY_MAX_BYTES,
}


class BodyTooLarge(Exception):
    """A streamed request body went over its cap."""


class _StreamingRequest(Protocol):
    def stream(self) -> AsyncIterator[bytes]: ...


def declared_size_problem(content_length: str | None, max_bytes: int) -> str | None:
    """Reason to refuse a request from its Content-Length alone, if any."""
    if content_length is None:
        return None
    try:
        size = int(content_length)
    except ValueError:
        return "invalid_request"
    return "payload_too_large" if size > max_bytes else None


async def capped_body(request: _StreamingRequest, max_bytes: int) -> AsyncIterator[bytes]:
    """Pass the request body through, raising ``BodyTooLarge`` past ``max_bytes``."""
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > max_bytes:
            raise BodyTooLarge()
        yield chunk


def is_body_too_large(exc: BaseException) -> bool:
    """Whether ``exc`` is, or was raised while handling, ``BodyTooLarge``.

    The HTTP client re-raises a failing request-body stream as its own error.
    """
    return any(
        isinstance(item, BodyTooLarge) for item in (exc, exc.__cause__, exc.__context__)
    )
