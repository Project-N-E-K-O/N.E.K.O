# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Blocking work that may never come back, run in a thread of its own (``nekoclaw.host.detached``)."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from nekoclaw.host.detached import NoThread, run_detached, wait_detached


async def test_what_the_work_returns_is_handed_back():
    ran_in: list[tuple[str, bool]] = []

    def work() -> int:
        thread = threading.current_thread()
        ran_in.append((thread.name, thread.daemon))
        return 7

    assert await asyncio.wait_for(run_detached(work, name="nekoclaw-test"), 30) == 7
    # In a thread of its own, one the process does not wait for when it exits.
    assert ran_in == [("nekoclaw-test", True)]
    assert threading.current_thread().name != "nekoclaw-test"


async def test_what_the_work_raises_is_raised_for_whoever_waits():
    def work() -> None:
        raise ValueError("no such folder")

    with pytest.raises(ValueError, match="no such folder"):
        await asyncio.wait_for(run_detached(work, name="nekoclaw-test"), 30)


def _ended_threads(monkeypatch: Any, name: str) -> tuple[list[Any], threading.Event]:
    """Note what ends the thread called ``name``, in place of the hook that would report it.

    The event is set once that was noted. What ends any other thread is
    reported as ever, and is not taken for it.
    """
    ended: list[Any] = []
    noted = threading.Event()
    reporting = threading.excepthook

    def hook(args: Any) -> None:
        if args.thread is None or args.thread.name != name:
            reporting(args)
            return
        ended.append(args.exc_type)
        noted.set()

    monkeypatch.setattr(threading, "excepthook", hook)
    return ended, noted


async def test_work_that_is_ended_by_something_that_is_no_exception_is_told_without_raising_that(monkeypatch):
    ended, noted = _ended_threads(monkeypatch, "nekoclaw-test-exit")

    def work() -> None:
        raise SystemExit(3)

    # Not ``SystemExit`` in the event loop; and the future is done, so nobody takes the work to be under way.
    future = run_detached(work, name="nekoclaw-test-exit")
    with pytest.raises(RuntimeError, match="the work ended without a result") as raised:
        await asyncio.wait_for(future, 30)
    # What ended it is told as the cause.
    assert isinstance(raised.value.__cause__, SystemExit) and raised.value.__cause__.code == 3
    # What ended the work went on its way in the thread: waited for, as it gets there after the future is done.
    assert await asyncio.to_thread(noted.wait, 30)
    assert ended == [SystemExit]


async def test_work_that_ends_with_what_a_future_does_not_take_is_told_all_the_same():
    def work() -> None:
        next(iter(()))

    # ``StopIteration`` cannot be set on a future; left so, nobody would ever be told the work is over.
    future = run_detached(work, name="nekoclaw-test")
    with pytest.raises(RuntimeError, match="the work ended with StopIteration") as raised:
        await asyncio.wait_for(future, 30)
    assert isinstance(raised.value.__cause__, StopIteration)


async def test_work_nobody_waits_for_any_more_goes_on_and_is_done_when_it_comes_back():
    release = threading.Event()

    def work() -> str:
        release.wait(120)
        return "late"

    future = run_detached(work, name="nekoclaw-test")
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(future), 0.05)
        # Given up on, and still under way.
        assert not future.done()
    finally:
        release.set()
    assert await asyncio.wait_for(future, 30) == "late"


def test_a_thread_that_cannot_be_started_is_told_at_once(monkeypatch):
    def no_start(self: Any) -> None:
        raise RuntimeError("can't start new thread")

    async def started() -> None:
        with monkeypatch.context() as patched:
            patched.setattr(threading.Thread, "start", no_start)
            # Told as what it is, apart from anything the work itself may raise.
            with pytest.raises(NoThread, match="can't start new thread"):
                run_detached(lambda: None, name="nekoclaw-test")
            with pytest.raises(NoThread, match="can't start new thread"):
                wait_detached(lambda: None, name="nekoclaw-test", seconds=30)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(started())
    finally:
        loop.close()


