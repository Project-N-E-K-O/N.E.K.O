"""Callback contracts that must survive pending preparation and retirement."""

import asyncio
from types import SimpleNamespace

import pytest

from main_logic.core.session_lifecycle import SessionOwnershipMixin


class Manager(SessionOwnershipMixin):
    pass


@pytest.mark.asyncio
async def test_pending_sid_rotation_keeps_async_callback_contract():
    manager = Manager()
    manager.session = object()
    calls = []

    async def rotate():
        calls.append("rotated")

    pending = SimpleNamespace(on_sid_rotate=rotate, get_host_turn_id=lambda: "current")
    manager._bind_owned_output_callbacks(pending)
    # OmniRealtimeClient _transport._emit_turn_finished awaits on_sid_rotate,
    # including ignored warmup/prime turns. A suppressed callback is still async.
    await pending.on_sid_rotate()
    assert calls == []
    assert pending.get_host_turn_id() is None


@pytest.mark.asyncio
async def test_sid_rotation_await_is_tracked_as_an_output_producer():
    manager = Manager()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def rotate():
        entered.set()
        await release.wait()

    async def close():
        pass

    session = SimpleNamespace(on_sid_rotate=rotate, close=close)
    manager.session = session
    record = manager._register_connection(session)
    manager._bind_owned_output_callbacks(session)
    task = asyncio.create_task(session.on_sid_rotate())
    try:
        await entered.wait()
        assert task in record.callbacks, "retirement must drain SID rotation waiting on its lock"
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_hot_swap_close_drains_inflight_owned_output_before_promote():
    manager = Manager()
    entered = asyncio.Event()
    release = asyncio.Event()
    writes = []

    async def output():
        entered.set()
        await release.wait()
        writes.append(manager.session)

    async def close():
        pass

    old = SimpleNamespace(on_text_delta=output, close=close)
    manager.session = old
    manager._register_connection(old)
    manager._bind_owned_output_callbacks(old)
    producing = asyncio.create_task(old.on_text_delta())
    try:
        await entered.wait()
        # _perform_final_swap_sequence awaits this exact method immediately
        # before installing the replacement. A callback can be a provider task,
        # distinct from the old receive loop that the swap already stopped.
        await manager._close_owned_session(old)
        manager.session = object()
        release.set()
        await asyncio.gather(producing, return_exceptions=True)
        assert writes == [], "old provider output crossed the close/promote boundary"
    finally:
        release.set()
        await asyncio.gather(producing, return_exceptions=True)


@pytest.mark.asyncio
async def test_provider_callback_can_close_its_own_connection_without_self_wait():
    manager = Manager()
    closed = asyncio.Event()

    async def close():
        closed.set()

    session = SimpleNamespace(close=close)

    async def output():
        await manager._close_owned_session(session)

    session.on_text_delta = output
    manager.session = session
    record = manager._register_connection(session)
    manager._bind_owned_output_callbacks(session)
    await asyncio.wait_for(session.on_text_delta(), 1)
    assert record.closed
    assert closed.is_set()
    assert not record.callbacks
