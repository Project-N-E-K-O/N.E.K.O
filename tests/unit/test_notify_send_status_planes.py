"""send_status reports the FRONTEND plane only; the monitor mirror is separate."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.core.notify import NotifyMixin


class _WebSocketState:
    """Self-referential stand-in for starlette's ``WebSocketState.CONNECTED``.

    ``send_status`` reads ``ws.client_state == ws.client_state.CONNECTED``, so
    the live state object has to expose the member it is compared against.
    """


_CONNECTED = _WebSocketState()
_CONNECTED.CONNECTED = _CONNECTED


class _ConnectedSocket:
    def __init__(self) -> None:
        self.client_state = _CONNECTED
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


class _StatusHarness(NotifyMixin):
    def __init__(self) -> None:
        self.websocket = _ConnectedSocket()
        self.sync_message_queue = MagicMock()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_status_reports_delivered_when_the_monitor_mirror_fails() -> None:
    harness = _StatusHarness()
    harness.sync_message_queue.put.side_effect = ValueError("monitor queue closed")

    delivered = await harness.send_status('{"code": "X"}')

    # The frontend really did receive it, so the caller's per-plane ledger must
    # be allowed to commit; otherwise every later mic frame re-sends this same
    # status to a client that already has it.
    assert harness.websocket.sent
    assert delivered is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_status_reports_undelivered_when_the_socket_fails() -> None:
    harness = _StatusHarness()
    harness.websocket.send_text = AsyncMock(side_effect=ValueError("socket gone"))

    delivered = await harness.send_status('{"code": "X"}')

    assert delivered is False
    harness.sync_message_queue.put.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_status_reports_undelivered_without_a_connected_socket() -> None:
    harness = _StatusHarness()
    harness.websocket = None

    assert await harness.send_status('{"code": "X"}') is False