def test_work_that_comes_back_after_the_loop_was_closed_is_dropped_quietly(monkeypatch):
    release = threading.Event()
    failed: list[Any] = []
    monkeypatch.setattr(threading, "excepthook", failed.append)

    def work() -> int:
        release.wait(120)
        return 1

    async def started() -> None:
        run_detached(work, name="nekoclaw-test-late")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(started())
    finally:
        loop.close()
    (thread,) = [thread for thread in threading.enumerate() if thread.name == "nekoclaw-test-late"]
    release.set()
    thread.join(30)

    # Nobody waits for it any more: it ends, and nothing is raised in its thread.
    assert not thread.is_alive() and failed == []


# ---------------------------------------------------------------------------
# Waited for in a thread (``wait_detached``)
# ---------------------------------------------------------------------------


def test_what_work_waited_for_in_a_thread_returns_or_raises_is_handed_on():
    ran_in: list[tuple[str, bool]] = []

    def work() -> int:
        thread = threading.current_thread()
        ran_in.append((thread.name, thread.daemon))
        return 7

    def failing() -> None:
        raise ValueError("no such folder")

    assert wait_detached(work, name="nekoclaw-test-wait", seconds=30) == 7
    # In a thread of its own, one the process does not wait for when it exits.
    assert ran_in == [("nekoclaw-test-wait", True)]
    with pytest.raises(ValueError, match="no such folder"):
        wait_detached(failing, name="nekoclaw-test-wait", seconds=30)


def test_work_waited_for_in_a_thread_is_given_up_on_when_its_time_is_up_and_goes_on(monkeypatch):
    release, over = threading.Event(), threading.Event()
    waited: list[Any] = []
    join = threading.Thread.join

    def watched(self: threading.Thread, timeout: Any = None) -> None:
        if self.name == "nekoclaw-test-wait":
            waited.append(timeout)
        join(self, timeout)

    monkeypatch.setattr(threading.Thread, "join", watched)

    def work() -> None:
        release.wait(120)
        over.set()

    try:
        with pytest.raises(TimeoutError):
            wait_detached(work, name="nekoclaw-test-wait", seconds=0.05)
        assert not over.is_set()
    finally:
        release.set()
    assert over.wait(30)
    # Waited for as long as it was told to, and no longer.
    assert waited == [0.05]


def test_work_waited_for_in_a_thread_that_is_ended_by_something_that_is_no_exception_is_not_taken_to_be_at_it(monkeypatch):
    ended, noted = _ended_threads(monkeypatch, "nekoclaw-test-wait-exit")

    def work() -> None:
        raise SystemExit(3)

    # It came back, so it is not given up on as if its time were up; and what ended it is not raised here.
    with pytest.raises(RuntimeError, match="the work ended without a result") as raised:
        wait_detached(work, name="nekoclaw-test-wait-exit", seconds=30)
    assert noted.wait(30) and ended == [SystemExit]
    # What ended it is told as the cause.
    assert isinstance(raised.value.__cause__, SystemExit) and raised.value.__cause__.code == 3


def test_what_ends_another_thread_is_reported_as_ever_and_not_taken_for_the_one_that_is_watched(monkeypatch):
    reported: list[Any] = []
    as_ever = threading.excepthook

    def failing() -> None:
        raise ValueError("another thread")

    other = threading.Thread(target=failing, name="nekoclaw-test-other", daemon=True)

    def reporting(args: Any) -> None:
        # Only what ends the thread of this test is noted here; whatever else ends meanwhile goes its way.
        if args.thread is other:
            reported.append(args.exc_type)
        else:
            as_ever(args)

    monkeypatch.setattr(threading, "excepthook", reporting)
    ended, noted = _ended_threads(monkeypatch, "nekoclaw-test-watched")

    other.start()
    other.join(30)

    assert reported == [ValueError]
    assert ended == [] and not noted.is_set()
