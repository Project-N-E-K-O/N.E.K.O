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

"""The proactive turn's copy onto the plugin bus: its frames and its words.

``prompt_ephemeral`` is the greeting / agent-callback / avatar-tap channel. It
runs the SAME budget ladder as ``stream_text`` and attaches the result to the
model turn, so it puts pictures in front of the model -- but it used to publish
nothing, and the ``conversations`` store had no writer anywhere in the repo. A
plugin could therefore never see either half of a proactive turn, while the
realtime twin published its frames all along.

Three properties are pinned here.

1. The frames a proactive turn attaches reach the frame bus, as the
   POST-ladder bytes and under a source label of their own. They are not the
   user's frames: he never shared them and does not know the turn happened, so
   a plugin filtering on ``"user"`` must not be handed them.

2. Both copies are gated on something that really happened, and the two gates
   answer different questions. The instruction and the frames ask "did the
   provider receive this?", answered by the first streamed chunk -- never by
   local state, because this function still has a three-attempt retry ladder
   and two cancellation checks below that point, each of which can end the turn
   with not one byte having reached the provider. The reply asks "did she say
   it?", answered by commitment: a turn that streamed only a ``[play_music:]``
   directive committed nothing, and an empty record would be the host inventing
   an utterance.

3. The record shape is the one ``ConversationRecord`` reads back. The fields
   already exist -- ``conversation_id`` / ``turn_type`` / ``lanlan_name`` /
   ``message_count`` in ``metadata``, ``content`` at the top -- and this fills
   them rather than inventing a parallel vocabulary.

Fixture note, learned from ``test_offline_provider_frame_publish.py``: that
file's client once raised before yielding any chunk, which after the
delivery-gating fix turned every publish assertion in it into a vacuous pass.
``_make_client`` here therefore streams a real content chunk BY DEFAULT, and
``test_the_fixture_is_not_vacuous`` asserts the default fixture actually
reaches both publishers.
"""

from __future__ import annotations

import asyncio
import time
import threading
import base64
import io
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIConnectionError
from PIL import Image

from app.agent_server import api_runtime as agent_runtime
from main_logic import agent_event_bus as bus
from main_logic.omni_offline_client._client import OmniOfflineClient
from plugin.core.bus.conversations import ConversationRecord
from plugin.message_plane.stores import (
    CONVERSATIONS_STORE_NAME,
    CONVERSATIONS_TOPIC,
    TopicStore,
)
from utils.llm_client import SystemMessage

pytestmark = pytest.mark.unit


_FRAME_PUBLISHER = (
    "main_logic.omni_offline_client._media."
    "publish_provider_frame_observed_best_effort"
)
_TURN_PUBLISHER = (
    "main_logic.omni_offline_client._lifecycle."
    "publish_conversation_turn_observed_best_effort"
)
# ``_lifecycle`` imports the asyncio MODULE, so this patches stdlib
# ``asyncio.sleep`` -- keep the window down to the single ``asyncio.run``.
_SLEEP = "main_logic.omni_offline_client._lifecycle.asyncio.sleep"

_INSTRUCTION = "======[系统通知] 他刚回来了======"


async def _no_backoff(_delay: float) -> None:
    """The retry ladder without its wall clock (1s + 2s between attempts)."""


def _connection_error() -> BaseException:
    """A transient provider failure: the retry path, not the give-up path."""
    return APIConnectionError(
        request=httpx.Request("POST", "http://provider.invalid/v1/chat"),
    )


