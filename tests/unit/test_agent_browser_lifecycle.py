from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

from brain.browser_use_adapter import BrowserUseAdapter


capabilities = importlib.import_module("app.agent_server.capabilities")


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_browser_use_adapter_is_single_flight_and_explicitly_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules = capabilities._shared.Modules
    original_adapter = modules.browser_use
    original_executor = modules.task_executor
    original_lock = modules.browser_use_init_lock
    original_capability = dict(modules.capability_cache["browser_use"])
    created = []

    class _FakeAdapter:
        def __init__(self) -> None:
            self.close_calls = 0
            created.append(self)

        async def close(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr(capabilities, "BrowserUseAdapter", _FakeAdapter)
    modules.browser_use = None
    modules.browser_use_init_lock = None
    modules.task_executor = SimpleNamespace(browser_use=None)
    try:
        first, second = await asyncio.gather(
            capabilities._ensure_browser_use_adapter(),
            capabilities._ensure_browser_use_adapter(),
        )

        assert first is second
        assert created == [first]
        assert modules.task_executor.browser_use is first

        await capabilities._close_browser_use_adapter()
        await capabilities._close_browser_use_adapter()

        assert first.close_calls == 1
        assert modules.browser_use is None
        assert modules.task_executor.browser_use is None
    finally:
        modules.browser_use = original_adapter
        modules.task_executor = original_executor
        modules.browser_use_init_lock = original_lock
        modules.capability_cache["browser_use"] = original_capability


@pytest.mark.asyncio
async def test_browser_adapter_close_stops_keep_alive_session() -> None:
    class _Session:
        def __init__(self) -> None:
            self.stop_calls = 0

        async def stop(self) -> None:
            self.stop_calls += 1

    adapter = object.__new__(BrowserUseAdapter)
    session = _Session()
    adapter._overlay_task = None
    adapter._browser_session = session
    adapter._session_ever_started = True
    adapter._agents = {"session": object()}

    await adapter.close()

    assert session.stop_calls == 1
    assert adapter._browser_session is None
    assert adapter._session_ever_started is False
    assert adapter._agents == {}
