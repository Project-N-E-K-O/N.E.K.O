# -*- coding: utf-8 -*-
"""Tests for the DNS-rebinding Host and WebSocket Origin guard."""
from __future__ import annotations

import asyncio
import json

import pytest

from utils.host_origin_guard import HostOriginGuardMiddleware


def _run(coro):
    return asyncio.run(coro)


def _scope(
    scope_type: str,
    host: str | None,
    *,
    origin: str | None = None,
    scheme: str | None = None,
    client=("127.0.0.1", 41000),
    extra_headers=(),
):
    headers = []
    if host is not None:
        headers.append((b"host", host.encode("ascii")))
    if origin is not None:
        headers.append((b"origin", origin.encode("ascii")))
    headers.extend(extra_headers)
    return {
        "type": scope_type,
        "scheme": scheme or ("ws" if scope_type == "websocket" else "http"),
        "method": "GET",
        "path": "/probe",
        "headers": headers,
        "client": client,
        "server": ("127.0.0.1", 48911),
    }


async def _drive(middleware, scope):
    called = {"hit": False}

    async def downstream(_scope, _receive, _send):
        called["hit"] = True
        if _scope["type"] == "websocket":
            await _send({"type": "websocket.accept"})
        else:
            await _send({"type": "http.response.start", "status": 200, "headers": []})
            await _send({"type": "http.response.body", "body": b"ok"})

    middleware.app = downstream

    async def receive():
        if scope["type"] == "websocket":
            return {"type": "websocket.connect"}
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return called["hit"], sent


def _make(*, trusted_hosts="", trusted_origins=""):
    return HostOriginGuardMiddleware(
        app=None,
        trusted_hosts=trusted_hosts,
        trusted_origins=trusted_origins,
    )


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:48911",
        "127.0.0.1:48911",
        "192.168.10.25:48911",
        "[::1]:48911",
        "[2001:db8::25]:48911",
        "::1",
    ],
)
def test_http_accepts_loopback_and_ip_literal_hosts(host):
    hit, sent = _run(_drive(_make(), _scope("http", host)))
    assert hit is True
    assert sent[0]["status"] == 200


def test_http_rejects_dns_rebinding_hostname():
    hit, sent = _run(_drive(_make(), _scope("http", "attacker.example:48911")))
    assert hit is False
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 400
    payload = json.loads(sent[1]["body"])
    assert payload["error_code"] == "untrusted_host"


@pytest.mark.parametrize(
    "host",
    [None, "[::1", "localhost:0", "localhost:65536", "bad_host.example"],
)
def test_http_rejects_missing_or_malformed_host(host):
    hit, sent = _run(_drive(_make(), _scope("http", host)))
    assert hit is False
    assert sent[0]["status"] == 400


def test_http_rejects_duplicate_host_headers():
    scope = _scope("http", "localhost", extra_headers=[(b"host", b"127.0.0.1")])
    hit, sent = _run(_drive(_make(), scope))
    assert hit is False
    assert sent[0]["status"] == 400


def test_explicit_trusted_host_and_subdomain_pattern_are_allowed():
    middleware = _make(trusted_hosts="neko.example:8443,*.internal.example")

    exact_hit, _ = _run(
        _drive(middleware, _scope("http", "neko.example:8443", scheme="https"))
    )
    wildcard_hit, _ = _run(
        _drive(middleware, _scope("http", "desktop.internal.example:48911"))
    )
    bare_suffix_hit, sent = _run(
        _drive(middleware, _scope("http", "internal.example:48911"))
    )

    assert exact_hit is True
    assert wildcard_hit is True
    assert bare_suffix_hit is False
    assert sent[0]["status"] == 400


def test_global_trusted_host_wildcard_is_ignored():
    hit, sent = _run(
        _drive(_make(trusted_hosts="*"), _scope("http", "attacker.example"))
    )
    assert hit is False
    assert sent[0]["status"] == 400


def test_trusted_hosts_are_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("NEKO_TRUSTED_HOSTS", "proxy.neko.example")
    monkeypatch.delenv("NEKO_TRUSTED_ORIGINS", raising=False)
    middleware = HostOriginGuardMiddleware(app=None)

    hit, _ = _run(_drive(middleware, _scope("http", "proxy.neko.example")))
    assert hit is True


