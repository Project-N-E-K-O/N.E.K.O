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
"""The arbiter's escalation policy: tear down by default, keep the connection
only when that is defensible.

Contracts, written so each can be falsified:

1.  **The default never changes.** Without the environment switch the arbiter
    behaves exactly as it always has, including the
    ``response arbiter failing closed`` line that attributes a field
    disconnect (issue #2561).

2.  **Fail-open applies only when the connection can be kept.** That means
    both halves: the transport is still usable, AND the arbiter can still
    tell whose events are whose. Five things falsify it, each with a paired
    case here so "always stand down" cannot pass for correct:

    - the queue consumer is suspended inside a transport write
    - a transport write just failed
    - the abandoned response has no id to attribute its later events by
    - an announced server-VAD response has no id yet
    - an unowned response is live with no id at all

3.  **A released turn is ended, not merely dropped.** The host runs the same
    end-of-turn work its terminal event drives — because it is the same
    implementation, not a second one.

4.  **A released turn's end-of-turn work lands on that turn, or not at all.**
    The lane can reopen mid-notification — the abandoned response's own
    terminal releases it — and the host's own turn can start from outside the
    arbiter entirely. So the guarantee is identity, not ordering: the host
    takes the released turn's id and its turn epoch, and stops the moment a
    new turn has started. The release still opens the lane last, which is why
    the ordering holds in the ordinary case.

The previous attempt (withdrawn PR #2592) grew these as separate boolean
conditions patched in over seven review rounds; see #2583 for how the 20
findings collapse into five invariants once they are read together.
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
    """Records what reached the wire and whether the transport was torn down."""

    send_behaviour = "ok"

    def __init__(self, *, fail_open: bool, on_stuck_release=None) -> None:
        self.sent: list[dict] = []
        self.aborted: list[str] = []
        self.gate = asyncio.Event()
        self.refusals = 0
        self.arbiter = RealtimeResponseArbiter(
            self._send,
            abort_transport=self._abort,
            fail_open=fail_open,
            on_stuck_release=on_stuck_release,
        )

    async def _send(self, event: dict) -> None:
        self.sent.append(event)
        if self.send_behaviour == "stall" and event.get("type") == "response.create":
            await self.gate.wait()
        if self.send_behaviour == "refuse_cancel" and (
            event.get("type") == "response.cancel"
        ):
            raise RuntimeError("1006 abnormal close")

    async def _abort(self, reason: str) -> None:
        self.aborted.append(reason)

    @property
    def dispatch_count(self) -> int:
        return [e.get("type") for e in self.sent].count("response.create")

    async def own_a_live_response(self, response_id: str | None = "resp-1"):
        ticket = await self.arbiter.enqueue(source="native")
        await asyncio.wait_for(ticket.sent, timeout=1)
        event: dict = {"type": "response.created"}
        if response_id is not None:
            event["response"] = {"id": response_id}
        self.arbiter.notify_response_created(event)
        return ticket


class _StallingHarness(_Harness):
    send_behaviour = "stall"


class _CancelRefusingHarness(_Harness):
    send_behaviour = "refuse_cancel"


@pytest.fixture
async def make_harness():
    """Build harnesses and reap their workers.

    Under fail-open ``_connection_available`` deliberately stays True, so the
    queue consumer keeps running after the assertions are done. The gate is
    opened first because a stalled worker is parked inside the write that
    ``shutdown`` waits on.
    """

    built: list[_Harness] = []

    def _factory(cls=_Harness, *, fail_open: bool, on_stuck_release=None) -> _Harness:
        harness = cls(fail_open=fail_open, on_stuck_release=on_stuck_release)
        built.append(harness)
        return harness

    yield _factory

    for harness in built:
        harness.gate.set()
        await harness.arbiter.shutdown("test teardown")


async def _stick_a_cancel(harness: _Harness) -> Exception | None:
    """Cancel a live response whose terminal never arrives; return the raise."""

    try:
        await harness.arbiter.cancel_current(timeout=0.05)
    except Exception as exc:  # noqa: BLE001 - the raise itself is asserted
        return exc
    return None


# ---------------------------------------------------------------------------
# Contract 1: the shipped default is untouched.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_switch_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv(FAIL_OPEN_ENV_VAR, raising=False)
    assert response_arbiter_fail_open_enabled() is False

    for truthy in ("1", "true", "TRUE", " yes ", "on", "On"):
        monkeypatch.setenv(FAIL_OPEN_ENV_VAR, truthy)
        assert response_arbiter_fail_open_enabled() is True, truthy

    # Including the words someone would type to turn it OFF.
    for falsy in ("", "   ", "0", "false", "no", "off", "maybe", "2"):
        monkeypatch.setenv(FAIL_OPEN_ENV_VAR, falsy)
        assert response_arbiter_fail_open_enabled() is False, falsy


@pytest.mark.unit
def test_the_arbiter_defaults_to_tearing_down_without_being_told():
    arbiter = RealtimeResponseArbiter(lambda event: asyncio.sleep(0))
    assert arbiter._fail_open is False


@pytest.mark.unit
def test_both_construction_sites_honour_the_switch(monkeypatch):
    # The reader can be perfect and every branch correct while real clients
    # still get the default, so pin the wiring itself — through the eager
    # constructor and through the lazy one in _responses.py.
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
    eager = _build()
    assert eager._response_arbiter._fail_open is True
    assert eager._response_arbiter._on_stuck_release is not None

    del eager._response_arbiter
    lazy = eager._ensure_response_arbiter()
    assert lazy._fail_open is True
    assert lazy._on_stuck_release is not None, (
        "the lazy construction point must inject the host notification too"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_default_still_tears_the_transport_down(make_harness, caplog):
    harness = make_harness(fail_open=False)
    await harness.own_a_live_response()

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        raised = await _stick_a_cancel(harness)

    assert isinstance(raised, asyncio.TimeoutError)
    assert harness.aborted == ["response cancellation terminal event timed out"]
    messages = [record.getMessage() for record in caplog.records]
    # The documented grep target for attributing a field disconnect.
    assert any("response arbiter failing closed" in m for m in messages)
    assert not any("failing open" in m for m in messages)

    dead = await harness.arbiter.enqueue(source="native-after")
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(dead.sent, timeout=1)


# ---------------------------------------------------------------------------
# Contract 2: fail-open applies only when the connection can be kept.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fail_open_keeps_the_transport_and_reopens_the_lane(
    make_harness, caplog
):
    harness = make_harness(fail_open=True)
    stuck = await harness.own_a_live_response()

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        raised = await _stick_a_cancel(harness)

    assert isinstance(raised, asyncio.TimeoutError)
    assert harness.aborted == []
    assert harness.arbiter._connection_available is True
    messages = [record.getMessage() for record in caplog.records]
    assert any("failing open, transport kept" in m for m in messages)
    assert not any("failing closed" in m for m in messages), (
        "that string is what tells an operator the arbiter hung up"
    )

    await _settle()
    assert stuck.done.done(), "the stuck ticket must be terminated, not orphaned"
    with pytest.raises(Exception):
        stuck.done.result()

    assert harness.arbiter.is_busy is False
    revived = await harness.arbiter.enqueue(source="native-after")
    await asyncio.wait_for(revived.sent, timeout=1)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-2"}}
    )
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-2"}}
    )
    await asyncio.wait_for(revived.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fail_open_keeps_queued_work_and_an_external_turn_pause(make_harness):
    # A connection loss fails the whole queue and force-opens dispatch, because
    # nothing can be sent anyway. Here the connection is fine: queued work is
    # still viable, and an external-ASR turn's pause is still holding proactive
    # work back on purpose.
    harness = make_harness(fail_open=True)
    await harness.own_a_live_response()
    harness.arbiter.pause_dispatch()
    proactive = await harness.arbiter.enqueue(source="proactive", priority=20)

    raised = await _stick_a_cancel(harness)
    assert isinstance(raised, asyncio.TimeoutError)
    await _settle()

    assert harness.dispatch_count == 1, (
        "the paused request must not dispatch: the user's external turn still "
        "owns dispatch permission"
    )
    assert not proactive.sent.done()
    assert harness.arbiter._connection_generation == 0, (
        "there is no replacement connection to protect in-flight cancels from"
    )

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
async def test_a_stalled_consumer_write_stands_the_hatch_down(make_harness, caplog):
    # Nothing the arbiter does to its own state unwinds an await parked in the
    # transport, and _run is the only consumer — keeping the connection would
    # leave it wedged while reporting recovery. The transport's close is what
    # wakes that write.
    harness = make_harness(_StallingHarness, fail_open=True)
    stuck = await harness.arbiter.enqueue(source="native")
    await _settle()
    assert harness.dispatch_count == 1
    assert not stuck.sent.done()
    assert harness.arbiter._worker_send_in_flight is True

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        raised = await _stick_a_cancel(harness)

    assert isinstance(raised, asyncio.TimeoutError)
    assert harness.aborted, "the hatch must stand down while the consumer is stuck"
    assert harness.arbiter._connection_available is False
    assert any(
        "suspended inside a transport write" in record.getMessage()
        for record in caplog.records
    ), "the log must say WHY an opted-in session tore down anyway"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_failed_consumer_write_does_not_latch_the_stand_down():
    # The flag is set around the write and must come down on every exit. If it
    # latched, fail-open would be silently disabled for this arbiter's whole
    # life while the log blamed a send that finished long ago.
    class _RefuseOnce(_Harness):
        async def _send(self, event: dict) -> None:
            self.sent.append(event)
            if event.get("type") == "response.create" and self.refusals == 0:
                self.refusals += 1
                raise RuntimeError("transport write refused")

    harness = _RefuseOnce(fail_open=True)
    try:
        doomed = await harness.arbiter.enqueue(source="native-doomed")
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(doomed.sent, timeout=1)
        await _settle()
        assert harness.arbiter._worker_send_in_flight is False

        await harness.own_a_live_response()
        raised = await _stick_a_cancel(harness)
        assert isinstance(raised, asyncio.TimeoutError)
        assert harness.aborted == [], (
            "a write that failed earlier must not disable the hatch forever"
        )
    finally:
        await harness.arbiter.shutdown("test teardown")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_refused_cancel_write_stands_the_hatch_down(make_harness, caplog):
    # _worker_send lowers its flag in a finally, so by the time the escalation
    # runs the "mid-write" condition already reads False — even though the
    # transport refused that very write and may have dropped its socket.
    harness = make_harness(_CancelRefusingHarness, fail_open=True)
    ticket = await harness.arbiter.enqueue(
        source="native", response_started_timeout=0.05, cancel_timeout=0.05
    )
    await asyncio.wait_for(ticket.sent, timeout=1)

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        with pytest.raises(Exception):
            await asyncio.wait_for(ticket.done, timeout=2)
        await _settle()

    assert harness.aborted, "an escalation after a refused write must tear down"
    assert harness.arbiter._connection_available is False
    assert any(
        "a transport write just failed" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_healthy_cancel_write_still_fails_open(make_harness, caplog):
    # The dual of the case above: same escalation site, same code path, but the
    # cancel write is accepted and only the terminal never comes. Without this
    # pair, standing down on every _cancel_after_timeout would look correct.
    #
    # Driven through the DONE timeout, not the started one. A started-timeout
    # escalation is id-less by construction — no response.created means no id
    # — so it can only ever exercise the stand-down. Reaching this site with an
    # attributable response requires the announcement to have arrived first.
    harness = make_harness(fail_open=True)
    ticket = await harness.arbiter.enqueue(
        source="native", response_done_timeout=0.05, cancel_timeout=0.05
    )
    await asyncio.wait_for(ticket.sent, timeout=1)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-1"}}
    )

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        with pytest.raises(Exception):
            await asyncio.wait_for(ticket.done, timeout=2)
        await _settle()

    assert harness.aborted == []
    assert harness.arbiter._connection_available is True
    assert any(
        "failing open" in record.getMessage() for record in caplog.records
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_id_less_abandoned_response_stands_the_hatch_down(
    make_harness, caplog
):
    # The create is on the wire, so the provider may still announce this
    # response later. Without an id there is nothing to tell that announcement
    # apart from the next turn's.
    harness = make_harness(fail_open=True)
    await harness.own_a_live_response(response_id=None)

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        raised = await _stick_a_cancel(harness)

    assert isinstance(raised, asyncio.TimeoutError)
    assert harness.aborted, "an unattributable abandoned response must tear down"
    assert any(
        "no id to attribute later events by" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_id_bearing_abandoned_response_still_fails_open(make_harness):
    # The dual: the provider supplied an id, so its later events are
    # attributable and the hatch applies.
    harness = make_harness(fail_open=True)
    await harness.own_a_live_response(response_id="resp-with-id")

    raised = await _stick_a_cancel(harness)

    assert isinstance(raised, asyncio.TimeoutError)
    assert harness.aborted == []
    assert harness.arbiter._connection_available is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_create_on_the_wire_without_an_announcement_stands_the_hatch_down(
    make_harness, caplog
):
    # The case that separates the two candidate criteria, and the one the
    # withdrawn #2592 got backwards.
    #
    # Here response.create has reached the provider but response.created has
    # not come back. Keyed on "did the create go out" this is a blocker — the
    # provider may still announce this response, and without an id that
    # announcement is indistinguishable from the next turn's. Keyed on "did
    # response.created come back" it looks safe, which is exactly wrong: a
    # request that never started cannot surprise anyone, but one whose create
    # is already out is the only kind that can.
    harness = make_harness(fail_open=True)
    ticket = await harness.arbiter.enqueue(source="native")
    await asyncio.wait_for(ticket.sent, timeout=1)
    owner = harness.arbiter._response_owner
    assert owner is not None
    assert owner.response_send_started is True, "the create reached the wire"
    assert owner.ticket.started.done() is False, "but nothing came back"

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        raised = await _stick_a_cancel(harness)

    assert isinstance(raised, asyncio.TimeoutError)
    assert harness.aborted, (
        "a response whose create is on the wire but unannounced cannot be "
        "told apart from the next one, so the hatch must stand down"
    )
    assert any(
        "no id to attribute later events by" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_request_whose_create_never_went_out_still_fails_open(make_harness):
    # The criterion is "did the create reach the wire", not "did
    # response.created come back". A request still waiting for dispatch has no
    # live response at all, so nothing of its can surprise us later.
    #
    # The withdrawn #2592 wrote this the other way round — keyed on
    # ticket.started — and pinned the wrong contract with a passing test.
    harness = make_harness(fail_open=True)
    harness.arbiter.pause_dispatch()
    queued = await harness.arbiter.enqueue(source="native")
    await _settle()
    assert harness.dispatch_count == 0
    assert not queued.sent.done()

    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "srv-1"}}
    )
    raised = await _stick_a_cancel(harness)

    assert isinstance(raised, asyncio.TimeoutError)
    assert harness.aborted == [], (
        "a request whose create never went out cannot emit a confusable "
        "announcement"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_pending_server_vad_response_stands_the_hatch_down(
    make_harness, caplog
):
    # Announced by speech_stopped, no id until its response.created arrives —
    # and cancel_current's bound is shorter than the missing-created backstop,
    # so an escalation can land inside that window.
    harness = make_harness(fail_open=True)
    harness.arbiter.notify_server_vad_response_pending()
    await _settle()
    assert harness.arbiter._server_vad_response_pending is True

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        raised = await _stick_a_cancel(harness)

    assert isinstance(raised, asyncio.TimeoutError)
    assert harness.aborted, "an unattributable VAD response must tear down"
    assert any(
        "server-VAD response has no id yet" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Contracts 3 and 4: the released turn is ended, and the host is told first.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_release_keeps_the_lane_closed_under_a_live_server_response(
    make_harness,
):
    # Greptile P1 on this PR. The abandoned turn's bookkeeping is what became
    # untrustworthy — a separately initiated server response tracked alongside
    # it is still tracking fine, and it is still RUNNING. Discarding its
    # identity and forcing the lane open would put the next create straight on
    # top of it (response_already_active, or two live responses).
    harness = make_harness(fail_open=True)
    await harness.own_a_live_response("resp-owned")
    # A server-initiated response starts and is tracked separately.
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "srv-live"}}
    )
    assert "srv-live" in harness.arbiter._server_response_ids

    raised = await _stick_a_cancel(harness)
    assert isinstance(raised, asyncio.TimeoutError)
    await _settle()

    assert harness.aborted == [], "the connection itself is fine"
    assert "srv-live" in harness.arbiter._server_response_ids, (
        "a live server response's identity is not this turn's to discard"
    )
    dispatched_before = harness.dispatch_count
    queued = await harness.arbiter.enqueue(source="native-after")
    await _settle()
    assert harness.dispatch_count == dispatched_before, (
        "and the lane stays closed while it runs"
    )

    # Its own terminal releases the lane through the ordinary path.
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "srv-live"}}
    )
    await _settle()
    assert harness.dispatch_count == dispatched_before + 1
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-next"}}
    )
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-next"}}
    )
    await asyncio.wait_for(queued.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_release_opens_the_lane_when_nothing_else_is_running(make_harness):
    # The dual: with no other live response, the release must actually reopen
    # the lane. Without this pair, never reopening would look correct.
    harness = make_harness(fail_open=True)
    await harness.own_a_live_response("resp-owned")
    assert harness.arbiter._server_response_ids == {}

    raised = await _stick_a_cancel(harness)
    assert isinstance(raised, asyncio.TimeoutError)
    await _settle()

    assert harness.arbiter.is_busy is False
    revived = await harness.arbiter.enqueue(source="native-after")
    await asyncio.wait_for(revived.sent, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_release_ends_the_turn_before_the_lane_opens(make_harness):
    # Ordering, not just occurrence. If the lane opened first, the next
    # request could dispatch while the host was still closing the previous
    # turn — and end up with that turn's speech id rotated underneath it.
    lane_open_at_notify: list[bool] = []

    async def _on_release(reason: str, response_id: str | None) -> None:
        lane_open_at_notify.append(harness.arbiter._idle.is_set())

    harness = make_harness(fail_open=True, on_stuck_release=_on_release)
    await harness.own_a_live_response()

    raised = await _stick_a_cancel(harness)
    assert isinstance(raised, asyncio.TimeoutError)
    await _settle()

    assert lane_open_at_notify == [False], (
        "the host must finish ending the turn while the lane is still closed"
    )
    assert harness.arbiter._idle.is_set() is True, "and the lane opens after"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_hanging_host_notification_cannot_wedge_the_consumer(
    make_harness, caplog, monkeypatch
):
    # Escalations raised inside _process run the notification on the sole queue
    # consumer. An unbounded host callback there would block every later
    # dispatch — the stalled-write wedge again, moved into project code.
    monkeypatch.setattr(
        "main_logic.omni_realtime_client._response_arbiter"
        "._STUCK_RELEASE_NOTIFY_TIMEOUT",
        0.05,
    )
    released = asyncio.Event()

    async def _never_returns(reason: str, response_id: str | None) -> None:
        await released.wait()

    harness = make_harness(fail_open=True, on_stuck_release=_never_returns)
    try:
        stuck = await harness.arbiter.enqueue(
            source="native", response_done_timeout=0.05, cancel_timeout=0.05
        )
        await asyncio.wait_for(stuck.sent, timeout=1)
        # An attributable response, so the escalation actually reaches the
        # release path where the notification runs.
        harness.arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-1"}}
        )

        with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
            with pytest.raises(Exception):
                await asyncio.wait_for(stuck.done, timeout=2)
            await _settle()

        assert any(
            "exceeded" in record.getMessage() for record in caplog.records
        ), "the bound must be visible, not silently swallowed"

        follow_up = await harness.arbiter.enqueue(source="native-after")
        await asyncio.wait_for(follow_up.sent, timeout=1)
    finally:
        released.set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_release_flushes_transcript_the_turn_never_got_to_send():
    # Codex P2 on this PR. Some providers emit transcript deltas and no
    # transcript-done, so the buffer is drained by response.done's fallback
    # flush — and a stalled lifecycle is precisely the case where that
    # terminal never comes. Resetting per-turn state without flushing first
    # loses whatever the turn already said: audio the user heard, with no text.
    from main_logic.omni_realtime_client import OmniRealtimeClient

    transcripts: list[tuple[str, bool]] = []

    async def _on_output_transcript(text: str, is_first: bool) -> None:
        transcripts.append((text, is_first))

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="free-model",
        api_type="free",
        on_output_transcript=_on_output_transcript,
    )

    async def _send(event: dict) -> None:
        return None

    arbiter = client._response_arbiter
    arbiter._send_event = _send
    arbiter._fail_open = True
    try:
        ticket = await arbiter.enqueue(source="native")
        await asyncio.wait_for(ticket.sent, timeout=1)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-1"}}
        )
        client._current_response_id = "resp-1"
        client._is_responding = True
        # The turn spoke, and its transcript is still buffered.
        client._audio_delta_count = 3
        client._output_transcript_buffer = "half a sentence"

        with pytest.raises(asyncio.TimeoutError):
            await arbiter.cancel_current(timeout=0.05)
        await _settle()

        # The contract is that the buffered text reaches the frontend. The
        # is_first flag is transport bookkeeping driven by response.created,
        # which this test does not go through, so it is not asserted here.
        assert [text for text, _ in transcripts] == ["half a sentence"], (
            "text the user already heard must not be dropped by the release"
        )
        assert client._output_transcript_buffer == ""
    finally:
        await arbiter.shutdown("test teardown")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_release_does_not_flush_a_turn_that_never_spoke():
    # The dual: the flush is conditional on the turn having produced audio, so
    # a silent turn must not push a stray transcript at the frontend.
    from main_logic.omni_realtime_client import OmniRealtimeClient

    transcripts: list[tuple[str, bool]] = []

    async def _on_output_transcript(text: str, is_first: bool) -> None:
        transcripts.append((text, is_first))

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="free-model",
        api_type="free",
        on_output_transcript=_on_output_transcript,
    )

    async def _send(event: dict) -> None:
        return None

    arbiter = client._response_arbiter
    arbiter._send_event = _send
    arbiter._fail_open = True
    try:
        ticket = await arbiter.enqueue(source="native")
        await asyncio.wait_for(ticket.sent, timeout=1)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-1"}}
        )
        client._current_response_id = "resp-1"
        client._is_responding = True
        client._audio_delta_count = 0
        client._output_transcript_buffer = "never spoken"

        with pytest.raises(asyncio.TimeoutError):
            await arbiter.cancel_current(timeout=0.05)
        await _settle()

        assert transcripts == []
    finally:
        await arbiter.shutdown("test teardown")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_release_runs_the_hosts_own_end_of_turn_work():
    # Contract 3, through the real client: the released turn goes through the
    # same three steps response.done drives, because it is the same code.
    from main_logic.omni_realtime_client import OmniRealtimeClient

    done_calls: list[str] = []

    async def _on_done() -> None:
        done_calls.append("done")

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="free-model",
        api_type="free",
        on_response_done=_on_done,
    )
    sent: list[dict] = []

    async def _send(event: dict) -> None:
        sent.append(event)

    arbiter = client._response_arbiter
    arbiter._send_event = _send
    arbiter._fail_open = True
    try:
        ticket = await arbiter.enqueue(source="native")
        await asyncio.wait_for(ticket.sent, timeout=1)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-1"}}
        )
        client._current_response_id = "resp-1"
        client._is_responding = True
        client._image_sent_this_turn = True

        with pytest.raises(asyncio.TimeoutError):
            await arbiter.cancel_current(timeout=0.05)
        await _settle()

        assert client._is_responding is False, (
            "the host must stop reporting a response in progress, or the "
            "connection the hatch saved is useless to proactive chat"
        )
        assert client._current_response_id is None, (
            "clearing the identity is what quarantines the abandoned "
            "response's later events"
        )
        assert client._image_sent_this_turn is False, (
            "per-turn state must not leak into the next turn"
        )
        assert done_calls == ["done"]
        assert client.is_active_response() is False
    finally:
        await arbiter.shutdown("test teardown")


# --------------------------------------------------------------------------
# Contract 5: the release is scoped across its own await.
#
# Notifying the host before opening the lane (contract 4) puts an await in the
# middle of the release. Three things can happen there that cannot happen in
# straight-line code: this task can be cancelled, the abandoned response's
# terminal can land, and the worker can wake and take a new owner. The release
# must act only on what it captured before that await, and must finish its
# bookkeeping either way.
#
# All four cases below were raised by Codex against the first version of this
# ordering — the fix for one invariant opening a window on another, which is
# the failure mode #2583 was reorganised to stop repeating.
# --------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_cancelled_release_still_finishes_its_bookkeeping(make_harness):
    # Without this, a cancellation mid-notification leaves `escalated` set on
    # the owner while the owner is never cleared: every later escalation sees
    # a duplicate and returns, and the lane never reopens. The connection the
    # hatch "saved" would then accept no further turns at all — strictly worse
    # than the teardown it replaced.
    entered = asyncio.Event()

    async def _blocks_forever(reason: str, response_id: str | None) -> None:
        entered.set()
        await asyncio.Event().wait()

    harness = make_harness(fail_open=True, on_stuck_release=_blocks_forever)
    owner = await harness.own_a_live_response("resp-1")
    assert owner is not None

    release = asyncio.create_task(
        harness.arbiter._release_stuck_lifecycle("cancelled mid-notify")
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    release.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(release, timeout=1)
    await _settle()

    assert harness.arbiter._response_owner is None, (
        "a release cancelled mid-notification must still give up the owner"
    )
    assert harness.arbiter.is_busy is False, "and still reopen the lane"
    revived = await harness.arbiter.enqueue(source="native-after")
    await asyncio.wait_for(revived.sent, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_release_does_not_erase_an_owner_that_arrived_meanwhile(
    make_harness,
):
    # The abandoned response's terminal can still land while the host is being
    # notified — the arbiter gave up on it, the provider did not. That clears
    # the owner and reopens the lane, so the next queued request dispatches and
    # installs itself as the new owner, all before the release resumes.
    # Clearing "the owner" unconditionally at that point erases a healthy
    # turn's ownership, and its terminal would arrive with nothing to resolve.
    resumed = asyncio.Event()
    notified = asyncio.Event()

    async def _hands_control_back(reason: str, response_id: str | None) -> None:
        notified.set()
        await resumed.wait()

    harness = make_harness(fail_open=True, on_stuck_release=_hands_control_back)
    first = await harness.own_a_live_response("resp-1")

    release = asyncio.create_task(
        harness.arbiter._release_stuck_lifecycle("overtaken by a new owner")
    )
    await asyncio.wait_for(notified.wait(), timeout=1)

    # The world moves while the notification is outstanding. The release
    # already woke this ticket, so its failure is expected; what matters is
    # that the terminal frees the ownership behind it.
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(first.done, timeout=1)
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-1"}}
    )
    await _settle()
    assert harness.arbiter._response_owner is None
    second = await harness.arbiter.enqueue(source="native-next")
    await asyncio.wait_for(second.sent, timeout=1)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-2"}}
    )
    successor = harness.arbiter._response_owner
    assert successor is not None and successor.source == "native-next"

    resumed.set()
    await asyncio.wait_for(release, timeout=1)
    await _settle()

    assert harness.arbiter._response_owner is successor, (
        "the release must not clear an owner it never observed"
    )
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-2"}}
    )
    # And its terminal still resolves, which is what the ownership was for.
    await asyncio.wait_for(second.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_undisturbed_release_still_clears_its_own_owner(make_harness):
    # The dual. "Only clear what you captured" must not become "never clear":
    # in the ordinary case nothing intervened and the owner is still the one
    # captured, so it goes.
    #
    # The worker clears ownership on its own way out too (_process's unwind),
    # which is enough to satisfy a naive assertion here — the first version of
    # this test passed even with the release's clear deleted. So the worker is
    # retired first and the same owner reinstalled by hand: with nothing else
    # running, the release is the only thing that can clear it.
    async def _returns_promptly(reason: str, response_id: str | None) -> None:
        return None

    harness = make_harness(fail_open=True, on_stuck_release=_returns_promptly)
    ticket = await harness.own_a_live_response("resp-1")
    owner = harness.arbiter._response_owner
    assert owner is not None

    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-1"}}
    )
    await asyncio.wait_for(ticket.done, timeout=1)
    await _settle()
    assert harness.arbiter._response_owner is None, "the worker dropped it"
    harness.arbiter._response_owner = owner

    await harness.arbiter._release_stuck_lifecycle("ordinary release")

    assert harness.arbiter._response_owner is None, (
        "an undisturbed release must give up the owner it captured"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_release_tells_the_host_which_response_it_abandoned(make_harness):
    # The host cannot finalize "the current turn" on trust: an owned response
    # can overlap a server-initiated one, and it is the server response's
    # response.created that last wrote the host's tracked id. The reason
    # string alone does not say which response died, so the id travels with it.
    seen: list[tuple[str, str | None]] = []

    async def _record(reason: str, response_id: str | None) -> None:
        seen.append((reason, response_id))

    harness = make_harness(fail_open=True, on_stuck_release=_record)
    await harness.own_a_live_response("resp-abandoned")

    await harness.arbiter._release_stuck_lifecycle("named release")
    await _settle()

    assert seen == [("named release", "resp-abandoned")], (
        "the abandoned response's identity must reach the host"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_id_less_orphan_response_stands_the_hatch_down(make_harness, caplog):
    # The gap the four existing blockers left open. A server-initiated
    # response.created arrives with no id and nobody owns it: there is no
    # owner to inspect, no remembered id, and no speech_stopped marker, so all
    # four blockers pass and the lane opens over a response that may still be
    # streaming. Its terminal will arrive id-less too, and complete whoever
    # holds the lane by then.
    harness = make_harness(fail_open=True)
    harness.arbiter.notify_response_created({"type": "response.created"})
    assert harness.arbiter._server_response_active is True
    assert harness.arbiter._server_response_ids == {}
    assert harness.arbiter._response_owner is None

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        await harness.arbiter._escalate("orphan with no id")

    assert harness.aborted == ["orphan with no id"], (
        "an unidentifiable live response must tear the transport down"
    )
    assert any(
        "no id at all" in record.getMessage() for record in caplog.records
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_release_with_nothing_to_release_says_so(make_harness, caplog):
    # Codex P2 on this PR, partially accepted. cancel_current()'s unowned
    # branch escalates over a server response the arbiter never owned, so the
    # release gives nothing up and the lane stays held by that response's id.
    # The generic line claims a turn was dropped and the lane reopened, which
    # is the opposite of what happened — and it is the line #2561 points field
    # reports at, so a wrong reading here costs a diagnosis.
    #
    # The finding's actual proposal (retire the id so the lane reopens) is
    # declined: test_the_release_keeps_the_lane_closed_under_a_live_server_response
    # pins the opposite, because the response may still be streaming.
    harness = make_harness(fail_open=True)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "srv-1"}}
    )
    assert harness.arbiter._response_owner is None

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        raised = await _stick_a_cancel(harness)

    assert isinstance(raised, asyncio.TimeoutError)
    assert harness.aborted == [], "the transport is kept"
    messages = [record.getMessage() for record in caplog.records]
    assert any("had no lifecycle to release" in message for message in messages), (
        "a release that released nothing must not report a dropped turn"
    )
    assert any("srv-1" in message for message in messages), (
        "and must name what is actually holding the lane"
    )
    assert not any(
        "failing open, transport kept" in message for message in messages
    ), "the two cases must not be reported with the same wording"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_release_that_gave_up_a_turn_still_says_that(make_harness, caplog):
    # The dual: when there really was a lifecycle to release, the wording
    # #2561 established stays exactly as it was.
    harness = make_harness(fail_open=True)
    await harness.own_a_live_response("resp-owned")

    with caplog.at_level(logging.WARNING, logger=ARBITER_LOGGER):
        raised = await _stick_a_cancel(harness)

    assert isinstance(raised, asyncio.TimeoutError)
    messages = [record.getMessage() for record in caplog.records]
    assert any("failing open, transport kept" in message for message in messages)
    assert not any("had no lifecycle to release" in message for message in messages)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_id_bearing_orphan_response_still_fails_open(make_harness):
    # The dual: the same unowned server response, but identified. Its later
    # events are attributable, so the hatch applies and the lane stays closed
    # only until that response retires.
    harness = make_harness(fail_open=True)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "srv-1"}}
    )

    await harness.arbiter._escalate("orphan with an id")

    assert harness.aborted == []
    assert harness.arbiter._connection_available is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_host_leaves_a_different_live_response_alone():
    # The host half of the same contract. The arbiter abandoned resp-1, but a
    # server-initiated resp-2 announced itself afterwards and is what the
    # transport is tracking. Finalizing "the current turn" would close resp-2
    # mid-stream: its audio keeps arriving with _is_responding already false,
    # and its own terminal finds nothing left to end.
    from main_logic.omni_realtime_client import OmniRealtimeClient

    done_calls: list[str] = []

    async def _on_done() -> None:
        done_calls.append("done")

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="free-model",
        api_type="free",
        on_response_done=_on_done,
    )
    client._current_response_id = "resp-2"
    client._is_responding = True
    client._image_sent_this_turn = True

    await client._on_arbiter_stuck_release("abandoned resp-1", "resp-1")

    assert client._is_responding is True, (
        "the live response must keep streaming"
    )
    assert client._current_response_id == "resp-2"
    assert client._image_sent_this_turn is True, (
        "and keep the per-turn state it is still using"
    )
    assert done_calls == [], "no turn ended, so nothing announces one"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_host_ends_the_turn_it_was_actually_tracking():
    # The dual, in both directions: the named response IS the tracked one, and
    # an unnamed release (the arbiter never learned an id) still finalizes.
    from main_logic.omni_realtime_client import OmniRealtimeClient

    for named_id in ("resp-1", None):
        done_calls: list[str] = []

        async def _on_done() -> None:
            done_calls.append("done")

        client = OmniRealtimeClient(
            "wss://example.invalid/realtime",
            "test-key",
            model="free-model",
            api_type="free",
            on_response_done=_on_done,
        )
        client._current_response_id = "resp-1"
        client._is_responding = True
        client._image_sent_this_turn = True

        await client._on_arbiter_stuck_release("abandoned", named_id)

        assert client._is_responding is False, f"named_id={named_id}"
        assert client._current_response_id is None, f"named_id={named_id}"
        assert client._image_sent_this_turn is False, f"named_id={named_id}"
        assert done_calls == ["done"], f"named_id={named_id}"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_blocked_transcript_flush_cannot_strand_this_turns_state():
    # The host callbacks run under the arbiter's 2s bound, so a host that
    # blocks in on_output_transcript gets its release cancelled at that await.
    # If the per-turn reset ran after it, this turn's flags would survive into
    # the next one — _image_sent_this_turn being the one that changes what the
    # model is told. Settling every synchronous write before the first await
    # makes the leak impossible rather than merely unlikely.
    from main_logic.omni_realtime_client import OmniRealtimeClient

    entered = asyncio.Event()

    async def _blocks(text: str, is_first: bool) -> None:
        entered.set()
        await asyncio.Event().wait()

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="free-model",
        api_type="free",
        on_output_transcript=_blocks,
    )
    client._current_response_id = "resp-1"
    client._is_responding = True
    client._image_sent_this_turn = True
    client._image_recognized_this_turn = True
    client._output_transcript_buffer = "已经说出口的半句"
    client._audio_delta_count = 3

    release = asyncio.create_task(client._on_arbiter_stuck_release("stalled", "resp-1"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    release.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(release, timeout=1)

    assert client._is_responding is False
    assert client._current_response_id is None
    assert client._image_sent_this_turn is False, (
        "a turn interrupted while flushing must not leak its image flags"
    )
    assert client._image_recognized_this_turn is False
    assert client._audio_delta_count == 0
    assert client._output_transcript_buffer == ""


# --------------------------------------------------------------------------
# Contract 4, restated: identity survives the release's own awaits.
#
# The previous round scoped the release against the ARBITER's state. These pin
# it against the HOST's, which is where turn identity actually lives — the
# arbiter cannot serialize against a turn the user starts through
# handle_new_message, because that path never consults the lane.
# --------------------------------------------------------------------------


def _free_client(**hooks):
    """A client on a provider WITHOUT server VAD, where sid rotation matters.

    The lanlan.app host is load-bearing, not decoration: ``_is_free_proxy``
    keys on it, and that is what makes ``_has_server_vad`` False. With any
    other host the same ``api_type="free"`` client HAS server VAD, rotates
    from ``speech_stopped`` instead, and these tests would pass without ever
    reaching the hook they are about.
    """

    from main_logic.omni_realtime_client import OmniRealtimeClient

    client = OmniRealtimeClient(
        "wss://lanlan.app/realtime",
        "test-key",
        model="free-model",
        api_type="free",
        **hooks,
    )
    assert client._has_server_vad is False, (
        "this fixture exists to cover the providers whose only sid rotation "
        "point is the turn-finished hook"
    )
    return client


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_id_less_overlap_is_not_the_releases_to_end():
    # Codex P2. An owned response resp-1 is overlapped by a server-initiated
    # response.created carrying NO id, which overwrites _current_response_id
    # with None. Treating None as "matches every abandoned id" finalizes that
    # overlapping response while it is still streaming: its half-sentence goes
    # out as a separate transcript, its audio counter is zeroed, and the turn
    # is ended a second time when its own terminal arrives.
    done_calls: list[str] = []
    transcripts: list[tuple[str, bool]] = []

    async def _on_done() -> None:
        done_calls.append("done")

    async def _on_transcript(text: str, is_first: bool) -> None:
        transcripts.append((text, is_first))

    client = _free_client(
        on_response_done=_on_done, on_output_transcript=_on_transcript
    )
    # The owner announced itself with an id...
    client._current_response_id = "resp-1"
    client._is_responding = True
    client._turn_epoch += 1
    # ...then an id-less response.created took the turn over.
    client._current_response_id = None
    client._turn_epoch += 1
    client._output_transcript_buffer = "第二个响应正在说"
    client._audio_delta_count = 2

    await client._on_arbiter_stuck_release("abandoned resp-1", "resp-1")

    assert client._is_responding is True, "the overlapping response is still live"
    assert client._audio_delta_count == 2, "and its audio accounting is untouched"
    assert client._output_transcript_buffer == "第二个响应正在说", (
        "its half-sentence stays buffered for its own terminal to flush whole"
    )
    assert transcripts == [], "nothing of its text is sent early"
    assert done_calls == [], "and no turn end is announced under it"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_named_release_still_ends_the_turn_it_named():
    # The dual, both ways: the named id matches, and an unnamed release (the
    # arbiter never learned an id, as on Gemini) still finalizes.
    for named_id in ("resp-1", None):
        done_calls: list[str] = []

        async def _on_done() -> None:
            done_calls.append("done")

        client = _free_client(on_response_done=_on_done)
        client._current_response_id = "resp-1"
        client._is_responding = True
        client._turn_epoch += 1

        await client._on_arbiter_stuck_release("abandoned", named_id)

        assert client._is_responding is False, f"named_id={named_id}"
        assert client._current_response_id is None, f"named_id={named_id}"
        assert done_calls == ["done"], f"named_id={named_id}"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_turn_that_started_mid_release_keeps_its_speech_id():
    # The damage the lane barrier was meant to prevent, closed from the side
    # that owns turn identity instead. The release parks in the host's
    # transcript flush; the abandoned response's real terminal lands, the lane
    # reopens, a successor dispatches and announces itself. The dead turn's
    # remaining hooks must not fire on it — on_sid_rotate above all, since
    # throwing away the successor's speech id makes TTS drop its text.
    entered = asyncio.Event()
    proceed = asyncio.Event()
    done_calls: list[int] = []
    rotations: list[int] = []

    async def _blocks(text: str, is_first: bool) -> None:
        entered.set()
        await proceed.wait()

    async def _on_done() -> None:
        done_calls.append(client._turn_epoch)

    async def _on_rotate() -> None:
        rotations.append(client._turn_epoch)

    client = _free_client(
        on_output_transcript=_blocks,
        on_response_done=_on_done,
        on_sid_rotate=_on_rotate,
    )
    client._current_response_id = "resp-1"
    client._is_responding = True
    client._turn_epoch += 1
    client._output_transcript_buffer = "旧轮说了半句"
    client._audio_delta_count = 1

    release = asyncio.create_task(
        client._on_arbiter_stuck_release("abandoned resp-1", "resp-1")
    )
    await asyncio.wait_for(entered.wait(), timeout=1)

    # A successor turn starts while the release is parked in the flush.
    client._current_response_id = "resp-2"
    client._is_responding = True
    client._turn_epoch += 1

    proceed.set()
    await asyncio.wait_for(release, timeout=2)

    assert rotations == [], (
        "rotating here would throw away the speech id the live turn is using"
    )
    assert done_calls == [], "and the live turn has not ended"
    assert client._current_response_id == "resp-2", "the successor is untouched"
    assert client._is_responding is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_undisturbed_release_still_runs_both_hooks():
    # The dual. "Stop when the epoch moves" must not become "never run".
    order: list[str] = []

    async def _on_done() -> None:
        order.append("done")

    async def _on_rotate() -> None:
        order.append("rotate")

    client = _free_client(on_response_done=_on_done, on_sid_rotate=_on_rotate)
    client._current_response_id = "resp-1"
    client._is_responding = True
    client._turn_epoch += 1

    await client._on_arbiter_stuck_release("abandoned resp-1", "resp-1")

    assert order == ["done", "rotate"], (
        "an undisturbed release runs both hooks, in the terminal path's order"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_blocked_transcript_flush_cannot_cost_the_speech_id_rotation():
    # For a provider without server VAD this hook is the ONLY rotation point,
    # and losing it silently drops every later turn's text at TTS. A host that
    # parks in on_output_transcript must not consume the whole notification
    # budget: the flush is best-effort and bounded, the rotation is neither.
    rotations: list[str] = []

    async def _never_returns(text: str, is_first: bool) -> None:
        await asyncio.Event().wait()

    async def _on_rotate() -> None:
        rotations.append("rotate")

    client = _free_client(
        on_output_transcript=_never_returns, on_sid_rotate=_on_rotate
    )
    client._current_response_id = "resp-1"
    client._is_responding = True
    client._turn_epoch += 1
    client._output_transcript_buffer = "说了半句"
    client._audio_delta_count = 1

    await asyncio.wait_for(
        client._on_arbiter_stuck_release("abandoned resp-1", "resp-1"),
        timeout=5,
    )

    assert rotations == ["rotate"], (
        "the rotation must survive a transcript callback that never returns"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_raising_transcript_flush_cannot_cost_the_rotation_either():
    # The same contract with no timing involved: the emit had no exception
    # guard at all, so a raising host callback skipped the rotation outright.
    rotations: list[str] = []

    async def _raises(text: str, is_first: bool) -> None:
        raise RuntimeError("frontend socket is gone")

    async def _on_rotate() -> None:
        rotations.append("rotate")

    client = _free_client(on_output_transcript=_raises, on_sid_rotate=_on_rotate)
    client._current_response_id = "resp-1"
    client._is_responding = True
    client._turn_epoch += 1
    client._output_transcript_buffer = "说了半句"
    client._audio_delta_count = 1

    await client._on_arbiter_stuck_release("abandoned resp-1", "resp-1")

    assert rotations == ["rotate"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_blocked_done_hook_cannot_cost_the_rotation():
    # The third trigger, and the one a bound on the transcript flush alone
    # misses entirely: on_response_done is the hook that blocks.
    rotations: list[str] = []

    async def _never_returns() -> None:
        await asyncio.Event().wait()

    async def _on_rotate() -> None:
        rotations.append("rotate")

    client = _free_client(on_response_done=_never_returns, on_sid_rotate=_on_rotate)
    client._current_response_id = "resp-1"
    client._is_responding = True
    client._turn_epoch += 1

    await asyncio.wait_for(
        client._on_arbiter_stuck_release("abandoned resp-1", "resp-1"),
        timeout=5,
    )

    assert rotations == ["rotate"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_terminal_path_leaves_the_hooks_unbounded():
    # The other half of "one implementation, not two": the release passes its
    # bounds in, and the ordinary response.done path must not inherit them. A
    # slow host there is slow, not cut off — no arbiter budget is being shared.
    from main_logic.omni_realtime_client import _transport

    finished: list[str] = []

    async def _slower_than_the_step_bound() -> None:
        await asyncio.sleep(_transport._STUCK_RELEASE_STEP_TIMEOUT * 3)
        finished.append("done")

    client = _free_client(on_response_done=_slower_than_the_step_bound)

    await client._notify_turn_finished()

    assert finished == ["done"], (
        "the terminal path must await the host, not bound it"
    )


@pytest.mark.unit
def test_every_turn_start_advances_the_epoch():
    # A completeness guard, not a checklist. The epoch is only sound while
    # every place that STARTS a turn bumps it, and a new provider path is
    # exactly where that gets forgotten. Discover the writers instead of
    # listing them, so a new one fails here rather than silently weakening the
    # release guard.
    import pathlib
    import re

    from main_logic.omni_realtime_client import _transport

    root = pathlib.Path(_transport.__file__).parent
    starts = 0
    offenders: list[str] = []
    for path in sorted(root.glob("*.py")):
        lines = path.read_text(encoding="utf-8").split("\n")
        for index, line in enumerate(lines):
            if not re.match(r"^\s*self\._is_responding\s*=\s*True\s*$", line):
                continue
            starts += 1
            if "self._turn_epoch += 1" not in "\n".join(lines[index : index + 3]):
                offenders.append(f"{path.name}:{index + 1}")
    assert starts >= 2, (
        "expected to find the turn-start sites; the search stopped matching, "
        f"which makes this guard vacuous (found {starts})"
    )
    assert offenders == [], (
        "every turn start must advance _turn_epoch within the next line or "
        f"two; these start a turn without it: {offenders}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_release_leaves_the_lane_to_a_successor_that_took_over(make_harness):
    # Greptile P1. Scoping the owner assignment stopped the release ERASING a
    # successor, but it still called _release_lane_if_clear() unconditionally —
    # and that helper knows nothing about owners. Every other caller of it
    # either just cleared the owner or guards on there being none; this was the
    # one site that could open the lane over a response it never observed,
    # letting the next response.create overlap a live one.
    resumed = asyncio.Event()
    notified = asyncio.Event()

    async def _hands_control_back(reason: str, response_id: str | None) -> None:
        notified.set()
        await resumed.wait()

    harness = make_harness(fail_open=True, on_stuck_release=_hands_control_back)
    first = await harness.own_a_live_response("resp-1")

    release = asyncio.create_task(
        harness.arbiter._release_stuck_lifecycle("overtaken by a new owner")
    )
    await asyncio.wait_for(notified.wait(), timeout=1)
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(first.done, timeout=1)
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-1"}}
    )
    await _settle()
    second = await harness.arbiter.enqueue(source="native-next")
    await asyncio.wait_for(second.sent, timeout=1)
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-2"}}
    )
    dispatched = harness.dispatch_count

    resumed.set()
    await asyncio.wait_for(release, timeout=1)
    await _settle()

    # Assert the lane state itself. `is_busy` is not the observable here — it
    # reports True off `_response_owner` alone, so it stays True whether or not
    # the lane was opened, and an earlier version of this test asserted it and
    # passed with the guard deleted.
    assert harness.arbiter._idle.is_set() is False, (
        "the successor holds the lane; the release must not open it underneath"
    )
    assert harness.arbiter._server_response_active is True, (
        "nor declare no response live while the successor is streaming"
    )

    # And the successor's own terminal is what opens it, as everywhere else in
    # the arbiter.
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-2"}}
    )
    await asyncio.wait_for(second.done, timeout=1)
    await _settle()
    assert harness.arbiter._idle.is_set() is True
    third = await harness.arbiter.enqueue(source="native-third")
    await asyncio.wait_for(third.sent, timeout=1)
    assert harness.dispatch_count == dispatched + 1
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-3"}}
    )
    harness.arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-3"}}
    )
    await asyncio.wait_for(third.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_release_with_nothing_to_release_does_not_end_a_host_turn():
    # Codex P2. cancel_current()'s unowned branch escalates over a server
    # response the arbiter never owned. It keeps that response's id on purpose
    # — the response may still be streaming — so telling the host to finalize
    # discards the turn on one side while the other goes on tracking it, and
    # the response's own later events are then stale-filtered against an
    # identity the host no longer holds.
    from main_logic.omni_realtime_client import OmniRealtimeClient

    done_calls: list[str] = []

    async def _on_done() -> None:
        done_calls.append("done")

    client = OmniRealtimeClient(
        "wss://lanlan.app/realtime",
        "test-key",
        model="free-model",
        api_type="free",
        on_response_done=_on_done,
    )
    sent: list[dict] = []

    async def _send(event: dict) -> None:
        sent.append(event)

    arbiter = client._response_arbiter
    arbiter._send_event = _send
    arbiter._fail_open = True
    try:
        # A server-initiated response the arbiter never owned, with an id.
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "srv-1"}}
        )
        client._current_response_id = "srv-1"
        client._is_responding = True
        assert arbiter._response_owner is None and arbiter._current is None

        with pytest.raises(asyncio.TimeoutError):
            await arbiter.cancel_current(timeout=0.05)
        await _settle()

        assert done_calls == [], (
            "no lifecycle was released, so no turn ended"
        )
        assert client._is_responding is True, (
            "the server response may still be streaming; the host must keep "
            "tracking it"
        )
        assert client._current_response_id == "srv-1", (
            "and keep the identity its later events are matched against"
        )
        assert "srv-1" in arbiter._server_response_ids, (
            "the two halves must agree about what is live"
        )
    finally:
        await arbiter.shutdown("test teardown")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_release_that_gave_up_a_lifecycle_still_ends_the_host_turn():
    # The dual: when there really was a lifecycle, the host still finalizes.
    # Without this pair, "never notify" would pass for correct.
    from main_logic.omni_realtime_client import OmniRealtimeClient

    done_calls: list[str] = []

    async def _on_done() -> None:
        done_calls.append("done")

    client = OmniRealtimeClient(
        "wss://lanlan.app/realtime",
        "test-key",
        model="free-model",
        api_type="free",
        on_response_done=_on_done,
    )
    sent: list[dict] = []

    async def _send(event: dict) -> None:
        sent.append(event)

    arbiter = client._response_arbiter
    arbiter._send_event = _send
    arbiter._fail_open = True
    try:
        ticket = await arbiter.enqueue(source="native")
        await asyncio.wait_for(ticket.sent, timeout=1)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-1"}}
        )
        client._current_response_id = "resp-1"
        client._is_responding = True

        with pytest.raises(asyncio.TimeoutError):
            await arbiter.cancel_current(timeout=0.05)
        await _settle()

        assert done_calls == ["done"]
        assert client._is_responding is False
        assert client._current_response_id is None
    finally:
        await arbiter.shutdown("test teardown")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_user_turn_starting_on_server_vad_advances_the_epoch():
    # Codex P2. On a server-VAD provider the user's turn starts at
    # speech_stopped — on_new_message assigns the new speech id and fires
    # USER_INPUT right there — and the provider's response.created only
    # follows later. Advancing the epoch only at response.created leaves that
    # whole gap open: a release suspended in a host callback resumes, still
    # believes the turn is its own, and finalizes against the speech id the
    # user turn just took.
    import json
    from unittest.mock import AsyncMock

    from main_logic.omni_realtime_client import OmniRealtimeClient

    rotations: list[int] = []

    async def _on_new_message() -> None:
        rotations.append(client._turn_epoch)

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="qwen-omni-turbo-realtime",
        api_type="qwen",
        on_new_message=_on_new_message,
    )
    assert client._has_server_vad is True, (
        "this case is about the providers that DO emit speech_stopped"
    )
    client.ws = AsyncMock()
    client.ws.__aiter__.return_value = [
        json.dumps({"type": "input_audio_buffer.speech_stopped"}),
    ]
    before = client._turn_epoch

    try:
        await client.handle_messages()
    finally:
        await client._response_arbiter.shutdown("test teardown")

    assert client._turn_epoch == before + 1, (
        "the user's turn boundary must advance the epoch, not only the "
        "provider's response.created"
    )
    assert rotations == [before + 1], (
        "and before on_new_message runs, since that is what takes the new "
        "speech id a suspended release must not finalize against"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_queued_request_is_not_a_turn_the_host_should_end(make_harness):
    # Codex P2, the second route into the same defect. An idle-wait timeout
    # escalates over a request that is still QUEUED — _current is set, but
    # nothing of it reached the provider under its own name, so there is no
    # owner. Meanwhile the host can be tracking a live server-initiated
    # response, because response.created is what sets its identity and that
    # event does not care who asked. Treating _current as a released turn
    # tells the host to finalize that live response.
    seen: list[tuple[str, str | None]] = []

    async def _record(reason: str, response_id: str | None) -> None:
        seen.append((reason, response_id))

    harness = make_harness(fail_open=True, on_stuck_release=_record)
    # A server response holds the lane; the queued request never became an
    # owner.
    harness.arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "srv-1"}}
    )
    ticket = await harness.arbiter.enqueue(source="native")
    await _settle()
    assert harness.arbiter._response_owner is None
    harness.arbiter._current = harness.arbiter._queued_by_ticket.get(id(ticket))

    await harness.arbiter._release_stuck_lifecycle("idle wait timed out")
    await _settle()

    assert seen == [], (
        "a queued request is not a turn; ending one would finalize the live "
        "server response the host is actually tracking"
    )
    assert "srv-1" in harness.arbiter._server_response_ids, (
        "which this release deliberately keeps, because it may still be "
        "streaming"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_release_adds_what_the_turn_said_to_the_repetition_history():
    # Codex P2. Ending a turn has to record its reply on EVERY path. A
    # provider that keeps losing response.done — precisely what the hatch
    # exists for — would otherwise never contribute an audible reply to
    # _recent_responses, so three identical turns in a row could not trigger
    # on_repetition_detected at all.
    client = _free_client()
    client._current_response_id = "resp-1"
    client._is_responding = True
    client._turn_epoch += 1
    client._current_response_transcript = "一模一样的回答"
    client._audio_delta_count = 2

    await client._on_arbiter_stuck_release("abandoned resp-1", "resp-1")

    assert client._recent_responses == ["一模一样的回答"], (
        "the released turn's reply must reach the repetition history"
    )
    assert client._last_response_transcript == "一模一样的回答"
    assert client._current_response_transcript == "", "and be consumed once"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_release_repeats_the_terminal_paths_repetition_bookkeeping():
    # The dual, and the point of contract 3: both paths run the SAME work
    # because it is the same implementation. Drive three identical turns
    # through the ordinary terminal path and through releases, and the
    # detection fires either way.
    repetitions: list[str] = []

    async def _on_repetition() -> None:
        repetitions.append("detected")

    client = _free_client(on_repetition_detected=_on_repetition)
    client._repetition_threshold = 0.5

    for turn in range(3):
        client._current_response_id = f"resp-{turn}"
        client._is_responding = True
        client._turn_epoch += 1
        client._current_response_transcript = "完全相同的一句话"
        client._audio_delta_count = 1
        await client._on_arbiter_stuck_release(f"abandoned resp-{turn}", f"resp-{turn}")

    assert repetitions == ["detected"], (
        "three released turns saying the same thing must trigger detection, "
        "exactly as three terminal turns would"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_turn_starting_inside_the_done_hook_still_gets_a_speech_id():
    # Codex P2. The two hooks are not independent: on_response_done queues
    # this turn's TTS-done sentinel, which CLOSES its speech id, and
    # on_sid_rotate is what hands out the next one. Re-checking the epoch
    # between them let a successor split the pair — old sid closed, no new one
    # issued — and on a provider without server VAD the successor then speaks
    # under a closed sid and its text is silently dropped.
    entered = asyncio.Event()
    proceed = asyncio.Event()
    rotations: list[str] = []

    async def _on_done() -> None:
        entered.set()
        await proceed.wait()

    async def _on_rotate() -> None:
        rotations.append("rotate")

    client = _free_client(on_response_done=_on_done, on_sid_rotate=_on_rotate)
    client._current_response_id = "resp-1"
    client._is_responding = True
    client._turn_epoch += 1

    release = asyncio.create_task(
        client._on_arbiter_stuck_release("abandoned resp-1", "resp-1")
    )
    await asyncio.wait_for(entered.wait(), timeout=1)

    # The successor announces itself while the done hook is still running —
    # after that hook has already closed the old speech id.
    client._turn_epoch += 1

    proceed.set()
    await asyncio.wait_for(release, timeout=2)

    assert rotations == ["rotate"], (
        "the rotation repairs the speech id the done hook just closed; "
        "skipping it strands the successor on a dead one"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_released_turns_late_terminal_is_still_accounted_for():
    # Codex P2. The release clears _current_response_id on purpose, so the
    # released response's real terminal always takes the stale-event branch.
    # That branch forwarded it to the arbiter and dropped everything else,
    # including the provider's exact token counts — so every turn the hatch
    # recovered vanished from usage statistics.
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    client = _free_client()
    client.ws = AsyncMock()
    client.ws.__aiter__.return_value = [
        json.dumps({"type": "response.created", "response": {"id": "resp-2"}}),
        # The released turn's real terminal, arriving after a successor
        # became current — which is what the release guarantees.
        json.dumps(
            {
                "type": "response.done",
                "response": {
                    "id": "resp-1",
                    "model": "free-model",
                    "usage": {
                        "input_tokens": 11,
                        "output_tokens": 22,
                        "total_tokens": 33,
                    },
                },
            }
        ),
    ]

    tracker = MagicMock()
    with patch(
        "utils.token_tracker.TokenTracker.get_instance", return_value=tracker
    ):
        try:
            await client.handle_messages()
        finally:
            await client._response_arbiter.shutdown("test teardown")

    assert tracker.record.call_count == 1, (
        "a stale terminal still cost tokens; quarantining the host "
        "finalization must not quarantine the accounting"
    )
    booked = tracker.record.call_args.kwargs
    assert booked["prompt_tokens"] == 11
    assert booked["completion_tokens"] == 22
    assert booked["total_tokens"] == 33


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_current_turns_terminal_is_accounted_for_exactly_once():
    # The dual, both ways: the ordinary path still books its usage, and the
    # shared helper does not let a terminal be counted twice.
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    client = _free_client()
    client.ws = AsyncMock()
    client.ws.__aiter__.return_value = [
        json.dumps({"type": "response.created", "response": {"id": "resp-1"}}),
        json.dumps(
            {
                "type": "response.done",
                "response": {
                    "id": "resp-1",
                    "model": "free-model",
                    "usage": {
                        "input_tokens": 5,
                        "output_tokens": 6,
                        "total_tokens": 11,
                    },
                },
            }
        ),
    ]

    tracker = MagicMock()
    with patch(
        "utils.token_tracker.TokenTracker.get_instance", return_value=tracker
    ):
        try:
            await client.handle_messages()
        finally:
            await client._response_arbiter.shutdown("test teardown")

    assert tracker.record.call_count == 1
    assert tracker.record.call_args.kwargs["total_tokens"] == 11


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_repeated_terminal_is_not_billed_twice():
    # Codex P2, self-inflicted by the previous commit. Recording usage on the
    # stale branch made a REPEATED response.done — which the transport
    # tolerates on purpose without finalizing the turn twice — bill the same
    # turn twice: the first takes the normal branch and clears
    # _current_response_id, so the duplicate necessarily takes the stale one.
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    client = _free_client()
    done = {
        "type": "response.done",
        "response": {
            "id": "resp-1",
            "model": "free-model",
            "usage": {"input_tokens": 7, "output_tokens": 8, "total_tokens": 15},
        },
    }
    client.ws = AsyncMock()
    client.ws.__aiter__.return_value = [
        json.dumps({"type": "response.created", "response": {"id": "resp-1"}}),
        json.dumps(done),
        json.dumps(done),  # the provider repeats it
    ]

    tracker = MagicMock()
    with patch("utils.token_tracker.TokenTracker.get_instance", return_value=tracker):
        try:
            await client.handle_messages()
        finally:
            await client._response_arbiter.shutdown("test teardown")

    assert tracker.record.call_count == 1, (
        "a repeated terminal is the same turn; billing it again overstates "
        "usage for a case the transport supports on purpose"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_two_different_turns_are_billed_separately():
    # The dual: deduplication must key on identity, not collapse everything.
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    def _done(rid: str, total: int) -> str:
        return json.dumps(
            {
                "type": "response.done",
                "response": {
                    "id": rid,
                    "model": "free-model",
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": total - 1,
                        "total_tokens": total,
                    },
                },
            }
        )

    client = _free_client()
    client.ws = AsyncMock()
    client.ws.__aiter__.return_value = [
        json.dumps({"type": "response.created", "response": {"id": "resp-1"}}),
        _done("resp-1", 10),
        json.dumps({"type": "response.created", "response": {"id": "resp-2"}}),
        _done("resp-2", 20),
    ]

    tracker = MagicMock()
    with patch("utils.token_tracker.TokenTracker.get_instance", return_value=tracker):
        try:
            await client.handle_messages()
        finally:
            await client._response_arbiter.shutdown("test teardown")

    assert tracker.record.call_count == 2
    assert [c.kwargs["total_tokens"] for c in tracker.record.call_args_list] == [10, 20]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_abandoned_transcript_is_dropped_rather_than_spoken_as_the_successors():
    # Codex P2, also self-inflicted: inserting the repetition check ahead of
    # the transcript flush gave the flush an await in front of it, so a
    # successor can start before it runs. handle_output_transcript publishes
    # and queues TTS under whatever speech id is CURRENT, so flushing then
    # speaks the abandoned turn's half-sentence as part of the successor's.
    entered = asyncio.Event()
    proceed = asyncio.Event()
    transcripts: list[tuple[str, bool]] = []

    async def _on_repetition() -> None:
        entered.set()
        await proceed.wait()

    async def _on_transcript(text: str, is_first: bool) -> None:
        transcripts.append((text, is_first))

    client = _free_client(
        on_repetition_detected=_on_repetition, on_output_transcript=_on_transcript
    )
    client._repetition_threshold = 0.5
    # Two prior identical turns so the third trips detection and parks there.
    client._recent_responses = ["一样的话", "一样的话"]
    client._current_response_id = "resp-1"
    client._is_responding = True
    client._turn_epoch += 1
    client._current_response_transcript = "一样的话"
    client._output_transcript_buffer = "旧轮没送出的半句"
    client._audio_delta_count = 1

    release = asyncio.create_task(
        client._on_arbiter_stuck_release("abandoned resp-1", "resp-1")
    )
    await asyncio.wait_for(entered.wait(), timeout=1)

    # A successor takes the turn while the repetition callback is parked.
    client._turn_epoch += 1

    proceed.set()
    await asyncio.wait_for(release, timeout=2)

    assert transcripts == [], (
        "the abandoned turn's trailing text must not go out under the "
        "successor's speech id"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_undisturbed_release_still_flushes_its_trailing_transcript():
    # The dual. Dropping it is only correct once a successor exists; a normal
    # release must still deliver what the turn already said, which is the
    # whole point of the fallback flush (audio with no text otherwise).
    transcripts: list[tuple[str, bool]] = []

    async def _on_transcript(text: str, is_first: bool) -> None:
        transcripts.append((text, is_first))

    client = _free_client(on_output_transcript=_on_transcript)
    client._current_response_id = "resp-1"
    client._is_responding = True
    client._turn_epoch += 1
    # What response.created sets, so the flag the host receives is the one a
    # real turn would have produced.
    client._is_first_transcript_chunk = True
    client._output_transcript_buffer = "旧轮没送出的半句"
    client._audio_delta_count = 1

    await client._on_arbiter_stuck_release("abandoned resp-1", "resp-1")

    assert transcripts == [("旧轮没送出的半句", True)]
