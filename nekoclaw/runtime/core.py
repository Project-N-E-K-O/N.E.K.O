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

"""The runtime itself: many familiar sessions on one event loop.

``RuntimeCore`` executes decoded commands. ``InProcessRuntime`` exposes it in
the caller's own process; ``nekoclaw.runtime.process.SubprocessRuntime``
exposes the same methods and the same events with the core running in a child
process. Both satisfy ``FamiliarRuntime``, so a host can switch between them
without other changes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from nekoclaw.runtime import protocol
from nekoclaw.runtime.host import HostBridge, NoHost
from nekoclaw.runtime.protocol import EventSink
from nekoclaw.runtime.session import FamiliarSession, SessionError

logger = logging.getLogger(__name__)

SessionOpener = Callable[[protocol.FamiliarSpec, HostBridge, EventSink], Awaitable[FamiliarSession]]

# How long the in-process runtime's shutdown waits for sessions that are being
# created or closed, and for the closes it starts itself.
SHUTDOWN_WAIT_SECONDS = 15.0


class FamiliarRuntime(Protocol):
    """Driver-facing interface of a runtime, in-process or in a subprocess.

    Every method returns once the command was handed over; outcomes arrive as
    events on the sink given at construction. A rejected command produces an
    ``error`` event rather than an exception.
    """

    async def start(self, spec: protocol.FamiliarSpec) -> None:
        """Create a session, continuing its transcript if one exists."""

    async def prompt(self, session_id: str, text: str) -> None:
        """Hand the session work; rejected while a run is in progress."""

    async def steer(self, session_id: str, text: str) -> None:
        """Deliver text before the next model request of the current run."""

    async def follow_up(self, session_id: str, text: str) -> None:
        """Deliver text once the current run has nothing left to do."""

    async def abort(self, session_id: str) -> None:
        """Stop the current run and the session's background jobs."""

    async def close(self, session_id: str) -> None:
        """Stop the session and forget it."""

    async def shutdown(self) -> None:
        """Close every session; a subprocess runtime also ends its process."""


