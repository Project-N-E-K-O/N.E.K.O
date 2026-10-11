"""A reply cut by ``end_session`` still closes its AI turn in the tracker.

The session close cuts a reply that is still streaming. A typed reply's
completion is bound to it and still runs, but an independent-ASR voice reply
and a proactive ``prompt_ephemeral`` reply complete through the session-level
``on_response_done`` / ``on_proactive_done``, which the retired connection's
output guard drops (``_bind_owned_output_callbacks``). The text the cut reply
already said then stays in ``_current_ai_turn_text`` and rides the next
session's first turn end into the activity tracker and topic sinks as part of
that reply. cross_server needs nothing: the session end closes its turn.

Drives a real ``LLMSessionManager`` through the real ``end_session``
retirement, with a real ``OmniOfflineClient`` over a scripted provider.
"""
import asyncio

import pytest

from tests.unit.session_handoff_harness import make_full_manager
from tests.unit.test_offline_late_completion_turn_ownership import (  # noqa: F401
    _client,
    _no_side_channels,
    _text,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _cut_by_session_end(monkeypatch, start_reply):
    parked, release = asyncio.Event(), asyncio.Event()

    async def park():
        parked.set()
        await release.wait()

    client = _client([[_text("我先说一半"), park, _text("，说完"), _text("", "stop")]])
    mgr, _, _ = await make_full_manager(monkeypatch)
    client.on_text_delta = mgr.handle_text_data
    client.on_response_done = mgr.handle_response_complete
    client.on_response_discarded = mgr.handle_response_discarded
    client.on_proactive_done = mgr.handle_proactive_complete
    mgr.session = client
    mgr.is_active = True
    # As _bind_session_lifecycle_callbacks installs every client.
    mgr._register_connection(client)
    mgr._bind_owned_output_callbacks(client)
    noted = []
    monkeypatch.setattr(mgr, "_note_ai_turn", lambda *, text=None, now=None: noted.append(text))

    reply = asyncio.create_task(start_reply(client))
    try:
        await asyncio.wait_for(parked.wait(), 5)
        assert mgr._current_ai_turn_text, "fixture: the cut reply already said something"
        await mgr.end_session()
    finally:
        release.set()
        await asyncio.gather(reply, return_exceptions=True)
        background = tuple(getattr(mgr, "_bg_tasks", ()))
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
    return mgr, noted


@pytest.mark.parametrize("start_reply", [
    pytest.param(lambda client: client._run_external_voice_stream("你好"), id="voice"),
    pytest.param(lambda client: client.prompt_ephemeral("说说任务结果"), id="proactive"),
])
async def test_a_reply_cut_by_session_end_closes_its_ai_turn(monkeypatch, start_reply):
    mgr, noted = await _cut_by_session_end(monkeypatch, start_reply)
    assert mgr._current_ai_turn_text == "", "the next session's first turn end would carry it"
    assert noted == ["我先说一半"], "the cut reply is its own AI turn"
    assert [m["data"] for m in mgr.sync_message_queue.queue if m.get("data") == "session end"] == [
        "session end"
    ], "cross_server's turn is closed by the session end"
