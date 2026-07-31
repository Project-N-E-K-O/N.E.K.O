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
    tell whose events are whose. Four things falsify it, each with a paired
    case here so "always stand down" cannot pass for correct:

    - the queue consumer is suspended inside a transport write
    - a transport write just failed
    - the abandoned response has no id to attribute its later events by
    - an announced server-VAD response has no id yet

3.  **A released turn is ended, not merely dropped.** The host runs the same
    end-of-turn work its terminal event drives — because it is the same
    implementation, not a second one.

4.  **The host is told before the lane opens.** Ending the turn has to finish
    before the next request can start one, or the next turn gets its speech id
    rotated and its shared turn state finalized underneath it.

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
async def test_the_release_ends_the_turn_before_the_lane_opens(make_harness):
    # Ordering, not just occurrence. If the lane opened first, the next
    # request could dispatch while the host was still closing the previous
    # turn — and end up with that turn's speech id rotated underneath it.
    lane_open_at_notify: list[bool] = []

    async def _on_release(reason: str) -> None:
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

    async def _never_returns(reason: str) -> None:
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
