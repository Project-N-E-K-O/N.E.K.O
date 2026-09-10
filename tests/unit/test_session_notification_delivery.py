"""Notification routing uses captured start identity across socket waits."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketState

from main_logic.core.notify import NotifyMixin


class Socket:
    client_state = WebSocketState.CONNECTED

    def __init__(self, *, block=False, fail=False):
        self.payloads = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = fail
        if not block:
            self.release.set()

    async def send_text(self, data):
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("closed display")
        self.payloads.append(json.loads(data))


class Manager(NotifyMixin):
    def __init__(self, display, owner, requester):
        self.websocket = display
        self._voice_input_websocket = owner
        self._voice_lease_connection_id = "lease"
        self._voice_lease_owner = "main"
        self.operation = SimpleNamespace(request_id="request-a", websocket=requester)
        self.valid = True

    def _current_start_request(self):
        return self.operation

    def _check_start_operation(self, operation):
        if operation is not self.operation or not self.valid:
            raise asyncio.CancelledError()


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["preparing", "started", "failed"])
@pytest.mark.asyncio
async def test_notification_keeps_requester_when_display_and_lease_move(kind):
    display, owner, requester = Socket(), Socket(), Socket()
    manager = Manager(display, owner, requester)
    await getattr(manager, f"send_session_{kind}")("audio")
    for socket in (display, owner, requester):
        assert len(socket.payloads) == 1
        assert socket.payloads[0]["request_id"] == "request-a"


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["preparing", "started", "failed"])
@pytest.mark.parametrize("handoff", ["revoke", "replace"])
@pytest.mark.asyncio
async def test_notification_does_not_fan_out_after_handoff_during_display_send(kind, handoff):
    display, owner, requester = Socket(block=True), Socket(), Socket()
    manager = Manager(display, owner, requester)
    task = asyncio.create_task(getattr(manager, f"send_session_{kind}")("audio"))
    await display.entered.wait()
    if handoff == "revoke":
        manager.valid = False
    else:
        manager.operation = SimpleNamespace(request_id="request-b", websocket=Socket())
    display.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert owner.payloads == []
    assert requester.payloads == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_status_keeps_delivery_receipt_after_operation_replaced():
    from queue import Queue

    display = Socket(block=True)
    manager = Manager(display, Socket(), Socket())
    manager.sync_message_queue = Queue()
    sending = asyncio.create_task(manager.send_status('{"code":"ASR_INDEPENDENT_READY"}'))
    await asyncio.wait_for(display.entered.wait(), 1)
    manager.operation = SimpleNamespace(request_id="request-b", websocket=Socket())
    display.release.set()
    assert await sending is True
    assert len(display.payloads) == 1
    assert manager.sync_message_queue.empty()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_status_does_not_swallow_cancellation_during_socket_write():
    from queue import Queue

    display = Socket(block=True)
    manager = Manager(display, Socket(), Socket())
    manager.sync_message_queue = Queue()
    sending = asyncio.create_task(manager.send_status('{"code":"ASR_INDEPENDENT_READY"}'))
    await asyncio.wait_for(display.entered.wait(), 1)
    sending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sending
    assert display.payloads == []
    assert manager.sync_message_queue.empty()


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["preparing", "started", "failed"])
@pytest.mark.asyncio
async def test_display_failure_does_not_drop_addressed_or_lease_notification(kind):
    display, owner, requester = Socket(fail=True), Socket(), Socket()
    manager = Manager(display, owner, requester)
    await getattr(manager, f"send_session_{kind}")("audio")
    assert len(owner.payloads) == len(requester.payloads) == 1


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["preparing", "started", "failed"])
@pytest.mark.asyncio
async def test_requester_on_voice_plane_gets_only_one_copy(kind):
    display, owner = Socket(), Socket()
    manager = Manager(display, owner, owner)
    await getattr(manager, f"send_session_{kind}")("audio")
    assert len(owner.payloads) == 1
