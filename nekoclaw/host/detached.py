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

``run_detached`` is for the event loop, ``wait_detached`` for a thread.

A look at a folder the user works in can hang for as long as the folder
does not answer: a network share that is down, a volume that went away.
Nothing stops such a call. What can be done is to wait for it only so
long, and to run it where a call that never returns does no harm: in a
thread of its own that the process does not wait for when it exits, and
that is none of the threads the rest of the host does its file work in.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class NoThread(RuntimeError):
    """No thread could be started for the work: it was not begun."""


def run_detached(work: Callable[[], T], *, name: str) -> asyncio.Future[T]:
    """Run blocking work in a thread of its own; return a future of what it returns or raises.

    Called from the event loop. The thread is a daemon thread: one that
    never comes back does not keep the process from exiting, and takes
    no worker of the default executor. Whoever waits for the future
    decides how long (``asyncio.wait_for`` around ``asyncio.shield``);
    the work goes on in its thread whether or not anybody still waits,
    and the future is done when it comes back, however it ended: once
    the loop has run what hands it over. A loop that was closed before
    that takes nothing, and the future is left as it was. What
    the work raises is raised for whoever waits. For ``StopIteration``,
    which a future does not take, that is a RuntimeError that names it;
    and when the work was ended by something that is no ``Exception``
    (``SystemExit``), a RuntimeError that says it ended without a
    result and has that as its cause, while that goes on its way in
    the thread.

    Raises ``NoThread`` when no thread can be started.
    """
    loop = asyncio.get_running_loop()
    done: asyncio.Future[T] = loop.create_future()

    def hand_over(result: T | None, error: Exception | None) -> None:
        if done.done():
            return
        if error is None:
            done.set_result(result)  # type: ignore[arg-type]
        else:
            done.set_exception(error)

    def run() -> None:
        result: T | None = None
        # What whoever waits is told when the work is ended by something
        # that is no ``Exception``: that is not raised in the event loop.
        error: Exception | None = RuntimeError("the work ended without a result")
        try:
            result = work()
            error = None
        except StopIteration as exc:
            # A future takes no ``StopIteration``.
            error = RuntimeError("the work ended with StopIteration")
            error.__cause__ = exc
        except Exception as exc:  # noqa: BLE001 - handed to whoever waits for it
            error = exc
        finally:
            _ended_by(error)
            try:
                loop.call_soon_threadsafe(hand_over, result, error)
            except RuntimeError:
                # The loop is closed: nobody waits for it any more.
                pass

    _start(threading.Thread(target=run, name=name, daemon=True))
    return done


def _ended_by(error: Exception | None) -> None:
    """Note, with the error that tells of work that ended without a result, what ended it.

    Called while that is on its way through the thread (in a
    ``finally``): something that is no ``Exception``, which is told as
    the cause and goes on its way.
    """
    ending = sys.exc_info()[1]
    if error is not None and ending is not None and not isinstance(ending, Exception):
        error.__cause__ = ending


def _start(thread: threading.Thread) -> None:
    try:
        thread.start()
    except RuntimeError as exc:
        # Told apart from what the work itself may raise.
        raise NoThread(str(exc)) from exc


def wait_detached(work: Callable[[], T], *, name: str, seconds: float) -> T:
    """Run blocking work in a thread of its own, and wait for it in this thread for no longer than ``seconds``.

    For a caller that is not on the event loop and holds something
    others wait for (work under a lock, in a worker thread): it gets on
    after ``seconds`` whatever the work does. Returns what the work
    returns and raises what it raises; when the work was ended by
    something that is no ``Exception`` (``SystemExit``), a RuntimeError
    that says it ended without a result, with that as its cause. Raises
    TimeoutError when it
    has not come back by then; it goes on in its thread, a daemon
    thread like that of ``run_detached``.

    Raises ``NoThread`` when no thread can be started.
    """
    came: list[tuple[T | None, Exception | None]] = []

    def run() -> None:
        # What whoever waits is told when the work is ended by something
        # that is no ``Exception``: it has come back, and is not at it.
        outcome: tuple[T | None, Exception | None] = (None, RuntimeError("the work ended without a result"))
        try:
            outcome = (work(), None)
        except Exception as exc:  # noqa: BLE001 - raised for whoever waits for it
            outcome = (None, exc)
        finally:
            _ended_by(outcome[1])
            came.append(outcome)

    thread = threading.Thread(target=run, name=name, daemon=True)
    _start(thread)
    thread.join(seconds)
    if not came:
        raise TimeoutError(f"{name} has not come back")
    result, error = came[0]
    if error is not None:
        raise error
    return result  # type: ignore[return-value]
