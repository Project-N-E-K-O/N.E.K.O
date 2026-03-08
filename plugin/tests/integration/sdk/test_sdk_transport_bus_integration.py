from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugin.sdk.bus.messages import MessageClient


class _OkRpc:
    async def request_async(self, *, op: str, args: dict[str, Any], timeout: float):
        _ = (op, args, timeout)
        return {
            "ok": True,
            "result": {
                "items": [
                    {"payload": {"plugin_id": "demo", "source": "s", "message_id": "m1", "content": "hello"}}
                ]
            },
        }

    def request(self, *, op: str, args: dict[str, Any], timeout: float):
        _ = (op, args, timeout)
        return {
            "ok": True,
            "result": {
                "items": [
                    {"payload": {"plugin_id": "demo", "source": "s", "message_id": "m1", "content": "hello"}}
                ]
            },
        }


class _TimeoutRpc:
    async def request_async(self, *, op: str, args: dict[str, Any], timeout: float):
        _ = (op, args, timeout)
        return None


class _PlaneRpc:
    def __init__(self, *, ok: bool) -> None:
        self._ok = ok

    async def request_async(self, *, op: str, args: dict[str, Any], timeout: float):
        _ = (op, args, timeout)
        if not self._ok:
            return {"ok": False, "error": {"code": "E_TEST", "message": "failed"}}
        return {
            "ok": True,
            "result": {
                "items": [
                    {"seq": 1, "payload": {"plugin_id": "demo", "source": "s", "priority": 1, "message_id": "p1"}}
                ]
            },
        }


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_transport_bus_message_client_success_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = SimpleNamespace(plugin_id="demo")
    client = MessageClient(ctx)

    monkeypatch.setattr("plugin.sdk.bus.messages._ensure_rpc", lambda _ctx: _OkRpc())
    monkeypatch.setattr("plugin.sdk.bus.messages._MessagePlaneRpcClient", lambda **kwargs: _PlaneRpc(ok=True))

    got = await client.get_async(max_count=10, timeout=1.0)
    assert got.count() == 1

    all_items = await client.get_message_plane_all_async(max_items=10, timeout=1.0, raw=True)
    assert all_items.count() == 1


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_transport_bus_message_client_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = SimpleNamespace(plugin_id="demo")
    client = MessageClient(ctx)

    monkeypatch.setattr("plugin.sdk.bus.messages._ensure_rpc", lambda _ctx: _TimeoutRpc())
    with pytest.raises(TimeoutError):
        await client.get_async(max_count=10, timeout=1.0)

    monkeypatch.setattr("plugin.sdk.bus.messages._MessagePlaneRpcClient", lambda **kwargs: _PlaneRpc(ok=False))
    with pytest.raises(RuntimeError):
        await client.get_message_plane_all_async(max_items=10, timeout=1.0, raw=True)
