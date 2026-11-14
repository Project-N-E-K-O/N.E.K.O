# -*- coding: utf-8 -*-
"""``admin_control(end_all)`` must stop every task and leave nothing behind.

The task HUD is purely event-driven (no HTTP polling). Tasks are commissions
on the NekoClaw host's ledger now: end_all stops them through
``stop_everything``, which writes each status first and tells the HUD from
that, so a card never stays "running" while something slow is torn down. How
a stopped commission reaches the HUD is covered in
``tests/unit/nekoclaw/test_agent_wiring.py``; this file covers what end_all
itself does around that call: background work that belongs to no commission
is cancelled, the browser the browser tools started is closed, and keys a
stopped keyboard-and-mouse call left held are let go of.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest


class _Desktop:
    """Stands in for ``nekoclaw.builtin.desktop.Desktop``: only what end_all touches."""

    def __init__(self, fails: bool = False) -> None:
        self.released = 0
        self.fails = fails

    def release_held(self) -> bool:
        self.released += 1
        if self.fails:
            raise RuntimeError("the display went away")
        return True


@pytest.fixture
def browser_closes(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """One entry each time the browser the tools started is closed. No real browser is touched."""
    from app.agent_server import api_routes as agent_srv

    closes: list[bool] = []

    async def _close_browser() -> None:
        closes.append(True)

    monkeypatch.setattr(agent_srv.builtin_provider, "close_browser", _close_browser)
    return closes


@pytest.fixture
def srv(monkeypatch: pytest.MonkeyPatch, browser_closes: list[bool]):
    from app.agent_server import api_routes as agent_srv

    M = agent_srv.Modules
    saved_background = set(M._background_tasks)
    M._background_tasks.clear()
    # No desktop either: making one would import pyautogui.
    monkeypatch.setattr(agent_srv.builtin_provider, "_desktop", None)
    monkeypatch.setattr(agent_srv, "stop_everything", AsyncMock(return_value=0))

    yield agent_srv

    M._background_tasks.clear()
    M._background_tasks.update(saved_background)


def test_end_all_stops_every_commission_before_tearing_down(srv, monkeypatch: pytest.MonkeyPatch):
    order: list[str] = []

    async def _stop_everything(reason: str = "") -> int:
        order.append(f"stop:{reason}")
        return 2

    async def _close(**kwargs):
        order.append(f"close_browser:{kwargs}")

    async def _released(group: str, commission_id: str) -> None:
        order.append(f"released:{group}:{commission_id}")

    monkeypatch.setattr(srv, "stop_everything", _stop_everything)
    monkeypatch.setattr(srv, "_close_browser", _close)
    monkeypatch.setattr(srv.builtin_provider, "released", _released)

    result = asyncio.run(srv.admin_control({"action": "end_all"}))

    assert result["success"] is True
    # The commissions are stopped (and the HUD told) before the slow teardown.
    # Stopping them is what cancels their calls, so the keys are let go of and
    # the browser is closed only after that; the keys first, which wait for
    # nothing the browser does.
    assert order == [
        "stop:Cancelled by user",
        "released:keyboard_mouse:",
        "close_browser:{'update_capability': False}",
    ]


def test_end_all_closes_the_browser_and_leaves_its_readiness_as_it_was(srv, browser_closes):
    capability_before = dict(srv.Modules.capability_cache["browser_use"])
    revision_before = srv.Modules.state_revision

    result = asyncio.run(srv.admin_control({"action": "end_all"}))

    assert result["success"] is True
    assert browser_closes == [True]
    # The switch was not touched: whether the browser tools can be used is unchanged.
    assert srv.Modules.capability_cache["browser_use"] == capability_before
    assert srv.Modules.state_revision == revision_before


def test_end_all_lets_go_of_keys_a_stopped_call_left_held(srv, monkeypatch: pytest.MonkeyPatch):
    desktop = _Desktop()
    monkeypatch.setattr(srv.builtin_provider, "_desktop", desktop)

    result = asyncio.run(srv.admin_control({"action": "end_all"}))

    assert result["success"] is True
    assert desktop.released == 1


def test_end_all_makes_no_desktop_just_to_release_keys(srv):
    result = asyncio.run(srv.admin_control({"action": "end_all"}))

    assert result["success"] is True
    # The keyboard and mouse were never used in this process: nothing to let go of.
    assert srv.builtin_provider._desktop is None


def test_end_all_finishes_when_closing_the_browser_or_releasing_keys_fails(
    srv, browser_closes, monkeypatch: pytest.MonkeyPatch
):
    desktop = _Desktop(fails=True)

    async def _broken_close() -> None:
        browser_closes.append(True)
        raise RuntimeError("the browser does not answer")

    monkeypatch.setattr(srv.builtin_provider, "close_browser", _broken_close)
    monkeypatch.setattr(srv.builtin_provider, "_desktop", desktop)

    result = asyncio.run(srv.admin_control({"action": "end_all"}))

    assert result == {"success": True, "message": "all tasks terminated and cleared"}
    # A browser that would not close did not keep the keys from being let go of.
    assert browser_closes == [True] and desktop.released == 1


def test_end_all_retries_browser_close_after_cancelling_tracked_teardown(
    srv, monkeypatch: pytest.MonkeyPatch,
):
    M = srv.Modules
    teardown_cancelled = asyncio.Event()
    retried = AsyncMock()
    monkeypatch.setattr(srv, "_close_browser", retried)

    async def _pending_teardown():
        try:
            await asyncio.sleep(3600)
        finally:
            teardown_cancelled.set()

    async def _scenario():
        # A close scheduled when the browser switch went off is itself tracked
        # background work, and end_all cancels all of that.
        pending = asyncio.create_task(_pending_teardown())
        M._background_tasks.add(pending)
        await asyncio.sleep(0)

        result = await srv.admin_control({"action": "end_all"})

        assert result["success"] is True
        assert pending.cancelled()
        assert teardown_cancelled.is_set()
        assert M._background_tasks == set()
        # So the browser is closed once more, as the last thing.
        retried.assert_awaited_once_with(update_capability=False)

    asyncio.run(_scenario())


def test_the_last_browser_close_comes_after_the_tracked_teardown_was_cancelled(srv, monkeypatch: pytest.MonkeyPatch):
    M = srv.Modules
    order: list[str] = []

    async def _close(**kwargs):
        order.append("close_browser")

    monkeypatch.setattr(srv, "_close_browser", _close)

    async def _pending_teardown():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            order.append("teardown cancelled")
            raise

    async def _scenario():
        M._background_tasks.add(asyncio.create_task(_pending_teardown()))
        await asyncio.sleep(0)
        await srv.admin_control({"action": "end_all"})

    asyncio.run(_scenario())

    assert order == ["teardown cancelled", "close_browser"]


def test_end_all_kills_a_browser_that_does_not_close_in_the_time_it_has(srv, monkeypatch: pytest.MonkeyPatch):
    import threading

    import config.nekoclaw_settings as settings

    # Shorter than the page waits for this request (5 seconds).
    assert settings.BROWSER_CLOSE_AT_END_ALL_SECONDS == 4.0
    monkeypatch.setattr(settings, "BROWSER_CLOSE_AT_END_ALL_SECONDS", 0.05)
    desktop = _Desktop()
    monkeypatch.setattr(srv.builtin_provider, "_desktop", desktop)
    killed_in: list[threading.Thread] = []
    monkeypatch.setattr(srv.builtin_provider, "kill_browser", lambda: killed_in.append(threading.current_thread()))
    closing: list[str] = []

    async def _slow_close() -> None:
        closing.append("asked")
        try:
            await asyncio.sleep(3600)
        finally:
            closing.append("given up")

    monkeypatch.setattr(srv.builtin_provider, "close_browser", _slow_close)

    async def _scenario() -> None:
        result = await asyncio.wait_for(srv.admin_control({"action": "end_all"}), 10.0)
        assert result["success"] is True
        assert closing == ["asked", "given up"] and desktop.released == 1
        # Killed, off the event loop.
        deadline = asyncio.get_running_loop().time() + 5.0
        while not killed_in and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert len(killed_in) == 1 and killed_in[0] is not threading.current_thread()

    asyncio.run(_scenario())


def test_end_all_does_not_kill_a_browser_that_closes_in_time(srv, browser_closes, monkeypatch: pytest.MonkeyPatch):
    killed: list[int] = []
    monkeypatch.setattr(srv.builtin_provider, "kill_browser", lambda: killed.append(1))

    result = asyncio.run(srv.admin_control({"action": "end_all"}))

    assert result["success"] is True
    assert browser_closes == [True] and killed == []


def test_end_all_finishes_when_the_browser_can_be_neither_closed_nor_killed(srv, monkeypatch: pytest.MonkeyPatch):
    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "BROWSER_CLOSE_AT_END_ALL_SECONDS", 0.05)

    async def _slow_close() -> None:
        await asyncio.sleep(3600)

    def _cannot_kill() -> None:
        raise RuntimeError("the process list cannot be read")

    monkeypatch.setattr(srv.builtin_provider, "close_browser", _slow_close)
    monkeypatch.setattr(srv.builtin_provider, "kill_browser", _cannot_kill)

    result = asyncio.run(asyncio.wait_for(srv.admin_control({"action": "end_all"}), 10.0))

    assert result == {"success": True, "message": "all tasks terminated and cleared"}


def test_end_all_answers_without_waiting_for_the_browser_to_be_killed(srv, monkeypatch: pytest.MonkeyPatch):
    import threading

    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "BROWSER_CLOSE_AT_END_ALL_SECONDS", 0.05)
    desktop = _Desktop()
    monkeypatch.setattr(srv.builtin_provider, "_desktop", desktop)
    killing = threading.Event()
    killed = threading.Event()
    let_it_finish = threading.Event()

    def _slow_kill() -> None:
        # Killing waits for the processes to be gone: seconds, on a bad day.
        killing.set()
        let_it_finish.wait(30)
        killed.set()

    async def _slow_close() -> None:
        # The keys were let go of before the browser was even asked.
        assert desktop.released == 1
        await asyncio.sleep(3600)

    monkeypatch.setattr(srv.builtin_provider, "close_browser", _slow_close)
    monkeypatch.setattr(srv.builtin_provider, "kill_browser", _slow_kill)

    async def _scenario() -> None:
        try:
            result = await asyncio.wait_for(srv.admin_control({"action": "end_all"}), 5.0)
            assert result["success"] is True
            # Answered while the kill is still under way: the page gives up waiting after 5 seconds.
            assert await asyncio.to_thread(killing.wait, 5.0)
            assert not killed.is_set()
        finally:
            let_it_finish.set()
        assert await asyncio.to_thread(killed.wait, 5.0)

    asyncio.run(_scenario())


def test_a_task_started_after_end_all_does_not_reach_the_browser_before_the_kill_is_over(
    srv, monkeypatch: pytest.MonkeyPatch
):
    import threading

    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "BROWSER_CLOSE_AT_END_ALL_SECONDS", 0.05)
    let_it_finish = threading.Event()
    order: list[str] = []

    def _slow_kill() -> None:
        let_it_finish.wait(30)
        order.append("killed")

    async def _slow_close() -> None:
        await asyncio.sleep(3600)

    class _Tools:
        async def call(self, tool_name, arguments, *, refused=None):
            order.append("called")
            return "done"

        def window(self):
            return None

    monkeypatch.setattr(srv.builtin_provider, "close_browser", _slow_close)
    monkeypatch.setattr(srv.builtin_provider, "kill_browser", _slow_kill)
    monkeypatch.setattr(srv.builtin_provider, "_browser", _Tools())
    monkeypatch.setattr(srv.builtin_provider, "_killing", set())

    async def _scenario() -> None:
        try:
            result = await asyncio.wait_for(srv.admin_control({"action": "end_all"}), 5.0)
            assert result["success"] is True
            # The next task calls a browser tool straight away.
            calling = asyncio.ensure_future(srv.builtin_provider._run_browser("browser_click", {"uid": "1_1"}))
            await asyncio.sleep(0.1)
            assert order == [] and not calling.done()
        finally:
            let_it_finish.set()
        assert await asyncio.wait_for(calling, 10.0) == "done"
        # The kill found the browser of before, not the one this call would have started.
        assert order == ["killed", "called"]

    asyncio.run(_scenario())


def test_end_all_asks_the_browser_to_close_without_waiting_for_the_keys(srv, monkeypatch: pytest.MonkeyPatch):
    import threading

    let_go = threading.Event()
    order: list[str] = []

    class _SlowDesktop:
        def release_held(self) -> bool:
            # A worker that still has the mouse: letting go waits for it.
            order.append("keys asked")
            let_go.wait(30)
            order.append("keys released")
            return True

    browser_asked = asyncio.Event()

    async def _close() -> None:
        order.append("browser asked")
        # The keys are still being let go of: the browser was not kept waiting for them.
        assert "keys released" not in order
        browser_asked.set()

    monkeypatch.setattr(srv.builtin_provider, "_desktop", _SlowDesktop())
    monkeypatch.setattr(srv.builtin_provider, "close_browser", _close)

    async def _scenario() -> dict:
        stopping = asyncio.ensure_future(srv.admin_control({"action": "end_all"}))
        try:
            await asyncio.wait_for(browser_asked.wait(), 10.0)
            # The browser is dealt with; the keys are not. Answered now, the page would say
            # everything was stopped with a key still held down.
            await asyncio.sleep(0.1)
            assert not stopping.done() and "keys released" not in order
        finally:
            let_go.set()
        return await asyncio.wait_for(stopping, 10.0)

    result = asyncio.run(_scenario())

    assert result["success"] is True
    # (Which of the two is asked first is a matter of when the worker thread gets going.)
    assert sorted(order) == ["browser asked", "keys asked", "keys released"]
    assert order.index("browser asked") < order.index("keys released")


def test_end_all_kills_a_browser_that_answered_in_time_but_could_not_be_closed(
    srv, browser_closes, monkeypatch: pytest.MonkeyPatch
):
    import threading

    killed = threading.Event()
    # Closing it failed: it is kept to be closed some other time, and runs on meanwhile.
    monkeypatch.setattr(srv.builtin_provider, "browser_unclosed", lambda: True)
    monkeypatch.setattr(srv.builtin_provider, "kill_browser", killed.set)
    monkeypatch.setattr(srv.builtin_provider, "_killing", set())

    async def _scenario() -> None:
        result = await asyncio.wait_for(srv.admin_control({"action": "end_all"}), 10.0)
        assert result["success"] is True
        assert browser_closes == [True]
        assert await asyncio.to_thread(killed.wait, 5.0)

    asyncio.run(_scenario())


def test_end_all_kills_nothing_when_the_browser_was_closed(srv, browser_closes, monkeypatch: pytest.MonkeyPatch):
    killed: list[int] = []
    monkeypatch.setattr(srv.builtin_provider, "browser_unclosed", lambda: False)
    monkeypatch.setattr(srv.builtin_provider, "kill_browser", lambda: killed.append(1))

    async def _scenario() -> None:
        result = await asyncio.wait_for(srv.admin_control({"action": "end_all"}), 10.0)
        assert result["success"] is True
        await asyncio.sleep(0.1)

    asyncio.run(_scenario())

    assert browser_closes == [True] and killed == []
