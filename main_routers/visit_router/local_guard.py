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

"""Local-origin gate shared by every visit entry point (design §4.6 preamble).

Two layers:

1. **Main gate: the real peer address must be loopback** (``127.0.0.0/8`` /
   ``::1``), read from ``client.host``, never from Origin / Host. Two hard
   preconditions keep ``client.host`` honest: ``NEKO_BEHIND_PROXY`` must be
   off (uvicorn then rewrites ``client.host`` from ``X-Forwarded-For``), and
   the request must not carry ``Forwarded`` / ``X-Forwarded-For`` /
   ``X-Real-IP`` at all (a direct local connection never sends them).
   ``NEKO_VISIT_ALLOW_NONLOCAL`` turns all three checks off.
2. **Second layer: Origin / Host allow-list + CSRF token**, the same scheme
   as ``WS /api/vmc/ws`` (``main_routers/vmc_router.py``).

Used by the transport WS here; PR-09a reuses it for ``/api/visit/*`` HTTP and
``visit_bind`` on the display socket.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Mapping

import config.visit_settings as visit_settings
from config import AUTOSTART_ALLOWED_ORIGINS, AUTOSTART_CSRF_TOKEN
from utils import local_ws_guard

PROXY_HEADERS: tuple[str, ...] = ("forwarded", "x-forwarded-for", "x-real-ip")
"""Request headers whose mere presence rejects a visit request."""

UNAUTHORIZED_CODE = "VISIT_E_UNAUTHORIZED"
"""Error code reported for every rejection of this gate."""


def behind_proxy() -> bool:
    """True when the server runs with ``NEKO_BEHIND_PROXY`` (read per call)."""
    return local_ws_guard.behind_proxy_enabled()


def allow_nonlocal() -> bool:
    """The ``NEKO_VISIT_ALLOW_NONLOCAL`` escape hatch (read from the settings module per call)."""
    return bool(visit_settings.NEKO_VISIT_ALLOW_NONLOCAL)


def is_loopback_host(host: Any) -> bool:
    """True iff ``host`` is a literal loopback IP (IPv4-mapped IPv6 included).

    Host names (``localhost``) do not count: ``client.host`` is always the
    socket peer address, so a name there means something rewrote it.
    """
    if not isinstance(host, str) or not host:
        return False
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return addr.is_loopback


def local_peer_allowed(client_host: Any, headers: Mapping[str, str]) -> bool:
    """Main gate: loopback peer, no proxy mode, no proxy headers (or the escape hatch)."""
    if allow_nonlocal():
        return True
    if behind_proxy():
        return False
    lowered = {str(k).lower() for k in headers.keys()}
    if any(name in lowered for name in PROXY_HEADERS):
        return False
    return is_loopback_host(client_host)


def websocket_origin_allowed(origin: str, request_host: str | None) -> bool:
    """Second layer for WebSockets: Origin host equals the server host or an allowed local host.

    Shared implementation with ``/api/vmc/ws`` (``utils.local_ws_guard``).
    """
    return local_ws_guard.websocket_origin_allowed(origin, request_host, AUTOSTART_ALLOWED_ORIGINS)


def valid_auth_frame(message: Any) -> bool:
    """First-frame check ``{type:'auth', csrf_token}`` (shared with ``/api/vmc/ws``)."""
    return local_ws_guard.valid_auth_frame(message, AUTOSTART_CSRF_TOKEN)
