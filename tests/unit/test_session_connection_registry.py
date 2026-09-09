"""Manager retirement must not replace the provider's reusable close API."""

import asyncio

import pytest

from main_logic.core.session_lifecycle import SessionOwnershipMixin


class ReconnectingProvider:
    def __init__(self):
        self.connected = True
        self.close_calls = 0
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_release.set()

    async def close(self):
        self.close_entered.set()
        await self.close_release.wait()
        self.connected = False
        self.close_calls += 1

    async def reconnect(self):
        await self.close()
        self.connected = True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_internal_close_reconnect_preserves_manager_resource_identity():
    manager = SessionOwnershipMixin()
    provider = ReconnectingProvider()
    manager.session = provider
    delivered = []
    provider.on_text_delta = lambda text: delivered.append(text)
    record = manager._register_connection(provider)
    manager._bind_owned_output_callbacks(provider)

    await provider.reconnect()
    await provider.on_text_delta("new connection")

    assert provider.connected
    assert not record.retired and not record.closed
    assert delivered == ["new connection"]
    await manager._close_owned_session(provider)
    assert not provider.connected
    assert record.retired and record.closed
    assert provider.close_calls == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manager_close_is_idempotent_and_survives_caller_cancellation():
    manager = SessionOwnershipMixin()
    provider = ReconnectingProvider()
    provider.close_release.clear()
    caller = asyncio.create_task(manager._close_owned_session(provider))
    await provider.close_entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    record = manager._connection_record(provider)
    assert record.retired and not record.closed
    provider.close_release.set()
    await manager._close_owned_session(provider)
    assert record.closed
    assert provider.close_calls == 1
