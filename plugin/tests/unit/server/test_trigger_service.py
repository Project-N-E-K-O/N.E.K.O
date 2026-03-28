from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugin.server.runs import trigger_service as module
from plugin.sdk.shared.core.events import EventMeta


class _Host:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], float | None]] = []

    async def trigger(
        self,
        entry_id: str,
        args: dict[str, object],
        timeout: float | None,
    ) -> dict[str, object]:
        self.calls.append((entry_id, args, timeout))
        return {"ok": True}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_execute_trigger_treats_metadata_timeout_zero_as_no_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _Host()
    handler = SimpleNamespace(
        meta=EventMeta(
            event_type="plugin_entry",
            id="run",
            timeout=0,
            metadata={"timeout": 0},
        ),
    )
    monkeypatch.setattr(
        module.state,
        "get_event_handlers_snapshot_cached",
        lambda timeout=1.0: {"dummy_plugin.run": handler},
    )

    response = await module._execute_trigger(
        host=host,
        plugin_id="dummy_plugin",
        entry_id="run",
        args={},
        trace_id="trace-1",
    )

    assert response == {"ok": True}
    assert host.calls == [("run", {}, None)]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_execute_trigger_treats_ctx_timeout_zero_as_no_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _Host()
    handler = SimpleNamespace(
        meta=EventMeta(
            event_type="plugin_entry",
            id="run",
            timeout=15,
            metadata={"timeout": 15},
        ),
    )
    monkeypatch.setattr(
        module.state,
        "get_event_handlers_snapshot_cached",
        lambda timeout=1.0: {"dummy_plugin.run": handler},
    )

    response = await module._execute_trigger(
        host=host,
        plugin_id="dummy_plugin",
        entry_id="run",
        args={"_ctx": {"entry_timeout": 0}},
        trace_id="trace-2",
    )

    assert response == {"ok": True}
    assert host.calls == [("run", {"_ctx": {"entry_timeout": 0}}, None)]
