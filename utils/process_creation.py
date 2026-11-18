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
"""Coordinate host-owned exec and fork creation without serializing startup.

A concurrent bare fork can retain Popen's temporary exec-error pipe writer,
leaving its parent stuck inside Popen before any scan/handshake timeout starts.
All cooperating creators in one process use this module; child initialization,
protocol I/O and process lifetime stay outside the creation window. This does
not make arbitrary third-party forks or OS process creation interruptible.
"""
from __future__ import annotations

from contextlib import contextmanager
import logging
import os
import subprocess
import threading
import time
from typing import Any, Iterator, Protocol


_logger = logging.getLogger(__name__)
_creation_lock = threading.Lock()
_CANCEL_POLL_SECONDS = 0.05


class ProcessCreationCancelled(RuntimeError):
    """A queued creator was retired before it could allocate a process."""


class ProcessCreationTimeout(TimeoutError):
    """The caller's deadline expired before process creation could begin."""


class _Startable(Protocol):
    def start(self) -> None: ...


def _reset_in_child() -> None:
    global _creation_lock
    # The parent may still own its copy. A child must never wait on that copy.
    _creation_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_in_child)


def _check_request(deadline: float | None, cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessCreationCancelled("process creation cancelled before launch")
    if deadline is not None and time.monotonic() >= deadline:
        raise ProcessCreationTimeout("process creation deadline expired before launch")


@contextmanager
def _creation_window(
    operation: str, deadline: float | None, cancel_event: threading.Event | None,
) -> Iterator[None]:
    queued_at = time.monotonic()
    lock = _creation_lock
    while True:
        _check_request(deadline, cancel_event)
        if deadline is None and cancel_event is None:
            lock.acquire()
            break
        interval = _CANCEL_POLL_SECONDS if deadline is None else max(0.0, deadline - time.monotonic())
        if cancel_event is not None:
            interval = min(interval, _CANCEL_POLL_SECONDS)
        if lock.acquire(timeout=interval):
            break
    acquired_at = time.monotonic()
    try:
        # Cancellation/expiry may win the race with lock handoff.
        _check_request(deadline, cancel_event)
        yield
    finally:
        lock.release()
        _logger.debug(
            "Process creation: operation=%s wait_ms=%.3f creation_ms=%.3f",
            operation, (acquired_at - queued_at) * 1000, (time.monotonic() - acquired_at) * 1000,
        )


def popen(
    *args: Any, deadline: float | None = None, cancel_event: threading.Event | None = None, **kwargs: Any,
) -> subprocess.Popen[Any]:
    """Create a subprocess; return ownership even if creation itself exhausts the deadline.

    The owner must charge elapsed creation time to subsequent I/O and reap a
    process returned after expiry. No thread timeout pretends to undo OS creation.
    """
    with _creation_window("exec", deadline, cancel_event):
        return subprocess.Popen(*args, **kwargs)


def start(
    process: _Startable, *, deadline: float | None = None, cancel_event: threading.Event | None = None,
) -> None:
    """Start a multiprocessing process; its handshake and lifetime belong to the caller."""
    with _creation_window("multiprocessing", deadline, cancel_event):
        process.start()
