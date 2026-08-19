"""Unit tests for the forward WebSocket connection (napcat_forward).

Covers QQClient's forward branch: dial URL / auth, receive -> normalization,
``call_action`` echo correlation, connect/disconnect state, and the
``_make_qq_connection`` mode dispatch. The reverse mode (``websockets.serve``)
and the open-platform channel are not covered here; see their own regression
tests.

Follows the repo ``tests/unit`` conventions: sync tests use ``asyncio.run``,
and ``websockets.connect`` is mocked (see the AsyncMock usage in
``test_connectivity_endpoint.py``).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from plugin.plugins.qq_auto_reply.qq_client import QQClient


class _FakeWS:
    """A minimal WS client protocol stand-in: supports send/close, and async
    iteration ends immediately."""

    def __init__(self):
        self.close_code = None
        self.sent: list[str] = []
        self.closed = False

    async def send(self, payload):
        self.sent.append(payload)

    async def close(self):
        self.closed = True

    def __aiter__(self):
        async def _gen():
            return
            yield  # pragma: no cover

        return _gen()


def _make_forward_client(*, onebot_url: str = "ws://127.0.0.1:3001", token: str = "") -> QQClient:
    client = QQClient(onebot_url=onebot_url, token=token, direction="forward")
    # Skip the async login-info fetch on first connect so no background task lingers.
    client._self_id = "10001"
    return client


# ---- connect / dial ---------------------------------------------------

async def _pump(cond, *, timeout: float = 1.0) -> None:
    """Run the event loop until ``cond()`` is truthy (used to wait for the
    background receive loop to finish dialing)."""
    import time as _t

    deadline = _t.monotonic() + timeout
    while not cond():
        if _t.monotonic() > deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(0)


def test_forward_dial_uses_token_header_and_query():
    """``_dial_forward`` dials directly: the URL carries access_token and the
    request carries a Bearer header."""
    async def run():
        client = _make_forward_client(onebot_url="ws://192.168.1.5:3001", token="s3cret")
        fake = _FakeWS()
        with patch("websockets.connect", AsyncMock(return_value=fake)) as mock_connect:
            assert await client._dial_forward() is True
            url = mock_connect.call_args.args[0]
            kwargs = mock_connect.call_args.kwargs
        assert url == "ws://192.168.1.5:3001?access_token=s3cret"
        assert kwargs["additional_headers"] == {"Authorization": "Bearer s3cret"}
        assert kwargs["ping_interval"] == 30
        assert kwargs["ping_timeout"] == 10
        # The outbound socket is installed into the shared fields, so the send
        # path is reused unchanged.
        assert client._main_client is fake
        assert client._connected_clients == {fake}
        assert client.is_connected()
        assert await client.get_login_status() == {
            "status": "online", "self_id": "10001", "nickname": None,
        }
        await client.disconnect()
        assert client._ws is None
        assert not client.is_connected()

    asyncio.run(run())


def test_forward_connect_starts_receive_loop_and_dials():
    """``connect()`` is non-blocking: after it returns, the background receive
    loop dials and installs the socket."""
    async def run():
        client = _make_forward_client(onebot_url="ws://127.0.0.1:3001", token="t")
        fake = _FakeWS()
        with patch("websockets.connect", AsyncMock(return_value=fake)) as mock_connect:
            await client.connect()
            await _pump(lambda: client.is_connected())
            assert mock_connect.called
            assert client._receive_task is not None
            assert not client._receive_task.done()
        await client.disconnect()

    asyncio.run(run())


def test_redact_url_masks_access_token():
    """The success log must not leak the token that _forward_ws_url appends."""
    client = _make_forward_client(token="s3cret")
    redacted = client._redact_url("ws://192.168.1.5:3001?access_token=s3cret")
    assert "s3cret" not in redacted
    assert "access_token=***" in redacted
    # A URL without a token is returned unchanged
    assert client._redact_url("ws://192.168.1.5:3001/ws") == "ws://192.168.1.5:3001/ws"


def test_redact_text_masks_token_in_exception():
    """Exception messages may embed the full URL (and token); they must be masked."""
    client = _make_forward_client(token="s3cret")
    redacted = client._redact_text(
        "invalid URI: ws://192.168.1.5:3001?access_token=s3cret"
    )
    assert "s3cret" not in redacted
    assert "access_token=***" in redacted


def test_redact_text_masks_url_encoded_token():
    """The URL-encoded token form (e.g. a+b/c= -> a%2Bb%2Fc%3D) must also be masked,
    otherwise a token with special chars leaks into logs via encoded exception URLs."""
    client = _make_forward_client(token="a+b/c=")
    redacted = client._redact_text(
        "invalid URI: ws://192.168.1.5:3001?access_token=a%2Bb%2Fc%3D"
    )
    assert "a%2Bb%2Fc%3D" not in redacted
    assert "a+b/c=" not in redacted
    assert "***" in redacted


def test_forward_connect_nonblocking_when_dial_fails():
    """``connect()`` does not fail when NapCat is not ready: the background loop
    retries and the connection stays down."""
    async def run():
        client = _make_forward_client()
        with patch(
            "websockets.connect",
            AsyncMock(side_effect=ConnectionRefusedError("conn refused")),
        ) as mock_connect:
            await client.connect()
            await _pump(lambda: mock_connect.called)
        assert client._receive_task is not None
        assert not client.is_connected()
        await client.disconnect()

    asyncio.run(run())


def test_forward_connect_is_idempotent_when_loop_running():
    async def run():
        client = _make_forward_client()
        fake = _FakeWS()
        with patch("websockets.connect", AsyncMock(return_value=fake)) as mock_connect:
            await client.connect()
            await _pump(lambda: client.is_connected())
            assert mock_connect.call_count == 1
            await client.connect()  # Loop is already running -> do not start another
            assert mock_connect.call_count == 1
        await client.disconnect()

    asyncio.run(run())


def test_forward_connect_appends_token_once_when_url_already_has_query():
    async def run():
        client = _make_forward_client(
            onebot_url="ws://127.0.0.1:3001/ws?access_token=old", token="new"
        )
        # If access_token is already present, do not append it again; the
        # Authorization header still carries the token.
        assert "old" in client._forward_ws_url()
        assert "access_token=new" not in client._forward_ws_url()
        fake = _FakeWS()
        with patch("websockets.connect", AsyncMock(return_value=fake)):
            await client.connect()
        await client.disconnect()

    asyncio.run(run())


# ---- is_connected / status ---------------------------------------------

def test_forward_is_connected_false_when_socket_dead():
    client = _make_forward_client()
    dead = _FakeWS()
    dead.close_code = 1006  # Simulate a socket closed after the connection dropped
    client._connected_clients = {dead}
    client._main_client = dead
    assert not client.is_connected()
    # The dead socket is cleaned up
    assert client._connected_clients == set()
    assert asyncio.run(client.get_login_status()) == {
        "status": "offline", "self_id": None, "nickname": None,
    }


def test_forward_get_login_status_offline_when_self_id_unknown():
    client = _make_forward_client()
    client._self_id = ""
    online = _FakeWS()
    client._connected_clients = {online}
    client._main_client = online
    assert client.is_connected()
    assert asyncio.run(client.get_login_status())["status"] == "offline"


# ---- receive -> normalization ------------------------------------------

def test_forward_process_incoming_to_receive_private():
    async def run():
        client = _make_forward_client()
        client._message_queue = asyncio.Queue(maxsize=100)
        raw = json.dumps({
            "post_type": "message",
            "message_type": "private",
            "user_id": 222,
            "self_id": 10001,
            "raw_message": "hello there",
            "message": [{"type": "text", "data": {"text": "hello there"}}],
            "message_id": "m1",
            "time": 1700000000,
            "sender": {"nickname": "Xiaoming"},
        })
        await client._process_incoming(raw)
        msg = await client.receive_message(timeout=1.0)
        assert msg is not None
        assert msg["message_type"] == "private"
        assert msg["channel"] == "onebot"
        assert msg["user_id"] == "222"
        assert msg["user_nickname"] == "Xiaoming"
        assert msg["content"] == "hello there"
        assert msg["message_id"] == "m1"

    asyncio.run(run())


# ---- call_action echo correlation ---------------------------------------

def test_forward_call_action_echo_correlation():
    async def run():
        client = _make_forward_client()
        fake = _FakeWS()
        client._ws = fake
        client._main_client = fake
        client._connected_clients = {fake}

        task = asyncio.create_task(client.call_action("get_login_info"))
        await asyncio.sleep(0)  # Let call_action send and register the future
        assert fake.sent, "call_action should have written the request to the socket"
        payload = json.loads(fake.sent[0])
        echo = payload["echo"]
        assert payload["action"] == "get_login_info"
        assert echo in client._pending_actions

        # Feed the response carrying the same echo back through the receive
        # entry point -> the correlation resolves the future.
        await client._process_incoming(json.dumps({
            "status": "ok", "data": {"user_id": 10001, "nickname": "bot"}, "echo": echo,
        }))
        data = await asyncio.wait_for(task, timeout=2.0)
        assert data == {"user_id": 10001, "nickname": "bot"}
        assert echo not in client._pending_actions

    asyncio.run(run())


# ---- disconnect ---------------------------------------------------------

def test_forward_disconnect_cancels_receive_task_and_closes_ws():
    async def run():
        client = _make_forward_client()
        fake = _FakeWS()
        client._ws = fake
        client._main_client = fake
        client._connected_clients = {fake}
        client._receive_task = asyncio.create_task(asyncio.sleep(3600))
        await client.disconnect()
        assert client._receive_task is None
        assert fake.closed
        assert client._ws is None
        assert client._main_client is None
        assert not client.is_connected()

    asyncio.run(run())


# ---- _make_qq_connection dispatch ---------------------------------------

def _plugin_stub(*, mode: str):
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    plugin = QQAutoReplyPlugin.__new__(QQAutoReplyPlugin)
    plugin._qq_settings = {
        "qq_connection_mode": mode,
        "onebot_url": "ws://192.168.1.5:3001",
        "token": "t",
        "qq_open_app_id": "",
        "qq_open_client_secret": "",
    }
    plugin.logger = None
    plugin._emit_log = lambda level, msg: None
    plugin._describe_reply_image = None
    plugin._transcribe_voice = None
    return plugin


def test_make_qq_connection_dispatches_napcat_forward():
    plugin = _plugin_stub(mode="napcat_forward")
    client = plugin._make_qq_connection()
    assert isinstance(client, QQClient)
    assert client.direction == "forward"
    assert client.mode == "napcat_forward"


def test_make_qq_connection_defaults_to_reverse():
    plugin = _plugin_stub(mode="napcat")
    client = plugin._make_qq_connection()
    assert isinstance(client, QQClient)
    assert client.direction == "reverse"
    assert client.mode == "napcat"


# ---- forward mode also manages local NapCat -----------------------------

def test_napcat_service_forward_timeout_wording():
    """In forward mode the connect-timeout message is the forward-mode wording,
    not the reverse-mode "no client connected" constant."""
    from types import SimpleNamespace as _NS

    from plugin.plugins.qq_auto_reply.napcat_service import QQNapcatService

    plugin = _NS(
        _qq_settings={"qq_connection_mode": "napcat_forward"},
        _startup_error=None,
        qq_client=_NS(is_connected=lambda: False),
        logger=_NS(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
        _napcat_process=None,
        _manages_napcat_process=False,
    )
    service = QQNapcatService(plugin)
    asyncio.run(service.wait_for_onebot_ready(timeout_seconds=0.3, poll_interval=0.05))

    err = plugin._startup_error or ""
    assert err == service._transient_timeout_error()
    assert err != QQNapcatService.TRANSIENT_TIMEOUT_ERROR
    # A transient error is not a hard failure: polling must not short-circuit.
    assert not service.has_hard_startup_error()


def test_ensure_napcat_started_launches_for_forward():
    """Forward mode also launches the local NapCat process (``_ensure_napcat_started``
    no longer early-returns)."""
    from types import SimpleNamespace as _NS
    from unittest.mock import AsyncMock

    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    for mode, expect_launch in (("napcat_forward", True), ("napcat", True), ("open_platform", False)):
        plugin = QQAutoReplyPlugin.__new__(QQAutoReplyPlugin)
        plugin._qq_settings = {"qq_connection_mode": mode}
        ensure = AsyncMock()
        plugin.napcat_service = _NS(ensure_napcat_started=ensure)
        asyncio.run(plugin._ensure_napcat_started())
        assert ensure.called is expect_launch, f"mode={mode}"

