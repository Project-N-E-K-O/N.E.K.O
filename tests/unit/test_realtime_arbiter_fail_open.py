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
"""The arbiter's escalation policy: fail-closed by default, fail-open opt-in.

When a response lifecycle cannot reach a terminal state, the arbiter escalates
through one chokepoint. Historically that always tore the realtime WebSocket
down — correct when the bookkeeping is untrustworthy, but a provider-side
event-timing quirk in the field would present as repeated
disconnect-and-rebuild with no way to reach the affected users except a hotfix
build. Issue #2583 adds an opt-in escape hatch: drop only the stuck turn.

Two halves are pinned here, and the second is the one that earns its keep:

1. The DEFAULT is unchanged. Nothing about this PR may alter what today's
   users get, so the fail-closed assertions are repeated against an explicitly
   default-constructed arbiter (``test_realtime_arbiter_native_path.py`` covers
   the same ground through the real client).
2. Fail-open is genuinely narrower than a connection loss. It must drop the
   stuck turn AND keep four things intact: the connection's usability, an
   external-ASR dispatch pause, the queued work behind the stuck turn, and the
   connection generation guarding in-flight cancel sends. Getting any of those
   wrong turns a benign escape hatch into a worse bug than the one it dodges —
   silently dead dispatch, or a proactive turn stealing the user's voice turn.
"""

import asyncio
import logging

import pytest

from main_logic.omni_realtime_client._response_arbiter import RealtimeResponseArbiter
from main_logic.omni_realtime_client._shared import (
    response_arbiter_fail_open_enabled,
)

ARBITER_LOGGER = "main_logic.omni_realtime_client._response_arbiter"
FAIL_OPEN_ENV_VAR = "NEKO_REALTIME_ARBITER_FAIL_OPEN"


async def _settle(times: int = 50) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


class _Harness:
    """Records what reached the wire and whether the transport was aborted."""

    def __init__(self, *, fail_open: bool) -> None:
        self.sent: list[dict] = []
        self.aborted: list[str] = []
        self.arbiter = RealtimeResponseArbiter(
            self._send,
            abort_transport=self._abort,
            fail_open=fail_open,
        )

    async def _send(self, event: dict) -> None:
        self.sent.append(event)

    async def _abort(self, reason: str) -> None:
        self.aborted.append(reason)

    @property
    def types(self) -> list[str]:
        return [event.get("type") for event in self.sent]

    @property
    def dispatch_count(self) -> int:
        """How many response.create events reached the wire.

        Asserted instead of the whole event list: ``cancel_current`` also puts
        a legitimate ``response.cancel`` on the wire, and a test about
        dispatch permission should not be coupled to that.
        """

        return self.types.count("response.create")

    async def own_a_live_response(self, response_id: str = "resp-1"):
        """Dispatch one request and let it become the live lane owner."""

        ticket = await self.arbiter.enqueue(source="native")
        await asyncio.wait_for(ticket.sent, timeout=1)
        self.arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": response_id}}
        )
        return ticket


@pytest.fixture
async def make_harness():
    """Build harnesses and guarantee their worker tasks are reaped.

    Under fail-open ``_connection_available`` deliberately stays True, so the
    queue consumer keeps running after the assertions are done. Left alone it
    outlives the test and pollutes whatever runs next in the same session; the
    explicit ``shutdown`` here is what keeps this file self-contained.
    """

    built: list[_Harness] = []

    def _factory(*, fail_open: bool) -> _Harness:
        harness = _Harness(fail_open=fail_open)
        built.append(harness)
        return harness

    yield _factory

    for harness in built:
        await harness.arbiter.shutdown("test teardown")


async def _stick_a_cancel(harness: _Harness) -> BaseException | None:
    """Cancel the live response whose terminal never arrives; return the raise.

    This drives the ``cancel_current`` escalation site, which is the one a
    barge-in reaches in production.
    """

    try:
        await harness.arbiter.cancel_current(timeout=0.05)
    except BaseException as exc:  # noqa: BLE001 - the raise itself is asserted
        return exc
    return None


# ---------------------------------------------------------------------------
# The environment-variable reader. Off unless explicitly turned on: a typo or
# an empty value must not silently change the shipped policy.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fail_open_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv(FAIL_OPEN_ENV_VAR, raising=False)
    assert response_arbiter_fail_open_enabled() is False

    for truthy in ("1", "true", "TRUE", " yes ", "on", "On"):
        monkeypatch.setenv(FAIL_OPEN_ENV_VAR, truthy)
        assert response_arbiter_fail_open_enabled() is True, truthy

    # Anything else keeps the shipped default, including the words a user might
    # reasonably type to turn it OFF.
    for falsy in ("", "   ", "0", "false", "no", "off", "maybe", "2"):
        monkeypatch.setenv(FAIL_OPEN_ENV_VAR, falsy)
        assert response_arbiter_fail_open_enabled() is False, falsy


