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

"""Sending the reports stored in the ledger's outbox.

A report for the main server is written to the ``outbox`` table first and
sent afterwards (design section 6.6). ``OutboxSender`` does the sending: it
hands each unsent row to a ``send`` callable, marks the row sent when that
returns True, and otherwise tries again later, waiting twice as long after
every failure up to ``OUTBOX_RETRY_MAX_SECONDS``. A report is therefore never
lost because the main server could not be reached at that moment.

Kinds of rows the host writes:

* ``task_result`` - how a commission ended; the fields are those of the agent
  server's existing ``task_result`` event.
* ``question``    - a commission waits for the user to answer or approve.
* ``correction``  - the second round overturned the receipt she was given.
* ``receipt``     - a first round that took longer than its budget concluded.

Without a ``send`` callable nothing is sent and rows stay in the table; the
callable is attached when the host is connected to the main dialogue.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from nekoclaw.host.ledger import Ledger, OutboxItem

logger = logging.getLogger(__name__)

# Returns True when the main server took the report.
SendReport = Callable[[OutboxItem], Awaitable[bool]]

# Far beyond any sensible maximum wait, and still a finite float.
_MAX_EXPONENT = 60


def retry_delay(attempts: int) -> float:
    """Return how long to wait after the given number of failed attempts."""
    from config.nekoclaw_settings import OUTBOX_RETRY_BASE_SECONDS, OUTBOX_RETRY_MAX_SECONDS

    # The exponent is capped before multiplying: 2 ** 1024 no longer fits in a
    # float, and a report can fail that often over a few days.
    exponent = min(max(attempts - 1, 0), _MAX_EXPONENT)
    return min(OUTBOX_RETRY_BASE_SECONDS * (2**exponent), OUTBOX_RETRY_MAX_SECONDS)


class OutboxSender:
    """Delivers outbox rows through ``send``, retrying the ones that fail."""

    def __init__(
        self,
        ledger: Ledger,
        send: SendReport | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ledger = ledger
        self._send = send
        self._clock = clock
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    def attach(self, send: SendReport | None) -> None:
        """Set or remove the callable that delivers reports."""
        self._send = send
        if send is not None:
            self.kick()

    def kick(self) -> None:
        """Note that there may be something to send. Starts the loop on first use."""
        if self._closed or self._send is None:
            return
        self._wake.set()
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run())

    async def flush(self) -> int:
        """Try every row that is due now, once. Returns how many were delivered."""
        send = self._send
        if send is None:
            return 0
        delivered = 0
        for item in await self._ledger.outbox_pending(due_by=self._clock()):
            if item.kind == "question" and item.commission_id:
                if not await self._ledger.outbox_unsent(item.id):
                    # Removed after this batch was read: the commission
                    # stopped waiting for it, even if it waits for another
                    # question by now.
                    continue
                if not await self._still_waiting(item.commission_id):
                    # It no longer waits, so the question is out of date. Its
                    # row is dropped here: it may have been stored after the
                    # commission stopped waiting, when nothing removed it.
                    await self._ledger.outbox_drop(item.id)
                    continue
            try:
                accepted = bool(await send(item))
                error = "" if accepted else "not accepted"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a failed send is retried later
                accepted = False
                error = f"{type(exc).__name__}: {exc}"
            if accepted:
                await self._ledger.outbox_mark_sent(item.id)
                delivered += 1
            else:
                attempts = item.attempts + 1
                await self._ledger.outbox_mark_failed(item.id, error, self._clock() + retry_delay(attempts))
                logger.info("report %s not delivered (attempt %d): %s", item.id, attempts, error)
        return delivered

    async def _still_waiting(self, commission_id: str) -> bool:
        commission = await self._ledger.get(commission_id)
        return commission is not None and commission.status == "waiting_user"

    async def _run(self) -> None:
        failures = 0
        while not self._closed and self._send is not None:
            self._wake.clear()
            try:
                await self.flush()
                # Over every unsent row, not only one batch of them.
                due = await self._ledger.outbox_next_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive a bad round
                # The rows could not be read or updated; that says nothing about
                # whether any are left, so try again after a growing pause.
                failures += 1
                logger.exception("outbox round failed (%d in a row)", failures)
                pause = retry_delay(failures)
            else:
                failures = 0
                if due is None:
                    if self._wake.is_set():
                        # A row arrived while this round was reading.
                        continue
                    # Nothing left; the next kick starts the loop again.
                    return
                pause = max(0.05, due - self._clock())
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