def _png_b64(width: int, height: int, colour=(18, 160, 90)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _text(content: str) -> SimpleNamespace:
    return SimpleNamespace(content=content)


def _make_client(chunks=None, error=None):
    """A client wired just far enough to run one ``prompt_ephemeral`` turn.

    ``_astream_visible_with_tools`` captures the messages, streams ``chunks``,
    then raises ``error`` if one was given (a normal stream end breaks out of
    the retry loop on its own -- unlike ``stream_text``, no raise is needed).

    ``chunks`` defaults to one REAL content chunk, and that default is
    load-bearing rather than scenery: both copies are gated on the turn
    actually happening, so a fixture that streamed nothing would make every
    publish assertion in this file vacuously true. Pass ``chunks=[]`` with an
    ``error`` to model a request the provider never accepted.
    """
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._pending_plugin_images = []
    client._proactive_image_to_inject = None
    client._proactive_image_staged_at = 0.0
    client._proactive_image_history_len = 0
    client.lanlan_name = "neko"
    client.master_name = "master"
    client.llm = SimpleNamespace(max_completion_tokens=2000)
    client.model = "m"
    client.vision_model = "vm"
    client._prefix_buffer_size = 0
    client.on_status_message = None
    client.on_text_delta = AsyncMock()
    client.on_response_done = AsyncMock()
    client._begin_reasoning_stream = MagicMock(return_value=1)
    client._notify_reasoning_done = AsyncMock()
    client.switch_model = AsyncMock()

    captured: List[list] = []

    async def _fake_astream(messages, **_overrides):
        captured.append(list(messages))
        for chunk in ([_text("欢迎回来喵~")] if chunks is None else list(chunks)):
            yield chunk
        if error is not None:
            raise error()

    client._astream_visible_with_tools = _fake_astream
    return client, captured


def _attached_b64(captured: List[list]) -> List[str]:
    assert captured, "the stream was never reached -- no turn was built"
    message = captured[0][-1]
    assert isinstance(message.content, list), "turn did not go multi-modal"
    return [
        item["image_url"]["url"].split(",", 1)[1]
        for item in message.content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]


class _FrameSpy:
    """Records every frame handed to the frame-bus publisher."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def __call__(self, lanlan_name, **kwargs):
        self.calls.append({"lanlan_name": lanlan_name, **kwargs})
        return True

    @property
    def images(self) -> List[str]:
        return [call["image_base64"] for call in self.calls]

    @property
    def sources(self) -> List[str]:
        return [call["source"] for call in self.calls]


class _TurnSpy:
    """Records every message handed to the conversation-bus publisher."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def __call__(self, lanlan_name, **kwargs):
        self.calls.append({"lanlan_name": lanlan_name, **kwargs})
        return True

    @property
    def turn_types(self) -> List[str]:
        return [call["turn_type"] for call in self.calls]

    @property
    def contents(self) -> List[str]:
        return [call["content"] for call in self.calls]


def _spies():
    return _FrameSpy(), _TurnSpy()


def _run(client, spies, *, instruction: str = _INSTRUCTION, **kwargs):
    frames, turns = spies
    with patch(_FRAME_PUBLISHER, frames), patch(_TURN_PUBLISHER, turns):
        return _run_turn(client.prompt_ephemeral(instruction, **kwargs))


# ---------------------------------------------------------------------------
# 0. The fixture itself.
# ---------------------------------------------------------------------------


def test_the_fixture_is_not_vacuous():
    """The default client reaches BOTH publishers, so assertions elsewhere bite.

    This is the guard the frame-publish file learned to need: its fixture once
    raised before yielding, and every "nothing was published" assertion in it
    passed for the wrong reason. If this test ever goes red, treat every
    ``== []`` assertion below as unproven.
    """
    client, captured = _make_client()
    spies = _spies()
    frames, turns = spies

    committed = _run(client, spies, images=[_png_b64(320, 200)])

    assert committed is True
    assert captured, "the provider was never called"
    assert frames.calls, "the frame publisher was never reached"
    assert turns.calls, "the conversation publisher was never reached"


# ---------------------------------------------------------------------------
# 1. The frames.
# ---------------------------------------------------------------------------


def test_a_proactive_turn_copies_its_post_ladder_frames():
    """What the bus gets is what the model got -- after the ladder, not before.

    The ladder re-encodes to the model resolution profile on nearly every turn,
    so publishing the caller's ``images`` would put a bigger, different picture
    on the bus than the model ever saw.

    Mutation: publish ``images`` instead of ``_budget_images``.
    """
    source = _png_b64(1600, 1200)
    client, captured = _make_client()
    spies = _spies()
    frames, _turns = spies

    _run(client, spies, images=[source])

    sent = _attached_b64(captured)
    assert len(sent) == 1
    assert sent[0] != source, "the ladder's output is what the provider saw"
    assert frames.images == sent
    assert source not in frames.images
    assert frames.calls[0]["lanlan_name"] == "neko"


def test_proactive_frames_are_not_labelled_as_the_users():
    """He never shared these and does not know this turn happened.

    A plugin filtering ``source == "user"`` -- or ``"screen"``, the label the
    user's own proactive-vision screenshot wears on the ``stream_text`` path --
    must not be handed a frame a callback pushed into a greeting.

    Mutation: label these ``_FRAME_SOURCE_USER`` (or ``_FRAME_SOURCE_SCREEN``).
    """
    client, _captured = _make_client()
    spies = _spies()
    frames, _turns = spies

    _run(client, spies, images=[_png_b64(320, 200), _png_b64(300, 200)])

    assert frames.sources == ["proactive", "proactive"]


def test_a_text_only_proactive_turn_publishes_no_frames():
    """No pictures, no frame records -- and the words still travel."""
    client, _captured = _make_client()
    spies = _spies()
    frames, turns = spies

    _run(client, spies)

    assert frames.calls == []
    assert turns.calls, "the conversation copy must not depend on images"


# ---------------------------------------------------------------------------
# 2. The delivery gate: the provider, not local state.
# ---------------------------------------------------------------------------


def test_a_turn_the_provider_never_accepted_copies_nothing():
    """The frames are attached and the request still never happens.

    ``astream`` is lazy: nothing is sent until the first ``__anext__``. This
    turn dies there, so no byte reached the provider -- yet the message was
    fully built and the ladder had already run. Publishing at attach time
    announces a delivery that did not happen, and the next turn's ladder would
    publish the same frames again.

    Mutation (a): publish where ``_bus_frames`` is staged (attach time). Red.
    """
    client, captured = _make_client(
        chunks=[], error=lambda: RuntimeError("provider refused"),
    )
    spies = _spies()
    frames, turns = spies

    committed = _run(client, spies, images=[_png_b64(320, 200)])

    assert committed is False
    assert _attached_b64(captured), "this turn has to be carrying frames"
    assert frames.calls == []
    assert turns.calls == []


def test_the_whole_retry_ladder_failing_leaves_the_bus_empty():
    """Three transient failures, none of which ever streams a chunk.

    ``prompt_ephemeral`` gives up silently after three attempts; a copy keyed
    on anything earlier than the first chunk asserts a delivery that the whole
    ladder failed to make.

    Mutation (a): publish at attach time. Red -- and once per attempt.
    """
    client, captured = _make_client(chunks=[], error=_connection_error)
    spies = _spies()
    frames, turns = spies

    with patch(_SLEEP, _no_backoff):
        committed = _run(client, spies, images=[_png_b64(320, 200)])

    assert committed is False
    assert len(captured) == 3, (
        f"the fixture must exercise all three attempts, saw {len(captured)}"
    )
    assert frames.calls == []
    assert turns.calls == []


def test_a_retried_turn_is_copied_once_not_once_per_attempt():
    """Every attempt reaches the provider; the copy still happens once.

    The chunks are content-less, so nothing is emitted and the ladder keeps
    retrying -- but the delivery gate opens on all three. What keeps the record
    set honest is that the staged copy is consumed on the first one, cleared
    BEFORE the await.

    Mutation (b): drop the ``_pending_bus_delivery = None`` clear. Red at three
    records for one frame, and three instruction records.
    """
    client, captured = _make_client(chunks=[_text("")], error=_connection_error)
    spies = _spies()
    frames, turns = spies

    with patch(_SLEEP, _no_backoff):
        _run(client, spies, images=[_png_b64(320, 200)])

    assert len(captured) == 3, (
        f"the fixture must exercise all three attempts, saw {len(captured)}"
    )
    assert len(frames.calls) == 1
    assert frames.images == _attached_b64(captured)
    assert turns.turn_types == ["proactive_instruction"]


# ---------------------------------------------------------------------------
# 3. The conversation turn.
# ---------------------------------------------------------------------------


def test_the_instruction_and_the_reply_both_reach_the_bus():
    """What the model got, and what she said back. In that order.

    Mutation: drop either publish call.
    """
    client, _captured = _make_client(chunks=[_text("欢迎回来喵~")])
    spies = _spies()
    _frames, turns = spies

    committed = _run(client, spies)

    assert committed is True
    assert turns.turn_types == ["proactive_instruction", "proactive_reply"]
    assert turns.contents == [_INSTRUCTION, "欢迎回来喵~"]
    assert [call["lanlan_name"] for call in turns.calls] == ["neko", "neko"]
    assert [call["source"] for call in turns.calls] == ["proactive", "proactive"]
    # message_count is the conversation's size as of each record, so a plugin
    # holding the reply knows it has the whole turn.
    assert [call["message_count"] for call in turns.calls] == [1, 2]


def test_a_reply_that_never_commits_is_never_copied():
    """Streaming started, and she still said nothing.

    A turn whose entire output is a ``[play_music:]`` directive emits deltas,
    commits no visible text, fires no ``on_committed_text`` and writes nothing
    to history. Copying a reply here would put an utterance on the bus that she
    never made -- and the record would be empty besides.

    Mutation (c): publish the reply from the first-chunk site instead of the
    commit boundary, or drop the ``if content_committed`` guard. Red.
    """
    client, _captured = _make_client(chunks=[_text("[play_music:demo]")])
    spies = _spies()
    _frames, turns = spies

    committed = _run(client, spies)

    assert committed is False
    # The instruction still travels: the provider really did receive it.
    assert turns.turn_types == ["proactive_instruction"]


def test_a_spoken_but_discarded_reply_is_not_copied():
    """She said it out loud and the turn still threw it away.

    An account-level failure mid-stream (arrears / rejected key) blanks
    ``assistant_message`` and returns: the text already reached the user's
    screen through ``on_text_delta``, but nothing is committed -- no
    ``on_committed_text``, nothing into history, nothing into the anti-repeat
    corpus. The bus is the same kind of consumer and must agree with the rest
    of them.

    This is the case that separates "gated on commitment" from "gated on we
    started streaming": both gates are open by the time the error lands, and
    only one of them closes again.

    Mutation (c): publish the reply from the streaming emit branch (keyed on
    ``emitted_any``) instead of the commit boundary. Red.
    """
    client, _captured = _make_client(
        chunks=[_text("欢迎回来喵~")],
        error=lambda: APIConnectionError(
            request=httpx.Request("POST", "http://provider.invalid/v1/chat"),
            message="account in bad standing",
        ),
    )
    client.on_status_message = AsyncMock()
    spies = _spies()
    _frames, turns = spies

    committed = _run(client, spies)

    assert committed is False
    client.on_text_delta.assert_awaited()  # the user did see it
    assert turns.turn_types == ["proactive_instruction"]


def test_the_copied_reply_is_the_sanitized_committed_text():
    """The bus sees the same string the commit callbacks and history see.

    Mutation: publish ``assistant_message`` (the raw stream) instead of
    ``committed_text``. Red on the leftover directive.
    """
    client, _captured = _make_client(
        chunks=[_text("[play_music:demo]"), _text("欢迎回来喵~")],
    )
    seen: List[str] = []
    frames, turns = _spies()

    with patch(_FRAME_PUBLISHER, frames), patch(_TURN_PUBLISHER, turns):
        committed = _run_turn(client.prompt_ephemeral(
            _INSTRUCTION, on_committed_text=seen.append,
        ))

    assert committed is True
    reply = [c for c in turns.calls if c["turn_type"] == "proactive_reply"]
    assert len(reply) == 1
    assert reply[0]["content"] == seen[0] == "欢迎回来喵~"


def test_an_account_level_failure_copies_neither_half():
    """A rejected API key returns before any chunk: nothing received, nothing said."""
    client, _captured = _make_client(
        chunks=[],
        error=lambda: APIConnectionError(
            request=httpx.Request("POST", "http://provider.invalid/v1/chat"),
            message="Error code: 401 - invalid api key",
        ),
    )
    client.on_status_message = AsyncMock()
    spies = _spies()
    frames, turns = spies

    committed = _run(client, spies, images=[_png_b64(320, 200)])

    assert committed is False
    assert frames.calls == []
    assert turns.calls == []


def test_the_frames_and_both_messages_share_one_turn_identity():
    """A plugin has to be able to put the pictures back with the words.

    The frames' ``turn_id`` and both conversation records' ``conversation_id``
    are the same minted value; nothing else ties them together, since a
    proactive turn has no externally supplied turn id.

    Mutation: mint a second uuid for the reply, or drop ``turn_id`` from the
    frame publish.
    """
    client, _captured = _make_client()
    spies = _spies()
    frames, turns = spies

    _run(client, spies, images=[_png_b64(320, 200)])

    ids = {call["turn_id"] for call in frames.calls}
    ids |= {call["conversation_id"] for call in turns.calls}
    assert len(ids) == 1, ids
    assert next(iter(ids)), "the turn identity must not be empty"


def test_two_proactive_turns_do_not_share_a_conversation_id():
    """Each ephemeral turn is its own conversation; merging them would glue
    unrelated greetings into one thread on the reader's side."""
    seen: List[str] = []
    for _ in range(2):
        client, _captured = _make_client()
        spies = _spies()
        _frames, turns = spies
        _run(client, spies)
        seen.append(turns.calls[0]["conversation_id"])

    assert seen[0] != seen[1]


# ---------------------------------------------------------------------------
# 4. Both copies are a courtesy: neither may cost the turn.
# ---------------------------------------------------------------------------


def test_a_failing_conversation_bus_never_costs_the_turn():
    """Mutation: drop the ``except Exception`` guard in
    ``_publish_conversation_turn``."""
    client, _captured = _make_client()

    async def _explode(*_args, **_kwargs):
        raise RuntimeError("plane down")

    with patch(_FRAME_PUBLISHER, _FrameSpy()), patch(_TURN_PUBLISHER, _explode):
        committed = _run_turn(client.prompt_ephemeral(_INSTRUCTION))

    assert committed is True
    assert client.on_response_done.await_count == 1
    client.on_text_delta.assert_awaited()


def test_a_failing_frame_bus_never_costs_the_turn():
    """Mutation: drop the ``except Exception`` guard in
    ``_publish_provider_frames``."""
    client, captured = _make_client()

    async def _explode(*_args, **_kwargs):
        raise RuntimeError("plane down")

    with patch(_FRAME_PUBLISHER, _explode), patch(_TURN_PUBLISHER, _TurnSpy()):
        committed = _run_turn(
            client.prompt_ephemeral(_INSTRUCTION, images=[_png_b64(320, 200)])
        )

    assert committed is True
    assert _attached_b64(captured), "the turn still carried its frames"


def test_a_cancelled_bus_publish_no_longer_reaches_the_proactive_turn():
    """The copy runs off the response path, so its cancellation stays there.

    This used to propagate, because the turn awaited the publish. It must not
    any more: a stalled or torn-down bus cannot be allowed to cost her the
    sentence she already said.
    """
    client, _captured = _make_client()

    async def _cancel(*_args, **_kwargs):
        raise asyncio.CancelledError()

    with patch(_FRAME_PUBLISHER, _FrameSpy()), patch(_TURN_PUBLISHER, _cancel):
        committed = _run_turn(client.prompt_ephemeral(_INSTRUCTION))

    assert committed is True


def test_the_turn_publisher_still_re_raises_cancellation():
    """The dual, one layer down: teardown must end the copy, not be eaten by it.

    Mutation: catch ``BaseException`` (or drop the CancelledError re-raise) in
    ``_publish_conversation_turn``.
    """
    client, _captured = _make_client()

    async def _cancel(*_args, **_kwargs):
        raise asyncio.CancelledError()

    async def _call():
        await client._publish_conversation_turn(
            "x", turn_type="proactive_reply", conversation_id="c", message_count=1,
        )

    with patch(_TURN_PUBLISHER, _cancel):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(_call())


# ---------------------------------------------------------------------------
# 5. The hop into the ``conversations`` store.
# ---------------------------------------------------------------------------



# Bound at import, before any test can patch the module attribute out from
# under us. ``_settled`` needs a yield that really yields.
_REAL_SLEEP = asyncio.sleep


async def _drain_background_tasks(timeout: float = 2.0) -> None:
    """Wait for the fired bus copies, then make sure none outlive this loop.

    Waiting on the TASKS rather than counting event-loop turns: a fixed number
    of ``sleep(0)`` is not a completion condition, so under load it reads a
    half-finished copy -- or leaves one pending for the next test, which then
    spends its patch on a frame from a turn that ended long ago.

    A deliberately stalled copy never finishes, so the wait is bounded and
    whatever is left is cancelled and collected HERE rather than abandoned at
    loop close. ``asyncio.wait`` runs on the loop clock, so the module-wide
    ``asyncio.sleep`` patch in the retry-ladder tests cannot defeat it.
    """
    deadline = time.monotonic() + timeout
    while True:
        pending = [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
        ]
        if not pending:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            return
        await asyncio.wait(pending, timeout=remaining)


async def _settled(coro):
    """Run a turn, then let its fire-and-forget bus copies finish.

    The publish no longer sits on the response path -- a cross-loop hop with no
    timeout must never hold up the user's reply -- so a turn can return before
    a single frame has reached the spy. Draining here is what keeps these
    assertions about the publish rather than about scheduling luck.
    """
    try:
        return await coro
    finally:
        # In a ``finally`` on purpose. A turn that raises still fired its bus
        # copies, and skipping the drain there leaves a task pending at loop
        # close -- which then surfaces inside the NEXT test.
        await _drain_background_tasks()


def _run_turn(coro):
    """``asyncio.run`` for a turn, with the bus copies drained before teardown."""
    return asyncio.run(_settled(coro))


def _turn_event(**overrides: Any) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "event_type": "conversation_turn_observed",
        "event_id": "turn-msg-1",
        "lanlan_name": "neko",
        "source": "proactive",
        "conversation_id": "conv-1",
        "turn_type": "proactive_reply",
        "content": "欢迎回来喵~",
        "message_count": 2,
    }
    event.update(overrides)
    return event


