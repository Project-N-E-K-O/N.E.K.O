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
        # How often it was told that something changed (``kick``).
        self._kicks = 0
        # Up to which note everything was last stopped (``Ledger.notes_mark``,
        # which numbers them as they are written and does not go by the
        # clock); None for never. A note written by then that is put back
        # after the stop is dropped.
        self.stopped_upto: int | None = None
        # Set while a stop's dropping is still to be made up for: the
        # ledger could not be written when the stop came. Nothing is handed
        # over before it is. The latest of them, where there are several.
        self._drop_upto: int | None = None
        # Up to which note that is noted next to the ledger too
        # (``Ledger.note_stop_owed``); None for not at all. A later stop
        # that could be written nowhere is not covered by the note of an
        # earlier one.
        self._noted_upto: int | None = None
        # One at a time, writing that note and removing it: the two are
        # done in worker threads, and a removal that ran after a later
        # stop's note would take that note with it.
        self._owed_lock = asyncio.Lock()
        # How many stops of everything are under way (``hold``): nothing is
        # handed over meanwhile.
        self._holds = 0

    def hold(self) -> None:
        """Hand nothing over until ``release``: everything is being stopped.

        A stop of everything drops what waits, and then stops the
        commissions one after the other, which takes a while. A note one
        of them writes meanwhile (it was at it when the stop came) is not
        handed over in between, to start work the stop would not know of:
        it waits, and is dropped when the stop is through (the caller
        calls ``stop_all`` again before ``release``).
        """
        self._holds += 1

    @property
    def stopping(self) -> bool:
        """Whether everything is being stopped right now (a ``hold`` is not yet released)."""
        return self._holds > 0

    def release(self) -> None:
        """Hand over again, once every ``hold`` was released."""
        self._holds = max(0, self._holds - 1)
        if not self._holds:
            self.kick()

    def kick(self) -> None:
        """Note that something may be due, or that what was held may go now. Starts the loop on first use."""
        if self._closed:
            return
        # Whatever was kept back is looked at again: what changed may be
        # what it waited for.
        self._kicks += 1
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
        # The notes on record this moment, by the number of the last: read
        # without the file, and never lower than what an earlier stop read.
        mark = self._ledger.notes_mark()
        self.stopped_upto = mark if self.stopped_upto is None else max(self.stopped_upto, mark)
        # With what an earlier stop still owes, should it reach further
        # (it does not: the numbers only go up; kept all the same, so that
        # what is owed is only ever forgotten for a write that covers it).
        upto = self._drop_upto = mark if self._drop_upto is None else max(self._drop_upto, mark)
        try:
            dropped = await self._ledger.wakes_void_all(reason, upto=upto)
        except BaseException:
            # Not dropped: the ledger could not be written. The next round
            # drops them before it hands anything over (``flush``), so none
            # of them starts a turn meanwhile; the caller is told. Noted
            # next to the ledger as well: a process that ends before the
            # round gets to it leaves the stop to the next one.
            await self._note_owed(upto, reason)
            self.kick()
            raise
        if self._drop_upto == upto:
            self._drop_upto = None
            await self._forget_owed(upto)
        return dropped

    async def _note_owed(self, upto: int, reason: str) -> None:
        from utils.asyncio_executor import to_thread_through_cancellation

        try:
            async with self._owed_lock:
                # Through a cancel too: a worker thread goes on when whoever
                # waits for it is cancelled, and the lock let go meanwhile
                # would let the other of the two (writing, removing) in
                # beside it.
                await to_thread_through_cancellation(self._ledger.note_stop_owed, upto, reason)
                # (The ledger keeps the note of a later stop where this one
                # is the earlier: two stops may get here in the other order.)
                self._noted_upto = upto if self._noted_upto is None else max(upto, self._noted_upto)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - kept in mind here all the same, and noted with the next round
            # Nothing on the disk says that everything was stopped: should
            # the process end before the ledger or this note can be written,
            # the next one hands over what the stop was to drop.
            logger.error(
                "a stop of everything could be written neither to the ledger nor next to it; "
                "it holds for as long as this process lasts, and is written as soon as either can be",
                exc_info=True,
            )

    async def _forget_owed(self, upto: int) -> None:
        """Remove the note of a stop that is on record by now: the one up to ``upto``, and no later one's."""
        async with self._owed_lock:
            if self._noted_upto is None or self._drop_upto is not None:
                # Nothing is noted; or a later stop came while this one was
                # made up for, and is still to be: its note stays, and
                # whoever makes up for it removes it.
                return
            from utils.asyncio_executor import to_thread_through_cancellation

            self._noted_upto = None
            await to_thread_through_cancellation(self._ledger.clear_stop_owed, upto)

    async def flush(self) -> float | None:
        """Hand over everything that is due now, once. Returns when to look again, or None when nothing is pending."""
        from config.nekoclaw_settings import (
            OUTBOX_RETRY_BASE_SECONDS,
            SESSION_WAKE_BATCH_ROWS,
            WAKE_COALESCE_MAX_SECONDS,
            WAKE_COALESCE_SECONDS,
            WAKE_DUE_PAGE_ROWS,
            WAKE_FAILED_RETRY_MAX_SECONDS,
        )

        while self._drop_upto is not None:
            # A stop of everything could not drop what waited then. Dropped
            # now, before anything is handed over; while this fails, the
            # round fails, and nothing is. Looked at again after every
            # write: a later stop that could not drop either is made up
            # for before this round reads anything.
            upto = self._drop_upto
            if self._noted_upto is None or self._noted_upto < upto:
                # Not noted next to the ledger either (neither could be
                # written when the stop came, the disk being full, say), or
                # noted for an earlier stop only.
                # Tried again with every round, ahead of the ledger: a
                # process that ends before the ledger can be written then
                # still leaves word of the stop to the next one.
                await self._note_owed(upto, STOPPED_REASON)
            await self._ledger.wakes_void_all(STOPPED_REASON, upto=upto)
            if self._drop_upto == upto:
                self._drop_upto = None
                await self._forget_owed(upto)
        now = self._clock()
        generation = self._generation
        # All that is due, a page after the other: with the first page
        # alone, what it leaves out would wait behind rows that are kept
        # back, or would be left with nothing to look at it again.
        due: list[WakeRequest] = []
        last: tuple[float, int] | None = None
        while True:
            page = await self._ledger.wakes_due(due_by=now, limit=WAKE_DUE_PAGE_ROWS, after=last)
            due.extend(page)
            if len(page) < WAKE_DUE_PAGE_ROWS:
                break
            last = (page[-1].due_at, page[-1].id)
        groups: dict[tuple[str, str | None], list[WakeRequest]] = {}
        for wake in due:
            groups.setdefault((wake.session_id, wake.commission_id), []).append(wake)
        # When the next look is needed for what is not handed over now.
        again: float | None = None

        def later(moment: float) -> None:
            nonlocal again
            again = moment if again is None else min(again, moment)

        for key, wakes in groups.items():
            if self._closed or self._holds:
                # Shutting down, or everything is being stopped: nothing
                # (more) is handed over. What waits stays on record;
                # ``release`` starts the next round.
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
            kicks = self._kicks
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
            if handed.outcome == "held" and self._kicks != kicks:
                # Something changed while this was being handed over (the
                # user answered, say): what kept it back when it was looked
                # at may be so no longer. Looked at again at once, and not
                # after the wait that went with what was so then.
                later(self._clock())
            elif handed.outcome == "held":
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
        """Stop handing over. What is being handed over this moment is let finish; what waits stays on record.

        The round under way is not cut off in the middle of handing
        something over (it looks, before every hand-over, whether this was
        closed, and hands over nothing more): it gets a moment to end, and
        is cancelled only when it takes longer.
        """
        from config.nekoclaw_settings import WAKES_CLOSE_WAIT_SECONDS

        self._closed = True
        self._wake.set()
        task, self._task = self._task, None
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), WAKES_CLOSE_WAIT_SECONDS)
        except asyncio.TimeoutError:
            task.cancel()
            # What it does as it is cut off (taking back what it had put on
            # record as handed over) gets as long again and no longer:
            # shutting down has a budget. What is left on record as on its
            # way is put right by the next process.
            await asyncio.wait({task}, timeout=WAKES_CLOSE_WAIT_SECONDS)
            if not task.done():
                # Nobody waits for it from here on, and the ledger is
                # closed under it: said once, here, and what it fails with
                # then is looked at so that it is not reported as an error
                # nobody took.
                logger.warning(
                    "the last round of wake requests did not end in time as the host shuts down; "
                    "what it leaves on record as on its way is put right by the next process"
                )
                task.add_done_callback(lambda done: done.cancelled() or done.exception())
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception:  # noqa: BLE001 - the round said what failed; closing goes on
            logger.debug("the last round of wake requests failed", exc_info=True)

