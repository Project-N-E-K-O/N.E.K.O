"""Startup acknowledgment and queued text use distinct, owned lifetimes."""

import asyncio

import pytest

from main_logic import core as core_module
from main_logic.core import lifecycle
from tests.unit.session_handoff_harness import (
    ProviderClient, drain_manager, make_full_manager,
)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("nested_kind", ["output", "lifecycle"])
async def test_started_ack_does_not_wait_for_queued_response_and_retirement_owns_flush(monkeypatch, nested_kind):
    manager, created, clients = await make_full_manager(monkeypatch)
    accepted = asyncio.Event()
    cancelled = asyncio.Event()
    submissions = []
    ack_entered = asyncio.Event()
    release_ack = asyncio.Event()
    original_send = manager.websocket.send_json

    async def send_json(payload):
        if payload.get("type") == "session_started":
            ack_entered.set()
            await release_ack.wait()
        await original_send(payload)

    manager.websocket.send_json = send_json

    class OfflineService(ProviderClient, core_module.OmniOfflineClient):
        def __init__(self, **kwargs):
            ProviderClient.__init__(self, **kwargs)
            self._pending_images = []
            clients.append(self)
            created.put_nowait(self)

        def update_max_response_length(self, *args, **kwargs):
            pass

        async def stream_text(self, text, **kwargs):
            submissions.append(text)
            callback = kwargs.get("on_turn_committed")
            if callable(callback):
                callback()
            if nested_kind == "output":
                await self.on_text_delta("accepted response", False)
            else:
                await manager._run_owned_lifecycle_callback(
                    self, self.on_text_delta, "accepted response", False,
                )
            accepted.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    monkeypatch.setattr(lifecycle, "OmniOfflineClient", OfflineService)
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda *args: None)
    starting = asyncio.create_task(manager.start_session(
        manager.websocket, input_mode="text", request_id="queued-text",
    ))
    try:
        client = await asyncio.wait_for(created.get(), 2)
        await manager.stream_data({"input_type": "text", "data": "first input"})
        client.allow_connect.set()
        await asyncio.wait_for(ack_entered.wait(), 2)
        assert manager.session_ready
        await asyncio.wait_for(manager.stream_data({"input_type": "text", "data": "during ack"}), 0.2)
        assert submissions == [], "input must remain queued until its startup acknowledgment finishes"
        release_ack.set()
        await asyncio.wait_for(accepted.wait(), 2)
        await asyncio.wait_for(asyncio.shield(starting), 0.2)
        assert any(item.get("type") == "session_started" for item in manager.websocket.messages)
        assert manager.session_ready
        record = manager._connection_record(client)
        active_flushes = [task for task in record.callbacks if not task.done()]
        assert len(active_flushes) == 1, "nested output must retain its enclosing flush registration"
        await manager.stream_data({"input_type": "text", "data": "second input"})
        assert submissions == ["first input"]
        assert [item["data"] for item in manager.pending_input_data] == ["second input"]
        await asyncio.wait_for(manager.end_session(by_server=True), 2)
        assert cancelled.is_set(), "retirement must cancel the actual streaming flush"
        assert all(task.done() for task in active_flushes)
        assert manager.pending_input_data == []
        assert manager._pending_input_flush_scheduled is None
    finally:
        release_ack.set()
        await asyncio.wait_for(drain_manager(manager, clients, starting), 3)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_unscheduled_flush_cannot_clear_successor_reservation(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    client = ProviderClient()
    clients.append(client)
    manager.session = client
    old_reservation = object()
    manager._pending_input_flush_scheduled = old_reservation
    flushing = manager._schedule_session_input_flush(old_reservation)
    flushing.cancel()  # Cancel before the coroutine has entered its first line.
    successor_reservation = object()
    manager._pending_input_flush_scheduled = successor_reservation
    manager.pending_input_data = [{"input_type": "text", "data": "successor input"}]
    try:
        await asyncio.gather(flushing, return_exceptions=True)
        assert manager._pending_input_flush_scheduled is successor_reservation
        assert manager.pending_input_data == [{"input_type": "text", "data": "successor input"}]
        assert flushing not in manager._connection_record(client).callbacks
    finally:
        await asyncio.wait_for(drain_manager(manager, clients), 3)
