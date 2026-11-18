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

"""Carrying out the wake requests stored in the ledger.

A session is woken by the host, never by itself: a note another session
wrote is to be handed to it, or a time its task agent set has come. Each is
a row of ``wake_requests``, written first and carried out afterwards, as a
report is written to the outbox first and sent afterwards (``outbox.py``).
``SessionWakes`` does the carrying out. It sleeps until the first pending
row is due, or until it is told that something changed (``kick``); it reads
no file to find out whether there is work, and nothing here runs at startup:
the first call that does work in the host starts it (``NekoClawHost``), and
it stops by itself when no row is pending.

What is due for one session is handed over together, so that several things
that come at once start one turn (``WAKE_COALESCE_SECONDS``). Handing over is
``TaskAgentManager.hand_over``, which knows what the session is at; this
module only decides when. A row that cannot be handed over now (NekoClaw is
switched off, the quota is used up, the session's commission waits for the
user) stays pending and is tried again later; it is never lost for that.

``stop_all`` is for a stop of everything: it drops the notes that wait, and
from then on nothing read before it starts a commission.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from nekoclaw.host.ledger import Ledger, WakeRequest

logger = logging.getLogger(__name__)

STOPPED_REASON = "everything was stopped"


class SessionWakes:
    """Hands sessions what waits for them when it is due. See the module docstring."""

    def __init__(self, ledger: Ledger, task_agents: Any, *, clock: Callable[[], float] | None = None) -> None:
        self._ledger = ledger
        self._task_agents = task_agents
        # The ledger's clock unless told otherwise: when a row is due is
        # written by it.
        self._clock = clock if clock is not None else ledger._clock
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        # Raised by ``stop_all``; what was read before the raise files nothing.
        self._generation = 0
        # (session, owner) -> not before when what waits for it is tried again.
        self._held_until: dict[tuple[str, str | None], float] = {}
        # (session, owner) -> how often in a row handing over failed outright.
        self._failures: dict[tuple[str, str | None], int] = {}

    def kick(self) -> None:
        """Note that something may be due, or that what was held may go now. Starts the loop on first use."""
        if self._closed:
            return
        # Whatever was kept back is looked at again: what changed may be
        # what it waited for.
        self._held_until.clear()
        self._wake.set()
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run())

    async def stop_all(self, reason: str = STOPPED_REASON) -> int:
        """Drop every note that waits to be handed over; returns how many. For a stop of everything.

        Raised first, with nothing awaited before it: a round that is
        handing something over this moment files no commission for it.
        What a commission asked for itself goes with that commission when
        the stop ends it (``Ledger.wakes_void_all``).
        """
        self._generation += 1
        return await self._ledger.wakes_void_all(reason)

    async def flush(self) -> float | None:
        """Hand over everything that is due now, once. Returns when to look again, or None when nothing is pending."""
        from config.nekoclaw_settings import (
            OUTBOX_RETRY_BASE_SECONDS,
            SESSION_WAKE_BATCH_ROWS,
            WAKE_COALESCE_MAX_SECONDS,
            WAKE_COALESCE_SECONDS,
            WAKE_FAILED_RETRY_MAX_SECONDS,
        )

        now = self._clock()
        generation = self._generation
        due = await self._ledger.wakes_due(due_by=now)
        groups: dict[tuple[str, str | None], list[WakeRequest]] = {}
        for wake in due:
            groups.setdefault((wake.session_id, wake.commission_id), []).append(wake)
        # When the next look is needed for what is not handed over now.
        again: float | None = None

        def later(moment: float) -> None:
            nonlocal again
            again = moment if again is None else min(again, moment)

        for key, wakes in groups.items():
            if self._closed:
                return None
            held = self._held_until.get(key)
            if held is not None and held > now:
                later(held)
                continue
            # What comes together starts one turn: wait a moment for more,
            # counted from the last that came, and not for ever.
            ready = min(
                max(wake.due_at for wake in wakes) + WAKE_COALESCE_SECONDS,
                min(wake.due_at for wake in wakes) + WAKE_COALESCE_MAX_SECONDS,
            )
            if ready > now:
                later(ready)
                continue
            batch = wakes[:SESSION_WAKE_BATCH_ROWS]
            try:
                handed = await self._task_agents.hand_over(
                    key[0], key[1], batch, still_wanted=lambda: generation == self._generation and not self._closed
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one session's trouble must not keep the others waiting
                failures = self._failures.get(key, 0) + 1
                self._failures[key] = failures
                pause = min(OUTBOX_RETRY_BASE_SECONDS * (2 ** min(failures - 1, 30)), WAKE_FAILED_RETRY_MAX_SECONDS)
                logger.exception("handing a session what waits for it failed (%d in a row)", failures)
                self._held_until[key] = self._clock() + pause
                later(self._held_until[key])
                continue
            self._failures.pop(key, None)
            if handed.outcome == "held":
                self._held_until[key] = self._clock() + max(handed.retry_in, 0.05)
                later(self._held_until[key])
            else:
                self._held_until.pop(key, None)
                if len(wakes) > len(batch):
                    # The rest follows at once.
                    later(self._clock())
        # The first that was not due when this round began: not the first
        # of all, which may be one that is due and kept back, in front of
        # another session's that is still to come. (What became due while
        # this round was handing over is among these, and is looked at next.)
        upcoming = await self._ledger.wakes_next_due(after=now)
        if upcoming is not None:
            later(upcoming)
        return again

    async def _run(self) -> None:
        from nekoclaw.host.outbox import retry_delay

        failures = 0
        while not self._closed:
            self._wake.clear()
            try:
                again = await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive a bad round
                # The rows could not be read; that says nothing about
                # whether any are left, so try again after a growing pause.
                failures += 1
                logger.exception("a round of wake requests failed (%d in a row)", failures)
                pause = retry_delay(failures)
            else:
                failures = 0
                if again is None:
                    if self._wake.is_set():
                        # A row arrived while this round was reading.
                        continue
                    # Nothing pending; the next kick starts the loop again.
                    return
                pause = max(0.05, again - self._clock())
            try:
                await asyncio.wait_for(self._wake.wait(), pause)
            except asyncio.TimeoutError:
                # The pause ran out without a kick; the next round looks again.
                pass

    async def close(self) -> None:
        self._closed = True
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
