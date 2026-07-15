from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

from brain.browser_use_adapter import BrowserUseAdapter


capabilities = importlib.import_module("app.agent_server.capabilities")
api_routes = importlib.import_module("app.agent_server.api_routes")


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_browser_use_availability_does_not_construct_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules = capabilities._shared.Modules
    original_computer_use = modules.computer_use
    original_capability = dict(modules.capability_cache["browser_use"])

    async def _unexpected_init():
        raise AssertionError("availability polling must not construct BrowserUseAdapter")

    monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: {"ready": True})
    monkeypatch.setattr(api_routes, "_browser_use_dependency_status", lambda: (True, ""))
    monkeypatch.setattr(api_routes, "_ensure_browser_use_adapter", _unexpected_init)
    modules.computer_use = SimpleNamespace(init_ok=True, last_error=None)
    try:
        status = await api_routes.browser_use_availability()

        assert status["ready"] is True
        assert status["provider"] == "browser-use"
    finally:
        modules.computer_use = original_computer_use
        modules.capability_cache["browser_use"] = original_capability


@pytest.mark.asyncio
async def test_browser_use_enable_returns_without_constructing_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules = capabilities._shared.Modules
    original_computer_use = modules.computer_use
    original_flags = dict(modules.agent_flags)
    original_notification = modules.notification
    original_capability = dict(modules.capability_cache["browser_use"])

    async def _unexpected_init():
        raise AssertionError("the short flag request must not construct BrowserUseAdapter")

    async def _ignore_status_update(*_args, **_kwargs):
        return None

    monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: {"ready": True})
    monkeypatch.setattr(api_routes, "_browser_use_dependency_status", lambda: (True, ""))
    monkeypatch.setattr(api_routes, "_ensure_browser_use_adapter", _unexpected_init)
    monkeypatch.setattr(api_routes, "_emit_agent_status_update", _ignore_status_update)
    modules.computer_use = SimpleNamespace(init_ok=True, last_error=None)
    modules.agent_flags["browser_use_enabled"] = False
    try:
        result = await api_routes.set_agent_flags(
            {"browser_use_enabled": True, "_persist_intent": False}
        )

        assert result["success"] is True
        assert modules.agent_flags["browser_use_enabled"] is True
    finally:
        modules.computer_use = original_computer_use
        modules.agent_flags.clear()
        modules.agent_flags.update(original_flags)
        modules.notification = original_notification
        modules.capability_cache["browser_use"] = original_capability


@pytest.mark.asyncio
async def test_llm_probe_keeps_unloaded_browser_use_ready_when_dependencies_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules = capabilities._shared.Modules
    original_computer_use = modules.computer_use
    original_browser_use = modules.browser_use
    original_capability = dict(modules.capability_cache["browser_use"])

    class _ComputerUse:
        @staticmethod
        def check_connectivity():
            return True, ""

    async def _ignore_status_update(*_args, **_kwargs):
        return None

    monkeypatch.setattr(capabilities, "_browser_use_dependency_status", lambda: (True, ""))
    monkeypatch.setattr(capabilities, "_emit_agent_status_update", _ignore_status_update)
    modules.computer_use = _ComputerUse()
    modules.browser_use = None
    try:
        await capabilities._fire_agent_llm_connectivity_check()

        assert modules.capability_cache["browser_use"]["ready"] is True
        assert modules.capability_cache["browser_use"]["reason"] == ""
    finally:
        modules.computer_use = original_computer_use
        modules.browser_use = original_browser_use
        modules.capability_cache["browser_use"] = original_capability


@pytest.mark.asyncio
async def test_direct_browser_run_keeps_adapter_returned_by_initializer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules = capabilities._shared.Modules
    original_adapter = modules.browser_use
    original_lock = modules.browser_use_dispatch_lock
    calls: list[str] = []

    class _Adapter:
        async def run_instruction(self, instruction: str):
            calls.append(instruction)
            return {"success": True}

    adapter = _Adapter()

    async def _ensure_adapter():
        # Simulate an asynchronous disable clearing the process singleton
        # after initialization but before the dispatch coroutine runs.
        modules.browser_use = None
        return adapter

    monkeypatch.setattr(api_routes, "_ensure_browser_use_adapter", _ensure_adapter)
    modules.browser_use_dispatch_lock = None
    try:
        result = await api_routes.browser_use_run({"instruction": "open example"})

        assert result["success"] is True
        assert calls == ["open example"]
    finally:
        modules.browser_use = original_adapter
        modules.browser_use_dispatch_lock = original_lock


