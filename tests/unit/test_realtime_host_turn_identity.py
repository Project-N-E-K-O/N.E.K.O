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
"""A turn's end-of-turn hooks belong to that turn — including when the turn
that replaced it was started by the HOST (issue #2612).

Contracts, each written so it can be falsified:

1.  **The hooks stand down once the host is on a new turn.** The host's speech
    id is its turn token; a turn that began under a different one has no
    business closing this one. Falsified by either hook firing after the host
    rotated.

2.  **Re-read between the hooks, not once at entry.** ``on_response_done`` is
    exactly where the host blocks (it awaits the frontend), so a turn that
    starts during it must still stop ``on_sid_rotate`` — which is the step that
    causes the field failure: on a provider without server VAD it discards the
    speech id the new turn is speaking under, and TTS upstream then drops every
    later turn's text for the life of the connection. Falsified by the rotation
    running after a rotation the host already did.

    This is the one condition allowed to split the pair, and only because it
    cannot produce the state that rule protects against ("old sid closed, no
    new one issued"): it is true precisely because the host issued a new one.

3.  **An ordinary turn is untouched.** Same hooks, same order, when the host
    stayed on the turn — this guard adds a stand-down, not a new default.

4.  **No accessor, no guard.** A client constructed without
    ``get_host_turn_id``, or whose host raises while answering, behaves exactly
    as it did before this existed. The fail-safe direction is to notify.

Contract 2 is why the check is separate from the arbiter's ``still_ours``
epoch comparison rather than folded into it: the epoch only counts turn starts
the transport observes, and ``handle_new_message`` off a text input or an
independent ASR utterance never reaches it. See
``test_realtime_arbiter_fail_open.py`` for the epoch side of the same
question.
"""

import asyncio
import contextlib
import json
import logging

import pytest

from main_logic.omni_realtime_client import _transport


class _RecordingSocket:
    """Socket double that also plays the server side.

    ``feed()`` pushes an event that ``handle_messages()`` reads out of its
    ``async for``; ``finish()`` ends the loop. Same shape as the one in
    ``test_realtime_arbiter_native_path.py``, and it exists here for the same
    reason: driving a whole turn through the REAL receive loop is what covers
    the sample point, which no amount of poking the guard directly can.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._inbound: asyncio.Queue = asyncio.Queue()

    async def send(self, payload) -> None:
        self.sent.append(json.loads(payload) if isinstance(payload, str) else payload)

    async def close(self) -> None:
        pass

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


@contextlib.contextmanager
def _records_from(logger: logging.Logger):
    """Collect a logger's own records, independent of propagation."""

    lines: list[str] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    sink = _Sink()
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(sink)
    try:
        yield lines
    finally:
        logger.removeHandler(sink)
        logger.setLevel(previous_level)


class _Host:
    """Stands in for the session manager's ``current_speech_id`` and hooks."""

    def __init__(self) -> None:
        self.speech_id: str | None = "sid-turn-1"
        self.calls: list[str] = []
        self.block_in_response_done: asyncio.Event | None = None

    def read_speech_id(self) -> str | None:
        return self.speech_id

    async def on_response_done(self) -> None:
        self.calls.append("response_done")
        if self.block_in_response_done is not None:
            await self.block_in_response_done.wait()

    async def on_sid_rotate(self) -> None:
        self.calls.append("sid_rotate")
        self.speech_id = "sid-rotated-by-hook"

    def starts_a_new_turn(self) -> None:
        """What ``handle_new_message`` does that this side never observes."""
        self.speech_id = "sid-turn-2"


def _free_client(host: _Host | None, **hooks):
    """A client on a provider WITHOUT server VAD, where sid rotation matters.

    The lanlan.app host is load-bearing: ``_is_free_proxy`` keys on it, and
    that is what makes ``_has_server_vad`` False. With any other host the same
    client rotates from ``speech_stopped`` instead and never reaches the hook
    these tests are about.
    """

    from main_logic.omni_realtime_client import OmniRealtimeClient

    client = OmniRealtimeClient(
        "wss://www.lanlan.app/realtime",
        "test-key",
        model="free-model",
        api_type="free",
        on_response_done=None if host is None else host.on_response_done,
        on_sid_rotate=None if host is None else host.on_sid_rotate,
        **hooks,
    )
    assert client._has_server_vad is False, (
        "this fixture exists to cover the providers whose only sid rotation "
        "point is the turn-finished hook"
    )
    return client


def _begin_turn(client) -> None:
    """Put the client where ``response.created`` leaves it.

    Only the identity bookkeeping matters here, but it is written as the event
    handler writes it — sampling the host id is part of starting a turn, not a
    step a caller remembers to add.
    """

    client._is_responding = True
    client._turn_epoch += 1
    client._current_turn_epoch = client._turn_epoch
    client._current_turn_host_id = client._read_host_turn_id()


