from __future__ import annotations


import pytest

from plugin.sdk import message_plane_transport as module


@pytest.mark.plugin_unit
def test_message_plane_rpc_client_request_dispatch_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    client = module.MessagePlaneRpcClient.__new__(module.MessagePlaneRpcClient)
    monkeypatch.setattr(client, "_is_in_event_loop", lambda: False)
    monkeypatch.setattr(client, "request_sync", lambda **kwargs: {"ok": True, "mode": "sync"})

    out = client.request(op="x", args={}, timeout=1.0)
    assert out["mode"] == "sync"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_message_plane_rpc_client_request_dispatch_async(monkeypatch: pytest.MonkeyPatch) -> None:
    client = module.MessagePlaneRpcClient.__new__(module.MessagePlaneRpcClient)
    monkeypatch.setattr(client, "_is_in_event_loop", lambda: True)

    async def _fake_async(**kwargs):
        return {"ok": True, "mode": "async"}

    monkeypatch.setattr(client, "request_async", _fake_async)

    coro = client.request(op="x", args={}, timeout=1.0)
    assert hasattr(coro, "__await__")
    out = await coro
    assert out["mode"] == "async"
