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


async def _stick_a_cancel(harness: _Harness) -> Exception | None:
    """Cancel the live response whose terminal never arrives; return the raise.

    This drives the ``cancel_current`` escalation site, which is the one a
    barge-in reaches in production. Catching ``Exception`` rather than
    ``BaseException`` is deliberate and sufficient: the expected raise is
    ``asyncio.TimeoutError``, which is the builtin ``TimeoutError`` on 3.11 and
    therefore an ``Exception``. A ``CancelledError`` here would mean the test
    itself was cancelled, which should propagate rather than be reported as
    the escalation's result.
    """

    try:
        await harness.arbiter.cancel_current(timeout=0.05)
    except Exception as exc:  # noqa: BLE001 - the raise itself is asserted
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
async def test_a_late_created_after_fail_open_closes_the_lane_on_purpose(make_harness):
    # Greptile P1 on PR #2592: after fail-open drops the abandoned ids, a
    # delayed response.created is recorded as a server-initiated response and
    # closes the lane again.
    #
    # That is deliberate, and the alternative is the defect the arbiter exists
    # to prevent: the provider has just said a response IS live, so dispatching
    # the next response.create would collide with it (response_already_active).
    # Abandoning our own ticket never made the server's response go away.
    #
    # What must hold is that it is a PAUSE, not a wedge — its terminal reopens
    # the lane normally. The no-terminal case is the test below.
    harness = make_harness(fail_open=True)
    await harness.own_a_live_response("resp-abandoned")

    raised = await _stick_a_cancel(harness)
    assert isinstance(raised, asyncio.TimeoutError)
    await _settle()
    assert harness.arbiter.is_busy is False

    # The abandoned response finally announces itself, THEN the next turn is
    # submitted — that ordering is the one Greptile's scenario is about.
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-abandoned"}}
    )
    await _settle()
    assert harness.arbiter.is_busy is True, (
        "a live provider response must hold the lane even though we gave up "
        "on our own ticket for it"
    )

    dispatched_before = harness.dispatch_count
    queued = await harness.arbiter.enqueue(source="native-next")
    await _settle()
    assert harness.dispatch_count == dispatched_before, (
        "dispatching under a live provider response is exactly the "
        "response_already_active collision the arbiter exists to prevent"
    )

    # Its terminal releases the lane through the ordinary path — no second
    # escalation, no teardown.
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-abandoned"}}
    )
    await _settle()
    assert harness.dispatch_count == dispatched_before + 1
    assert harness.aborted == []
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-next"}}
    )
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-next"}}
    )
    await asyncio.wait_for(queued.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_late_created_with_no_terminal_reopens_on_the_staleness_bound(
    make_harness, caplog
):
    # The other half of the Greptile P1: if that late response's terminal never
    # arrives either, recovery must NOT require a second escalation. Recording
    # the id arms the staleness timer (_remember_server_response_id ->
    # _arm_stale_release_timer), and that timer reopens the lane on its own.
    harness = make_harness(fail_open=True)
    await harness.own_a_live_response("resp-abandoned")

    raised = await _stick_a_cancel(harness)
    assert isinstance(raised, asyncio.TimeoutError)
    await _settle()

    # Shrink the bound BEFORE the id is recorded: the timer's deadline is
    # computed from the value in force at that moment. Deliberately nothing is
    # enqueued while the timer runs — ``enqueue`` ratchets
    # ``_server_response_max_age`` up to the ticket's own
    # ``response_done_timeout`` (60s by default), which would push the deadline
    # back out of unit-test range. That ratchet is by design and is what makes
    # the staleness release beat a later ticket's own idle-wait timeout: the
    # timer is armed from the created timestamp, the ticket's deadline only
    # starts once it reaches the idle wait, so the release always lands first.
    harness.arbiter._server_response_max_age = 0.05
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        harness.arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-abandoned"}}
        )
        await _settle()
        assert harness.arbiter.is_busy is True
        assert harness.arbiter._stale_release_handle is not None, (
            "recording a server response id must arm the staleness timer — "
            "that timer IS the recovery path when its terminal never comes"
        )

        # No terminal ever comes for the late response.
        await asyncio.sleep(0.2)
        await _settle()

    assert harness.arbiter._server_response_ids == {}
    assert harness.arbiter.is_busy is False, (
        "the staleness bound must reopen the lane by itself"
    )
    assert harness.aborted == [], "recovery must not cost the connection"
    assert not any(
        "failing open" in record.getMessage()
        or "failing closed" in record.getMessage()
        for record in caplog.records
    ), "recovery must not need a second escalation"

    # And the reopened lane really serves the next turn.
    revived = await harness.arbiter.enqueue(source="native-next")
    await asyncio.wait_for(revived.sent, timeout=1)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-next"}}
    )
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-next"}}
    )
    await asyncio.wait_for(revived.done, timeout=1)


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