# ---------------------------------------------------------------------------
# Contract 3 first: the shape of an ordinary turn is the baseline everything
# else is measured against.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_ordinary_turn_still_runs_both_hooks_in_order():
    host = _Host()
    client = _free_client(host, get_host_turn_id=host.read_speech_id)
    _begin_turn(client)

    await client._notify_turn_finished()

    assert host.calls == ["response_done", "sid_rotate"]


# ---------------------------------------------------------------------------
# Contract 1: the host moved on before the notification ran.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_neither_hook_runs_once_the_host_is_on_a_new_turn():
    host = _Host()
    client = _free_client(host, get_host_turn_id=host.read_speech_id)
    _begin_turn(client)
    # A text input or an independent-ASR utterance took a fresh speech id.
    # Nothing about it reached this transport, so the turn epoch is unchanged.
    epoch_before = client._turn_epoch
    host.starts_a_new_turn()
    assert client._turn_epoch == epoch_before, (
        "the premise of this test is that the epoch cannot see this turn "
        "start; if it can, the guard under test is not the one being exercised"
    )

    await client._notify_turn_finished()

    assert host.calls == [], (
        "the dead turn must not announce its end under the live one, and must "
        "not rotate away the speech id the live one is speaking under"
    )
    assert host.speech_id == "sid-turn-2", "the live turn keeps its own id"


# ---------------------------------------------------------------------------
# Contract 2: the host moved on WHILE the notification ran.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_rotation_stands_down_when_a_turn_starts_during_response_done():
    host = _Host()
    host.block_in_response_done = asyncio.Event()
    client = _free_client(host, get_host_turn_id=host.read_speech_id)
    _begin_turn(client)

    notify = asyncio.create_task(client._notify_turn_finished())
    for _ in range(10):
        await asyncio.sleep(0)
    assert host.calls == ["response_done"], "the first hook should be in flight"

    host.starts_a_new_turn()
    host.block_in_response_done.set()
    await asyncio.wait_for(notify, timeout=1)

    assert host.calls == ["response_done"], (
        "the rotation is the step that discards the live turn's speech id; on "
        "a provider without server VAD that silences every later turn"
    )
    assert host.speech_id == "sid-turn-2"


# ---------------------------------------------------------------------------
# Contract 4: unwired hosts and hosts that cannot answer.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_without_the_accessor_the_hooks_are_unconditional():
    host = _Host()
    client = _free_client(host)  # no get_host_turn_id
    _begin_turn(client)
    assert client._current_turn_host_id is None
    host.starts_a_new_turn()

    await client._notify_turn_finished()

    assert host.calls == ["response_done", "sid_rotate"], (
        "an unwired client must behave exactly as it did before #2612"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_host_that_cannot_answer_gets_notified_anyway():
    host = _Host()

    def _raises() -> str | None:
        raise RuntimeError("host is mid-teardown")

    client = _free_client(host, get_host_turn_id=host.read_speech_id)
    _begin_turn(client)
    assert client._current_turn_host_id == "sid-turn-1"
    client.get_host_turn_id = _raises

    # Its own handler rather than ``caplog``: this module's logger does not
    # always propagate to the root once the app's logging setup has been
    # imported by another test, and a log assertion that quietly depends on
    # test ordering is worse than no log assertion.
    with _records_from(_transport.logger) as logged:
        await client._notify_turn_finished()

    assert host.calls == ["response_done", "sid_rotate"], (
        "withholding the end of a turn is the worse failure; an unreadable "
        "host disables the guard rather than the hooks"
    )
    assert any("turn guard is off" in line for line in logged), (
        "a guard that silently stopped guarding is the thing nobody notices"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_whole_turn_through_the_receive_loop_samples_and_compares():
    """The sample point, not just the guard.

    Every case above puts the client where ``response.created`` leaves it by
    hand, so all of them would still pass if the handler stopped sampling the
    host id at all. This one lets the real loop do it: created, then the host
    takes a turn of its own, then the provider's terminal arrives.
    """
    host = _Host()
    client = _free_client(host, get_host_turn_id=host.read_speech_id)
    socket = _RecordingSocket()
    client.ws = socket
    receive_loop = asyncio.create_task(client.handle_messages())

    socket.feed({"type": "response.created", "response": {"id": "resp-1"}})
    await _settle()
    assert client._current_turn_host_id == "sid-turn-1", (
        "the created handler is where the turn's host identity is taken"
    )

    host.starts_a_new_turn()
    socket.feed({"type": "response.done", "response": {"id": "resp-1"}})
    await _settle()

    assert host.calls == []
    assert host.speech_id == "sid-turn-2"

    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_turn_that_began_before_the_host_had_an_id_is_not_guarded():
    """``None`` is "nothing to compare", not "everything is different"."""
    host = _Host()
    host.speech_id = None
    client = _free_client(host, get_host_turn_id=host.read_speech_id)
    _begin_turn(client)
    host.speech_id = "sid-turn-2"

    await client._notify_turn_finished()

    assert host.calls == ["response_done", "sid_rotate"]
