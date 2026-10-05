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

"""Shared checks of local-only WebSocket endpoints (``/api/vmc/ws``, ``/api/visit/transport/ws``).

One implementation of the Origin allow-list, the first-frame CSRF ``auth``
check and the ``NEKO_BEHIND_PROXY`` flag, so a fix to one endpoint cannot
miss another. The token and the allowed origins are passed in by the caller
(each router keeps its own module-level copy, which is what tests patch).
"""

from __future__ import annotations

import os
import secrets
from typing import Any, Iterable
from urllib.parse import urlsplit


def behind_proxy_enabled() -> bool:
    """True when ``NEKO_BEHIND_PROXY`` is set (read per call; same parsing as the server entry point)."""
    return os.environ.get("NEKO_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes")


def websocket_origin_allowed(origin: str, request_host: str | None, allowed_origins: Iterable[str]) -> bool:
    """Origin host equals the server host, or the host of one of ``allowed_origins``."""
    try:
        parsed_origin = urlsplit(origin or "")
    except ValueError:
        return False
    if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.hostname:
        return False
    origin_host = parsed_origin.hostname.lower()
    if request_host and origin_host == request_host.lower():
        return True
    for allowed_origin in allowed_origins:
        try:
            allowed_host = urlsplit(allowed_origin).hostname
        except (TypeError, ValueError):
            continue
        if allowed_host and allowed_host.lower() == origin_host:
            return True
    return False


def valid_auth_frame(message: Any, expected_token: str | None) -> bool:
    """First frame is ``{type:'auth', csrf_token}`` with the expected token (constant-time compare)."""
    if not isinstance(message, dict) or message.get("type") != "auth":
        return False
    token = message.get("csrf_token")
    return bool(
        isinstance(token, str)
        and token
        and expected_token
        and secrets.compare_digest(token, expected_token)
    )
