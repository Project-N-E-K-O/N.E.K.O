from __future__ import annotations

import asyncio
import threading

import pytest

from plugin.core.state import state
from plugin.server import lifecycle
from plugin.server.messaging.bus_subscriptions import BusSubscriptionManager


@pytest.mark.asyncio
async def test_retire_and_stop_subscription_manager_on_its_owner_loop(monkeypatch):
    manager = BusSubscriptionManager()
    monkeypatch.setattr(lifecycle, 'bus_subscription_manager', manager)
    monkeypatch.setattr(state, '_bus_subscriptions', {
        name: {} for name in ('messages', 'events', 'lifecycle', 'runs', 'export', 'conversations')
    })
    entered = threading.Event()
    cancelled = threading.Event()
    notices = []

    class Host:
        async def push_bus_change(self, **event):
            notices.append((event, asyncio.get_running_loop()))
            if event['op'] == 'record':
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

    monkeypatch.setattr(state, 'get_plugin_hosts_snapshot_cached', lambda timeout=1: {'p': Host()})
    state.add_bus_subscription('conversations', 's', {
        'from_plugin': 'p', 'contract': 'conversations_v2', 'view': True,
    })
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        ready.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(manager.stop())
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    thread = threading.Thread(target=run, name='subscription-test-loop', daemon=True)
    thread.start()
    try:
        assert await asyncio.to_thread(ready.wait, 2)
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(manager.start(), loop))
        assert manager._task.get_loop() is loop
        info = state.get_bus_subscriptions('conversations')['s']
        loop.call_soon_threadsafe(manager._conversation_enqueue, 's', info, 'record', {'text': 'queued'})
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.wait_for(lifecycle._on_subscription_loop(
            manager.invalidate_conversations('plane reset')
        ), 2)
        assert cancelled.is_set()
        assert state.get_bus_subscriptions('conversations') == {}
        assert [event['op'] for event, _ in notices] == ['record', 'subscription.closed']
        assert notices[-1][0]['delta'] == {'reason': 'plane reset'}
        assert all(owner is loop for _, owner in notices)
        assert manager._conversation_total_count == manager._conversation_total_bytes == 0
        await asyncio.wait_for(lifecycle._on_subscription_loop(manager.stop()), 2)
        assert manager._task is None and manager._conversation_task is None
    finally:
        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        await asyncio.to_thread(thread.join, 3)
        assert not thread.is_alive()
