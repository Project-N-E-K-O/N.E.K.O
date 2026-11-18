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

"""Blocking work that may never come back, run where it holds nothing up.

A look at a folder the user works in can hang for as long as the folder
does not answer: a network share that is down, a volume that went away.
Nothing stops such a call. What can be done is to wait for it only so
long, and to run it where a call that never returns does no harm: in a
thread of its own that the process does not wait for when it exits, and
that is none of the threads the rest of the host does its file work in.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def run_detached(work: Callable[[], T], *, name: str) -> asyncio.Future[T]:
    """Run blocking work in a thread of its own; return a future of what it returns or raises.

    Called from the event loop. The thread is a daemon thread: one that
    never comes back does not keep the process from exiting, and takes
    no worker of the default executor. Whoever waits for the future
    decides how long (``asyncio.wait_for`` around ``asyncio.shield``);
    the work goes on in its thread whether or not anybody still waits,
    and the future is done when it comes back.

    Raises RuntimeError when no thread can be started.
    """
    loop = asyncio.get_running_loop()
    done: asyncio.Future[T] = loop.create_future()

    def hand_over(result: T | None, error: BaseException | None) -> None:
        if done.done():
            return
        if error is None:
            done.set_result(result)  # type: ignore[arg-type]
        else:
            done.set_exception(error)

    def run() -> None:
        try:
            result, error = work(), None
        except BaseException as exc:  # noqa: BLE001 - handed to whoever waits for it
            result, error = None, exc
        try:
            loop.call_soon_threadsafe(hand_over, result, error)
        except RuntimeError:
            # The loop is closed: nobody waits for it any more.
            pass

    threading.Thread(target=run, name=name, daemon=True).start()
    return done
