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
"""ASGI Host and WebSocket Origin guard for local N.E.K.O services.

The desktop services intentionally accept requests addressed to loopback and
IP literals.  IP literals cannot be changed by DNS rebinding, while allowing
them keeps LAN access and Docker's published ports working.  DNS hostnames are
denied by default and can be enabled explicitly with ``NEKO_TRUSTED_HOSTS``.

Web browsers do not apply CORS to WebSocket handshakes.  For that reason a
present WebSocket ``Origin`` must be same-origin, another loopback origin, or
listed in ``NEKO_TRUSTED_ORIGINS``.  A missing Origin is retained for native
clients, which do not provide the browser attack primitive this guard targets.

Both environment variables are comma-separated.  Trusted hosts accept an
exact hostname (optionally with a port) or a ``*.example.com`` subdomain
pattern.  Trusted origins must be complete ``http://`` or ``https://`` origins.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

TRUSTED_HOSTS_ENV = "NEKO_TRUSTED_HOSTS"
TRUSTED_ORIGINS_ENV = "NEKO_TRUSTED_ORIGINS"

_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HTTP_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}


@dataclass(frozen=True)
class _Authority:
    hostname: str
    port: int | None
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None

    @property
    def is_loopback(self) -> bool:
        if self.ip is not None:
            return self.ip.is_loopback
        return self.hostname == "localhost"


@dataclass(frozen=True)
class _TrustedHost:
    hostname: str
    port: int | None
    wildcard: bool = False


@dataclass(frozen=True)
class _Origin:
    scheme: str
    authority: _Authority


def _canonicalize_hostname(
    value: str,
) -> tuple[str, ipaddress.IPv4Address | ipaddress.IPv6Address | None] | None:
    if not value or "%" in value:
        # Scoped IPv6 addresses are not valid DNS-rebinding targets, but their
        # multiple wire spellings make exact Origin comparisons error-prone.
        return None

    try:
        parsed_ip = ipaddress.ip_address(value)
    except ValueError:
        parsed_ip = None

    if parsed_ip is not None:
        return str(parsed_ip).lower(), parsed_ip

    hostname = value.rstrip(".")
    if not hostname:
        return None
    try:
        hostname = hostname.encode("idna").decode("ascii").lower()
    except (UnicodeError, UnicodeDecodeError):
        return None

    if len(hostname) > 253:
        return None
    labels = hostname.split(".")
    if any(not _HOST_LABEL_RE.fullmatch(label) for label in labels):
        return None
    return hostname, None


def _parse_port(value: str) -> int | None:
    if not value or not value.isascii() or not value.isdecimal():
        return None
    port = int(value)
    if not 1 <= port <= 65535:
        return None
    return port


def _parse_authority(value: str) -> _Authority | None:
    """Parse an HTTP authority without relying on Starlette's Host parser."""
    if not value or value != value.strip():
        return None
    if any(ord(char) <= 0x20 or char in "/\\?#@," for char in value):
        return None

    hostname_text: str
    port: int | None = None
    if value.startswith("["):
        closing = value.find("]")
        if closing <= 1:
            return None
        hostname_text = value[1:closing]
        remainder = value[closing + 1 :]
        if remainder:
            if not remainder.startswith(":"):
                return None
            port = _parse_port(remainder[1:])
            if port is None:
                return None
        canonical = _canonicalize_hostname(hostname_text)
        if canonical is None or not isinstance(canonical[1], ipaddress.IPv6Address):
            return None
    else:
        # A bare IPv6 literal has several colons and no unambiguous port.  It is
        # accepted for tolerant native clients; standards-compliant HTTP clients
        # use the bracketed form handled above.
        canonical = _canonicalize_hostname(value)
        if canonical is not None and isinstance(canonical[1], ipaddress.IPv6Address):
            hostname_text = value
        else:
            if value.count(":") > 1:
                return None
            if ":" in value:
                hostname_text, port_text = value.rsplit(":", 1)
                port = _parse_port(port_text)
                if port is None:
                    return None
            else:
                hostname_text = value
            canonical = _canonicalize_hostname(hostname_text)
            if canonical is None:
                return None

    hostname, parsed_ip = canonical
    return _Authority(hostname=hostname, port=port, ip=parsed_ip)


def _parse_origin(value: str) -> _Origin | None:
    if not value or value != value.strip():
        return None
    if any(ord(char) <= 0x20 or char == "\\" for char in value):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        return None
    if not parsed.netloc or parsed.username is not None or parsed.password is not None:
        return None
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None

    authority = _parse_authority(parsed.netloc)
    if authority is None:
        return None
    return _Origin(scheme=scheme, authority=authority)