# ---------------------------------------------------------------------------
# The host's own response state has to be released too (Codex P2 on PR #2592).
#
# ``_is_responding`` lives on the client, is set on response.created, and is
# cleared only by that response's own response.done or an interruption. The
# turn fail-open abandons is precisely the one whose terminal never comes — so
# without a notification the client reports busy forever and the proactive
# gates hold back exactly the work the kept-alive connection was for.
#
# These use a REAL client on purpose: _Harness has no _is_responding at all,
# which is why every other case in this file is blind to the defect.
# ---------------------------------------------------------------------------


class _ClientRig:
    """A real OmniRealtimeClient whose arbiter can be driven to escalate."""

    def __init__(self) -> None:
        from main_logic.omni_realtime_client import OmniRealtimeClient

        self.sent: list[dict] = []
        self.client = OmniRealtimeClient(
            "wss://example.invalid/realtime",
            "test-key",
            model="free-model",
            api_type="free",
        )

        async def _send(event: dict) -> None:
            self.sent.append(event)

        # Replace the transport write, not the arbiter: the point is to drive
        # the production arbiter the client built for itself. A non-suspending
        # stub is required — a stalling one would trip the worker-send guard
        # above and make the policy stand down instead of releasing.
        self.client._response_arbiter._send_event = _send

    @property
    def arbiter(self):
        return self.client._response_arbiter

    async def drive_to_escalation(self) -> Exception | None:
        ticket = await self.arbiter.enqueue(source="native")
        await asyncio.wait_for(ticket.sent, timeout=1)
        # Go through the transport's own handler so _is_responding is set the
        # way production sets it, not by assignment.
        self.client._response_arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-1"}}
        )
        self.client._current_response_id = "resp-1"
        self.client._is_responding = True
        try:
            await self.arbiter.cancel_current(timeout=0.05)
        except Exception as exc:  # noqa: BLE001 - the raise itself is asserted
            return exc
        return None


@pytest.fixture
async def client_rig(monkeypatch):
    monkeypatch.setenv(FAIL_OPEN_ENV_VAR, "1")
    rig = _ClientRig()
    yield rig
    await rig.arbiter.shutdown("test teardown")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fail_open_release_clears_the_clients_response_state(client_rig):
    assert client_rig.arbiter._fail_open is True
    raised = await client_rig.drive_to_escalation()
    assert isinstance(raised, asyncio.TimeoutError)
    await _settle()

    assert client_rig.arbiter.is_busy is False
    assert client_rig.client._is_responding is False, (
        "the host must stop reporting a response in progress, or the "
        "connection fail-open kept alive is useless to proactive chat"
    )
    assert client_rig.client.is_active_response() is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_lazily_built_arbiter_gets_the_same_notification(client_rig):
    # _responses.py builds an arbiter on demand when one is missing. That is a
    # second, independent injection point; wiring only the eager one leaves the
    # lazy path silently unnotified.
    del client_rig.client._response_arbiter
    rebuilt = client_rig.client._ensure_response_arbiter()

    async def _send(event: dict) -> None:
        client_rig.sent.append(event)

    rebuilt._send_event = _send
    assert rebuilt._on_stuck_release is not None, (
        "the lazy construction point must inject the host notification too"
    )

    raised = await client_rig.drive_to_escalation()
    assert isinstance(raised, asyncio.TimeoutError)
    await _settle()
    assert client_rig.client._is_responding is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_release_clears_an_abandoned_turns_output_suppression(client_rig):
    # Codex P2 on PR #2592, and the exact dual of the case below: a turn
    # requested with skipped=True raises _skip_until_next_response, and only
    # that turn's own response.done lowers it — the terminal fail-open just
    # gave up on. Left raised, the transport suppresses the NEXT healthy
    # response's text and audio until its own done, so the hatch silently
    # costs a second turn.
    #
    # The flag is set explicitly here rather than through create_response
    # because no production caller passes skipped=True on this path today;
    # the guard is defensive, and a test that did not raise the flag first
    # would assert nothing at all.
    client_rig.client._skip_until_next_response = True

    raised = await client_rig.drive_to_escalation()
    assert isinstance(raised, asyncio.TimeoutError)
    await _settle()

    assert client_rig.client._skip_until_next_response is False, (
        "the abandoned turn's output suppression must be lifted with it"
    )
    assert client_rig.client._is_responding is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_release_notification_touches_nothing_else(client_rig):
    # Both of these would be tempting to reset here and both would be wrong:
    # _skip_until_next_response is cleared only by response.done, so setting it
    # would mute the entire next turn; _interrupted gates the AI-activity
    # timestamps that proactive delivery uses to avoid talking over audio the
    # provider is still streaming.
    client_rig.client._skip_until_next_response = False
    client_rig.client._interrupted = False

    raised = await client_rig.drive_to_escalation()
    assert isinstance(raised, asyncio.TimeoutError)
    await _settle()

    assert client_rig.client._is_responding is False
    assert client_rig.client._skip_until_next_response is False, (
        "setting this would silence the next turn's text and audio entirely"
    )
    assert client_rig.client._interrupted is False, (
        "setting this would suppress the AI-activity timestamps proactive "
        "delivery depends on"
    )
    assert client_rig.client._current_response_id == "resp-1", (
        "response identity is kept for terminal attribution, same as "
        "handle_interruption keeps it"
    )


