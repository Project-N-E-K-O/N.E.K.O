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
"""Native-path regression cover for ``RealtimeResponseArbiter``.

``RealtimeResponseArbiter`` arrived with the independent-ASR work, but it is
NOT gated on it: ``OmniRealtimeClient.__init__`` constructs one
unconditionally, and every realtime response now goes through it --
``create_response``, tool results, proactive chat, Gemini's manual-VAD commit
and interruption. A user who never enables independent ASR is on this code
path for every single turn.

The independent-ASR tests cover the arbiter richly from the ASR side. What
was missing was the other side: that a plain native turn, with no external
voice turns anywhere, still behaves exactly as it did before the queue
existed. The only pre-existing native assertion touching it was LOOSENED to
accommodate the arbiter's new ``event_id`` stamp, and the proactive-chat
integration tests were not touched at all.

These tests therefore assert the boring things on purpose: the event reaches
the socket, in order, once, with nothing else added; the ticket resolves; the
lane is released; and none of the new failure machinery (idle wait, ack
barrier, done timeout, transport fail-close) fires on a turn that is simply
working. A regression in any of those would be invisible to the ASR suites
and would hit every user.
"""

import asyncio
import json
import logging

import pytest

from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_realtime_client import _response_arbiter as _arbiter_module
from main_logic.omni_realtime_client._response_arbiter import RealtimeResponseArbiter
from main_logic.tool_calling import ToolResult


def _native_client(api_type: str = "qwen", model: str = "qwen-omni-turbo-realtime"):
    """A client with no independent ASR anywhere in the picture."""

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model=model,
        api_type=api_type,
    )
    return client


