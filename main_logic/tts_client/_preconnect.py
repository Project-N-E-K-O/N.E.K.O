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

"""A socket opened ahead of the next speech, shared by the WebSocket workers.

Core sends ``TTS_PRECONNECT_SENTINEL`` when a reply's LLM request starts. A
worker that declares ``supports_preconnect`` answers it by opening the next
speech's socket while the model is still thinking, so the TLS and WebSocket
handshakes no longer sit between the first text and the first audio.

The spare is bound to no speech: whichever connect the speech path makes next
adopts it. The rules every worker relies on live here, in one place:

* At most one connection at a time. The worker tells ``start`` what else it
  holds (its current socket, a warmup socket still closing); the spare's own
  retired sockets close in tracked tasks, and nothing new opens until those
  closes are done.
* A spare nobody adopts closes after ``idle_seconds``; a new preconnect while
  one is kept restarts that window.
* A speech adopting a spare still in progress waits for its connect like for
  a fresh one, then gets the fresh socket's confirmation window counted from
  when it connected.
* Cancelling a spare only ever cuts its connect or confirm phase. Every close
  runs in its own tracked task; a socket that is already closed gets none.
* Waiting for those closes before a fresh connect yields to a queued
  interrupt or shutdown.
"""

from __future__ import annotations

import asyncio
import functools
import queue as queue_module
from typing import Any, Awaitable, Callable, Optional

from ._infra import TTS_SHUTDOWN_SENTINEL, _ws_is_open
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

# A preconnected socket waits at most this long for a speech to adopt it.
# It covers the LLM's time to first text (a few seconds, longer with tool
# rounds or a vision request); after that the reply most likely failed, and
# some services bill connection time, so the socket is closed.
PRECONNECT_IDLE_SECONDS = 15.0
# Bound for the spare's own post-connect handshake (Step's
# ``tts.connection.done``). It runs while the LLM is still thinking, so it can
# wait longer than the speech path would.
PRECONNECT_HANDSHAKE_TIMEOUT_SECONDS = 3.0
# How long a speech waits for a spare still confirming, counted from when the
# spare connected: the same window a fresh socket gets.
NEW_SOCKET_CONFIRM_TIMEOUT_SECONDS = 1.0
# A retired spare's close never waits longer than this for the peer.
SPARE_CLOSE_TIMEOUT_SECONDS = 2.0
# Workers whose upstream keeps a finished round's socket open until the next
# speech retire it on a preconnect, so the spare can open without a second
# connection. "Finished" also requires this long without audio on it, in case
# a done event arrived ahead of the round's last audio: Qwen's server_commit
# mode can pause 1-2s between sentences.
FINISHED_ROUND_QUIET_SECONDS = 3.0

_CONTROL_REQUEST_IDS = frozenset({TTS_SHUTDOWN_SENTINEL, "__interrupt__"})


def defer_queued_work_until_control(request_queue, deferred) -> bool:
    """Move a queued interrupt or shutdown ahead of other queued work.

    Drains ``request_queue`` without blocking into ``deferred`` until it meets
    a control request, which goes to the front of ``deferred``. Returns
    whether there was one. The worker reads ``deferred`` before the queue.
    """
    control_request = None
    while True:
        try:
            queued_request = request_queue.get_nowait()
        except queue_module.Empty:
            break
        except (AttributeError, NotImplementedError):
            break
        if queued_request[0] in _CONTROL_REQUEST_IDS:
            control_request = queued_request
            break
        deferred.append(queued_request)
    if control_request is None:
        return False
    deferred.appendleft(control_request)
    return True


def worker_supports_preconnect(worker) -> bool:
    """Whether a worker callable handles ``TTS_PRECONNECT_SENTINEL``.

    Resolvers bind per-voice options with ``functools.partial`` (ElevenLabs
    binds its base URL), which hides the function's attributes, so the flag is
    read off the wrapped function.
    """
    while isinstance(worker, functools.partial):
        if getattr(worker, "supports_preconnect", False):
            return True
        worker = worker.func
    return bool(getattr(worker, "supports_preconnect", False))


