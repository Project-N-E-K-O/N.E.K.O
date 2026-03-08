from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugin.sdk.bus.messages import MessageClient


class _FakeBusRpc:
    def __init__(self) -> None:
        self.items = [
            {"plugin_id": "p1", "source": "s1", "priority": 1, "content": "a", "message_id": "m1"},
            {"plugin_id": "p1", "source": "s2", "priority": 2, "content": "b", "message_id": "m2"},
            {"plugin_id": "p2", "source": "s1", "priority": 3, "content": "c", "message_id": "m3"},
        ]

    async def request_async(self, *, op: str, args: dict[str, Any], timeout: float):
        _ = (op, args, timeout)
        return {"ok": True, "result": {"items": list(self.items)}}

    def request(self, *, op: str, args: dict[str, Any], timeout: float):
        _ = (op, args, timeout)
        return {"ok": True, "result": {"items": list(self.items)}}


@pytest.mark.plugin_integration
@pytest.mark.asyncio
@pytest.mark.parametrize("inplace", [False, True])
@pytest.mark.parametrize("incremental", [False, True])
async def test_bus_reload_watch_async_combinations(
    monkeypatch: pytest.MonkeyPatch,
    inplace: bool,
    incremental: bool,
) -> None:
    ctx = SimpleNamespace(plugin_id="demo")
    client = MessageClient(ctx)
    ctx.bus = SimpleNamespace(messages=client)

    fake_rpc = _FakeBusRpc()
    monkeypatch.setattr("plugin.sdk.bus.messages._ensure_rpc", lambda _ctx: fake_rpc)

    base = await client.get_async(plugin_id=None, max_count=50, timeout=1.0)
    expr = base.filter(source="s1", strict=False).limit(10)

    refreshed = await expr.reload_with_async(ctx, inplace=inplace, incremental=incremental)
    assert len(refreshed) >= 1
    if inplace:
        assert refreshed is expr

    watcher = await expr.watch_async(ctx=ctx, bus="messages", debounce_ms=0.0)
    # avoid importing plugin.core.state in this integration suite
    monkeypatch.setattr(
        watcher,
        "_state_subscribe",
        lambda bus, on_event: (on_event("add", {"record": {"message_id": "m4"}}), (lambda: None))[1],
    )

    started = await watcher.start_async()
    assert started is watcher
    await watcher.stop_async()
