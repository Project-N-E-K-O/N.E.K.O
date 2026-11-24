"""Security tests for OneBotClient reverse WebSocket binding and auth (F0524)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from utils.connection.onebot.onebot_client import OneBotClient
from utils.connection.qq.factory import create_qq_connection


def _fake_ws(*, path: str = "/", headers: dict[str, str] | None = None, remote_host: str = "127.0.0.1"):
    return SimpleNamespace(
        request=SimpleNamespace(path=path, headers=headers or {}),
        remote_address=(remote_host, 54321),
    )


def test_onebot_and_qq_factory_default_to_loopback_host():
    """F0524: empty/unspecified onebot_url must default to 127.0.0.1, never 0.0.0.0."""
    client = OneBotClient(onebot_url="")
    assert client._listen_host == "127.0.0.1"

    client.onebot_url = "ws://0.0.0.0:6199"
    assert client._listen_host == "0.0.0.0"
    client.onebot_url = ""
    assert client._listen_host == "127.0.0.1"

    conn = create_qq_connection({})
    assert isinstance(conn, OneBotClient)
    assert conn._listen_host == "127.0.0.1"
    assert conn.onebot_url == "ws://127.0.0.1:6199"


@pytest.mark.parametrize("bad_url", ["ws://192.168.1.50:6199", "ws://10.0.0.5:6199", "ws://[2001:db8::1]:6199"])
async def test_reverse_connect_refuses_non_loopback_without_token(bad_url: str):
    """F0524: reverse WS server must refuse explicit non-loopback non-wildcard interfaces when token is empty."""
    client = OneBotClient(onebot_url=bad_url, token="", direction="reverse")
    with pytest.raises(ValueError):
        await client.connect()


@pytest.mark.parametrize("wildcard_url", ["ws://0.0.0.0:6199", "ws://[::]:6199"])
async def test_reverse_connect_downgrades_wildcard_without_token_to_loopback_and_rejects_remote_peer(
    monkeypatch, wildcard_url: str
):
    """F0524: wildcard bind without token downgrades to 127.0.0.1 with warning, blocks remote peers, and restores on token."""
    served: list[tuple[str, int]] = []
    emitted: list[tuple[str, str]] = []

    async def fake_serve(_handler, *, host, port, **_kw):
        served.append((host, port))
        return SimpleNamespace(close=lambda: None, wait_closed=lambda: _noop())

    async def _noop():
        return None

    monkeypatch.setattr("utils.connection.onebot.onebot_client.websockets.serve", fake_serve)

    client = OneBotClient(
        onebot_url=wildcard_url,
        token="",
        direction="reverse",
        emit_log=lambda level, msg: emitted.append((level, msg)),
    )
    configured_host = client._listen_host
    await client.connect()

    assert served == [("127.0.0.1", 6199)]
    assert client._listen_host == configured_host
    assert client._bound_listen_host == "127.0.0.1"
    warn_logs = [entry for entry in emitted if entry[0] == "WARN"]
    assert len(warn_logs) == 1
    assert "127.0.0.1" in warn_logs[0][1]

    assert client._check_token(_fake_ws(remote_host="127.0.0.1")) is True
    assert client._check_token(_fake_ws(remote_host="::1")) is True
    assert client._check_token(_fake_ws(remote_host="203.0.113.9")) is False
    assert client._check_token(_fake_ws(remote_host="192.168.1.50")) is False

    await client.disconnect()
    assert client._bound_listen_host is None
    client.token = "strong-token"
    await client.connect()
    assert served == [("127.0.0.1", 6199), (configured_host, 6199)]


async def test_reverse_connect_allows_non_loopback_with_token_and_loopback_without_token(monkeypatch):
    """F0524: non-loopback with token or loopback without token calls websockets.serve."""
    served: list[tuple[str, int]] = []

    async def fake_serve(_handler, *, host, port, **_kw):
        served.append((host, port))
        return SimpleNamespace(close=lambda: None, wait_closed=lambda: None)

    monkeypatch.setattr("utils.connection.onebot.onebot_client.websockets.serve", fake_serve)

    c_nonlocal = OneBotClient(onebot_url="ws://0.0.0.0:6199", token="strong-token", direction="reverse")
    await c_nonlocal.connect()

    c_local = OneBotClient(onebot_url="ws://127.0.0.1:6200", token="", direction="reverse")
    await c_local.connect()

    assert served == [("0.0.0.0", 6199), ("127.0.0.1", 6200)]


def test_check_token_enforces_loopback_and_constant_time_token_comparison():
    """F0524: empty-token bypass is restricted to loopback peers on loopback/wildcard hosts; configured tokens require exact match."""
    c_remote_empty = OneBotClient(onebot_url="ws://192.168.1.50:6199", token="")
    assert c_remote_empty._check_token(_fake_ws(remote_host="127.0.0.1")) is False

    c_wildcard_empty = OneBotClient(onebot_url="ws://0.0.0.0:6199", token="")
    assert c_wildcard_empty._check_token(_fake_ws(remote_host="127.0.0.1")) is True
    assert c_wildcard_empty._check_token(_fake_ws(remote_host="::1")) is True
    assert c_wildcard_empty._check_token(_fake_ws(remote_host="203.0.113.9")) is False
    assert c_wildcard_empty._check_token(_fake_ws(remote_host="192.168.1.50")) is False
    assert c_wildcard_empty._check_token(_fake_ws(remote_host="")) is False

    c_local_empty = OneBotClient(onebot_url="ws://127.0.0.1:6199", token="")
    assert c_local_empty._check_token(_fake_ws(remote_host="127.0.0.1")) is True
    assert c_local_empty._check_token(_fake_ws(remote_host="::1")) is True
    assert c_local_empty._check_token(_fake_ws(remote_host="203.0.113.9")) is False
    assert c_local_empty._check_token(SimpleNamespace(remote_address=None, request=None)) is False
    assert c_local_empty._check_token(SimpleNamespace(remote_address=(), request=None)) is False
    assert c_local_empty._check_token(_fake_ws(remote_host="")) is False

    c_auth = OneBotClient(onebot_url="ws://0.0.0.0:6199", token="my-secret-token")
    assert c_auth._check_token(_fake_ws(path="/?access_token=my-secret-token")) is True
    assert c_auth._check_token(_fake_ws(headers={"Authorization": "Bearer my-secret-token"})) is True
    assert c_auth._check_token(_fake_ws(path="/?access_token=wrong")) is False
    assert c_auth._check_token(_fake_ws(headers={"Authorization": "Bearer wrong"})) is False
    assert c_auth._check_token(_fake_ws()) is False
