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
            f"{cross_server.MONITOR_SYNC_URL}/sync/Mimi",
            f"{cross_server.MONITOR_SYNC_URL}/sync_binary/Mimi",
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


def test_connector_defaults_to_configured_monitor_token():
    import inspect
    default = inspect.signature(cross_server.run_sync_connector).parameters["monitor_auth_token"].default
    assert default == (cross_server.MONITOR_TOKEN or None)


@pytest.mark.asyncio
async def test_auth_rejection_warns_once_and_backs_off(monkeypatch):
    import aiohttp
    attempts = []
    enough = asyncio.Event()

    class Session:
        async def ws_connect(self, url, **kwargs):
            attempts.append(asyncio.get_running_loop().time())
            if len(attempts) >= 3:
                enough.set()
            raise aiohttp.WSServerHandshakeError(None, (), status=403, message="Forbidden")

        async def close(self):
            pass

    warnings = []
    monkeypatch.setattr(cross_server.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(cross_server.logger, "warning", lambda msg, *a, **k: warnings.append(msg))
    slot = cross_server._WSSlot("sync", "ws://monitor/sync/Mimi", "Mimi")
    task = asyncio.create_task(cross_server._slot_maintainer(slot, backoff_max=0.01, auth_retry=0.05))
    try:
        await asyncio.wait_for(enough.wait(), 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(warnings) == 1
    assert "NEKO_MONITOR_TOKEN" in warnings[0]
    gaps = [b - a for a, b in zip(attempts, attempts[1:])]
    assert min(gaps) >= 0.04
