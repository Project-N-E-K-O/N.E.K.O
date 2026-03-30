import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from qq_client import QQClient


@pytest.fixture
def client():
    return QQClient("ws://127.0.0.1:3001", token="test_token")


@pytest.fixture
def client_no_token():
    return QQClient("ws://127.0.0.1:3001")


# ── connect ──────────────────────────────────────────────────────────────────

class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_appends_token_to_url(self, client):
        mock_ws = MagicMock()
        with patch("qq_client.websockets.connect", new_callable=AsyncMock, return_value=mock_ws) as mock_connect:
            await client.connect()
            call_url = mock_connect.call_args[0][0]
            assert "access_token=test_token" in call_url

    @pytest.mark.asyncio
    async def test_connect_sends_auth_header(self, client):
        mock_ws = MagicMock()
        with patch("qq_client.websockets.connect", new_callable=AsyncMock, return_value=mock_ws) as mock_connect:
            await client.connect()
            headers = mock_connect.call_args[1].get("additional_headers", {})
            assert headers.get("Authorization") == "Bearer test_token"

    @pytest.mark.asyncio
    async def test_connect_no_token_no_auth_header(self, client_no_token):
        mock_ws = MagicMock()
        with patch("qq_client.websockets.connect", new_callable=AsyncMock, return_value=mock_ws) as mock_connect:
            await client_no_token.connect()
            headers = mock_connect.call_args[1].get("additional_headers")
            assert not headers

    @pytest.mark.asyncio
    async def test_connect_raises_on_failure(self, client):
        with patch("qq_client.websockets.connect", side_effect=ConnectionRefusedError("refused")):
            with pytest.raises(ConnectionRefusedError):
                await client.connect()


# ── disconnect ────────────────────────────────────────────────────────────────

class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_closes_websocket(self, client):
        mock_ws = AsyncMock()
        client.ws = mock_ws
        client._receive_task = asyncio.create_task(asyncio.sleep(10))
        await client.disconnect()
        mock_ws.close.assert_called_once()
        assert client.ws is None

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected_does_not_raise(self, client):
        await client.disconnect()  # ws is None, should be fine


# ── receive_message ───────────────────────────────────────────────────────────

def _make_private_msg(user_id="123", content="hello", nickname="Alice"):
    return {
        "post_type": "message",
        "message_type": "private",
        "user_id": user_id,
        "raw_message": content,
        "message_id": 1,
        "time": 1700000000,
        "sender": {"nickname": nickname},
        "message": [],
    }


def _make_group_msg(group_id="999", user_id="123", content="hi", at_self=False, self_id="bot1"):
    segments = []
    if at_self:
        segments.append({"type": "at", "data": {"qq": self_id}})
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": group_id,
        "user_id": user_id,
        "raw_message": content,
        "message_id": 2,
        "time": 1700000001,
        "self_id": self_id,
        "sender": {"card": "Bob"},
        "message": segments,
    }


class TestReceiveMessage:
    @pytest.mark.asyncio
    async def test_receive_private_message(self, client):
        raw = _make_private_msg()
        await client._message_queue.put(raw)
        msg = await client.receive_message(timeout=1.0)
        assert msg["message_type"] == "private"
        assert msg["user_id"] == "123"
        assert msg["content"] == "hello"
        assert msg["user_nickname"] == "Alice"
        assert "group_id" not in msg

    @pytest.mark.asyncio
    async def test_receive_group_message(self, client):
        raw = _make_group_msg()
        await client._message_queue.put(raw)
        msg = await client.receive_message(timeout=1.0)
        assert msg["message_type"] == "group"
        assert msg["group_id"] == "999"
        assert msg["user_id"] == "123"
        assert msg["is_at_bot"] is False

    @pytest.mark.asyncio
    async def test_receive_group_message_at_bot(self, client):
        raw = _make_group_msg(at_self=True, self_id="bot1")
        await client._message_queue.put(raw)
        msg = await client.receive_message(timeout=1.0)
        assert msg["is_at_bot"] is True

    @pytest.mark.asyncio
    async def test_receive_returns_none_on_timeout(self, client):
        msg = await client.receive_message(timeout=0.05)
        assert msg is None

    @pytest.mark.asyncio
    async def test_user_id_is_string(self, client):
        raw = _make_private_msg(user_id=123456)
        await client._message_queue.put(raw)
        msg = await client.receive_message(timeout=1.0)
        assert isinstance(msg["user_id"], str)

    @pytest.mark.asyncio
    async def test_sender_card_used_as_nickname_fallback(self, client):
        raw = _make_group_msg()
        raw["sender"] = {"card": "CardName"}
        await client._message_queue.put(raw)
        msg = await client.receive_message(timeout=1.0)
        assert msg["user_nickname"] == "CardName"


# ── _check_at_bot ─────────────────────────────────────────────────────────────

class TestCheckAtBot:
    def test_at_bot_by_self_id(self, client):
        raw = _make_group_msg(at_self=True, self_id="bot42")
        assert client._check_at_bot(raw) is True

    def test_at_all_counts_as_at_bot(self, client):
        raw = {
            "self_id": "bot1",
            "message": [{"type": "at", "data": {"qq": "all"}}],
        }
        assert client._check_at_bot(raw) is True

    def test_at_other_user_not_at_bot(self, client):
        raw = {
            "self_id": "bot1",
            "message": [{"type": "at", "data": {"qq": "other_user"}}],
        }
        assert client._check_at_bot(raw) is False

    def test_no_at_segment(self, client):
        raw = {"self_id": "bot1", "message": [{"type": "text", "data": {"text": "hello"}}]}
        assert client._check_at_bot(raw) is False

    def test_message_not_list(self, client):
        raw = {"self_id": "bot1", "message": "[CQ:at,qq=bot1]"}
        assert client._check_at_bot(raw) is False


# ── send_message ──────────────────────────────────────────────────────────────

class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_private_message(self, client):
        mock_ws = AsyncMock()
        client.ws = mock_ws
        await client.send_message("123", "hello")
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["action"] == "send_private_msg"
        assert sent["params"]["user_id"] == 123
        assert sent["params"]["message"] == "hello"

    @pytest.mark.asyncio
    async def test_send_group_message(self, client):
        mock_ws = AsyncMock()
        client.ws = mock_ws
        await client.send_group_message("999", "hi group")
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["action"] == "send_group_msg"
        assert sent["params"]["group_id"] == 999
        assert sent["params"]["message"] == "hi group"

    @pytest.mark.asyncio
    async def test_send_raises_when_not_connected(self, client):
        with pytest.raises(RuntimeError, match="Not connected"):
            await client.send_message("123", "hello")

    @pytest.mark.asyncio
    async def test_send_group_raises_when_not_connected(self, client):
        with pytest.raises(RuntimeError, match="Not connected"):
            await client.send_group_message("999", "hi")


# ── _receive_loop ─────────────────────────────────────────────────────────────

class TestReceiveLoop:
    @pytest.mark.asyncio
    async def test_receive_loop_queues_private_and_group_messages(self, client):
        private_raw = json.dumps(_make_private_msg())
        group_raw = json.dumps(_make_group_msg())
        # Non-message event that should be ignored
        other_raw = json.dumps({"post_type": "notice", "notice_type": "group_increase"})

        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(side_effect=[
            private_raw,
            group_raw,
            other_raw,
            asyncio.CancelledError(),
        ])
        client.ws = mock_ws

        task = asyncio.create_task(client._receive_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert client._message_queue.qsize() == 2