# ---------------------------------------------------------------------------
# Fail-open declines to apply while the queue consumer is parked inside a
# transport write (Codex P2 on PR #2592). Fail-open's whole premise is "the
# transport is still usable"; a write that never returns IS that premise being
# falsified. Nothing the escalation does to the arbiter's own state unwinds
# that await, and _run is the only consumer — so keeping the connection would
# wedge every later request while reporting the lane recovered. Tearing the
# transport down is what unblocks the write, so the hatch stands down.
# ---------------------------------------------------------------------------


class _StallingHarness(_Harness):
    """Harness whose response.create write parks until the test releases it."""

    def __init__(self, *, fail_open: bool) -> None:
        super().__init__(fail_open=fail_open)
        self.gate = asyncio.Event()

    async def _send(self, event: dict) -> None:
        self.sent.append(event)
        if event.get("type") == "response.create":
            await self.gate.wait()


@pytest.fixture
async def make_stalling_harness():
    built: list[_StallingHarness] = []

    def _factory(*, fail_open: bool) -> _StallingHarness:
        harness = _StallingHarness(fail_open=fail_open)
        built.append(harness)
        return harness

    yield _factory

    for harness in built:
        # Open the gate BEFORE shutdown: the worker is parked inside the write,
        # and shutdown awaits it. Reaping without releasing reproduces the very
        # hang this test is about, in the teardown.
        harness.gate.set()
        await harness.arbiter.shutdown("test teardown")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_stalled_worker_send_makes_fail_open_stand_down(
    make_stalling_harness, caplog
):
    harness = make_stalling_harness(fail_open=True)
    stuck = await harness.arbiter.enqueue(source="native")
    await _settle()
    # The worker is now parked inside the response.create write.
    assert harness.types == ["response.create"]
    assert not stuck.sent.done()
    assert harness.arbiter._worker_send_in_flight is True

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        raised = await _stick_a_cancel(harness)

    assert isinstance(raised, asyncio.TimeoutError)
    # (a) it tore the transport down after all...
    assert harness.aborted == [
        "response cancellation terminal event timed out"
    ], "fail-open must decline while the only consumer is stuck in a write"
    # (b) ...and latched the connection shut, which is what unblocks the write
    assert harness.arbiter._connection_available is False
    messages = [record.getMessage() for record in caplog.records]
    assert any("failing closed" in message for message in messages)
    assert any("worker_send_in_flight=True" in message for message in messages), (
        "the log must say WHY an opted-in session failed closed anyway, or the "
        "field cannot tell this apart from a plain fail-closed session"
    )

    # (c) later work fails fast instead of queueing behind a wedged consumer
    later = await harness.arbiter.enqueue(source="native-after")
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(later.sent, timeout=1)


class _RaisingHarness(_Harness):
    """Harness whose FIRST response.create write fails; later ones succeed.

    Only the first, so the test can go on to prove the arbiter still works
    afterwards rather than just proving the stub keeps raising.
    """

    def __init__(self, *, fail_open: bool) -> None:
        super().__init__(fail_open=fail_open)
        self.refusals = 0

    async def _send(self, event: dict) -> None:
        self.sent.append(event)
        if event.get("type") == "response.create" and self.refusals == 0:
            self.refusals += 1
            raise RuntimeError("transport write refused")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_failed_worker_send_does_not_latch_the_stand_down_flag():
    # The stand-down flag is set around the write and must come back down on
    # EVERY exit, not just the successful one. A write that raises is an
    # ordinary path (the ticket fails and the worker moves on); if the flag
    # stayed up, fail-open would be silently disabled for the rest of this
    # arbiter's life and every later escalation would tear the transport down
    # while the log still blamed a send that finished long ago.
    harness = _RaisingHarness(fail_open=True)
    try:
        failed = await harness.arbiter.enqueue(source="native-doomed")
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(failed.sent, timeout=1)
        await _settle()

        assert harness.arbiter._worker_send_in_flight is False, (
            "the flag must clear even when the write raises"
        )

        # And fail-open still applies afterwards: a fresh stuck turn releases
        # instead of tearing the transport down.
        harness.sent.clear()
        ticket = await harness.arbiter.enqueue(source="native")
        await asyncio.wait_for(ticket.sent, timeout=1)
        harness.arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-1"}}
        )
        raised = await _stick_a_cancel(harness)
        assert isinstance(raised, asyncio.TimeoutError)
        assert harness.aborted == [], (
            "a previously failed write must not permanently disable fail-open"
        )
        assert harness.arbiter._connection_available is True
    finally:
        await harness.arbiter.shutdown("test teardown")


