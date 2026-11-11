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


class FamiliarRuntime(Protocol):
    """Driver-facing interface of a runtime, in-process or in a subprocess.

    Every method returns once the command was handed over; outcomes arrive as
    events on the sink given at construction. A rejected command produces an
    ``error`` event rather than an exception.
    """

    async def start(self, spec: protocol.FamiliarSpec) -> None: ...

    async def prompt(self, session_id: str, text: str) -> None: ...

    async def steer(self, session_id: str, text: str) -> None: ...

    async def follow_up(self, session_id: str, text: str) -> None: ...

    async def abort(self, session_id: str) -> None: ...

    async def close(self, session_id: str) -> None: ...

    async def shutdown(self) -> None: ...


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
        self._starting: set[str] = set()
        self._closing: set[str] = set()
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
        elif isinstance(command, protocol.PromptCommand):
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
        if session_id in self._sessions or session_id in self._starting:
            self._reject(session_id, "start", f'session "{session_id}" already exists')
            return
        self._starting.add(session_id)
        try:
            session = await self._open_session(spec, self._host, self._emit)
        except SessionError as exc:
            self._reject(session_id, "start", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - reported to the host, never raised into the pump
            logger.exception("could not start session %s", session_id)
            self._reject(session_id, "start", f"{type(exc).__name__}: {exc}")
            return
        finally:
            self._starting.discard(session_id)
        self._sessions[session_id] = session

    def _text_command(self, session_id: str, command: str, text: str) -> None:
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
            self._reject(session_id, "close", f'unknown session "{session_id}"')
            return
        self._closing.add(session_id)
        try:
            await session.close()
        except Exception:  # noqa: BLE001 - a failing close still removes the session
            logger.exception("closing session %s failed", session_id)
        finally:
            self._closing.discard(session_id)

    async def shutdown(self) -> None:
        """Close every session."""
        sessions = list(self._sessions.values())
        self._sessions.clear()
        await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)


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
        await self._core.shutdown()