class _RecordingSocket:
    """Socket double that records what the arbiter actually put on the wire.

    Also plays the server side: ``feed()`` pushes an event that
    ``handle_messages()`` will read out of its ``async for``, and ``finish()``
    ends the loop. That is what lets a test drive one whole native turn
    through the REAL receive loop rather than poking the arbiter's notify_*
    methods directly.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self._inbound: asyncio.Queue = asyncio.Queue()

    async def send(self, payload) -> None:
        self.sent.append(json.loads(payload) if isinstance(payload, str) else payload)

    async def close(self) -> None:
        self.closed = True

    @property
    def types(self) -> list[str]:
        return [event.get("type") for event in self.sent]

    def feed(self, event: dict) -> None:
        self._inbound.put_nowait(json.dumps(event))

    def finish(self) -> None:
        self._inbound.put_nowait(None)

    async def __aiter__(self):
        while True:
            message = await self._inbound.get()
            if message is None:
                return
            yield message


async def _settle(times: int = 50) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


def _complete_turn(arbiter: RealtimeResponseArbiter, response_id: str) -> None:
    """Drive the server side of one successful response."""

    arbiter.notify_response_created({"type": "response.created", "response": {"id": response_id}})
    arbiter.notify_response_terminal({"type": "response.done", "response": {"id": response_id}})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_plain_native_response_reaches_the_socket_unchanged():
    # The baseline every other test here builds on: one create_response, one
    # response.create on the wire, nothing else bolted on but the event id.
    sent: list[dict] = []

    async def _send(event: dict) -> None:
        sent.append(event)

    arbiter = RealtimeResponseArbiter(_send)
    ticket = await arbiter.enqueue(source="native")
    await asyncio.wait_for(ticket.sent, timeout=1)

    assert [event["type"] for event in sent] == ["response.create"]
    # An event_id is the ONLY addition the arbiter is allowed to make.
    assert set(sent[0]) <= {"type", "event_id"}

    _complete_turn(arbiter, "resp-1")
    await asyncio.wait_for(ticket.done, timeout=1)
    await arbiter.wait_until_idle(timeout=1)
    assert arbiter.is_busy is False
    assert arbiter.current_source is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_native_turns_serialize_in_submission_order():
    # Lane serialization is new. A native user firing two turns back to back
    # must see them in order, and the second must not be dispatched while the
    # first is still live -- previously both went straight to the socket.
    sent: list[dict] = []

    async def _send(event: dict) -> None:
        sent.append(event)

    arbiter = RealtimeResponseArbiter(_send)
    first = await arbiter.enqueue(
        source="native-1",
        response_event={"type": "response.create", "marker": 1},
    )
    await asyncio.wait_for(first.sent, timeout=1)

    second_task = asyncio.create_task(
        arbiter.enqueue(
            source="native-2",
            response_event={"type": "response.create", "marker": 2},
        )
    )
    await _settle()
    # The first turn is still live, so the second has NOT reached the wire.
    assert [event.get("marker") for event in sent] == [1]

    _complete_turn(arbiter, "resp-1")
    second = await asyncio.wait_for(second_task, timeout=1)
    await asyncio.wait_for(second.sent, timeout=1)
    assert [event.get("marker") for event in sent] == [1, 2]

    _complete_turn(arbiter, "resp-2")
    await asyncio.wait_for(second.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_working_native_turn_never_aborts_the_transport():
    # _fail_closed() physically closes the websocket and latches
    # _fatal_error_occurred. Before this PR no client-side timer could kill a
    # realtime socket at all, so a spurious fire is a new and total failure
    # mode for every native user. A turn that completes normally must not
    # arm it.
    aborted: list[str] = []

    async def _send(event: dict) -> None:
        return None

    async def _abort(reason: str) -> None:
        aborted.append(reason)

    arbiter = RealtimeResponseArbiter(_send, abort_transport=_abort)
    ticket = await arbiter.enqueue(source="native")
    await asyncio.wait_for(ticket.sent, timeout=1)
    _complete_turn(arbiter, "resp-1")
    await asyncio.wait_for(ticket.done, timeout=1)
    await arbiter.wait_until_idle(timeout=1)
    await _settle()

    assert aborted == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_server_vad_turn_releases_the_lane_for_queued_native_work():
    # notify_response_created fires for EVERY response.created, including a
    # pure server-VAD turn the client never asked for -- that is the normal
    # shape of a native voice turn. It clears the idle gate, so anything
    # queued behind it (tool results, proactive chat, prime_context) waits on
    # a turn the arbiter does not own. The release path has to work, or that
    # queued work hangs forever.
    sent: list[dict] = []

    async def _send(event: dict) -> None:
        sent.append(event)

    arbiter = RealtimeResponseArbiter(_send)
    # A server-initiated turn: created without any enqueue behind it.
    arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "server-turn"}}
    )

    queued_task = asyncio.create_task(arbiter.enqueue(source="native-after-vad"))
    await _settle()
    assert sent == [], "queued work must wait behind the live server turn"

    arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "server-turn"}}
    )
    ticket = await asyncio.wait_for(queued_task, timeout=1)
    await asyncio.wait_for(ticket.sent, timeout=1)
    assert [event["type"] for event in sent] == ["response.create"]

    _complete_turn(arbiter, "resp-after")
    await asyncio.wait_for(ticket.done, timeout=1)
    await arbiter.wait_until_idle(timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_interruption_clears_the_lane_for_the_next_native_turn():
    # Interruption routes through cancel_current() now. If it fails to release
    # the lane, the NEXT native turn never dispatches -- the user speaks and
    # she never answers again for the rest of the session.
    sent: list[dict] = []

    async def _send(event: dict) -> None:
        sent.append(event)

    arbiter = RealtimeResponseArbiter(_send)
    ticket = await arbiter.enqueue(source="native")
    await asyncio.wait_for(ticket.sent, timeout=1)
    arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-1"}}
    )

    # cancel_current blocks until the terminal event lands, so the server side
    # has to be driven concurrently -- exactly how the real receive loop does it.
    cancel_task = asyncio.create_task(arbiter.cancel_current(timeout=1))
    await _settle()
    # CodeRabbit: assert the cancel actually reached the wire BEFORE injecting
    # the terminal. Injecting response.cancelled by hand and only checking that
    # the lane reopened would stay green with the response.cancel send deleted
    # -- the barge-in would silently stop telling the server to stop talking,
    # and she would keep speaking over the user.
    assert [event["type"] for event in sent] == [
        "response.create",
        "response.cancel",
    ]
    arbiter.notify_response_terminal(
        {"type": "response.cancelled", "response": {"id": "resp-1"}}
    )
    await asyncio.wait_for(cancel_task, timeout=1)
    await arbiter.wait_until_idle(timeout=1)

    follow_up = await arbiter.enqueue(source="native-next")
    await asyncio.wait_for(follow_up.sent, timeout=1)
    assert sent[-1]["type"] == "response.create"
    _complete_turn(arbiter, "resp-2")
    await asyncio.wait_for(follow_up.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconnect_restores_dispatch_for_a_native_client():
    # A dropped realtime connection fails everything in flight; the arbiter
    # must then accept work again after reset_connection_state(), or a native
    # user's reconnect leaves a permanently mute character.
    sent: list[dict] = []

    async def _send(event: dict) -> None:
        sent.append(event)

    arbiter = RealtimeResponseArbiter(_send)
    arbiter.notify_connection_lost("socket closed")

    dead = await arbiter.enqueue(source="native-during-outage")
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(dead.sent, timeout=1)

    arbiter.reset_connection_state()
    revived = await arbiter.enqueue(source="native-after-reconnect")
    await asyncio.wait_for(revived.sent, timeout=1)
    assert [event["type"] for event in sent] == ["response.create"]
    _complete_turn(arbiter, "resp-1")
    await asyncio.wait_for(revived.done, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_client_wires_an_arbiter_for_every_native_user():
    # The premise of this whole file: the arbiter is not opt-in. If it ever
    # becomes gated, these tests would pass vacuously against a bypass.
    client = _native_client()
    assert isinstance(client._response_arbiter, RealtimeResponseArbiter)
    assert client._ensure_response_arbiter() is client._response_arbiter


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_native_create_response_runs_through_the_arbiter_end_to_end():
    # CodeRabbit: holding an arbiter reference proves nothing. If
    # create_response ever wrote to the socket directly, every test above
    # would stay green because they all drive the arbiter by hand.
    #
    # So drive the REAL entry point against a recording socket and the REAL
    # receive loop, and assert the two things only an arbitrated path can
    # produce: the item-ack barrier (response.create is withheld until the
    # server acknowledges the conversation item) and lane serialization (a
    # second create_response puts nothing on the wire while the first is
    # still live). A direct-to-socket bypass fails both.
    client = _native_client()
    socket = _RecordingSocket()
    client.ws = socket
    receive_loop = asyncio.create_task(client.handle_messages())

    first = asyncio.create_task(client.create_response("hello"))
    await _settle()

    # Barrier: the item is out, the response.create is NOT.
    assert socket.types == ["conversation.item.create"]
    item_id = socket.sent[0]["item"]["id"]

    socket.feed(
        {
            "type": "conversation.item.created",
            "item": {"id": item_id, "role": "user", "type": "message"},
        }
    )
    await _settle()
    assert socket.types == ["conversation.item.create", "response.create"]
    await asyncio.wait_for(first, timeout=1)

    socket.feed({"type": "response.created", "response": {"id": "resp-native-1"}})
    await _settle()
    arbiter = client._response_arbiter
    assert arbiter.is_busy is True

    # Serialization: a second turn submitted while the first is live must not
    # reach the wire at all.
    second = asyncio.create_task(client.create_response("and again"))
    await _settle()
    assert socket.types == ["conversation.item.create", "response.create"], (
        "a second native turn must queue behind the live one, not bypass the lane"
    )

    socket.feed({"type": "response.done", "response": {"id": "resp-native-1"}})
    await _settle()
    # The lane reopened, so the queued turn dispatched its own item.
    assert socket.types[2] == "conversation.item.create"
    second_item_id = socket.sent[2]["item"]["id"]
    assert second_item_id != item_id

    socket.feed(
        {
            "type": "conversation.item.created",
            "item": {"id": second_item_id, "role": "user", "type": "message"},
        }
    )
    await _settle()
    await asyncio.wait_for(second, timeout=1)
    assert socket.types == [
        "conversation.item.create",
        "response.create",
        "conversation.item.create",
        "response.create",
    ]

    socket.feed({"type": "response.created", "response": {"id": "resp-native-2"}})
    socket.feed({"type": "response.done", "response": {"id": "resp-native-2"}})
    await _settle()
    await arbiter.wait_until_idle(timeout=1)
    assert arbiter.is_busy is False
    assert arbiter.current_source is None

    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


def _wired_client(api_type: str = "qwen", model: str = "qwen-omni-turbo-realtime"):
    """A native client attached to a recording socket, ready for its real
    receive loop. The caller owns creating/joining the ``handle_messages``
    task so a failed assertion cannot leak an unawaited loop silently."""

    client = _native_client(api_type=api_type, model=model)
    socket = _RecordingSocket()
    client.ws = socket
    return client, socket


async def _finish_loop(socket: _RecordingSocket, receive_loop: asyncio.Task) -> None:
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


async def _ack_item_and_expect_create(socket: _RecordingSocket, index: int) -> None:
    """Acknowledge the conversation item at ``socket.sent[index]`` and wait for
    the arbiter to put the paired response.create on the wire."""

    item_id = socket.sent[index]["item"]["id"]
    socket.feed(
        {
            "type": "conversation.item.created",
            "item": {"id": item_id, "role": "user", "type": "message"},
        }
    )
    await _settle()
    assert socket.types[index + 1] == "response.create"


# ---------------------------------------------------------------------------
# T1 — the server-VAD lane close every native voice turn walks (speech_stopped
# → notify_server_vad_response_pending → arm backstop). Until now the two-phase
# call had zero native-side assertions: arm_server_vad_response_pending_timeout
# had no test anywhere in the repo.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_speech_preempts_an_inflight_native_request():
    # The contract, stated once: the user's voice always wins. An explicit
    # native request that has not finished dispatching when the user's
    # utterance ends must be failed promptly — not raced against the server's
    # automatic response.
    client, socket = _wired_client()
    receive_loop = asyncio.create_task(client.handle_messages())

    create = asyncio.create_task(client.create_response("hello"))
    await _settle()
    # Parked on the item-ack barrier: item out, response.create withheld.
    assert socket.types == ["conversation.item.create"]

    socket.feed({"type": "input_audio_buffer.speech_stopped"})
    await _settle()

    assert create.done(), (
        "an utterance ending must fail the in-flight explicit request "
        "immediately, not leave it racing the automatic VAD response"
    )
    exc = create.exception()
    assert isinstance(exc, RuntimeError)
    assert "pending server VAD response" in str(exc)
    assert "response.create" not in socket.types, (
        "the losing explicit request must never emit its response.create"
    )

    # The automatic response runs its own lifecycle; afterwards the lane must
    # be clean for the next native turn — no residue from the failed request.
    socket.feed({"type": "response.created", "response": {"id": "vad-1"}})
    socket.feed({"type": "response.done", "response": {"id": "vad-1"}})
    await _settle()

    follow_up = asyncio.create_task(client.create_response("next turn"))
    await _settle()
    assert socket.types[-1] == "conversation.item.create"
    await _ack_item_and_expect_create(socket, len(socket.sent) - 1)
    socket.feed({"type": "response.created", "response": {"id": "resp-2"}})
    socket.feed({"type": "response.done", "response": {"id": "resp-2"}})
    await _settle()
    await asyncio.wait_for(follow_up, timeout=1)
    await client._response_arbiter.wait_until_idle(timeout=1)
    assert client.is_active_response() is False
    assert socket.closed is False

    await _finish_loop(socket, receive_loop)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_created_backstop_arms_only_after_receive_handling_resumes():
    # _transport feeds the arbiter in two phases on speech_stopped: mark the
    # pending VAD response BEFORE the on_new_message callback, arm the
    # missing-created backstop AFTER it (in a finally). The comment in
    # _transport.py explains why: response.created cannot be observed while
    # the receive loop is blocked inside the callback, so arming earlier would
    # let a slow callback expire a perfectly real VAD response. This is the
    # first test to pin that ordering.
    client, socket = _wired_client()
    observations: list[tuple[bool, bool]] = []

    async def _on_new_message() -> None:
        arbiter = client._response_arbiter
        observations.append(
            (
                arbiter._server_vad_response_pending,
                arbiter._server_vad_pending_handle is not None,
            )
        )

    client.on_new_message = _on_new_message
    receive_loop = asyncio.create_task(client.handle_messages())

    socket.feed({"type": "input_audio_buffer.speech_stopped"})
    await _settle()

    # Inside the callback: pending already marked, backstop NOT yet armed.
    assert observations == [(True, False)]
    # After the callback returned: the finally armed the backstop.
    arbiter = client._response_arbiter
    assert arbiter._server_vad_pending_handle is not None

    # The real VAD response arriving disarms the backstop and takes the lane.
    socket.feed({"type": "response.created", "response": {"id": "vad-1"}})
    await _settle()
    assert arbiter._server_vad_pending_handle is None
    socket.feed({"type": "response.done", "response": {"id": "vad-1"}})
    await _settle()
    await arbiter.wait_until_idle(timeout=1)

    await _finish_loop(socket, receive_loop)


# ---------------------------------------------------------------------------
# T3 — the missing-created backstop itself (_SERVER_VAD_RESPONSE_STARTED_
# TIMEOUT). Zero hits in tests/ before this: if it silently stopped firing,
# a provider-side pre-creation failure would wedge every queued native turn;
# if it ever tore the transport instead, a benign expiry would disconnect the
# user.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vad_backstop_expiry_reopens_the_lane_without_tearing_the_transport(
    monkeypatch,
):
    monkeypatch.setattr(_arbiter_module, "_SERVER_VAD_RESPONSE_STARTED_TIMEOUT", 0.05)
    client, socket = _wired_client()
    receive_loop = asyncio.create_task(client.handle_messages())

    socket.feed({"type": "input_audio_buffer.speech_stopped"})
    await _settle()

    queued = asyncio.create_task(
        client.create_response("queued behind a response that never starts")
    )
    await _settle()
    assert socket.types == [], (
        "queued work must hold while the announced VAD response is pending"
    )

    # No response.created ever arrives. The backstop must reopen the lane…
    await asyncio.sleep(0.2)
    await _settle()
    assert socket.types == ["conversation.item.create"], (
        "backstop expiry must release the lane so queued native work dispatches"
    )
    # …and it must do so by releasing, never by tearing the connection down.
    assert socket.closed is False

    await _ack_item_and_expect_create(socket, 0)
    socket.feed({"type": "response.created", "response": {"id": "resp-1"}})
    socket.feed({"type": "response.done", "response": {"id": "resp-1"}})
    await _settle()
    await asyncio.wait_for(queued, timeout=1)
    await client._response_arbiter.wait_until_idle(timeout=1)
    assert client.is_active_response() is False

    await _finish_loop(socket, receive_loop)


# ---------------------------------------------------------------------------
# T4 — the real native barge-in. Everywhere else in the suite
# handle_interruption is an AsyncMock; here the actual transport handler runs:
# speech_started → handle_interruption → a bare response.cancel on the wire
# (native interruption does NOT route through arbiter.cancel_current — that
# API's only production caller is the independent-ASR prepare path).
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_native_barge_in_cancels_on_the_wire_and_the_cancelled_done_still_settles():
    client, socket = _wired_client()
    done_callbacks: list[bool] = []

    async def _on_done() -> None:
        done_callbacks.append(True)

    client.on_response_done = _on_done
    receive_loop = asyncio.create_task(client.handle_messages())

    create = asyncio.create_task(client.create_response("hello"))
    await _settle()
    await _ack_item_and_expect_create(socket, 0)
    await asyncio.wait_for(create, timeout=1)
    socket.feed({"type": "response.created", "response": {"id": "resp-1"}})
    await _settle()
    assert client._is_responding is True
    done_total_before = client._response_done_total

    # The user starts speaking over her.
    socket.feed({"type": "input_audio_buffer.speech_started"})
    await _settle()
    assert socket.types[-1] == "response.cancel", (
        "a native barge-in must physically tell the server to stop talking"
    )
    assert client._is_responding is False
    # #2345 deliberately KEEPS the cancelled response identity so the terminal
    # below is processed as this turn's done rather than dropped stale.
    assert client._current_response_id == "resp-1"

    socket.feed(
        {"type": "response.done", "response": {"id": "resp-1", "status": "cancelled"}}
    )
    await _settle()
    # The cancelled turn's terminal must take the NON-stale path: counters,
    # the on_response_done callback and the _interrupted reset all belong to
    # this turn. (The stale filter would still forward the terminal to the
    # arbiter, so lane release alone cannot distinguish the two paths — these
    # two assertions can.)
    assert client._response_done_total == done_total_before + 1
    assert done_callbacks, (
        "on_response_done must fire for the cancelled turn's terminal"
    )
    assert client._interrupted is False

    # And the lane is open for the follow-up turn.
    follow_up = asyncio.create_task(client.create_response("next"))
    await _settle()
    assert socket.types[-1] == "conversation.item.create"
    await _ack_item_and_expect_create(socket, len(socket.sent) - 1)
    socket.feed({"type": "response.created", "response": {"id": "resp-2"}})
    socket.feed({"type": "response.done", "response": {"id": "resp-2"}})
    await _settle()
    await asyncio.wait_for(follow_up, timeout=1)
    await client._response_arbiter.wait_until_idle(timeout=1)
    assert socket.closed is False

    await _finish_loop(socket, receive_loop)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_filtered_terminal_still_reaches_the_arbiter_and_frees_the_lane():
    # Crossed lifecycles: a newer response.created becomes current before the
    # owner's terminal arrives, so the transport's stale filter classifies
    # that terminal as stale. The filter must still forward it to the arbiter
    # (the explicitly-commented branch in handle_messages) or the owner holds
    # the lane until its 60s staleness bound — every queued native turn hangs.
    client, socket = _wired_client()
    receive_loop = asyncio.create_task(client.handle_messages())

    create = asyncio.create_task(client.create_response("hello"))
    await _settle()
    await _ack_item_and_expect_create(socket, 0)
    await asyncio.wait_for(create, timeout=1)
    socket.feed({"type": "response.created", "response": {"id": "resp-a"}})
    await _settle()

    # A server-initiated response crosses in before resp-a's terminal.
    socket.feed({"type": "response.created", "response": {"id": "resp-b"}})
    await _settle()
    assert client._current_response_id == "resp-b"

    # resp-a's terminal is now stale by id — but it must free resp-a's owner.
    socket.feed({"type": "response.done", "response": {"id": "resp-a"}})
    socket.feed({"type": "response.done", "response": {"id": "resp-b"}})
    await _settle()

    follow_up = asyncio.create_task(client.create_response("next"))
    await _settle()
    assert socket.types[-1] == "conversation.item.create", (
        "both terminals delivered means the lane must be open again"
    )
    await _ack_item_and_expect_create(socket, len(socket.sent) - 1)
    socket.feed({"type": "response.created", "response": {"id": "resp-c"}})
    socket.feed({"type": "response.done", "response": {"id": "resp-c"}})
    await _settle()
    await asyncio.wait_for(follow_up, timeout=1)
    await client._response_arbiter.wait_until_idle(timeout=1)

    await _finish_loop(socket, receive_loop)


# ---------------------------------------------------------------------------
# T5 — tool results. The existing tool-calling tests drive a __new__-built
# client with no receive loop and only count wire events, which stays green
# under a direct-send bypass; these run the real path.
# ---------------------------------------------------------------------------


def _tool_result(call_id: str = "call-1") -> ToolResult:
    return ToolResult(call_id=call_id, name="get_weather", output={"ok": True})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_consecutive_tool_results_serialize_on_the_wire():
    client, socket = _wired_client()
    receive_loop = asyncio.create_task(client.handle_messages())

    first = asyncio.create_task(client._send_tool_result_openai_realtime(_tool_result()))
    await _settle()
    # No ack barrier on tool results: item and response.create go out together.
    assert socket.types == ["conversation.item.create", "response.create"]
    assert socket.sent[0]["item"]["type"] == "function_call_output"
    await asyncio.wait_for(first, timeout=1)
    socket.feed({"type": "response.created", "response": {"id": "resp-t1"}})
    await _settle()

    second = asyncio.create_task(
        client._send_tool_result_openai_realtime(_tool_result("call-2"))
    )
    await _settle()
    assert len(socket.sent) == 2, (
        "the second tool result must not put a single byte on the wire while "
        "the first tool response is still live"
    )

    socket.feed({"type": "response.done", "response": {"id": "resp-t1"}})
    await _settle()
    assert socket.types == [
        "conversation.item.create",
        "response.create",
        "conversation.item.create",
        "response.create",
    ]
    await asyncio.wait_for(second, timeout=1)
    socket.feed({"type": "response.created", "response": {"id": "resp-t2"}})
    socket.feed({"type": "response.done", "response": {"id": "resp-t2"}})
    await _settle()
    await client._response_arbiter.wait_until_idle(timeout=1)
    assert socket.closed is False

    await _finish_loop(socket, receive_loop)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_tool_result_outranks_earlier_queued_injected_text():
    # priority=5 in _tools.py is the repo's only priority-5 enqueue. Its
    # meaning: a tool result (the model is mid-conversation waiting on it)
    # beats queued priority-10 text injection even when the text was
    # submitted first. Both are enqueued before the worker selects, which is
    # exactly the state _next_queued's priority ordering exists for.
    client, socket = _wired_client()
    receive_loop = asyncio.create_task(client.handle_messages())

    text_task = asyncio.create_task(client.create_response("catch-up text"))
    tool_task = asyncio.create_task(
        client._send_tool_result_openai_realtime(_tool_result())
    )
    await _settle()

    assert socket.sent[0]["item"]["type"] == "function_call_output", (
        "the tool result (priority 5) must be selected over the "
        "earlier-queued injected text (priority 10)"
    )
    assert socket.types[:2] == ["conversation.item.create", "response.create"]
    await asyncio.wait_for(tool_task, timeout=1)
    socket.feed({"type": "response.created", "response": {"id": "resp-t1"}})
    socket.feed({"type": "response.done", "response": {"id": "resp-t1"}})
    await _settle()

    # Now the text dispatches, behind its ack barrier.
    assert socket.types[2] == "conversation.item.create"
    assert socket.sent[2]["item"]["type"] == "message"
    await _ack_item_and_expect_create(socket, 2)
    socket.feed({"type": "response.created", "response": {"id": "resp-2"}})
    socket.feed({"type": "response.done", "response": {"id": "resp-2"}})
    await _settle()
    await asyncio.wait_for(text_task, timeout=1)
    await client._response_arbiter.wait_until_idle(timeout=1)

    await _finish_loop(socket, receive_loop)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_async_tool_rejection_frees_the_lane_without_tearing_the_transport():
    # Pre-#2345 a rejected tool response.create had no observer at all (not
    # even on_rejected): the turn just died silently. Now the error event is
    # routed to the owning ticket via notify_error, which must free the lane
    # promptly — not leave the next turn waiting for the 5s started-timeout,
    # and never escalate a routed rejection into a transport teardown.
    client, socket = _wired_client()
    receive_loop = asyncio.create_task(client.handle_messages())

    tool_task = asyncio.create_task(
        client._send_tool_result_openai_realtime(_tool_result())
    )
    await _settle()
    assert socket.types == ["conversation.item.create", "response.create"]
    await asyncio.wait_for(tool_task, timeout=1)

    create_event_id = socket.sent[1]["event_id"]
    socket.feed(
        {
            "type": "error",
            "error": {
                "message": "response.create rejected by provider",
                "event_id": create_event_id,
            },
        }
    )
    await _settle()

    # The lane must be free NOW — a follow-up dispatches within a settle, not
    # after the started-timeout backstop.
    follow_up = asyncio.create_task(client.create_response("still alive"))
    await _settle()
    assert socket.types[-1] == "conversation.item.create", (
        "a routed rejection must free the lane immediately"
    )
    assert socket.closed is False, (
        "a routed rejection is not a transport failure"
    )
    await _ack_item_and_expect_create(socket, len(socket.sent) - 1)
    socket.feed({"type": "response.created", "response": {"id": "resp-2"}})
    socket.feed({"type": "response.done", "response": {"id": "resp-2"}})
    await _settle()
    await asyncio.wait_for(follow_up, timeout=1)
    await client._response_arbiter.wait_until_idle(timeout=1)

    await _finish_loop(socket, receive_loop)


# ---------------------------------------------------------------------------
# T2 — hot-swap prime_context(skipped=False). Its production trigger is the
# final swap sequence in main_logic/core/lifecycle.py; every pre-existing
# prime_context test used skipped=True or Gemini, neither of which enters the
# arbiter. NOTE the model here must be non-qwen: _responses.py carves qwen out
# of the create_response path (update_session instead), so a qwen client would
# make this test pass without touching the queue.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hot_swap_prime_runs_the_item_ack_barrier():
    client, socket = _wired_client(api_type="free", model="free-model")
    receive_loop = asyncio.create_task(client.handle_messages())

    prime = asyncio.create_task(
        client.prime_context("[context] the task finished", skipped=False)
    )
    await _settle()
    assert socket.types == ["conversation.item.create"], (
        "prime must persist the context item and wait for its ack before "
        "requesting the announce response"
    )
    await _ack_item_and_expect_create(socket, 0)
    socket.feed({"type": "response.created", "response": {"id": "resp-prime"}})
    socket.feed({"type": "response.done", "response": {"id": "resp-prime"}})
    await _settle()
    await asyncio.wait_for(prime, timeout=1)
    await client._response_arbiter.wait_until_idle(timeout=1)

    await _finish_loop(socket, receive_loop)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prime_dispatch_failure_surfaces_a_type_the_swap_must_catch():
    # The final swap sequence wraps prime_context in a TARGETED except whose
    # job is "abandon this swap, keep the old session". A dispatch failure
    # surfaces from the arbiter ticket as RuntimeError (or ConnectionError /
    # TimeoutError) — none of which is websockets' ConnectionClosed or
    # AttributeError. This pins the surfaced type family so the lifecycle
    # handler's tuple visibly must include it (see the paired test in
    # test_hot_swap_cancellation.py for the handler side).
    from websockets import exceptions as web_exceptions

    client, socket = _wired_client(api_type="free", model="free-model")
    receive_loop = asyncio.create_task(client.handle_messages())

    prime = asyncio.create_task(
        client.prime_context("[context] the task finished", skipped=False)
    )
    await _settle()
    assert socket.types == ["conversation.item.create"]

    item_event_id = socket.sent[0]["event_id"]
    socket.feed(
        {
            "type": "error",
            "error": {"message": "invalid item payload", "event_id": item_event_id},
        }
    )
    await _settle()

    assert prime.done(), "a routed rejection must fail the prime promptly"
    exc = prime.exception()
    assert isinstance(exc, RuntimeError)
    assert not isinstance(exc, (web_exceptions.ConnectionClosed, AttributeError)), (
        "the surfaced dispatch failure is NOT in the swap handler's legacy "
        "except tuple — the handler must list these types explicitly"
    )

    await client._response_arbiter.wait_until_idle(timeout=1)
    await _finish_loop(socket, receive_loop)


# ---------------------------------------------------------------------------
# T8 — the protect-her-speech contract, stated directly: program-initiated
# text (proactive chat) queues behind a live response instead of preempting
# it. Until now this held only implicitly through lane semantics; no test
# asserted it as a contract.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_queued_proactive_text_never_preempts_a_live_response():
    client, socket = _wired_client()
    receive_loop = asyncio.create_task(client.handle_messages())

    # She is mid-answer: a live (server-side) response holds the lane.
    socket.feed({"type": "response.created", "response": {"id": "live-1"}})
    await _settle()
    assert client.is_active_response() is True

    inject = asyncio.create_task(
        client.inject_text_and_request_response("proactive nudge")
    )
    await _settle()
    assert socket.types == [], (
        "not a single byte of the proactive inject may reach the wire while "
        "she is still speaking"
    )

    socket.feed({"type": "response.done", "response": {"id": "live-1"}})
    await _settle()
    assert socket.types[0] == "conversation.item.create", (
        "the queued inject dispatches only after the live response finished"
    )
    await _ack_item_and_expect_create(socket, 0)
    socket.feed({"type": "response.created", "response": {"id": "resp-inject"}})
    socket.feed({"type": "response.done", "response": {"id": "resp-inject"}})
    await _settle()
    ticket = await asyncio.wait_for(inject, timeout=1)
    assert ticket is not None
    await client._response_arbiter.wait_until_idle(timeout=1)
    assert client.is_active_response() is False

    await _finish_loop(socket, receive_loop)


# ---------------------------------------------------------------------------
# T7 — the fail-close chokepoint. All six _fail_closed call sites tear the
# transport down through one function; that function now logs the initiator,
# the reason and the lane state. Issue #2561 is the motivating incident: an
# unattributable disconnect had to be ruled out via build provenance because
# exactly this log line did not exist.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fail_close_logs_initiator_reason_and_lane_state(caplog):
    aborted: list[str] = []

    async def _send(event: dict) -> None:
        return None

    async def _abort(reason: str) -> None:
        aborted.append(reason)

    arbiter = RealtimeResponseArbiter(_send, abort_transport=_abort)
    ticket = await arbiter.enqueue(source="native")
    await asyncio.wait_for(ticket.sent, timeout=1)
    arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-1"}}
    )

    with caplog.at_level(
        logging.WARNING, logger="main_logic.omni_realtime_client._response_arbiter"
    ):
        # No terminal ever arrives for the cancelled response; cancel_current
        # fails closed and re-raises the timeout to its caller.
        with pytest.raises(asyncio.TimeoutError):
            await arbiter.cancel_current(timeout=0.05)

    assert aborted, "the cancel-terminal timeout must fail closed"
    fail_close_logs = [
        record.getMessage()
        for record in caplog.records
        if "failing closed" in record.getMessage()
    ]
    assert fail_close_logs, (
        "the fail-close chokepoint must log BEFORE tearing the transport, or "
        "a field disconnect cannot be attributed to the arbiter at all"
    )
    message = fail_close_logs[0]
    assert "response cancellation terminal event timed out" in message
    assert "queue_depth" in message
    assert "owner=native" in message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_whose_terminal_arrives_in_time_never_fails_closed(caplog):
    # The dual of the test above: the same cancel path with a timely terminal
    # must neither abort the transport nor emit the fail-close log line.
    aborted: list[str] = []

    async def _send(event: dict) -> None:
        return None

    async def _abort(reason: str) -> None:
        aborted.append(reason)

    arbiter = RealtimeResponseArbiter(_send, abort_transport=_abort)
    ticket = await arbiter.enqueue(source="native")
    await asyncio.wait_for(ticket.sent, timeout=1)
    arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-1"}}
    )

    with caplog.at_level(
        logging.WARNING, logger="main_logic.omni_realtime_client._response_arbiter"
    ):
        cancel_task = asyncio.create_task(arbiter.cancel_current(timeout=1))
        await _settle()
        arbiter.notify_response_terminal(
            {"type": "response.cancelled", "response": {"id": "resp-1"}}
        )
        await asyncio.wait_for(cancel_task, timeout=1)

    assert aborted == []
    assert not any(
        "failing closed" in record.getMessage() for record in caplog.records
    )
    await arbiter.wait_until_idle(timeout=1)
