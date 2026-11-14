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

"""Which commission is using the desktop, and which the browser.

There is one desktop and, for now, one browser. The built-in tools are small
steps (one click, one key), so a lock around each call would not keep two
task agents apart: they would take turns clicking. A commission therefore
occupies a group of built-in tools from its first call until it lets go of it:

* the run of its task agent ends (finished, stopped or failed);
* it starts waiting for the user (a question, a permission), so the user gets
  the desktop back to answer;
* it ends.

Another commission that wants the group meanwhile waits, for a bounded time,
and is then told who has it.

A commission and the task agents it started are one team and share an
occupation. A parent waits for its children with a tool call inside its own
run, so a child that had to wait for the parent to let go would never get its
turn. Within a team only single calls are kept apart; that is done where the
calls are carried out.

Everything here runs on the event loop and nothing is awaited while the state
is changed, so no lock is needed. The state is kept in memory only: a host
that restarts has no task agent left that could occupy anything.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Returns the commission a team is named after: the one no task agent started.
RootLookup = Callable[[str], Awaitable[str]]
# Told that a team began (``"started"``) or stopped (``"ended"``) occupying a
# group: ``(group, state, team commission id)``. Must not block.
ActivityListener = Callable[[str, str, str], None]
# Called once a group is free again: ``(group, team commission id)``.
ReleasedCallback = Callable[[str, str], Awaitable[None]]


@dataclass(slots=True)
class Occupation:
    """One team's hold on a group."""

    team: str
    # What the team is working on, to tell another commission that waits.
    title: str
    # The team's commissions that used the group and have not let go.
    members: set[str] = field(default_factory=set)


class BuiltinLeases:
    """Keeps track of which team occupies which group of built-in tools."""

    def __init__(
        self,
        *,
        root_of: RootLookup,
        on_activity: ActivityListener | None = None,
        on_released: ReleasedCallback | None = None,
        wait_seconds: float | None = None,
    ) -> None:
        self._root_of = root_of
        self._on_activity = on_activity
        self._on_released = on_released
        self._wait_seconds = wait_seconds
        self._held: dict[str, Occupation] = {}
        self._waiters: set[asyncio.Future[None]] = set()
        self._cleanups: set[asyncio.Task[Any]] = set()

    def set_activity_listener(self, listener: ActivityListener | None) -> None:
        self._on_activity = listener

    def holder(self, group: str) -> Occupation | None:
        """Return who occupies a group right now, or None."""
        return self._held.get(group)

    def occupied(self) -> dict[str, str]:
        """Return the occupied groups with the team that has each."""
        return {group: held.team for group, held in self._held.items()}

    def _wait_limit(self) -> float:
        if self._wait_seconds is not None:
            return self._wait_seconds
        from config.nekoclaw_settings import BUILTIN_OCCUPIED_WAIT_SECONDS

        return BUILTIN_OCCUPIED_WAIT_SECONDS

    def _tell(self, group: str, state: str, team: str) -> None:
        if self._on_activity is None:
            return
        try:
            self._on_activity(group, state, team)
        except Exception:  # noqa: BLE001 - a display that fails must not hold the work up
            logger.warning("telling about built-in activity failed", exc_info=True)

    async def acquire(self, group: str, commission_id: str, *, title: str = "") -> Occupation | None:
        """Occupy a group for a commission, waiting while another team has it.

        Returns None once the commission's team has the group. When the wait
        runs out, returns the occupation that is still in the way.
        """
        team = await self._root_of(commission_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._wait_limit()
        while True:
            held = self._held.get(group)
            if held is None:
                self._held[group] = Occupation(team=team, title=title, members={commission_id})
                self._tell(group, "started", team)
                return None
            if held.team == team:
                held.members.add(commission_id)
                return None
            remaining = deadline - loop.time()
            if remaining <= 0:
                return held
            waiter: asyncio.Future[None] = loop.create_future()
            self._waiters.add(waiter)
            try:
                await asyncio.wait_for(waiter, remaining)
            except asyncio.TimeoutError:
                # Looked at once more: it may have come free at the last moment.
                deadline = loop.time()
            finally:
                self._waiters.discard(waiter)

    def release(self, commission_id: str) -> list[str]:
        """Let go of everything a commission occupies; return the groups that came free.

        A group stays occupied while another commission of the same team
        still uses it.
        """
        freed: list[tuple[str, Occupation]] = []
        for group, held in list(self._held.items()):
            if commission_id not in held.members:
                continue
            held.members.discard(commission_id)
            if not held.members:
                del self._held[group]
                freed.append((group, held))
        if not freed:
            return []
        for waiter in list(self._waiters):
            if not waiter.done():
                waiter.set_result(None)
        for group, held in freed:
            self._tell(group, "ended", held.team)
            if self._on_released is not None:
                self._spawn(self._on_released(group, held.team))
        return [group for group, _held in freed]

    def release_all(self) -> None:
        """Let go of every occupation; for shutting down."""
        for held in list(self._held.values()):
            for member in list(held.members):
                self.release(member)

    def _spawn(self, work: Awaitable[None]) -> None:
        try:
            task = asyncio.ensure_future(work)
        except RuntimeError:
            # No running loop (a release during interpreter teardown): nothing to clean up with.
            close = getattr(work, "close", None)
            if close is not None:
                close()
            return
        self._cleanups.add(task)
        task.add_done_callback(self._cleanup_done)

    def _cleanup_done(self, task: "asyncio.Task[Any]") -> None:
        self._cleanups.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("cleaning up after a built-in group was released failed", exc_info=task.exception())

    async def settle(self) -> None:
        """Wait for the cleanups started by releases so far."""
        while self._cleanups:
            await asyncio.gather(*list(self._cleanups), return_exceptions=True)
