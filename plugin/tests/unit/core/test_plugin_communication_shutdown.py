from __future__ import annotations

import asyncio
import threading

import pytest

from plugin.core.communication import PluginCommunicationResourceManager


class _Transport:
    async def recv(self, timeout_ms=None):
        await asyncio.sleep(10)
        return None

    async def send_command(self, msg):
        return None


class _Logger:
    def debug(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_comm_manager_shutdown_tolerates_cross_loop_uplink_task() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )

    ready = threading.Event()
    holder: dict[str, object] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _spawn() -> None:
            manager._uplink_consumer_task = loop.create_task(asyncio.sleep(10))
            holder["loop"] = loop
            ready.set()

        loop.run_until_complete(_spawn())
        loop.run_until_complete(asyncio.sleep(0.2))
        loop.close()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    assert ready.wait(timeout=2.0)

    await manager.shutdown(timeout=0.1)
    thread.join(timeout=1.0)