@pytest.mark.unit
def test_the_arbiter_defaults_to_fail_closed_without_being_told():
    # The constructor default is the shipped policy; a construction site that
    # forgets to pass the flag must not accidentally opt users in.
    arbiter = RealtimeResponseArbiter(lambda event: asyncio.sleep(0))
    assert arbiter._fail_open is False


@pytest.mark.unit
def test_client_construction_honours_the_environment_switch(monkeypatch):
    # The whole escape hatch is worthless if the wiring from variable to
    # arbiter is missing: the reader could be perfect and every policy branch
    # correct while real clients still get the default. Build the actual client
    # both ways and read the policy off the arbiter it constructed.
    from main_logic.omni_realtime_client import OmniRealtimeClient

    def _build() -> OmniRealtimeClient:
        return OmniRealtimeClient(
            "wss://example.invalid/realtime",
            "test-key",
            model="free-model",
            api_type="free",
        )

    monkeypatch.delenv(FAIL_OPEN_ENV_VAR, raising=False)
    assert _build()._response_arbiter._fail_open is False

    monkeypatch.setenv(FAIL_OPEN_ENV_VAR, "1")
    assert _build()._response_arbiter._fail_open is True, (
        "the construction site must pass the environment policy through, or "
        "the escape hatch cannot be reached by any real session"
    )


# ---------------------------------------------------------------------------
# Half 1: the default policy is untouched.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_default_policy_still_tears_the_transport_down(caplog, make_harness):
    harness = make_harness(fail_open=False)
    await harness.own_a_live_response()

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        raised = await _stick_a_cancel(harness)

    assert isinstance(raised, asyncio.TimeoutError)
    assert harness.aborted == ["response cancellation terminal event timed out"]
    messages = [record.getMessage() for record in caplog.records]
    # The documented grep target for attributing a field disconnect must keep
    # its exact wording (issue #2561 / PR #2577).
    assert any("response arbiter failing closed" in message for message in messages)
    assert not any("failing open" in message for message in messages)

    # And the connection is latched shut: later work fails fast rather than
    # queueing against a socket that is gone.
    dead = await harness.arbiter.enqueue(source="native-after")
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(dead.sent, timeout=1)


