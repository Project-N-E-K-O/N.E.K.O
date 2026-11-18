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

from nekoclaw.host.detached import run_detached


async def test_what_the_work_returns_is_handed_back():
    ran_in: list[tuple[str, bool]] = []

    def work() -> int:
        thread = threading.current_thread()
        ran_in.append((thread.name, thread.daemon))
        return 7

    assert await run_detached(work, name="nekoclaw-test") == 7
    # In a thread of its own, one the process does not wait for when it exits.
    assert ran_in == [("nekoclaw-test", True)]
    assert threading.current_thread().name != "nekoclaw-test"


async def test_what_the_work_raises_is_raised_for_whoever_waits():
    def work() -> None:
        raise ValueError("no such folder")

    with pytest.raises(ValueError, match="no such folder"):
        await run_detached(work, name="nekoclaw-test")


async def test_work_nobody_waits_for_any_more_goes_on_and_is_done_when_it_comes_back():
    release = threading.Event()

    def work() -> str:
        release.wait(60)
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
            with pytest.raises(RuntimeError, match="can't start new thread"):
                run_detached(lambda: None, name="nekoclaw-test")

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
        release.wait(60)
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
