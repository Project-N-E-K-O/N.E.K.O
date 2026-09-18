"""External ASR turn dispatch pauses must survive failed submissions.

``prepare_external_voice_turn`` pauses arbiter dispatch under a per-turn
pause id and ``submit_external_text_turn`` briefly resumes dispatch so an
older completed turn can flow ahead of a newer paused turn (WARM_IDLE
overlap). The newer turn's pause must be re-armed even when the older
turn's ``ticket.sent`` fails — a transport error or a newer prepare's
``cancel_current`` — or queued proactive work could dispatch ahead of the
newer turn's user text.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_realtime_client._response_arbiter import RealtimeResponseArbiter

pytestmark = pytest.mark.usefixtures("arbiter_logs_reach_caplog")


def _make_client(send) -> tuple[OmniRealtimeClient, RealtimeResponseArbiter]:
    client = OmniRealtimeClient.__new__(OmniRealtimeClient)
    client._is_gemini = False
    client._connection_generation = 0
    arbiter = RealtimeResponseArbiter(send)
    client._response_arbiter = arbiter
    return client, arbiter


async def test_failed_submit_rearms_newer_turn_pause():
    sent_events: list[dict] = []
    arbiter: RealtimeResponseArbiter | None = None

    async def send(event):
        if event["type"] == "conversation.item.create":
            raise RuntimeError("transport send failed")
        sent_events.append(dict(event))
        if event["type"] == "response.create":
            arbiter.notify_response_created({})
            arbiter.notify_response_terminal({})

    client, arbiter = _make_client(send)
    # A newer turn prepared before this older turn's final dispatch.
    client._external_voice_turn_pause_id = "turn-new"
    arbiter.pause_dispatch()

    with pytest.raises(RuntimeError):
        await client.submit_external_text_turn("hello", turn_id="turn-old")

    # The failure path must restore the newer turn's pause.
    assert client._external_voice_turn_pause_id == "turn-new"
    assert not arbiter._dispatch_allowed.is_set()

    # Queued proactive work stays gated behind the restored pause.
    proactive = await arbiter.enqueue(source="proactive")
    for _ in range(10):
        await asyncio.sleep(0)
    assert proactive.sent.done() is False
    assert sent_events == []

    client.abandon_external_voice_turn("turn-new")
    await asyncio.wait_for(proactive.sent, 1)
    assert sent_events[-1]["type"] == "response.create"
    await arbiter.shutdown()


async def test_unclaimed_dispatch_pause_expires_and_releases_the_lane(caplog):
    """A pause nobody redeems must not outlive its bound.

    The pause is a promise that some turn is about to take the lane, and only
    the party that armed it can redeem it. When that party never comes back —
    an ASR turn whose provider final never arrives, an ownership slot
    overwritten by a newer turn — there is no other release path, and the
    barrier it blocks is reached before the first provider send, so the stall
    produces no provider event and no bounded wait to report.
    """

    sent_events: list[dict] = []
    arbiter: RealtimeResponseArbiter | None = None

    async def send(event):
        sent_events.append(dict(event))
        if event["type"] == "response.create":
            arbiter.notify_response_created({})
            arbiter.notify_response_terminal({})

    _client, arbiter = _make_client(send)
    # Bound disabled first, to show what holds the lane shut: with the pause
    # unclaimed and no expiry, nothing else in the arbiter ever opens it.
    arbiter._dispatch_pause_timeout = 0
    arbiter.pause_dispatch("turn-lost")

    ticket = await arbiter.enqueue(source="external_asr")
    for _ in range(20):
        await asyncio.sleep(0.005)
    assert ticket.sent.done() is False
    assert sent_events == []

    # Same unclaimed pause, now bounded.
    arbiter._dispatch_pause_timeout = 0.05
    arbiter.pause_dispatch("turn-lost")
    with caplog.at_level("WARNING"):
        await asyncio.wait_for(ticket.sent, 2)

    assert sent_events[-1]["type"] == "response.create"
    assert arbiter._dispatch_allowed.is_set()
    assert any(
        "held paused" in record.getMessage() and "turn-lost" in record.getMessage()
        for record in caplog.records
    )
    await arbiter.shutdown()


async def test_claimed_dispatch_pause_is_not_expired():
    """Counter-test: expiry must not fire while the pause is still owned.

    Without this, the bound above would also pass for an implementation that
    simply stopped pausing, which would let queued proactive work dispatch
    ahead of a user turn that is still being prepared.
    """

    sent_events: list[dict] = []

    async def send(event):
        sent_events.append(dict(event))

    client, arbiter = _make_client(send)
    arbiter._dispatch_pause_timeout = 5.0
    client._external_voice_turn_pause_id = "turn-new"
    arbiter.pause_dispatch("turn-new")

    proactive = await arbiter.enqueue(source="proactive")
    for _ in range(20):
        await asyncio.sleep(0.01)

    assert proactive.sent.done() is False
    assert sent_events == []
    assert not arbiter._dispatch_allowed.is_set()
    await arbiter.shutdown()


async def test_prepare_leaves_a_reply_that_has_sent_nothing_alone():
    """A new user turn must not discard a reply nobody is hearing yet.

    Independent ASR cuts one spoken sentence into several turns, so a prepare
    routinely lands while the previous turn's reply is still parked before its
    first send. Cancelling that is not barge-in — nothing is being said over
    the user — and the turn it discards is a complete sentence that then never
    gets answered at all. The companion test below covers the case where the
    provider *is* already acting on the reply, which must still be cancelled.
    """

    sent_events: list[dict] = []
    arbiter: RealtimeResponseArbiter | None = None

    async def send(event):
        sent_events.append(dict(event))
        if event["type"] == "response.create":
            arbiter.notify_response_created({})
            arbiter.notify_response_terminal({})

    client, arbiter = _make_client(send)
    client.handle_interruption = AsyncMock()
    arbiter._dispatch_pause_timeout = 0

    # An older completed turn parked behind a pause: selected by the worker but
    # blocked at the barrier, so not one byte of it has reached the provider.
    arbiter.pause_dispatch("turn-hold")
    older = await arbiter.enqueue(source="external_asr")
    for _ in range(10):
        await asyncio.sleep(0)
    assert not arbiter.has_live_response

    cancels: list[int] = []
    original_cancel = arbiter.cancel_current

    async def counting_cancel(*args, **kwargs):
        cancels.append(1)
        await original_cancel(*args, **kwargs)

    arbiter.cancel_current = counting_cancel
    await client.prepare_external_voice_turn(turn_id="turn-new")

    assert cancels == []
    assert older.sent.done() is False

    # The older turn is intact and still answerable once the lane opens.
    client.abandon_external_voice_turn("turn-new")
    await asyncio.wait_for(older.sent, 2)
    assert sent_events[-1]["type"] == "response.create"
    await arbiter.shutdown()


async def test_newer_prepare_interrupt_keeps_dispatch_paused_for_new_turn():
    sent_events: list[dict] = []
    arbiter: RealtimeResponseArbiter | None = None
    release_item_send = asyncio.Event()

    async def send(event):
        if event["type"] == "conversation.item.create":
            await release_item_send.wait()
        sent_events.append(dict(event))
        if event["type"] == "response.create":
            arbiter.notify_response_created({})
            arbiter.notify_response_terminal({})

    client, arbiter = _make_client(send)
    client.handle_interruption = AsyncMock()

    submit = asyncio.create_task(
        client.submit_external_text_turn("hello", turn_id="turn-old")
    )
    for _ in range(50):
        if arbiter.current_source == "external_asr":
            break
        await asyncio.sleep(0)
    assert arbiter.current_source == "external_asr"

    # A newer turn's prepare interrupts the still-unsent older item.
    prepare = asyncio.create_task(
        client.prepare_external_voice_turn(turn_id="turn-new")
    )
    for _ in range(10):
        await asyncio.sleep(0)
    release_item_send.set()
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(submit, 2)
    await asyncio.wait_for(prepare, 2)

    # Dispatch stays paused for the newer turn's user text.
    assert client._external_voice_turn_pause_id == "turn-new"
    assert not arbiter._dispatch_allowed.is_set()
    assert all(
        event["type"] != "response.create" for event in sent_events
    )
    await arbiter.shutdown()
