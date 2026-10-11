"""A vision switch never pulls the client out from under a streaming reply.

``switch_model`` builds the vision client (an await) and swaps it in. A reply
streaming on the old client meanwhile keeps reading from it, so closing that
client right after the swap breaks the reply's stream. Three calls switch:
``prompt_ephemeral`` for proactive media, ``stream_text`` for the user's own
images, and ``prepare_for_tool_images`` from inside a tool handler.

Real ``OmniOfflineClient`` and real ``switch_model``; the provider transport
is scripted, and ``create_chat_llm_async`` parks on a gate so a reply can
begin while the switch is building its client. A scripted read on a closed
client raises, as a read on a closed connection pool does.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import main_logic.omni_offline_client._streaming as offline_streaming
from tests.unit.test_offline_provider_frame_publish import _connection_error, _png_b64
from tests.unit.test_offline_turn_cancellation_e2e import _client, _emitted, _text

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_token_tracker(monkeypatch):
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())


class _Switch:
    """The real switch over a gated client factory; records every client."""

    def __init__(self, client, monkeypatch):
        del client.switch_model  # the fixture stubs it on the instance
        client.model = "chat-x"
        client.base_url = "http://chat.invalid/v1"
        client.api_key = "chat-key"
        client.provider_type = None
        client.vision_model = "vision-x"
        client.vision_base_url = "http://vision.invalid/v1"
        client.vision_api_key = "vision-key"
        client.vision_provider_type = None
        client._model_switch_lock = None
        self.client = client
        self.old = client.llm
        self.old.closed = 0
        self.old.aclose = self._closer(self.old)
        self.entered, self.gate = asyncio.Event(), asyncio.Event()
        self.created = []
        self.vision_requests = []
        # Set to (closing, release) to park the close of a client built here.
        self.park_new_close = None
        monkeypatch.setattr(offline_streaming, "create_chat_llm_async", self._create)

    def _closer(self, llm, parked=False):
        async def aclose():
            if parked and self.park_new_close is not None:
                closing, release = self.park_new_close
                closing.set()
                await release.wait()
            llm.closed += 1
        return aclose

    async def _create(self, *_args, **_kwargs):
        self.entered.set()
        await self.gate.wait()

        def astream(messages, **_kw):
            self.vision_requests.append(list(messages))

            async def run():
                yield _text("看到了。")
                yield _text("", "stop")
            return run()

        llm = SimpleNamespace(astream=astream, max_completion_tokens=100, closed=0)
        llm.aclose = self._closer(llm, parked=True)
        self.created.append(llm)
        return llm

    def read(self, seen):
        """A scripted read on the old client: fails once it was closed."""
        async def read():
            seen.append(self.old.closed)
            if self.old.closed:
                raise _connection_error()
        return read


def _parked():
    parked, release = asyncio.Event(), asyncio.Event()

    async def park():
        parked.set()
        await release.wait()

    return park, parked, release


async def test_a_reply_begun_during_the_ephemeral_vision_switch_keeps_its_client(monkeypatch):
    """The reported race: an agent callback with media passes the early
    decline and starts switching to the vision model; the user's reply begins
    while the vision client is being built and streams on the old one. The
    switch drops itself instead of swapping and closing that client, and the
    callback declines; the reply streams to its end."""
    client = _client()
    switch = _Switch(client, monkeypatch)
    park, parked, release = _parked()
    seen = []
    client.script = [[_text("前半，"), park, switch.read(seen), _text("后半。"), _text("", "stop")]]

    callback = asyncio.create_task(client.prompt_ephemeral(
        "callback with media", images=[_png_b64(4, 4, (9, 9, 9))],
    ))
    await asyncio.wait_for(switch.entered.wait(), 5)
    reply = asyncio.create_task(client.stream_text("用户说话"))
    await asyncio.wait_for(parked.wait(), 5)
    switch.gate.set()
    assert await asyncio.wait_for(callback, 5) is False
    release.set()
    await asyncio.wait_for(reply, 10)

    assert seen == [0], "the reply read on a client the switch had closed"
    assert _emitted(client) == ["前半，", "后半。"]
    assert client.llm is switch.old and client.model == "chat-x"
    assert [llm.closed for llm in switch.created] == [1], "the unused client is closed"
    assert switch.old.closed == 0
    assert switch.vision_requests == []
    client.on_proactive_done.assert_not_awaited()


async def test_a_dropped_switch_never_sends_the_media_to_the_conversation_model(monkeypatch):
    """The switch dropped itself because a reply began. Should that reply
    end before the callback would have begun, the callback still declines:
    going on would send its images on the conversation client, which was
    never switched to the vision model."""
    client = _client()
    switch = _Switch(client, monkeypatch)
    closing, close_release = asyncio.Event(), asyncio.Event()
    switch.park_new_close = (closing, close_release)
    park, parked, release = _parked()
    client.script = [
        [_text("前半，"), park, _text("后半。"), _text("", "stop")],
        [_text("回调。"), _text("", "stop")],
    ]

    callback = asyncio.create_task(client.prompt_ephemeral(
        "callback with media", images=[_png_b64(4, 4, (9, 9, 9))],
    ))
    await asyncio.wait_for(switch.entered.wait(), 5)
    reply = asyncio.create_task(client.stream_text("用户说话"))
    await asyncio.wait_for(parked.wait(), 5)
    switch.gate.set()
    await asyncio.wait_for(closing.wait(), 5)  # dropped: closing the unused client
    release.set()
    await asyncio.wait_for(reply, 10)
    close_release.set()

    assert await asyncio.wait_for(callback, 5) is False
    assert len(client.requests) == 1, "only the reply reached the provider"
    assert client.llm is switch.old


async def test_a_reply_begun_after_the_ephemeral_preload_stops_the_switch(monkeypatch):
    """The preload is an await between the early decline and the switch; a
    reply that begins there is seen before any client is built."""
    import memory.anti_repeat as anti_repeat

    client = _client()
    switch = _Switch(client, monkeypatch)
    switch.gate.set()
    other = {}

    async def preload(_name):
        other["generation"] = client._begin_response_generation()

    corpus = MagicMock()
    corpus.apreload = preload
    monkeypatch.setattr(anti_repeat, "get_anti_repeat_corpus", lambda: corpus)

    delivered = await client.prompt_ephemeral(
        "avatar", images=[_png_b64(4, 4, (9, 9, 9))], completion_mode="response",
    )
    assert delivered is False
    assert not switch.entered.is_set(), "no vision client is built for a declined turn"
    assert client.llm is switch.old
    assert client._active_response_generation == other["generation"]
    client._finish_response_generation(other["generation"])


async def test_a_user_switch_leaves_the_displaced_reply_its_client_until_it_returns(monkeypatch):
    """The user's own images switch while a callback reply streams on the old
    client. The user's reply needs the switch and takes over the callback,
    but the callback is still reading when the swap happens: the old client
    is closed only after the last reply call returns."""
    client = _client()
    switch = _Switch(client, monkeypatch)
    park, parked, release = _parked()
    seen = []
    client.script = [[_text("回调前半，"), park, switch.read(seen), _text("后半。"), _text("", "stop")]]

    callback = asyncio.create_task(client.prompt_ephemeral("callback"))
    await asyncio.wait_for(parked.wait(), 5)
    reply = asyncio.create_task(client.stream_text(
        "看这张", turn_images=[_png_b64(4, 4, (9, 9, 9))],
    ))
    await asyncio.wait_for(switch.entered.wait(), 5)
    switch.gate.set()
    await asyncio.wait_for(reply, 10)
    assert client.llm is switch.created[0]
    assert switch.old.closed == 0, "closed while the callback still streams on it"
    release.set()
    await asyncio.wait_for(callback, 10)

    assert seen == [0]
    assert len(switch.vision_requests) == 1
    assert switch.old.closed == 1, "closed once nothing streams on it"


async def test_a_tool_image_switch_waits_for_the_streaming_reply_and_close_ends_the_wait(monkeypatch):
    """A tool handler's switch (``prepare_for_tool_images``) runs while
    another reply streams on the old client; that client stays open. If the
    session closes first, ``close()`` closes it, and the reply returning
    later does not close it twice."""
    client = _client()
    switch = _Switch(client, monkeypatch)
    switch.gate.set()
    park, parked, release = _parked()
    client.script = [[_text("前半，"), park, _text("后半。"), _text("", "stop")]]

    reply = asyncio.create_task(client.stream_text("用户说话"))
    await asyncio.wait_for(parked.wait(), 5)
    assert await client.prepare_for_tool_images() is True
    assert client.llm is switch.created[0]
    assert switch.old.closed == 0
    await client.close()
    assert switch.old.closed == 1
    assert switch.created[0].closed == 1
    release.set()
    await asyncio.wait_for(reply, 10)
    assert switch.old.closed == 1


async def test_a_switch_with_no_reply_in_flight_closes_the_old_client_at_once(monkeypatch):
    client = _client()
    switch = _Switch(client, monkeypatch)
    switch.gate.set()
    assert await client.switch_model("vision-x", use_vision_config=True) is True
    assert client.llm is switch.created[0]
    assert switch.old.closed == 1


def _recorded_closer(name, done, park=None):
    """An async close that records ``name`` once it completes; ``park`` is a
    (parked, release) pair it waits on first."""
    async def close():
        if park is not None:
            parked, release = park
            parked.set()
            await release.wait()
        done.append(name)
    return close


async def test_a_sweep_cancelled_midway_leaves_the_rest_to_closes_sweep():
    """The last reply call to return closes the clients a switch replaced
    while it streamed. ``close()`` cancels that voice task while the first of
    those closes is still running: the closes not yet started must still run,
    in ``close()``'s own sweep, instead of being dropped with the task."""
    from main_logic.omni_offline_client._lifecycle import _retire_replaced_clients

    client = _client()

    async def aclose():
        pass

    client.llm.aclose = aclose
    park, parked, release = _parked()
    client.script = [[_text("前半，"), park, _text("后半。"), _text("", "stop")]]
    client._external_voice_submit_task = None
    voice = asyncio.create_task(client._run_external_voice_stream("用户说话"))
    await asyncio.wait_for(parked.wait(), 5)

    done = []
    closing, never = asyncio.Event(), asyncio.Event()
    await _retire_replaced_clients(client, [
        _recorded_closer("first", done, park=(closing, never)),
        _recorded_closer("second", done),
    ])
    assert done == [], "closed while the reply still streams"
    release.set()
    await asyncio.wait_for(closing.wait(), 5)  # the reply returned; its sweep runs

    await asyncio.wait_for(client.close(), 5)
    await asyncio.gather(voice, return_exceptions=True)

    assert done == ["second"]
    assert client._retired_client_closers == []