@pytest.mark.asyncio
async def test_browser_use_disable_schedules_close_outside_flag_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules = capabilities._shared.Modules
    original_flags = dict(modules.agent_flags)
    original_notification = modules.notification
    scheduled = []
    close_calls = []

    async def _close_adapter(**_kwargs):
        close_calls.append(True)

    async def _ignore_status_update(*_args, **_kwargs):
        return None

    def _capture_task(coro):
        scheduled.append(coro)
        return None

    monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: {"ready": True})
    monkeypatch.setattr(api_routes, "_close_browser_use_adapter", _close_adapter)
    monkeypatch.setattr(api_routes, "_create_tracked_task", _capture_task)
    monkeypatch.setattr(api_routes, "_emit_agent_status_update", _ignore_status_update)
    modules.agent_flags["browser_use_enabled"] = True
    try:
        result = await api_routes.set_agent_flags(
            {"browser_use_enabled": False, "_persist_intent": False}
        )

        assert result["success"] is True
        assert modules.agent_flags["browser_use_enabled"] is False
        assert close_calls == []
        assert len(scheduled) == 1

        task_result = await scheduled[0]
        assert task_result is None
        assert close_calls == [True]
    finally:
        modules.agent_flags.clear()
        modules.agent_flags.update(original_flags)
        modules.notification = original_notification


@pytest.mark.asyncio
async def test_browser_use_normal_close_preserves_dependency_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules = capabilities._shared.Modules
    original_adapter = modules.browser_use
    original_lock = modules.browser_use_init_lock
    original_capability = dict(modules.capability_cache["browser_use"])

    class _Adapter:
        async def close(self) -> None:
            return None

    monkeypatch.setattr(capabilities, "_browser_use_dependency_status", lambda: (True, ""))
    modules.browser_use = _Adapter()
    modules.browser_use_init_lock = None
    try:
        await capabilities._close_browser_use_adapter()

        assert modules.browser_use is None
        assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}
    finally:
        modules.browser_use = original_adapter
        modules.browser_use_init_lock = original_lock
        modules.capability_cache["browser_use"] = original_capability


@pytest.mark.asyncio
async def test_browser_use_close_waits_for_active_dispatch() -> None:
    modules = capabilities._shared.Modules
    original_adapter = modules.browser_use
    original_dispatch_lock = modules.browser_use_dispatch_lock
    original_init_lock = modules.browser_use_init_lock
    original_capability = dict(modules.capability_cache["browser_use"])
    close_called = asyncio.Event()

    class _Adapter:
        async def close(self) -> None:
            close_called.set()

    dispatch_lock = asyncio.Lock()
    await dispatch_lock.acquire()
    modules.browser_use = _Adapter()
    modules.browser_use_dispatch_lock = dispatch_lock
    modules.browser_use_init_lock = None
    try:
        close_task = asyncio.create_task(capabilities._close_browser_use_adapter())
        await asyncio.sleep(0)

        assert not close_called.is_set()
        assert not close_task.done()

        dispatch_lock.release()
        assert await close_task is None
        assert close_called.is_set()
        assert modules.browser_use is None
    finally:
        if dispatch_lock.locked():
            dispatch_lock.release()
        modules.browser_use = original_adapter
        modules.browser_use_dispatch_lock = original_dispatch_lock
        modules.browser_use_init_lock = original_init_lock
        modules.capability_cache["browser_use"] = original_capability