# ---------------------------------------------------------------------------
# Half 2: fail-open drops the turn and nothing else.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fail_open_keeps_the_transport_and_reopens_the_lane(caplog, make_harness):
    harness = make_harness(fail_open=True)
    stuck = await harness.own_a_live_response()

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        raised = await _stick_a_cancel(harness)

    # The caller still learns the cancellation did not settle...
    assert isinstance(raised, asyncio.TimeoutError)
    # ...but nobody hung up.
    assert harness.aborted == []
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "response arbiter failing open, transport kept" in message
        for message in messages
    ), "an escape-hatch escalation must still be attributable in the log"
    assert not any("failing closed" in message for message in messages), (
        "fail-open must not emit the disconnect grep target — that string is "
        "what tells an operator the arbiter hung up on the user"
    )

    # The stuck turn's caller is not left waiting forever.
    await _settle()
    assert stuck.done.done(), "the stuck ticket must be terminated, not orphaned"
    with pytest.raises(Exception):
        stuck.done.result()

    # The lane is usable again on the same connection.
    assert harness.arbiter.is_busy is False
    revived = await harness.arbiter.enqueue(source="native-after")
    await asyncio.wait_for(revived.sent, timeout=1)
    assert harness.types[-1] == "response.create"
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-2"}}
    )
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-2"}}
    )
    await asyncio.wait_for(revived.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fail_open_keeps_queued_work_instead_of_failing_it(make_harness):
    # A connection loss fails the whole queue because nothing can be sent. Here
    # the connection is fine, so work queued behind the stuck turn is still
    # viable and must simply dispatch once the lane reopens. Failing it would
    # silently drop a user's next turn.
    harness = make_harness(fail_open=True)
    await harness.own_a_live_response()
    queued = await harness.arbiter.enqueue(source="native-queued")
    await _settle()
    assert harness.dispatch_count == 1, "queued work waits behind the owner"

    raised = await _stick_a_cancel(harness)
    assert isinstance(raised, asyncio.TimeoutError)
    await _settle()

    assert not queued.sent.cancelled()
    assert queued.sent.done() and queued.sent.exception() is None, (
        "queued work must survive a fail-open escalation and dispatch"
    )
    assert harness.dispatch_count == 2
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-q"}}
    )
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-q"}}
    )
    await asyncio.wait_for(queued.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fail_open_does_not_release_an_external_turn_pause(make_harness):
    # pause_dispatch() is how an external-ASR turn holds queued proactive work
    # back until the user's own text is in. _mark_connection_lost force-sets
    # the dispatch gate (safe when the socket is dead, since nothing can be
    # sent anyway); on a live connection the same move would let proactive
    # chat win the race against the user's voice turn.
    harness = make_harness(fail_open=True)
    await harness.own_a_live_response()
    harness.arbiter.pause_dispatch()
    proactive = await harness.arbiter.enqueue(source="proactive", priority=20)

    raised = await _stick_a_cancel(harness)
    assert isinstance(raised, asyncio.TimeoutError)
    await _settle()

    assert harness.dispatch_count == 1, (
        "the paused proactive request must NOT dispatch: the user's external "
        "turn still owns dispatch permission"
    )
    assert not proactive.sent.done()

    # Releasing the pause the normal way still works — the gate was untouched,
    # not broken.
    harness.arbiter.resume_dispatch()
    await asyncio.wait_for(proactive.sent, timeout=1)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-p"}}
    )
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-p"}}
    )
    await asyncio.wait_for(proactive.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fail_open_does_not_bump_the_connection_generation(make_harness):
    # The generation counter exists so a cancel-send that outlives its
    # connection cannot fire into the replacement. Fail-open has no
    # replacement connection, so bumping it would cancel sends that are still
    # correctly aimed at the live one.
    harness = make_harness(fail_open=True)
    await harness.own_a_live_response()
    generation_before = harness.arbiter._connection_generation

    raised = await _stick_a_cancel(harness)
    assert isinstance(raised, asyncio.TimeoutError)

    assert harness.arbiter._connection_generation == generation_before
    assert harness.arbiter._connection_available is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fail_open_clears_the_bookkeeping_it_gave_up_on(make_harness):
    # The escalation says "I no longer expect this response's terminal". If the
    # remembered server response ids survived, _release_lane_if_clear would
    # keep the lane shut waiting for exactly that terminal — the wedge the
    # caller escalated about, now without even a disconnect to end it.
    harness = make_harness(fail_open=True)
    await harness.own_a_live_response("resp-live")
    # A second, server-initiated response is also being tracked.
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "srv-1"}}
    )
    assert harness.arbiter._server_response_ids

    raised = await _stick_a_cancel(harness)
    assert isinstance(raised, asyncio.TimeoutError)
    await _settle()

    # Pin the policy first. Clearing this bookkeeping is something BOTH
    # policies do (fail-closed reaches it through _mark_connection_lost), so
    # without these two lines the test would stay green against a switch
    # hardwired to fail-closed — asserting less than its own name claims.
    assert harness.aborted == []
    assert harness.arbiter._connection_available is True

    assert harness.arbiter._server_response_ids == {}
    assert harness.arbiter._server_response_active is False
    assert harness.arbiter._server_vad_response_pending is False
    assert harness.arbiter._server_vad_pending_handle is None
    assert harness.arbiter.is_busy is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fail_open_survives_a_stuck_idle_wait_and_serves_the_next_turn(make_harness):
    # The idle-wait site is the most valuable fail-open case: a queued request
    # waited out its whole allowance for a lane held by bookkeeping that never
    # resolved. Under the default that kills the connection; here it costs one
    # turn.
    harness = make_harness(fail_open=True)
    # A live server response nobody owns holds the lane shut.
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "srv-wedged"}}
    )

    starved = await harness.arbiter.enqueue(
        source="native-starved",
        response_done_timeout=0.05,
    )
    await asyncio.sleep(0.2)
    await _settle()

    assert harness.aborted == [], "a wedged lane must not cost the connection"
    assert starved.sent.done() and starved.sent.exception() is not None, (
        "the starved request itself is the turn that gets dropped"
    )

    # The next request goes through on the same connection.
    revived = await harness.arbiter.enqueue(source="native-after")
    await asyncio.wait_for(revived.sent, timeout=1)
    assert harness.types == ["response.create"]
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-2"}}
    )
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-2"}}
    )
    await asyncio.wait_for(revived.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_default_policy_kills_the_connection_on_the_same_stuck_idle_wait(make_harness):
    # The dual of the test above, so the pair proves the policy switch is what
    # changes the outcome rather than the scenario being unreachable.
    harness = make_harness(fail_open=False)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "srv-wedged"}}
    )

    starved = await harness.arbiter.enqueue(
        source="native-starved",
        response_done_timeout=0.05,
    )
    await asyncio.sleep(0.2)
    await _settle()

    assert harness.aborted == ["realtime response idle wait timed out"]
    assert starved.sent.done() and starved.sent.exception() is not None
    dead = await harness.arbiter.enqueue(source="native-after")
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(dead.sent, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_healthy_turn_escalates_under_neither_policy(caplog, make_harness):
    # Neither policy may fire on a turn that simply works. Parameterised over
    # both so a future change to one branch cannot quietly start escalating.
    for fail_open in (False, True):
        harness = make_harness(fail_open=fail_open)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
            ticket = await harness.own_a_live_response()
            harness.arbiter.notify_response_terminal(
                {"type": "response.done", "response": {"id": "resp-1"}}
            )
            await asyncio.wait_for(ticket.done, timeout=1)
            await harness.arbiter.wait_until_idle(timeout=1)
            await _settle()

        assert harness.aborted == [], fail_open
        assert harness.types == ["response.create"], fail_open
        assert not any(
            "failing closed" in record.getMessage()
            or "failing open" in record.getMessage()
            for record in caplog.records
        ), fail_open