async def test_a_sweep_leaves_closes_queued_meanwhile_to_the_next_one():
    """A sweep takes the closes waiting when it starts. A reply call that
    begins while one of them runs may stream on a client a switch replaces
    meanwhile; that close waits for the reply to return."""
    from main_logic.omni_offline_client._lifecycle import _retire_replaced_clients

    client = _client()
    done = []
    closing, close_release = asyncio.Event(), asyncio.Event()
    sweep = asyncio.create_task(_retire_replaced_clients(client, [
        _recorded_closer("first", done, park=(closing, close_release)),
    ]))
    await asyncio.wait_for(closing.wait(), 5)

    park, parked, release = _parked()
    client.script = [[_text("前半，"), park, _text("后半。"), _text("", "stop")]]
    reply = asyncio.create_task(client.stream_text("用户说话"))
    await asyncio.wait_for(parked.wait(), 5)
    await _retire_replaced_clients(client, [_recorded_closer("second", done)])
    close_release.set()
    await asyncio.wait_for(sweep, 5)
    assert done == ["first"], "closed a client the streaming reply may be reading"

    release.set()
    await asyncio.wait_for(reply, 5)
    assert done == ["first", "second"]


async def test_a_close_cancelled_in_its_own_sweep_leaves_the_rest_queued():
    """A second cancellation: the sweep is cancelled midway, ``close()``'s
    own sweep takes the closes put back, and that close is cancelled in turn.
    What neither sweep started stays queued, and the next sweep runs it."""
    from main_logic.omni_offline_client._lifecycle import _retire_replaced_clients

    client = _client()

    async def aclose():
        pass

    client.llm.aclose = aclose
    done = []
    never = asyncio.Event()
    first_in, second_in = asyncio.Event(), asyncio.Event()
    sweep = asyncio.create_task(_retire_replaced_clients(client, [
        _recorded_closer("first", done, park=(first_in, never)),
        _recorded_closer("second", done, park=(second_in, never)),
        _recorded_closer("third", done),
    ]))
    await asyncio.wait_for(first_in.wait(), 5)
    sweep.cancel()
    await asyncio.gather(sweep, return_exceptions=True)
    assert len(client._retired_client_closers) == 2

    closing = asyncio.create_task(client.close())
    await asyncio.wait_for(second_in.wait(), 5)
    closing.cancel()
    await asyncio.gather(closing, return_exceptions=True)
    assert len(client._retired_client_closers) == 1

    await asyncio.wait_for(client.close(), 5)
    assert done == ["third"]
    assert client._retired_client_closers == []