class _CancelRefusingHarness(_Harness):
    """Harness that accepts response.create but refuses response.cancel."""

    async def _send(self, event: dict) -> None:
        self.sent.append(event)
        if event.get("type") == "response.cancel":
            raise RuntimeError("1006 abnormal close")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_refused_cancel_write_forces_the_fail_closed_path(caplog):
    # Codex P2 on PR #2592. _worker_send lowers its in-flight flag in a
    # finally, so by the time _cancel_after_timeout escalates, the "consumer
    # is mid-write" guard already reads False — even though the transport
    # refused that very write, and on the fatal branch has already dropped its
    # socket. Reopening the lane there would dispatch queued work onto a
    # connection that cannot carry it, so the escalation has to carry the
    # write's outcome with it.
    harness = _CancelRefusingHarness(fail_open=True)
    try:
        # response_started_timeout drives _cancel_after_timeout: the create
        # goes out, response.created never arrives, so the worker cancels —
        # and that cancel write is the one the transport refuses.
        ticket = await harness.arbiter.enqueue(
            source="native",
            response_started_timeout=0.05,
            cancel_timeout=0.05,
        )
        await asyncio.wait_for(ticket.sent, timeout=1)

        with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
            with pytest.raises(Exception):
                await asyncio.wait_for(ticket.done, timeout=2)
            await _settle()

        assert harness.types == ["response.create", "response.cancel"]
        assert harness.aborted, (
            "an escalation that follows a refused write must fail closed, not "
            "reopen the lane on a transport that just rejected a send"
        )
        assert harness.arbiter._connection_available is False
        messages = [record.getMessage() for record in caplog.records]
        assert any("failing closed" in message for message in messages)
        assert any(
            "transport_write_failed=True" in message for message in messages
        ), "the log must record why an opted-in session failed closed"

        later = await harness.arbiter.enqueue(source="native-after")
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(later.sent, timeout=1)
    finally:
        await harness.arbiter.shutdown("test teardown")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_terminal_timeout_with_a_healthy_cancel_still_fails_open(caplog):
    # The dual: same escalation site, same code path, but the cancel write is
    # accepted — only the terminal never arrives. That is the ordinary stuck
    # lifecycle the hatch exists for, and it must still release. Without this
    # pair, forcing fail-closed on every _cancel_after_timeout escalation
    # would look correct.
    harness = _Harness(fail_open=True)
    try:
        ticket = await harness.arbiter.enqueue(
            source="native",
            response_started_timeout=0.05,
            cancel_timeout=0.05,
        )
        await asyncio.wait_for(ticket.sent, timeout=1)

        with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
            with pytest.raises(Exception):
                await asyncio.wait_for(ticket.done, timeout=2)
            await _settle()

        assert harness.aborted == []
        assert harness.arbiter._connection_available is True
        assert any(
            "failing open" in record.getMessage() for record in caplog.records
        )

        revived = await harness.arbiter.enqueue(source="native-after")
        await asyncio.wait_for(revived.sent, timeout=1)
    finally:
        await harness.arbiter.shutdown("test teardown")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_stalled_send_outcome_is_identical_under_both_policies(
    make_stalling_harness,
):
    # The dual: having stood down, fail-open converges on exactly the default
    # terminal state. That equivalence is the argument that the stand-down can
    # never be worse than what ships today.
    outcomes = {}
    for fail_open in (False, True):
        harness = make_stalling_harness(fail_open=fail_open)
        stuck = await harness.arbiter.enqueue(source="native")
        await _settle()
        assert not stuck.sent.done()

        raised = await _stick_a_cancel(harness)
        later = await harness.arbiter.enqueue(source="native-after")
        later_error = None
        try:
            await asyncio.wait_for(later.sent, timeout=1)
        except Exception as exc:  # noqa: BLE001 - the type is the assertion
            later_error = type(exc).__name__

        outcomes[fail_open] = (
            type(raised).__name__,
            tuple(harness.aborted),
            harness.arbiter._connection_available,
            later_error,
        )

    assert outcomes[True] == outcomes[False], (
        f"stalled-send terminal state must not depend on the policy: {outcomes}"
    )


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
