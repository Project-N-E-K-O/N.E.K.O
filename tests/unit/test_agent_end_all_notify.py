# -*- coding: utf-8 -*-
"""``admin_control(end_all)`` must stop every task and leave nothing behind.

The task HUD is purely event-driven (no HTTP polling). Tasks are commissions
on the NekoClaw host's ledger now: end_all stops them through
``stop_everything``, which writes each status first and tells the HUD from
that, so a card never stays "running" while something slow is torn down. How
a stopped commission reaches the HUD is covered in
``tests/unit/nekoclaw/test_agent_wiring.py``; this file covers what end_all
itself does around that call.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def srv(monkeypatch: pytest.MonkeyPatch):
    from app.agent_server import api_routes as agent_srv

    M = agent_srv.Modules
    saved_background = set(M._background_tasks)
    M._background_tasks.clear()
    # No real adapters: end_all would otherwise reach for the keyboard-and-mouse
    # worker and a browser session.
    monkeypatch.setattr(M, "computer_use", None)
    monkeypatch.setattr(M, "browser_use", None)

    yield agent_srv

    M._background_tasks.clear()
    M._background_tasks.update(saved_background)


def test_end_all_stops_every_commission_before_tearing_down(srv, monkeypatch: pytest.MonkeyPatch):
    order: list[str] = []

    async def _stop_everything(reason: str = "") -> int:
        order.append(f"stop:{reason}")
        return 2

    async def _close(**kwargs):
        order.append("close_browser")

    monkeypatch.setattr(srv, "stop_everything", _stop_everything)
    monkeypatch.setattr(srv, "_close_browser_use_adapter", _close)

    result = asyncio.run(srv.admin_control({"action": "end_all"}))

    assert result["success"] is True
    # The commissions are stopped (and the HUD told) before the slow teardown.
    assert order == ["stop:Cancelled by user", "close_browser"]


class _KeyboardWorker:
    """A keyboard-and-mouse adapter whose worker thread may still be going."""

    def __init__(self) -> None:
        self.waited: list[float] = []
        self.cancelled = 0

    def cancel_running(self) -> None:
        self.cancelled += 1

    def wait_for_completion(self, timeout: float = 15.0) -> bool:
        self.waited.append(timeout)
        return True

    def reset(self) -> None:
        pass


def test_end_all_waits_for_the_keyboard_worker_even_with_no_call_on_record(srv, monkeypatch: pytest.MonkeyPatch):
    # A cancelled call is taken off the record after a bounded wait even if
    # its thread has not stopped; end_all must not skip the wait for that.
    worker = _KeyboardWorker()
    monkeypatch.setattr(srv.Modules, "computer_use", worker)
    monkeypatch.setattr(srv, "stop_everything", AsyncMock(return_value=0))
    monkeypatch.setattr(srv, "_close_browser_use_adapter", AsyncMock())
    assert srv.builtin_provider.is_running() is False

    result = asyncio.run(srv.admin_control({"action": "end_all"}))

    assert result["success"] is True
    assert worker.cancelled == 1 and worker.waited == [10.0]


def test_end_all_retries_browser_close_after_cancelling_tracked_teardown(
    srv, monkeypatch: pytest.MonkeyPatch,
):
    M = srv.Modules
    teardown_cancelled = asyncio.Event()
    retried = AsyncMock()
    monkeypatch.setattr(srv, "stop_everything", AsyncMock(return_value=0))
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
        assert M._background_tasks == set()
        retried.assert_awaited_once_with(update_capability=False)

    asyncio.run(_scenario())
