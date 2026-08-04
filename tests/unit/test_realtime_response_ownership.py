import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_realtime_client._response_arbiter import (
    RealtimeResponseArbiter,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_idless_orphan_releases_at_its_own_stale_deadline():
    async def send(_event):
        return None

    arbiter = RealtimeResponseArbiter(send)
    arbiter._server_response_max_age = 0.01
    try:
        assert arbiter.notify_response_created({"type": "response.created"})
        assert not arbiter._idle.is_set()

        await arbiter.wait_until_idle(timeout=0.2)

        assert arbiter._idless_server_response_at is None
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_idless_orphan_survives_an_id_bearing_owner_terminal():
    async def send(_event):
        return None

    arbiter = RealtimeResponseArbiter(send)
    try:
        owner = await arbiter.enqueue(source="owner")
        await asyncio.wait_for(owner.sent, 0.2)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-owner"}}
        )
        arbiter.notify_response_created({"type": "response.created"})

        successor = await arbiter.enqueue(source="successor")
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-owner"}}
        )
        await asyncio.sleep(0)

        assert successor.sent.done() is False
        assert arbiter._idless_server_response_at is not None

        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"status": "completed"}}
        )
        await asyncio.wait_for(successor.sent, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_late_created_is_one_shot_and_idless_successor_completes():
    async def send(_event):
        return None

    arbiter = RealtimeResponseArbiter(send)
    try:
        previous = await arbiter.enqueue(source="previous")
        await asyncio.wait_for(previous.sent, 0.2)
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"status": "completed"}}
        )
        with pytest.raises(RuntimeError, match="before response.created"):
            await asyncio.wait_for(previous.done, 0.2)

        successor = await arbiter.enqueue(source="successor")
        await asyncio.sleep(0)
        assert successor.sent.done() is False

        assert not arbiter.notify_response_created({"type": "response.created"})
        await asyncio.wait_for(successor.sent, 0.2)

        assert arbiter.notify_response_created({"type": "response.created"})
        await asyncio.wait_for(successor.started, 0.2)
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"status": "completed"}}
        )
        await asyncio.wait_for(successor.done, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retired_created_gate_expires_when_announcement_never_arrives(
    monkeypatch,
):
    from main_logic.omni_realtime_client import _response_arbiter as arbiter_module

    monkeypatch.setattr(
        arbiter_module,
        "_SERVER_VAD_RESPONSE_STARTED_TIMEOUT",
        0.01,
    )

    async def send(_event):
        return None

    arbiter = RealtimeResponseArbiter(send)
    try:
        previous = await arbiter.enqueue(source="previous")
        await asyncio.wait_for(previous.sent, 0.2)
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"status": "completed"}}
        )
        with pytest.raises(RuntimeError, match="before response.created"):
            await asyncio.wait_for(previous.done, 0.2)

        successor = await arbiter.enqueue(source="successor")
        await asyncio.wait_for(successor.sent, 0.2)
        assert arbiter.notify_response_created({"type": "response.created"})
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_owner_release_retires_its_inflight_cancel_send():
    sent: list[str] = []
    cancel_entered = asyncio.Event()
    cancel_gate = asyncio.Event()

    async def send(event):
        if event["type"] == "response.cancel":
            cancel_entered.set()
            await cancel_gate.wait()
        sent.append(event["type"])

    arbiter = RealtimeResponseArbiter(send)
    try:
        ticket = await arbiter.enqueue(
            source="owner",
            events_before_response=(
                {
                    "type": "conversation.item.create",
                    "event_id": "item-event",
                    "item": {"id": "item-target", "role": "user"},
                },
            ),
            response_event={
                "type": "response.create",
                "event_id": "response-event",
            },
            ack_expected=True,
            expected_item_id="item-target",
            expected_item_role="user",
            item_ack_timeout=0.01,
        )
        await asyncio.wait_for(ticket.sent, 0.2)

        arbiter.notify_error("item-event", "item rejected late")
        await asyncio.wait_for(cancel_entered.wait(), 0.2)
        arbiter.notify_response_terminal(
            {
                "type": "response.done",
                "response": {"id": "resp-owner", "status": "completed"},
            }
        )
        for _ in range(20):
            if not arbiter._cancel_send_tasks:
                break
            await asyncio.sleep(0)

        cancel_gate.set()
        await asyncio.sleep(0)
        assert "response.cancel" not in sent
    finally:
        cancel_gate.set()
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transport_ignores_retired_created_before_exposing_successor():
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
    )
    client.ws = AsyncMock()
    client.ws.__aiter__.return_value = [
        json.dumps({"type": "response.created"}),
        json.dumps(
            {"type": "response.created", "response": {"id": "resp-successor"}}
        ),
    ]
    client._response_arbiter.notify_response_created = Mock(
        side_effect=[False, True]
    )
    client._close_failed_transport = AsyncMock()

    try:
        await client.handle_messages()

        assert client._response_created_total == 2
        assert client._announces_responses is True
        assert client._current_response_id == "resp-successor"
        assert client._is_responding is True
    finally:
        await client.close()