class RuntimeCore:
    """Owns the sessions of one runtime and carries out commands on them."""

    def __init__(
        self,
        host: HostBridge,
        emit: EventSink,
        *,
        open_session: SessionOpener | None = None,
    ) -> None:
        self._host = host
        self._emit = emit
        self._open_session: SessionOpener = open_session or FamiliarSession.open
        self._sessions: dict[str, FamiliarSession] = {}
        # Sessions being created; each future is done once the attempt is over.
        self._starting: dict[str, asyncio.Future[None]] = {}
        # Sessions being closed; each future is done once the session is gone.
        self._closing: dict[str, asyncio.Future[None]] = {}
        # Calls of ``shutdown`` at work right now. While there is one, no new
        # session and no new work is accepted. A count, not a flag: a second
        # call that returns first must not reopen the door for the first.
        self._shutdowns = 0
        # Sessions whose creation outlasted a shutdown; closed on arrival.
        self._abandoned: set[str] = set()
        # Keeps the tasks that close sessions alive until they are done.
        self._close_tasks: set[asyncio.Future[None]] = set()
        self.last_command_at = time.monotonic()

    # -- state ----------------------------------------------------------------

    @property
    def session_ids(self) -> list[str]:
        return list(self._sessions)

    @property
    def idle(self) -> bool:
        """True when no session is being created or closed, runs, or has a running job."""
        if self._starting or self._closing:
            return False
        return not any(session.busy for session in self._sessions.values())

    def _reject(self, session_id: str, command: str, message: str) -> None:
        self._emit(
            protocol.dump(
                protocol.ErrorEvent(session_id=session_id, command=command, message=message)
            )
        )

    def _session(self, session_id: str, command: str) -> FamiliarSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            self._reject(session_id, command, f'unknown session "{session_id}"')
        return session

    # -- commands -------------------------------------------------------------

    async def handle(self, command: Any) -> None:
        """Carry out one validated command from ``protocol.parse_command``."""
        self.last_command_at = time.monotonic()
        if isinstance(command, protocol.StartCommand):
            await self.start(command.spec)
            return
        # A driver may send the next command right after ``start`` without
        # waiting for ``started``. Creating a session takes a while, so such a
        # command waits for it here instead of failing with "unknown session".
        # Waiting commands resume in the order they arrived. The future is
        # shared by all of them, hence the shield: a caller that is cancelled
        # while waiting must not cancel it for the others.
        opening = self._starting.get(getattr(command, "session_id", ""))
        if opening is not None:
            await asyncio.shield(opening)
        if isinstance(command, protocol.PromptCommand):
            self.prompt(command.session_id, command.text)
        elif isinstance(command, protocol.SteerCommand):
            self.steer(command.session_id, command.text)
        elif isinstance(command, protocol.FollowUpCommand):
            self.follow_up(command.session_id, command.text)
        elif isinstance(command, protocol.AbortCommand):
            self.abort(command.session_id)
        elif isinstance(command, protocol.CloseCommand):
            await self.close(command.session_id)
        else:
            raise TypeError(f"not a session command: {type(command).__name__}")

    async def start(self, spec: protocol.FamiliarSpec) -> None:
        session_id = spec.session_id
        if self._shutdowns:
            # It would be created behind the back of the shutdown and stay open.
            self._reject(session_id, "start", "the runtime is shutting down")
            return
        if session_id in self._sessions or session_id in self._starting:
            self._reject(session_id, "start", f'session "{session_id}" already exists')
            return
        opening: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._starting[session_id] = opening
        try:
            # A session of the same id may still be closing. It has to finish
            # writing its transcript before the new one reads it, and its
            # ``closed`` event has to precede the new ``started``.
            closing = self._closing.get(session_id)
            if closing is not None:
                await asyncio.shield(closing)
            session = await self._open_session(spec, self._host, self._emit)
        except SessionError as exc:
            self._reject(session_id, "start", str(exc))
        except Exception as exc:  # noqa: BLE001 - reported to the host, never raised into the pump
            logger.exception("could not start session %s", session_id)
            self._reject(session_id, "start", f"{type(exc).__name__}: {exc}")
        else:
            if session_id in self._abandoned:
                # A shutdown stopped waiting for this session. It must not
                # stay open behind it, so it is closed the moment it exists.
                try:
                    await session.close()
                except Exception:  # noqa: BLE001 - nothing more can be done for it
                    logger.exception("closing late session %s failed", session_id)
            else:
                self._sessions[session_id] = session
        finally:
            # The session is registered (or the attempt has failed) before the
            # commands that waited for it run.
            self._abandoned.discard(session_id)
            del self._starting[session_id]
            opening.set_result(None)

    def _text_command(self, session_id: str, command: str, text: str) -> None:
        if self._shutdowns:
            # New work would begin behind the back of the shutdown.
            self._reject(session_id, command, "the runtime is shutting down")
            return
        session = self._session(session_id, command)
        if session is None:
            return
        try:
            getattr(session, command)(text)
        except SessionError as exc:
            self._reject(session_id, command, str(exc))

    def prompt(self, session_id: str, text: str) -> None:
        self._text_command(session_id, "prompt", text)

    def steer(self, session_id: str, text: str) -> None:
        self._text_command(session_id, "steer", text)

    def follow_up(self, session_id: str, text: str) -> None:
        self._text_command(session_id, "follow_up", text)

    def abort(self, session_id: str) -> None:
        session = self._session(session_id, "abort")
        if session is not None:
            session.abort()

    async def close(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            closing = self._closing.get(session_id)
            if closing is None:
                self._reject(session_id, "close", f'unknown session "{session_id}"')
            else:
                # Already on its way out, through an earlier close or a
                # shutdown. The caller gets what it asked for: it waits for the
                # same close instead of being told the session is unknown.
                await asyncio.shield(closing)
            return
        await asyncio.shield(self._begin_close(session_id, session))

    def _begin_close(self, session_id: str, session: FamiliarSession) -> asyncio.Future[None]:
        """Start closing a session that has left the table.

        The close runs as a task of its own and stays registered until the
        session is gone, whatever becomes of the caller. A new session of the
        same id waits for it either way, so it never reads a transcript the
        old one is still writing.
        """
        closed: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._closing[session_id] = closed

        async def run() -> None:
            try:
                await session.close()
            except Exception:  # noqa: BLE001 - a failing close still removes the session
                logger.exception("closing session %s failed", session_id)
            finally:
                del self._closing[session_id]
                closed.set_result(None)

        task = asyncio.ensure_future(run())
        self._close_tasks.add(task)
        task.add_done_callback(self._close_tasks.discard)
        return closed

    async def shutdown(self, timeout: float | None = None) -> None:
        """Close every session, including those still being created or closed.

        The sessions in the table start closing at once, which stops their
        background commands first thing. A session on its way in is not in the
        table yet and one on its way out has already left it; both are waited
        for alongside: the former is closed as soon as it exists, the latter
        finishes its own cleanup. New sessions and new work are rejected
        meanwhile. Afterwards the core accepts sessions again.

        ``timeout`` bounds the waiting, in seconds; ``None`` waits as long as
        it takes. When it runs out, or when the caller is cancelled, the
        closes already begun carry on by themselves and stay registered, and
        (after a timeout) a session still being created is closed the moment
        it exists. Either way no session is left that was never told to stop.
        """
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        self._shutdowns += 1
        try:
            while True:
                for session_id, session in list(self._sessions.items()):
                    self._begin_close(session_id, session)
                self._sessions.clear()
                under_way = [*self._starting.values(), *self._closing.values()]
                if not under_way:
                    break
                remaining = None if deadline is None else max(0.0, deadline - loop.time())
                if remaining == 0.0:
                    self._abandoned.update(self._starting)
                    logger.warning(
                        "shutdown stopped waiting for %d session(s) still being created or closed",
                        len(under_way),
                    )
                    break
                # Not awaited directly: giving up this wait must not cancel a
                # future other callers share. A start that finishes puts its
                # session in the table, hence the loop.
                await asyncio.wait(under_way, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self._shutdowns -= 1


class InProcessRuntime:
    """``FamiliarRuntime`` running in the caller's process, on the caller's loop."""

    def __init__(
        self,
        on_event: EventSink,
        host: HostBridge | None = None,
        *,
        open_session: SessionOpener | None = None,
    ) -> None:
        self._core = RuntimeCore(host if host is not None else NoHost(), on_event, open_session=open_session)

    @property
    def core(self) -> RuntimeCore:
        return self._core

    async def start(self, spec: protocol.FamiliarSpec) -> None:
        await self._core.handle(protocol.StartCommand(spec=spec))

    async def prompt(self, session_id: str, text: str) -> None:
        await self._core.handle(protocol.PromptCommand(session_id=session_id, text=text))

    async def steer(self, session_id: str, text: str) -> None:
        await self._core.handle(protocol.SteerCommand(session_id=session_id, text=text))

    async def follow_up(self, session_id: str, text: str) -> None:
        await self._core.handle(protocol.FollowUpCommand(session_id=session_id, text=text))

    async def abort(self, session_id: str) -> None:
        await self._core.handle(protocol.AbortCommand(session_id=session_id))

    async def close(self, session_id: str) -> None:
        await self._core.handle(protocol.CloseCommand(session_id=session_id))

    async def shutdown(self) -> None:
        # Bounded: a session whose creation hangs must not hold up the host's
        # own shutdown for good.
        await self._core.shutdown(timeout=SHUTDOWN_WAIT_SECONDS)