def _genai_halves_closed(genai_client):
    api = genai_client._api_client
    return api._httpx_client.is_closed, api._async_httpx_client.is_closed


async def test_a_switch_closes_both_halves_of_the_replaced_genai_client(monkeypatch):
    """Replies stream on ``genai.Client.aio``; the client's own ``close()``
    releases only its sync half, so the switch closes the async one too."""
    from google import genai

    client = _client()
    switch = _Switch(client, monkeypatch)
    switch.gate.set()
    old_genai = genai.Client(api_key="test-key")
    client._genai_client = old_genai

    assert await client.prepare_for_tool_images() is True

    assert client._genai_client is None
    assert _genai_halves_closed(old_genai) == (True, True)
    assert switch.old.closed == 1


async def test_close_closes_both_halves_of_the_genai_client():
    from google import genai

    client = _client()
    genai_client = genai.Client(api_key="test-key")
    client._genai_client = genai_client

    await client.close()

    assert client._genai_client is None
    assert _genai_halves_closed(genai_client) == (True, True)


async def test_a_failing_async_half_still_closes_the_sync_half():
    client = _client()
    closed = []

    async def aclose():
        closed.append("aio")
        raise RuntimeError("aclose failed")

    genai_client = SimpleNamespace(
        aio=SimpleNamespace(aclose=aclose), close=lambda: closed.append("sync"),
    )
    client._genai_client = genai_client

    await client.close()

    assert closed == ["aio", "sync"]
    assert client._genai_client is None