def ws_may_hold_connection(ws_conn) -> bool:
    """Whether the server may still count this socket as a live connection.

    True while it is open or still closing; ``_ws_is_open`` alone turns False
    as soon as the close starts.
    """
    if ws_conn is None:
        return False
    state = getattr(ws_conn, "state", None)
    if state is None:
        return _ws_is_open(ws_conn)
    return getattr(state, "name", "") != "CLOSED"


async def _close_best_effort(ws_conn) -> None:
    try:
        await ws_conn.close()
    except Exception as close_exc:
        logger.debug("关闭未用上的预建 TTS socket 失败: %s", close_exc)


class SpareSocket:
    """Owns the one socket a worker opens ahead of its next speech.

    ``connect`` opens the transport (a ``websockets.connect`` call with a
    short ``close_timeout``). ``confirm``, when given, runs the provider's
    handshake on the connected socket and returns a truthy payload, or a falsy
    one on failure; without it the payload is ``True``. Only handshake steps
    that depend on neither the text nor its language belong in ``confirm``.

    ``defer_control`` moves a queued interrupt or shutdown to the front of the
    worker's own queue and returns whether there was one; ``await_cleanup``
    stops waiting as soon as it does.

    Timing values left as None are read from this module when the instance is
    created, so tests can patch the module constants.
    """

    def __init__(
        self,
        *,
        connect: Callable[[], Awaitable[Any]],
        defer_control: Callable[[], bool],
        confirm: Optional[Callable[[Any], Awaitable[Any]]] = None,
        idle_seconds: Optional[float] = None,
        handshake_timeout: Optional[float] = None,
        confirm_timeout: Optional[float] = None,
    ):
        self._connect = connect
        self._confirm = confirm
        self._defer_control = defer_control
        self._idle_seconds = (
            PRECONNECT_IDLE_SECONDS if idle_seconds is None else idle_seconds
        )
        self._handshake_timeout = (
            PRECONNECT_HANDSHAKE_TIMEOUT_SECONDS
            if handshake_timeout is None else handshake_timeout
        )
        self._confirm_timeout = (
            NEW_SOCKET_CONFIRM_TIMEOUT_SECONDS
            if confirm_timeout is None else confirm_timeout
        )
        # Resolves to (ws, payload) or None.
        self._task: Optional[asyncio.Task] = None
        self._expiry: Optional[asyncio.Task] = None
        # The spare's connect progress, read by a speech adopting it mid-handshake.
        self._progress: Optional[dict] = None
        # Retired sockets still closing; referenced until done.
        self._cleanup: set = set()
        # A preconnect that waits for those closes (see start(after_cleanup=True)).
        self._deferred_blocked: Optional[Callable[[], bool]] = None

    # ── state ────────────────────────────────────────────────────────────

    @property
    def closing(self) -> bool:
        """Whether a retired socket is still closing."""
        return bool(self._cleanup)

    # ── open / expire ────────────────────────────────────────────────────

    async def _open(self, progress: dict):
        """Connect and confirm off the speech path.

        Sets ``progress["connected"]`` (and ``connected_at``, loop time) once
        the socket is connected, so a speech adopting it mid-handshake can
        time the confirmation from there, as for a fresh socket. Returns
        (ws, payload), or None on any failure. A failure is not reported, a
        server rejection included: the next speech connects the normal way and
        reports whatever the server says then.
        """
        spare_ws = None
        payload = None
        try:
            spare_ws = await self._connect()
            progress["connected_at"] = asyncio.get_running_loop().time()
            progress["connected"].set()
            if self._confirm is None:
                payload = True
            else:
                payload = await asyncio.wait_for(
                    self._confirm(spare_ws), timeout=self._handshake_timeout,
                )
        except asyncio.CancelledError:
            self.close_tracked(spare_ws)
            raise
        except Exception as e:
            logger.debug("提前建立 TTS 连接失败: %s", e)
        if payload:
            return spare_ws, payload
        # Not awaited here: cancelling this task must only ever cut the
        # connect or confirm phase, never a close already under way.
        self.close_tracked(spare_ws)
        return None

    async def _expire(self, owned_task) -> None:
        await asyncio.sleep(self._idle_seconds)
        if self._task is owned_task:
            logger.debug("预建 TTS 连接 %.0fs 内未被使用，关闭", self._idle_seconds)
            self._discard(from_expiry=True)

    def _restart_expiry(self) -> None:
        if self._expiry is not None and not self._expiry.done():
            self._expiry.cancel()
        self._expiry = asyncio.create_task(self._expire(self._task))

    # ── cleanup tracking ─────────────────────────────────────────────────

    def _keep_until_done(self, task) -> None:
        self._cleanup.add(task)
        task.add_done_callback(self._cleanup_done)

    def _cleanup_done(self, task) -> None:
        if not task.cancelled() and task.exception() is None:
            result = task.result()
            if isinstance(result, tuple):
                # A retired spare that finished despite its cancellation
                # (wait_for can return a result that raced the cancel):
                # nobody adopts its socket, so close it, tracked before this
                # task leaves the set so the slot is never seen free.
                self.close_tracked(result[0])
        self._cleanup.discard(task)
        if self._cleanup or self._deferred_blocked is None:
            return
        blocked = self._deferred_blocked
        self._deferred_blocked = None
        # The worker may have taken another connection meanwhile; start()
        # checks again.
        self.start(blocked)

    def close_tracked(self, ws_conn):
        """Close a socket in its own tracked task.

        Never awaited by whoever retires the socket, so cancelling that caller
        cannot abandon a close half way (websockets does not abort the
        transport when the wait for the close is cancelled) and drop it from
        the tracking that keeps the next connect from overlapping it. A socket
        that is known to be closed has nothing left to wait for; one whose
        state cannot be read is closed anyway. Returns the close task, or
        None when there is nothing to close.
        """
        if ws_conn is None:
            return None
        if getattr(getattr(ws_conn, "state", None), "name", None) == "CLOSED":
            return None
        task = asyncio.create_task(_close_best_effort(ws_conn))
        self._keep_until_done(task)
        return task

    async def close_tracked_and_wait(self, ws_conn, limit_seconds: float) -> None:
        """Close a socket in a tracked task and wait for it, up to a limit.

        For a socket whose receiver should keep reading what is already on
        the wire while the close completes, as an inline close used to allow.
        Past the limit the close carries on in the background, still tracked;
        the wait never cancels it.
        """
        task = self.close_tracked(ws_conn)
        if task is not None:
            await asyncio.wait({task}, timeout=limit_seconds)

    def _retire_task(self, task) -> None:
        """Get rid of a spare without waiting on its socket's close."""
        if not task.done():
            # Cuts only its connect or confirm phase: its cancellation path
            # hands any socket to close_tracked.
            task.cancel()
            self._keep_until_done(task)
            return
        result = task.result()
        if result is not None:
            self.close_tracked(result[0])

    async def await_cleanup(self) -> bool:
        """Let retired sockets finish closing before the speech connects.

        Returns False, with the control message moved to the front, when an
        interrupt or shutdown is queued meanwhile; the caller then drops this
        connect.
        """
        while self._cleanup:
            await asyncio.wait(set(self._cleanup), timeout=0.05)
            if self._defer_control():
                return False
        return True

    async def drain(self, limit_seconds: float) -> None:
        """Give retired sockets' closes a bounded wait at worker exit.

        Looped because a cancelled spare hands its socket to a new close task
        as the cancellation lands. Callers keep this out of a ``finally`` that
        returns (see tests/unit/test_no_return_through_looping_finally.py).
        """
        deadline = asyncio.get_running_loop().time() + limit_seconds
        while self._cleanup:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            await asyncio.wait(set(self._cleanup), timeout=remaining)

    # ── public lifecycle ─────────────────────────────────────────────────

    def start(
        self,
        blocked: Optional[Callable[[], bool]] = None,
        *,
        after_cleanup: bool = False,
    ) -> None:
        """Open a spare for the next speech, unless one connection is enough.

        ``blocked`` reports whether the worker itself still holds a connection
        (its current socket, a warmup socket closing in the background). While
        a retired socket of this spare is still closing nothing opens; with
        ``after_cleanup`` the request waits for those closes instead of being
        dropped, then checks ``blocked`` again.
        """
        self._deferred_blocked = None
        if self._task is not None:
            result = self._task.result() if self._task.done() else None
            if not self._task.done() or (result is not None and _ws_is_open(result[0])):
                # Still connecting, or usable: one spare at a time. A new
                # reply just started, so its idle window restarts from now
                # rather than expiring under this reply.
                self._restart_expiry()
                return
            # It failed, or the server dropped it while it idled: replace it
            # instead of ignoring preconnects until it expires.
            self._discard()
        if blocked is not None and blocked():
            return
        if self._cleanup:
            if after_cleanup:
                self._deferred_blocked = blocked or (lambda: False)
            return
        self._progress = {"connected": asyncio.Event(), "connected_at": None}
        self._task = asyncio.create_task(self._open(self._progress))
        self._restart_expiry()

    def _discard(self, *, from_expiry: bool = False) -> None:
        task = self._task
        expiry = self._expiry
        self._task = None
        self._expiry = None
        self._progress = None
        if expiry is not None and not from_expiry and not expiry.done():
            expiry.cancel()
        if task is not None:
            self._retire_task(task)

    def discard(self) -> None:
        """Drop the spare (and any preconnect waiting on a close)."""
        self._deferred_blocked = None
        self._discard()

    async def take(self):
        """Hand the spare to a speech, waiting for it if still connecting.

        A spare still in progress is held to the same bounds as a fresh
        socket, phase by phase. While it is still connecting the speech waits
        for the connect, unbounded here exactly like a fresh
        ``websockets.connect`` (the library's own open timeout applies). Once
        connected, it gets the fresh socket's confirmation window counted from
        when it connected. Since it started earlier, neither phase ends later
        than it would on a fresh socket. Its own longer handshake bound only
        applies while nothing waits on it. Returns (ws, payload) or None;
        raises asyncio.TimeoutError, with the spare cancelled, when the window
        passes, so the caller treats it exactly like a fresh socket's
        confirmation timeout.
        """
        # A preconnect still waiting for a close is not a spare; the speech
        # waits for that close itself (await_cleanup) and connects.
        self._deferred_blocked = None
        task = self._task
        if task is None:
            return None
        expiry = self._expiry
        progress = self._progress
        self._task = None
        self._expiry = None
        self._progress = None
        if expiry is not None and not expiry.done():
            expiry.cancel()
        if not task.done():
            try:
                connected_wait = asyncio.ensure_future(progress["connected"].wait())
                try:
                    await asyncio.wait(
                        {task, connected_wait}, return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    connected_wait.cancel()
                if not task.done():
                    confirm_elapsed = (
                        asyncio.get_running_loop().time() - progress["connected_at"]
                    )
                    await asyncio.wait(
                        {task},
                        timeout=max(0.0, self._confirm_timeout - confirm_elapsed),
                    )
            except asyncio.CancelledError:
                # Nothing references the spare any more; do not leave its
                # socket open behind a cancelled worker. Retired like any
                # other spare: a finished one has its socket closed, an
                # unfinished one is cancelled and tracked until it is.
                self._retire_task(task)
                raise
            if not task.done():
                self._retire_task(task)
                raise asyncio.TimeoutError
        result = task.result()
        if result is None:
            return None
        if not _ws_is_open(result[0]):
            # The server dropped it while it idled; connect normally.
            self._retire_task(task)
            return None
        return result
