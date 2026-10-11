"""Retain delayed process startup independently of an async request lifetime."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
import threading


def start_owned_process(start, *args) -> Future:
    """Use a daemon startup thread, not the loop's shutdown-joined executor."""
    result = Future()

    def run():
        if not result.set_running_or_notify_cancel():
            return
        try:
            handles = start(*args)
        except BaseException as exc:
            result.set_exception(exc)
        else:
            result.set_result(handles)

    threading.Thread(
        target=run, name="voice-model-process-startup", daemon=True
    ).start()
    return result


class PendingStartupRetirement:
    """A daemon owner recovers late handles even if its async waiter is cancelled.

    Startup owns cleanup on ordinary failure. A caller may supply the exception
    type that carries handles after a partially failed startup. Replacement
    admission must remain closed until confirmed_stopped becomes true.
    """

    def __init__(self, startup, stop, *, recordings=(), pending_error=None):
        self._startup = startup
        self._stop = stop
        self._recordings = recordings
        self._pending_error = pending_error
        self._confirmed = threading.Event()
        self._retry = threading.Event()
        self.last_error = None
        self._thread = threading.Thread(
            target=self._reap, name="voice-model-startup-retirement", daemon=True
        )
        self.retirement_task = asyncio.create_task(self._wait())
        self._thread.start()

    @property
    def confirmed_stopped(self):
        return self._confirmed.is_set() and not self._thread.is_alive()

    def _reap(self):
        try:
            try:
                process, receiver = self._startup.result()
            except BaseException as exc:
                if self._pending_error is None or not isinstance(
                    exc, self._pending_error
                ):
                    self._confirmed.set()
                    return
                process, receiver = exc.process, exc.receiver
            while True:
                try:
                    self._stop(process)
                except Exception as exc:
                    self.last_error = type(exc).__name__
                    self._retry.wait(0.5)
                else:
                    receiver.close()
                    self._confirmed.set()
                    return
        finally:
            # Startup may still be serializing these arrays until it returns.
            for pcm in self._recordings:
                pcm.fill(0)
            self._recordings = ()
            self._startup = None

    async def _wait(self):
        while not self.confirmed_stopped:
            await asyncio.sleep(0.01)