def _patch_publish_record(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    from plugin.server.messaging import plane_bridge

    captured: List[Dict[str, Any]] = []

    def _capture(*, store: str, record: Dict[str, Any], topic: str = "all") -> None:
        captured.append({"store": store, "topic": topic, "record": record})

    monkeypatch.setattr(plane_bridge, "publish_record", _capture)
    return captured


def _enable_user_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    flags = dict(agent_runtime.Modules.agent_flags or {})
    flags["user_plugin_enabled"] = True
    monkeypatch.setattr(agent_runtime.Modules, "agent_flags", flags)


def test_the_turn_rides_the_existing_session_pub_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No new socket: main_server cannot write to the message plane itself."""
    sent: List[Dict[str, Any]] = []

    async def _capture(event: Dict[str, Any]) -> bool:
        sent.append(event)
        return True

    monkeypatch.setattr(bus, "publish_session_event_threadsafe", _capture)

    ok = asyncio.run(bus.publish_conversation_turn_observed_best_effort(
        "neko",
        content="欢迎回来喵~",
        turn_type="proactive_reply",
        conversation_id="conv-1",
        source="proactive",
        message_count=2,
    ))

    assert ok is True
    assert len(sent) == 1
    event = sent[0]
    assert event["event_type"] == bus.CONVERSATION_TURN_OBSERVED_EVENT
    assert event["event_type"] == "conversation_turn_observed"
    assert event["content"] == "欢迎回来喵~"
    assert event["conversation_id"] == "conv-1"
    assert event["turn_type"] == "proactive_reply"
    assert event["message_count"] == 2
    assert event["lanlan_name"] == "neko"


def test_an_empty_message_is_not_published(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: List[Dict[str, Any]] = []

    async def _capture(event: Dict[str, Any]) -> bool:
        sent.append(event)
        return True

    monkeypatch.setattr(bus, "publish_session_event_threadsafe", _capture)

    ok = asyncio.run(bus.publish_conversation_turn_observed_best_effort(
        "neko", content="   ", turn_type="proactive_reply",
        conversation_id="conv-1", source="proactive",
    ))

    assert ok is False
    assert sent == []


def test_the_forward_writes_into_the_conversations_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The store the SDK reads, on the topic it hardcodes.

    ``ConversationClient._get_impl`` asks for ``store="conversations"``,
    ``topic="all"``; a record filed anywhere else is invisible to every plugin.
    """
    _enable_user_plugins(monkeypatch)
    captured = _patch_publish_record(monkeypatch)

    assert agent_runtime._forward_conversation_turn(_turn_event()) is True
    assert len(captured) == 1
    assert captured[0]["store"] == CONVERSATIONS_STORE_NAME == "conversations"
    assert captured[0]["topic"] == CONVERSATIONS_TOPIC == "all"


def test_the_forwarded_record_reads_back_through_the_sdk_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fill the fields ``ConversationRecord`` already has, not new ones.

    ``from_raw`` reads ``content`` off the payload and
    ``conversation_id`` / ``turn_type`` / ``lanlan_name`` / ``message_count``
    out of ``metadata``. A record that put them anywhere else would arrive as a
    ConversationRecord with every one of those fields empty.

    Mutation: hoist any of the four to the top level of the record.
    """
    _enable_user_plugins(monkeypatch)
    captured = _patch_publish_record(monkeypatch)

    agent_runtime._forward_conversation_turn(_turn_event())
    record = ConversationRecord.from_raw(captured[0]["record"])

    assert record.kind == "conversation"
    assert record.content == "欢迎回来喵~"
    assert record.conversation_id == "conv-1"
    assert record.turn_type == "proactive_reply"
    assert record.lanlan_name == "neko"
    assert record.message_count == 2
    assert record.source == "proactive"


def test_the_record_survives_the_store_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through a real TopicStore, which is what a light read gives a plugin.

    ``TopicStore._extract_index`` projects ``conversation_id`` out of the
    metadata; ``from_index`` is the constructor a stored item comes back
    through. Putting the id anywhere but ``metadata`` loses it here.
    """
    _enable_user_plugins(monkeypatch)
    captured = _patch_publish_record(monkeypatch)
    agent_runtime._forward_conversation_turn(_turn_event())

    store = TopicStore(name=CONVERSATIONS_STORE_NAME, maxlen=8)
    event = store.publish(CONVERSATIONS_TOPIC, captured[0]["record"])

    assert event["index"]["conversation_id"] == "conv-1"
    assert event["index"]["id"] == "turn-msg-1"
    record = ConversationRecord.from_index(event["index"], event["payload"])
    assert record.conversation_id == "conv-1"
    assert record.turn_type == "proactive_reply"
    assert record.content == "欢迎回来喵~"
    assert record.message_count == 2


def test_the_forward_is_skipped_when_no_plugin_can_read_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With user plugins off there is no reader; do not retain what she said."""
    flags = dict(agent_runtime.Modules.agent_flags or {})
    flags["user_plugin_enabled"] = False
    monkeypatch.setattr(agent_runtime.Modules, "agent_flags", flags)
    captured = _patch_publish_record(monkeypatch)

    assert agent_runtime._forward_conversation_turn(_turn_event()) is False
    assert captured == []


def test_the_forward_ignores_an_event_without_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_user_plugins(monkeypatch)
    captured = _patch_publish_record(monkeypatch)

    assert agent_runtime._forward_conversation_turn(_turn_event(content="")) is False
    assert agent_runtime._forward_conversation_turn(_turn_event(content=None)) is False
    assert agent_runtime._forward_conversation_turn(_turn_event(content="  ")) is False
    assert captured == []


def test_session_event_dispatch_routes_conversation_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The branch must be wired: a helper nothing routes to forwards nothing."""
    _enable_user_plugins(monkeypatch)
    captured = _patch_publish_record(monkeypatch)

    asyncio.run(agent_runtime._on_session_event(_turn_event()))

    assert len(captured) == 1
    assert captured[0]["record"]["content"] == "欢迎回来喵~"


def test_a_lookup_by_id_finds_this_turn_and_not_another_conversations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The writer's placement and the reader's predicate have to agree.

    ``_forward_conversation_turn`` files ``conversation_id`` inside
    ``metadata`` and ``TopicStore._extract_index`` projects it into the index;
    ``TopicStore.query`` matches against that index. This joins the two halves
    on one real store, which is what ``ctx.bus.conversations.get_by_id`` runs
    against.

    The "and not another conversation's" assertion is the load-bearing one.
    The filter used to be dropped on the floor -- schema rejected the field,
    and the server never forwarded it -- so a lookup came back full of
    unrelated recent turns while every layer reported success. A test that only
    checked "the turn is in there" passed against exactly that.
    """
    _enable_user_plugins(monkeypatch)
    captured = _patch_publish_record(monkeypatch)

    agent_runtime._forward_conversation_turn(_turn_event())
    agent_runtime._forward_conversation_turn(
        _turn_event(
            conversation_id="conv-2",
            event_id="turn-msg-2",
            content="在吗喵？",
        )
    )
    assert len(captured) == 2

    store = TopicStore(name=CONVERSATIONS_STORE_NAME, maxlen=8)
    for call in captured:
        store.publish(CONVERSATIONS_TOPIC, call["record"])

    items = store.query(
        topic=CONVERSATIONS_TOPIC,
        conversation_id="conv-1",
        limit=50,
    )

    assert len(items) == 1
    record = ConversationRecord.from_index(items[0]["index"], items[0]["payload"])
    assert record.conversation_id == "conv-1"
    assert record.content == "欢迎回来喵~"
    # conv-2 is the NEWER turn, so an unfiltered "recent turns" read would have
    # led with it.
    assert "在吗喵？" not in [
        (ev.get("payload") or {}).get("content") for ev in items
    ]

    # ...and both turns really are in the store: the exclusion above is the
    # filter working, not an empty corpus.
    assert len(store.query(topic=CONVERSATIONS_TOPIC, limit=50)) == 2

    # An id nobody wrote is an empty result, not an error.
    assert store.query(topic=CONVERSATIONS_TOPIC, conversation_id="conv-nope", limit=50) == []


def test_close_cancels_and_collects_the_in_flight_bus_copies():
    """A copy must not outlive the session that fired it.

    Parked inside the cross-thread handoff, a copy keeps its base64 and its
    reference to the client alive, and would publish for a session that is
    gone if the bridge ever recovered. Cancel then collect: a cancelled task
    has not stopped until it has been awaited.

    Mutation: drop the ``cancel()`` loop, or the ``gather``, in
    ``_cancel_bus_copies``.
    """
    client, _captured = _make_client()

    async def _check():
        started = asyncio.Event()

        async def _parked():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                pass

        task = client._fire_bus_task(_parked())
        assert task is not None
        await asyncio.wait_for(started.wait(), timeout=5)

        # 直接 await，不在这里包 wait_for：包一层会白送一次调度让出，
        # 于是「取消了但不收集」也能让下面的 done() 通过——变异验证抓到过。
        # deadline 挪到整个 _check() 外面，没有 cancel 的版本仍然会失败而不
        # 是把跑测进程连同脚本一起挂死。
        await client._cancel_bus_copies()

        assert task.done(), "close 返回时抄送还在跑"
        assert task.cancelled()
        assert not client._bus_bg_tasks, "集合没被清空"

    # deadline 在最外层：守卫的失败形态必须是「失败」，不是「挂起」。
    asyncio.run(asyncio.wait_for(_check(), timeout=5))


def test_a_stalled_instruction_copy_does_not_hold_up_the_proactive_turn():
    """The ordering join must not become a new way to hang the turn.

    The reply copy waits for the instruction copy so a plugin never reads a
    reply with no instruction in front of it. That wait lives inside the fired
    copy, not on the turn: a stalled bridge would otherwise hang the very
    teardown the previous fix moved the publish out of.

    Mutation: await the instruction task inside ``prompt_ephemeral``'s
    ``finally`` instead of chaining. This test then hangs rather than fails, so
    it carries its own deadline.
    """
    entered = threading.Event()

    async def _never_returns(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    client, _captured = _make_client()

    async def _turn():
        with patch(_FRAME_PUBLISHER, _FrameSpy()), patch(_TURN_PUBLISHER, _never_returns):
            committed = await asyncio.wait_for(
                client.prompt_ephemeral(_INSTRUCTION), timeout=5,
            )
            for _ in range(50):
                await _REAL_SLEEP(0)
            assert entered.is_set(), "前提没成立：指令抄送根本没被调用"
            return committed

    assert asyncio.run(_turn()) is True


def test_a_refused_instruction_copy_takes_the_reply_with_it(monkeypatch):
    """No instruction on the bus means no reply on the bus either.

    The reply carries ``message_count=2`` -- it tells a plugin "you are holding
    a complete round". Publishing it when the instruction copy was refused at
    the in-flight cap makes that a lie, and a lying record is worse than a
    missing one.

    Deterministic rather than racy: with the cap at 1 the frame copy takes the
    only slot, so the instruction is refused in the same event-loop tick.

    Mutation: relax the caller's check to ``if content_committed:``.
    """
    client, _captured = _make_client()
    spies = _spies()
    frames, turns = spies

    # 只拒掉**指令**那一次 fire。用 cap=1 复现的话，回复的抄送会被同一个 cap
    # 一起挡住，于是把判据放松也照样是空的——那条守卫就一直在为错误的理由
    # 变绿（变异验证抓到的正是这一点）。这里精确模拟意见描述的形态：指令被
    # 拒，而回复到得了发布点（真实时序里，占着名额的帧抄送此时已经跑完）。
    real_fire = client._fire_bus_task
    fired: list = []

    def _refuse_the_instruction(coro):
        fired.append(coro.__qualname__)
        if len(fired) == 2:
            coro.close()
            return None
        return real_fire(coro)

    client._fire_bus_task = _refuse_the_instruction

    committed = _run(client, spies, images=[_png_b64(320, 200)])

    assert committed is True, "回合本身不该受影响"
    assert frames.calls, "前提没成立：帧抄送没发生"
    assert len(fired) >= 2, f"前提没成立：指令那次 fire 没发生 ({fired})"
    assert "_publish_conversation_turn" in fired[1], (
        f"前提没成立：被拒的不是指令那次 ({fired})"
    )
    assert turns.turn_types == [], (
        f"指令被拒，回复却上了总线: {turns.turn_types}"
    )


def test_the_cap_is_what_refused_it_not_something_else(monkeypatch):
    """Premise guard for the test above: without the cap, both turns publish.

    If the instruction stopped being published for an unrelated reason, the
    test above would pass while proving nothing.
    """
    client, _captured = _make_client()
    spies = _spies()
    frames, turns = spies

    _run(client, spies, images=[_png_b64(320, 200)])

    assert frames.calls
    assert turns.turn_types == ["proactive_instruction", "proactive_reply"]


def test_a_proactive_tool_image_carries_the_turn_id():
    """A tool image inside a proactive turn belongs to that turn.

    The instruction, the reply and the turn's own frames all use one id; a
    tool frame published with ``turn_id=None`` cannot be correlated with any of
    them, which is the whole reason the field exists.

    Mutation: drop ``_tool_frames_turn_id`` from the proactive tool-loop call.
    """
    client, _captured = _make_client()
    seen: list = []

    async def _capture(messages, **overrides):
        seen.append(overrides.get("_tool_frames_turn_id"))
        yield _text("看到了")

    client._astream_visible_with_tools = _capture
    spies = _spies()
    _frames, turns = spies

    _run(client, spies)

    assert seen and seen[0], "工具帧的 turn_id 没被传进 tool loop"
    # 与同一轮的对话记录同一个 id，而不是另外生成的某个值——否则「传了个
    # turn_id」和「传了这一轮的 turn_id」这两件事分不开。
    assert turns.calls, "前提没成立：这一轮没有对话记录可比对"
    assert seen[0] == turns.calls[0]["conversation_id"]


def test_close_stops_new_bus_copies_from_starting():
    """Draining is not enough on its own.

    A stream parked on its first chunk wakes after the drain and would fire a
    fresh copy, which then outlives the closed session with nothing left to
    collect it.

    Mutation: drop the ``_bus_copies_closed`` latch (keep only the drain).
    """
    client, _captured = _make_client()

    async def _check():
        await client._cancel_bus_copies()

        async def _late():
            return None

        assert client._fire_bus_task(_late()) is None, (
            "close 之后仍然接受了新的抄送"
        )

    asyncio.run(_check())


from main_logic.omni_offline_client._lifecycle import (  # noqa: E402
    _BUS_TURN_TYPE_INSTRUCTION,
)


def test_a_reply_is_dropped_when_the_instruction_publish_failed():
    """Waiting for the instruction is not the same as it having landed.

    ``_publish_conversation_turn`` swallows a refusing bridge and a raising
    one alike, so the task completes either way. Reading only "it finished"
    lets the reply out with ``message_count=2`` in front of nothing -- the same
    orphan record the refused-task check exists to prevent, arriving by the
    other door.

    Mutation: go back to a bare ``await instruction_task`` and ignore its
    result, or make ``_publish_conversation_turn`` return ``None`` again.
    """
    client, _captured = _make_client()
    frames = _FrameSpy()
    published: list = []

    async def _refuse_the_instruction(lanlan_name, **kwargs):
        published.append(kwargs["turn_type"])
        # 指令被 bridge 拒收（返回 False，不是抛异常——两条路都得覆盖）。
        return kwargs["turn_type"] != _BUS_TURN_TYPE_INSTRUCTION

    with patch(_FRAME_PUBLISHER, frames), patch(_TURN_PUBLISHER, _refuse_the_instruction):
        committed = _run_turn(client.prompt_ephemeral(_INSTRUCTION))

    assert committed is True, "回合本身不该受影响"
    assert published == [_BUS_TURN_TYPE_INSTRUCTION], (
        f"指令发布失败后，回复仍然被送上了总线: {published}"
    )


def test_a_raising_instruction_publish_also_drops_the_reply():
    """The dual: the publisher raising, not refusing.

    ``_publish_conversation_turn`` catches it and now reports False, so the
    two failure shapes converge on the same outcome.
    """
    client, _captured = _make_client()
    frames = _FrameSpy()
    published: list = []

    async def _explode_on_the_instruction(lanlan_name, **kwargs):
        published.append(kwargs["turn_type"])
        if kwargs["turn_type"] == _BUS_TURN_TYPE_INSTRUCTION:
            raise RuntimeError("plane down")
        return True

    with patch(_FRAME_PUBLISHER, frames), patch(_TURN_PUBLISHER, _explode_on_the_instruction):
        committed = _run_turn(client.prompt_ephemeral(_INSTRUCTION))

    assert committed is True
    assert published == [_BUS_TURN_TYPE_INSTRUCTION], (
        f"指令发布抛异常后，回复仍然被送上了总线: {published}"
    )