@pytest.mark.asyncio
async def test_browser_use_reenable_stays_ready_after_pending_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules = capabilities._shared.Modules
    original_adapter = modules.browser_use
    original_computer_use = modules.computer_use
    original_init_lock = modules.browser_use_init_lock
    original_dispatch_lock = modules.browser_use_dispatch_lock
    original_lifecycle_seq = modules.browser_use_lifecycle_seq
    original_flags = dict(modules.agent_flags)
    original_notification = modules.notification
    original_capability = dict(modules.capability_cache["browser_use"])
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    tasks: list[asyncio.Task] = []

    class _Adapter:
        _ready_import = True

        async def close(self) -> None:
            close_started.set()
            await release_close.wait()

    async def _ignore_status_update(*_args, **_kwargs):
        return None

    def _capture_task(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: {"ready": True})
    monkeypatch.setattr(api_routes, "_browser_use_dependency_status", lambda: (True, ""))
    monkeypatch.setattr(capabilities, "_browser_use_dependency_status", lambda: (True, ""))
    monkeypatch.setattr(api_routes, "_create_tracked_task", _capture_task)
    monkeypatch.setattr(api_routes, "_emit_agent_status_update", _ignore_status_update)
    modules.browser_use = _Adapter()
    modules.computer_use = SimpleNamespace(init_ok=True, last_error=None)
    modules.browser_use_init_lock = None
    modules.browser_use_dispatch_lock = None
    modules.agent_flags["browser_use_enabled"] = True
    try:
        await api_routes.set_agent_flags(
            {"browser_use_enabled": False, "_persist_intent": False}
        )
        await close_started.wait()

        enable_task = asyncio.create_task(
            api_routes.set_agent_flags(
                {"browser_use_enabled": True, "_persist_intent": False}
            )
        )
        result = await asyncio.wait_for(enable_task, timeout=0.2)
        assert result["success"] is True
        assert modules.agent_flags["browser_use_enabled"] is True
        assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}

        release_close.set()
        await asyncio.gather(*tasks)

        assert modules.browser_use is None
        assert modules.agent_flags["browser_use_enabled"] is True
        assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}
    finally:
        release_close.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        modules.browser_use = original_adapter
        modules.computer_use = original_computer_use
        modules.browser_use_init_lock = original_init_lock
        modules.browser_use_dispatch_lock = original_dispatch_lock
        modules.browser_use_lifecycle_seq = original_lifecycle_seq
        modules.agent_flags.clear()
        modules.agent_flags.update(original_flags)
        modules.notification = original_notification
        modules.capability_cache["browser_use"] = original_capability


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
            self._ready_import = True
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
async def test_browser_use_failed_import_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules = capabilities._shared.Modules
    original_adapter = modules.browser_use
    original_lock = modules.browser_use_init_lock
    original_capability = dict(modules.capability_cache["browser_use"])
    created = []

    class _FailedAdapter:
        def __init__(self) -> None:
            self._ready_import = False
            self.last_error = "browser-use import failed"
            created.append(self)

    monkeypatch.setattr(capabilities, "BrowserUseAdapter", _FailedAdapter)
    modules.browser_use = None
    modules.browser_use_init_lock = None
    try:
        assert await capabilities._ensure_browser_use_adapter() is None
        assert await capabilities._ensure_browser_use_adapter() is None
        assert modules.browser_use is None
        assert len(created) == 2
        assert modules.capability_cache["browser_use"]["reason"] == "AGENT_BU_MODULE_NOT_LOADED"
    finally:
        modules.browser_use = original_adapter
        modules.browser_use_init_lock = original_lock
        modules.capability_cache["browser_use"] = original_capability


@pytest.mark.asyncio
async def test_browser_use_ensure_waits_for_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules = capabilities._shared.Modules
    original_adapter = modules.browser_use
    original_lock = modules.browser_use_init_lock
    original_capability = dict(modules.capability_cache["browser_use"])
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    created = []

    class _FakeAdapter:
        def __init__(self) -> None:
            self._ready_import = True
            created.append(self)

        async def close(self) -> None:
            close_started.set()
            await release_close.wait()

    closing_adapter = _FakeAdapter()
    monkeypatch.setattr(capabilities, "BrowserUseAdapter", _FakeAdapter)
    modules.browser_use = closing_adapter
    modules.browser_use_init_lock = None
    try:
        close_task = asyncio.create_task(capabilities._close_browser_use_adapter())
        await close_started.wait()
        ensure_task = asyncio.create_task(capabilities._ensure_browser_use_adapter())
        await asyncio.sleep(0)

        assert not ensure_task.done()
        release_close.set()
        assert await close_task is None
        replacement = await ensure_task
        assert replacement is created[1]
        assert replacement is not closing_adapter
    finally:
        release_close.set()
        modules.browser_use = original_adapter
        modules.browser_use_init_lock = original_lock
        modules.capability_cache["browser_use"] = original_capability


@pytest.mark.asyncio
async def test_cancelled_browser_use_close_keeps_adapter_for_shutdown_retry() -> None:
    modules = capabilities._shared.Modules
    original_adapter = modules.browser_use
    original_lock = modules.browser_use_init_lock
    original_capability = dict(modules.capability_cache["browser_use"])
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    class _Adapter:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            close_started.set()
            await release_close.wait()

    adapter = _Adapter()
    modules.browser_use = adapter
    modules.browser_use_init_lock = None
    try:
        close_task = asyncio.create_task(capabilities._close_browser_use_adapter())
        await close_started.wait()
        close_task.cancel()
        cancelled_result = await asyncio.gather(close_task, return_exceptions=True)
        assert len(cancelled_result) == 1
        assert isinstance(cancelled_result[0], asyncio.CancelledError)

        assert modules.browser_use is adapter
        release_close.set()
        assert await capabilities._close_browser_use_adapter() is None
        assert adapter.close_calls == 2
        assert modules.browser_use is None
    finally:
        release_close.set()
        modules.browser_use = original_adapter
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
