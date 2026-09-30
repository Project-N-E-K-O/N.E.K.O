"""Monitor connector credentials stay in headers and out of diagnostics."""

import asyncio
from types import SimpleNamespace

import pytest

from main_logic import cross_server


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "monitor-test-secret"])
async def test_monitor_connector_passes_headers_to_transport(monkeypatch, token):
    calls = []
    connected = asyncio.Event()
    parked = asyncio.Event()

    class Session:
        async def ws_connect(self, url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 2:
                connected.set()
            return SimpleNamespace(close=self.close)

        async def close(self):
            pass

    async def reader(*_args):
        await parked.wait()

    monkeypatch.setattr(cross_server.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(cross_server, "_slot_reader", reader)
    connector = asyncio.create_task(cross_server.run_sync_connector(
        asyncio.Queue(), "Mimi", config={"monitor": True, "bullet": False},
        monitor_auth_token=token,
    ))
    try:
        await asyncio.wait_for(connected.wait(), 1)
        assert {url for url, _ in calls} == {
            f"ws://127.0.0.1:{cross_server.MONITOR_SERVER_PORT}/sync/Mimi",
            f"ws://127.0.0.1:{cross_server.MONITOR_SERVER_PORT}/sync_binary/Mimi",
        }
        for url, kwargs in calls:
            assert kwargs["heartbeat"] == 10
            if token:
                assert kwargs["headers"] == {"Authorization": f"Bearer {token}"}
                assert token not in url
            else:
                assert "headers" not in kwargs
    finally:
        connector.cancel()
        await asyncio.gather(connector, return_exceptions=True)