def test_testserver_is_limited_to_starlette_testclient_scope():
    testclient_scope = _scope(
        "http", "testserver", client=("testclient", 50000)
    )
    production_scope = _scope("http", "testserver", client=("127.0.0.1", 50000))

    testclient_hit, _ = _run(_drive(_make(), testclient_scope))
    production_hit, sent = _run(_drive(_make(), production_scope))

    assert testclient_hit is True
    assert production_hit is False
    assert sent[0]["status"] == 400


def test_http_only_checks_host_not_origin():
    hit, sent = _run(
        _drive(
            _make(),
            _scope("http", "127.0.0.1:48911", origin="https://attacker.example"),
        )
    )
    assert hit is True
    assert sent[0]["status"] == 200


def test_websocket_without_origin_is_allowed_for_native_clients():
    hit, sent = _run(
        _drive(_make(), _scope("websocket", "127.0.0.1:48911"))
    )
    assert hit is True
    assert sent[0]["type"] == "websocket.accept"


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("127.0.0.1:48911", "http://127.0.0.1:48911"),
        ("[::1]:48911", "http://[::1]:48911"),
    ],
)
def test_websocket_same_origin_is_allowed(host, origin):
    hit, sent = _run(
        _drive(_make(), _scope("websocket", host, origin=origin))
    )
    assert hit is True
    assert sent[0]["type"] == "websocket.accept"


def test_websocket_loopback_origins_can_cross_local_aliases_and_ports():
    hit, sent = _run(
        _drive(
            _make(),
            _scope(
                "websocket",
                "127.0.0.1:48916",
                origin="http://localhost:48911",
            ),
        )
    )
    assert hit is True
    assert sent[0]["type"] == "websocket.accept"


def test_websocket_cross_origin_is_rejected_with_policy_violation():
    hit, sent = _run(
        _drive(
            _make(),
            _scope(
                "websocket",
                "192.168.10.25:48911",
                origin="https://attacker.example",
            ),
        )
    )
    assert hit is False
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_websocket_duplicate_or_malformed_origin_is_rejected():
    duplicate_scope = _scope(
        "websocket",
        "127.0.0.1:48911",
        origin="http://127.0.0.1:48911",
        extra_headers=[(b"origin", b"http://localhost:48911")],
    )
    malformed_scope = _scope(
        "websocket", "127.0.0.1:48911", origin="null"
    )

    duplicate_hit, duplicate_sent = _run(_drive(_make(), duplicate_scope))
    malformed_hit, malformed_sent = _run(_drive(_make(), malformed_scope))

    assert duplicate_hit is False
    assert duplicate_sent[0]["code"] == 1008
    assert malformed_hit is False
    assert malformed_sent[0]["code"] == 1008


@pytest.mark.parametrize(
    ("configured_origin", "browser_origin"),
    [
        ("https://ui.neko.example", "https://ui.neko.example:443"),
        ("https://ui.neko.example:443", "https://ui.neko.example"),
    ],
)
def test_websocket_explicit_origin_allowlist_normalizes_default_port(
    configured_origin, browser_origin, monkeypatch
):
    monkeypatch.setenv("NEKO_TRUSTED_ORIGINS", configured_origin)
    monkeypatch.delenv("NEKO_TRUSTED_HOSTS", raising=False)
    middleware = HostOriginGuardMiddleware(app=None)
    hit, sent = _run(
        _drive(
            middleware,
            _scope(
                "websocket",
                "192.168.10.25:48916",
                origin=browser_origin,
            ),
        )
    )
    assert hit is True
    assert sent[0]["type"] == "websocket.accept"


def test_secure_websocket_requires_https_same_origin():
    middleware = _make(trusted_hosts="neko.example")
    secure_scope = _scope(
        "websocket",
        "neko.example",
        origin="https://neko.example",
        scheme="wss",
    )
    insecure_scope = _scope(
        "websocket",
        "neko.example",
        origin="http://neko.example",
        scheme="wss",
    )

    secure_hit, _ = _run(_drive(middleware, secure_scope))
    insecure_hit, sent = _run(_drive(middleware, insecure_scope))

    assert secure_hit is True
    assert insecure_hit is False
    assert sent[0]["code"] == 1008


def test_lifespan_scope_passes_through():
    middleware = _make()
    hit, _ = _run(
        _drive(middleware, {"type": "lifespan", "headers": []})
    )
    assert hit is True
