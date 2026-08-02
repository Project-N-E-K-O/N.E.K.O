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
"""Escalation is scoped to the lifecycle its caller observed.

The contract, stated so it can be falsified:

    An escalation acts on the request whose failure its caller watched, at
    most once. If that request is no longer the one the arbiter is running,
    or has already been escalated over, the escalation does nothing — no
    transport teardown, no lane release, no ticket failed.

The "at most once" half is not decoration: several callers can be waiting on
the same stuck request and their timeouts fire independently. Under today's
teardown a repeat is merely noise, but every effect an escalation grows from
here — releasing a lane, finalizing a host turn — is one that must not happen
twice to the same turn.

Every escalation site in the arbiter is reached by waiting on one specific
request's timeout. By the time that timeout fires the arbiter may have moved
on: the stuck lifecycle finished, the worker picked up the next queued item,
and ``_current``/``_response_owner`` now name a healthy turn. Escalating on
"whatever is current" then tears down that healthy turn instead — which is
how two concurrent cancellers cost an extra request each time.

This was the structural gap behind several findings on PR #2592 (see
#2583 for the full catalogue): patching individual state conditions could not
fix it, because the state is shared and the request is the real scope.
"""

import asyncio
import logging

import pytest

from main_logic.omni_realtime_client._response_arbiter import RealtimeResponseArbiter

ARBITER_LOGGER = "main_logic.omni_realtime_client._response_arbiter"


async def _settle(times: int = 50) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


class _Harness:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.aborted: list[str] = []
        self.arbiter = RealtimeResponseArbiter(self._send, abort_transport=self._abort)

    async def _send(self, event: dict) -> None:
        self.sent.append(event)

    async def _abort(self, reason: str) -> None:
        self.aborted.append(reason)

    @property
    def dispatch_count(self) -> int:
        return [e.get("type") for e in self.sent].count("response.create")


