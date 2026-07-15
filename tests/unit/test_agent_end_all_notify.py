# -*- coding: utf-8 -*-
"""``admin_control(end_all)`` must notify the frontend about every task it clears.

The task HUD is purely event-driven (no HTTP polling). A dispatch coroutine
wedged inside an LLM call never emits its own terminal event, so end_all must
emit ``task_update(cancelled)`` for every queued/running registry entry before
clearing the registry — otherwise the HUD card stays "running" forever and the
cancel button appears to do nothing.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def srv(monkeypatch: pytest.MonkeyPatch):
    from app.agent_server import api_routes as agent_srv

    M = agent_srv.Modules
    saved_registry = dict(M.task_registry)
    saved_handles = dict(M.task_async_handles)
    saved_background = set(M._background_tasks)
    saved_browser_use = M.browser_use
    saved_active_bu = M.active_browser_use_task_id
    M.task_registry.clear()
    M.task_async_handles.clear()
    M._background_tasks.clear()

    monkeypatch.setattr(agent_srv, "_emit_main_event", AsyncMock())
    monkeypatch.setattr(agent_srv._task_tracker, "record_completed", MagicMock())

    yield agent_srv

    M.task_registry.clear()
    M.task_registry.update(saved_registry)
    M.task_async_handles.clear()
    M.task_async_handles.update(saved_handles)
    M._background_tasks.clear()
    M._background_tasks.update(saved_background)
    M.browser_use = saved_browser_use
    M.active_browser_use_task_id = saved_active_bu


def test_end_all_emits_cancelled_for_every_active_task(srv):
    M = srv.Modules
    M.task_registry["t-running"] = {
        "id": "t-running", "type": "browser_use", "status": "running",
        "params": {"instruction": "x"}, "lanlan_name": "lanlan",
    }
    M.task_registry["t-queued"] = {
        "id": "t-queued", "type": "computer_use", "status": "queued",
        "params": {}, "lanlan_name": "lanlan",
    }
    M.task_registry["t-done"] = {
        "id": "t-done", "type": "browser_use", "status": "completed",
        "params": {}, "lanlan_name": "lanlan",
    }

    result = asyncio.run(srv.admin_control({"action": "end_all"}))

    assert result["success"] is True
    assert M.task_registry == {}

    emitted = {}
    for call in srv._emit_main_event.await_args_list:
        if call.args and call.args[0] == "task_update":
            task = call.kwargs.get("task") or {}
            emitted[task.get("id")] = task.get("status")
    assert emitted.get("t-running") == "cancelled"
    assert emitted.get("t-queued") == "cancelled"
    # Already-terminal tasks must not be re-announced.
    assert "t-done" not in emitted


def test_cancel_queued_browser_task_keeps_shared_browser_alive(srv):
    """Cancelling a browser_use task that is still waiting for the dispatch
    slot must NOT tear down the shared browser session — that would kill the
    unrelated task currently using it."""
    M = srv.Modules
    M.browser_use = MagicMock()
    M.browser_use.cancel = AsyncMock()
    M.active_browser_use_task_id = "t-running"
    M.task_registry["t-queued"] = {
        "id": "t-queued", "type": "browser_use", "status": "queued",
        "params": {"instruction": "x"}, "lanlan_name": "lanlan",
    }

    result = asyncio.run(srv.cancel_task("t-queued"))

    assert result["success"] is True
    assert M.task_registry["t-queued"]["status"] == "cancelled"
    M.browser_use.cancel.assert_not_called()
    assert M.active_browser_use_task_id == "t-running"


def test_cancel_active_browser_task_tears_down_browser(srv):
    M = srv.Modules
    M.browser_use = MagicMock()
    M.browser_use.cancel = AsyncMock()
    M.active_browser_use_task_id = "t-active"
    M.task_registry["t-active"] = {
        "id": "t-active", "type": "browser_use", "status": "running",
        "params": {"instruction": "x"}, "lanlan_name": "lanlan",
    }

    async def _scenario():
        result = await srv.cancel_task("t-active")
        # let the fire-and-forget teardown runner execute
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return result

    result = asyncio.run(_scenario())

    assert result["success"] is True
    assert M.task_registry["t-active"]["status"] == "cancelled"
    M.browser_use.cancel.assert_called_once()
    assert M.active_browser_use_task_id is None


def test_end_all_cancels_untracked_direct_browser_run(srv):
    """A direct /browser_use/run call must be tracked in _background_tasks so
    end_all can cancel it — otherwise it would survive end_all still holding
    the dispatch mutex and every later browser task would queue forever."""
    M = srv.Modules
    saved_lock = M.browser_use_dispatch_lock
    M.browser_use_dispatch_lock = None
    M.browser_use = MagicMock()
    M.browser_use._browser_session = None

    async def _stall(instruction, **kwargs):
        await asyncio.sleep(3600)

    M.browser_use.run_instruction = _stall

    async def _scenario():
        run = asyncio.create_task(srv.browser_use_run({"instruction": "x"}))
        await asyncio.sleep(0.05)  # let it acquire the lock and stall
        assert M.browser_use_dispatch_lock is not None
        assert M.browser_use_dispatch_lock.locked()

        result = await srv.admin_control({"action": "end_all"})
        assert result["success"] is True

        resp = await run
        assert resp.status_code == 500
        assert not M.browser_use_dispatch_lock.locked()

    try:
        asyncio.run(_scenario())
    finally:
        M.browser_use_dispatch_lock = saved_lock


def test_end_all_cancels_orphan_dispatch_handles(srv):
    """Handles registered only in task_async_handles (not _background_tasks)
    must still receive the cancel."""
    M = srv.Modules

    async def _scenario():
        orphan = asyncio.create_task(asyncio.sleep(3600))
        M.task_async_handles["t-orphan"] = orphan
        await asyncio.sleep(0)  # let the task start

        result = await srv.admin_control({"action": "end_all"})
        assert result["success"] is True
        assert orphan.cancelled()

    asyncio.run(_scenario())


def test_end_all_retries_browser_close_after_cancelling_tracked_teardown(
    srv, monkeypatch: pytest.MonkeyPatch,
):
    M = srv.Modules
    teardown_cancelled = asyncio.Event()
    retried = AsyncMock()
    monkeypatch.setattr(srv, "_close_browser_use_adapter", retried)

    async def _pending_teardown():
        try:
            await asyncio.sleep(3600)
        finally:
            teardown_cancelled.set()

    async def _scenario():
        pending = asyncio.create_task(_pending_teardown())
        M._background_tasks.add(pending)
        await asyncio.sleep(0)

        result = await srv.admin_control({"action": "end_all"})

        assert result["success"] is True
        assert pending.cancelled()
        assert teardown_cancelled.is_set()
        retried.assert_awaited_once_with(update_capability=False)

    asyncio.run(_scenario())
