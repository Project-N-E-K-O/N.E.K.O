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
import re
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_realtime_client._response_arbiter import RealtimeResponseArbiter


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


@pytest.mark.unit
def test_the_python_pin_that_this_arbiter_depends_on_stays_gated():
    # ``requires-python = "==3.11.*"`` is not a routine bump: the arbiter's
    # resume -> await -> re-pause hand-off relies on 3.11 task-yield ordering
    # and #2516 is still open. The pyproject comment says raising it is a
    # "gated change" -- this is the gate, so that claim is true rather than
    # aspirational.
    #
    # CodeRabbit asked instead for a `sys.version_info >= (3, 12): sys.exit()`
    # kill switch in launcher.py / launcher_core / the main_server entry
    # points. Declined here on blast radius: #2516 is a response-ORDERING
    # defect (proactive chat can jump ahead of user speech), not a crash, and
    # hard-exiting every entry point would brick the whole application for
    # anyone already running 3.12 over a bug most of them will never hit.
    # Shipping that is a release decision, not a review fix. Widening the pin
    # is what needs stopping, and that is what this stops.
    pyproject = _REPO_ROOT / "pyproject.toml"
    with pyproject.open("rb") as handle:
        config = tomllib.load(handle)

    assert config["project"]["requires-python"] == "==3.11.*", (
        "requires-python was widened while #2516 (RealtimeResponseArbiter "
        "dispatch ordering on 3.12+) is still open. Fix the dequeue gate "
        "first -- see the comment above requires-python in pyproject.toml."
    )


@pytest.mark.unit
def test_every_runtime_that_actually_executes_this_code_is_pinned_to_311():
    # CodeRabbit, correctly: the assertion above guards the PIN, not the
    # INTERPRETER. requires-python only binds at install resolution, so
    # `python3.12 launcher.py`, a CI matrix bump, or a Docker base image that
    # quietly moves on would all hit #2516 without touching a character of
    # pyproject.toml -- a paper gate.
    #
    # Auto-discovered rather than a hand-kept list: a new workflow, or a new
    # docker/Dockerfile.*, is covered the moment it is added. A hardcoded
    # roster is the failure mode this whole class of gate keeps dying of.
    offenders: list[str] = []

    version_decl = re.compile(r"^\s*(python-version|PYTHON_VERSION):\s*(\S.*?)\s*$")
    workflows = sorted((_REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "no workflows found -- the discovery glob is broken, not the pin"
    for workflow in workflows:
        for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            match = version_decl.match(line)
            if not match:
                continue
            value = match.group(2).strip().strip("'\"")
            # An `${{ env.PYTHON_VERSION }}` indirection is fine: the
            # PYTHON_VERSION declaration it resolves to is itself matched here.
            if value == "3.11" or value.startswith("${{"):
                continue
            offenders.append(f"{workflow.name}:{number} -> {value}")

    dockerfiles = sorted((_REPO_ROOT / "docker").glob("Dockerfile*"))
    assert dockerfiles, "no Dockerfiles found -- the discovery glob is broken, not the pin"
    for dockerfile in dockerfiles:
        minors = set(re.findall(r"python3\.(\d+)", dockerfile.read_text(encoding="utf-8")))
        stray = minors - {"11"}
        if stray:
            offenders.append(f"{dockerfile.name} -> python3.{', python3.'.join(sorted(stray))}")

    assert not offenders, (
        "a runtime that executes RealtimeResponseArbiter moved off Python 3.11 "
        "while #2516 (dispatch ordering on 3.12+) is still open:\n  "
        + "\n  ".join(offenders)
        + "\nFix the dequeue gate first -- see the module docstring in "
        "main_logic/omni_realtime_client/_response_arbiter.py."
    )