@pytest.fixture
async def harness():
    built = _Harness()
    yield built
    await built.arbiter.shutdown("test teardown")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_escalation_for_a_finished_request_does_nothing(harness, caplog):
    # The core case. A caller watches request A, A's cancellation times out —
    # but by then A has completed and the worker is running B. The late
    # escalation must not touch B.
    first = await harness.arbiter.enqueue(source="turn-a")
    await asyncio.wait_for(first.sent, timeout=1)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-a"}}
    )
    observed = harness.arbiter._current

    # A finishes and B takes the lane.
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-a"}}
    )
    await asyncio.wait_for(first.done, timeout=1)
    second = await harness.arbiter.enqueue(source="turn-b")
    await asyncio.wait_for(second.sent, timeout=1)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-b"}}
    )
    assert harness.arbiter._response_owner is not None
    assert harness.arbiter._response_owner is not observed

    with caplog.at_level(logging.DEBUG, logger=ARBITER_LOGGER):
        await harness.arbiter._escalate("late escalation", observed=observed)

    assert harness.aborted == [], (
        "an escalation naming a finished request must not tear down the "
        "transport the next turn is using"
    )
    assert harness.arbiter._connection_available is True
    assert harness.arbiter._response_owner is not None, (
        "the healthy turn must keep its ownership"
    )
    assert not second.done.done(), "and its ticket must not be failed"
    assert any(
        "no longer current" in record.getMessage() for record in caplog.records
    ), "a skipped escalation must say so, or it looks like nothing happened"

    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-b"}}
    )
    await asyncio.wait_for(second.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_escalation_for_the_running_request_still_acts(harness):
    # The dual. Without it, "never escalate" would pass the test above.
    ticket = await harness.arbiter.enqueue(source="turn-a")
    await asyncio.wait_for(ticket.sent, timeout=1)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-a"}}
    )
    observed = harness.arbiter._response_owner
    assert observed is not None

    await harness.arbiter._escalate("real escalation", observed=observed)

    assert harness.aborted == ["real escalation"]
    assert harness.arbiter._connection_available is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_unnamed_escalation_still_acts(harness):
    # cancel_current's no-current branch escalates over an unowned server
    # response and has nothing to name. That path must keep working.
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "srv-1"}}
    )

    await harness.arbiter._escalate("unowned server response stuck")

    assert harness.aborted == ["unowned server response stuck"]
    assert harness.arbiter._connection_available is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_escalation_whose_terminal_just_arrived_does_nothing(harness, caplog):
    # The race a timeout cannot avoid: notify_response_terminal clears
    # _response_owner synchronously, but the worker has not run its finally,
    # so _current still names a request that is in fact finished. Identity
    # alone would call that stuck.
    ticket = await harness.arbiter.enqueue(source="turn-a")
    await asyncio.wait_for(ticket.sent, timeout=1)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-a"}}
    )
    observed = harness.arbiter._current
    assert observed is not None

    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-a"}}
    )
    # Deliberately no settle: the worker has not unwound, so _current still
    # points at the finished request — exactly the window being tested.
    assert harness.arbiter._current is observed
    assert observed.terminal is not None and observed.terminal.done()

    with caplog.at_level(logging.DEBUG, logger=ARBITER_LOGGER):
        await harness.arbiter._escalate("racing escalation", observed=observed)

    assert harness.aborted == [], (
        "a request whose terminal just landed is finished, not stuck"
    )
    assert harness.arbiter._connection_available is True
    assert any(
        "its terminal arrived" in record.getMessage() for record in caplog.records
    )
    await asyncio.wait_for(ticket.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_escalation_after_its_own_cancel_still_acts(harness):
    # The dual, and the reason the check excludes cancelled futures: the
    # escalation paths cancel the terminal themselves immediately before
    # calling in. Treating that as an arrival would suppress every real
    # escalation — which is what a first attempt at this guard did.
    ticket = await harness.arbiter.enqueue(
        source="turn-a", response_started_timeout=0.05, cancel_timeout=0.05
    )
    await asyncio.wait_for(ticket.sent, timeout=1)

    with pytest.raises(Exception):
        await asyncio.wait_for(ticket.done, timeout=2)
    await _settle()

    assert harness.aborted, (
        "a terminal cancelled by the escalation path is not an arrival"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_two_concurrent_cancellers_cost_only_the_stuck_turn(harness):
    # The user-visible shape of the same defect, driven through the public
    # API instead of the private one: two callers cancel the same stuck
    # request. The first escalation is legitimate. The second must not go on
    # to tear down whatever the arbiter picked up next.
    stuck = await harness.arbiter.enqueue(source="stuck")
    await asyncio.wait_for(stuck.sent, timeout=1)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-stuck"}}
    )

    first = asyncio.create_task(harness.arbiter.cancel_current(timeout=0.05))
    second = asyncio.create_task(harness.arbiter.cancel_current(timeout=0.05))
    results = await asyncio.gather(first, second, return_exceptions=True)

    assert all(isinstance(r, asyncio.TimeoutError) for r in results)
    # Both watched the same request, so at most one teardown is warranted and
    # the second must not add another.
    assert len(harness.aborted) == 1, (
        f"a second canceller must not escalate again: {harness.aborted}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_terminal_that_failed_is_not_mistaken_for_one_that_arrived(harness):
    # The guard above says "its terminal actually arrived". But notify_error
    # finishes that SAME future with set_exception when the provider reports a
    # correlated error — during the cancel grace period that is the ordinary
    # way a stuck lifecycle ends — and an error is not an arrival. Treating it
    # as one skipped the recovery outright: no teardown, no release, and the
    # lane left owned until some later timeout noticed.
    ticket = await harness.arbiter.enqueue(source="turn-a")
    await asyncio.wait_for(ticket.sent, timeout=1)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-a"}}
    )
    observed = harness.arbiter._response_owner
    assert observed is not None

    harness.arbiter.notify_error(None, "response_already_active")
    assert observed.terminal is not None and observed.terminal.done()
    assert observed.terminal.exception() is not None, (
        "notify_error finishes the terminal future with an exception"
    )

    await harness.arbiter._escalate("terminal never arrived", observed=observed)

    assert harness.aborted == ["terminal never arrived"], (
        "a lifecycle whose terminal FAILED is stuck, not finished"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_terminal_that_arrived_with_a_result_still_suppresses_escalation(
    harness,
):
    # The dual, and the case the guard exists for: notify_response_terminal
    # completes the future with a RESULT, and escalating then would end an
    # already-completed turn a second time.
    ticket = await harness.arbiter.enqueue(source="turn-a")
    await asyncio.wait_for(ticket.sent, timeout=1)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-a"}}
    )
    observed = harness.arbiter._current
    assert observed is not None

    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-a"}}
    )
    assert observed.terminal is not None and observed.terminal.done()
    assert observed.terminal.exception() is None

    await harness.arbiter._escalate("racing escalation", observed=observed)

    assert harness.aborted == []
    assert harness.arbiter._connection_available is True
    await asyncio.wait_for(ticket.done, timeout=1)