def _read_trusted_hosts(value: str | None) -> tuple[_TrustedHost, ...]:
    trusted: list[_TrustedHost] = []
    for raw_item in (value or "").split(","):
        item = raw_item.strip()
        if not item or item == "*":
            # A global wildcard would restore the DNS-rebinding vulnerability.
            continue
        wildcard = item.startswith("*.")
        authority = _parse_authority(item[2:] if wildcard else item)
        if authority is None or (wildcard and authority.ip is not None):
            continue
        trusted.append(
            _TrustedHost(
                hostname=authority.hostname,
                port=authority.port,
                wildcard=wildcard,
            )
        )
    return tuple(trusted)


def _read_trusted_origins(value: str | None) -> tuple[_Origin, ...]:
    origins: list[_Origin] = []
    for raw_item in (value or "").split(","):
        origin = _parse_origin(raw_item.strip())
        if origin is not None and origin not in origins:
            origins.append(origin)
    return tuple(origins)


def _header_values(scope, name: bytes) -> list[str] | None:
    values: list[str] = []
    for key, value in scope.get("headers") or ():
        if not isinstance(key, bytes) or not isinstance(value, bytes):
            return None
        if key.lower() != name:
            continue
        try:
            values.append(value.decode("ascii"))
        except UnicodeDecodeError:
            return None
    return values


def _effective_port(authority: _Authority, scheme: str) -> int | None:
    return authority.port if authority.port is not None else _HTTP_DEFAULT_PORTS.get(scheme)


def _origins_match(left: _Origin, right: _Origin) -> bool:
    return (
        left.scheme == right.scheme
        and left.authority.hostname == right.authority.hostname
        and _effective_port(left.authority, left.scheme)
        == _effective_port(right.authority, right.scheme)
    )


class HostOriginGuardMiddleware:
    """Block DNS-rebinding Host values and cross-origin browser WebSockets."""

    def __init__(
        self,
        app,
        *,
        trusted_hosts: str | None = None,
        trusted_origins: str | None = None,
    ):
        self.app = app
        self.trusted_hosts = _read_trusted_hosts(
            os.getenv(TRUSTED_HOSTS_ENV) if trusted_hosts is None else trusted_hosts
        )
        self.trusted_origins = _read_trusted_origins(
            os.getenv(TRUSTED_ORIGINS_ENV) if trusted_origins is None else trusted_origins
        )

    async def __call__(self, scope, receive, send):
        scope_type = scope.get("type")
        if scope_type not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        host_values = _header_values(scope, b"host")
        if host_values is None or len(host_values) != 1:
            await self._reject(scope_type, send)
            return
        authority = _parse_authority(host_values[0])
        if authority is None or not self._host_is_allowed(authority, scope):
            await self._reject(scope_type, send)
            return

        if scope_type == "websocket" and not self._websocket_origin_is_allowed(
            authority, scope
        ):
            await self._reject(scope_type, send)
            return

        await self.app(scope, receive, send)

    def _host_is_allowed(self, authority: _Authority, scope) -> bool:
        if authority.ip is not None or authority.hostname == "localhost":
            return True

        # Starlette's TestClient uses this exact synthetic pair.  Restricting
        # both sides prevents "testserver" becoming a production hostname.
        client = scope.get("client")
        if (
            authority.hostname == "testserver"
            and isinstance(client, (tuple, list))
            and len(client) >= 1
            and client[0] == "testclient"
        ):
            return True

        request_scheme = str(scope.get("scheme") or "http").lower()
        request_port = _effective_port(authority, request_scheme)
        for trusted in self.trusted_hosts:
            hostname_matches = authority.hostname == trusted.hostname
            if trusted.wildcard:
                hostname_matches = authority.hostname.endswith("." + trusted.hostname)
            if not hostname_matches:
                continue
            if trusted.port is None or trusted.port == request_port:
                return True
        return False

    def _websocket_origin_is_allowed(self, host: _Authority, scope) -> bool:
        origin_values = _header_values(scope, b"origin")
        if origin_values is None or len(origin_values) > 1:
            return False
        if not origin_values:
            return True

        origin = _parse_origin(origin_values[0])
        if origin is None:
            return False

        websocket_scheme = str(scope.get("scheme") or "ws").lower()
        expected_origin_scheme = "https" if websocket_scheme == "wss" else "http"
        if (
            origin.scheme == expected_origin_scheme
            and origin.authority.hostname == host.hostname
            and _effective_port(origin.authority, origin.scheme)
            == _effective_port(host, websocket_scheme)
        ):
            return True

        # Local frontends legitimately connect across N.E.K.O's loopback ports
        # and may mix localhost with 127.0.0.1 / ::1.
        if host.is_loopback and origin.authority.is_loopback:
            return True

        return any(_origins_match(origin, trusted) for trusted in self.trusted_origins)

    async def _reject(self, scope_type: str, send) -> None:
        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return

        body = json.dumps(
            {"ok": False, "error_code": "untrusted_host", "error": "Invalid Host header"},
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
