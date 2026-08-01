import asyncio

import pytest

import main_logic.omni_realtime_client._response_arbiter as arbiter_module

RealtimeResponseArbiter = arbiter_module.RealtimeResponseArbiter


async def _run(deliver_stale_item_created: bool, fail_open: bool = False):
    """Server response holds the lane; B is selected while it is still live."""

    sent = []
    aborted = []
    arbiter = None

    async def send(event):
        sent.append(dict(event))
        if event["type"] == "conversation.item.create" and event["item"]["id"] == "item-B":
            # B's own item is NEVER acknowledged. One unrelated server-
            # initiated response is announced (and finishes) in the window.
            arbiter.notify_response_created(
                {"type": "response.created", "response": {"id": "resp-T"}}
            )
            arbiter.notify_response_terminal(
                {
                    "type": "response.done",
                    "response": {"id": "resp-T", "status": "completed"},
                }
            )
        # B's response.create is silently redundant: no announcement at all.

    async def abort(reason):
        aborted.append(reason)

    arbiter = RealtimeResponseArbiter(send, abort_transport=abort, fail_open=fail_open)

    # A server-initiated response is live and holds the lane.
    arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-S"}}
    )

    ticket = await arbiter.enqueue(
        source="external_asr",
        events_before_response=(
            {"type": "conversation.item.create", "item": {"id": "item-B", "role": "user"}},
        ),
        response_event={"type": "response.create"},
        ack_expected=True,
        expected_item_id="item-B",
        expected_item_role="user",
        item_ack_timeout=0.05,
        response_started_timeout=0.15,
        cancel_timeout=0.05,
    )
    # Let the worker select B: _current = B while it waits for the lane.
    for _ in range(50):
        if arbiter.current_source == "external_asr":
            break
        await asyncio.sleep(0)
    assert arbiter.current_source == "external_asr"
    assert not arbiter._idle.is_set(), "the lane must still be held by resp-S"

    if deliver_stale_item_created:
        # resp-S streams: the provider acknowledges ITS item while B, already
        # _current, has sent nothing at all.
        arbiter.notify_item_created({"item": {"id": "item-of-resp-S", "role": "assistant"}})

    queued = arbiter._queued_by_ticket[id(ticket)]
    assert queued.item_created_seen is deliver_stale_item_created, (
        "the flag is set before this request has sent a single event"
    )
    assert sent == [], "B has sent nothing yet"

    # resp-S terminates; the lane opens and B dispatches.
    arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-S", "status": "completed"}}
    )

    try:
        result = await asyncio.wait_for(ticket.done, 2.0)
        outcome = ("completed", result)
    except Exception as exc:  # noqa: BLE001
        outcome = ("failed", repr(exc))
    adopted = queued.response_id
    await arbiter.shutdown()
    return outcome, adopted, aborted, sent


@pytest.mark.asyncio
async def test_stale_flag_enables_adoption():
    outcome, adopted, aborted, sent = await _run(True)
    print("WITH stale item.created:", outcome, "adopted=", adopted, "aborted=", aborted)
    print("sent:", [e["type"] for e in sent])


@pytest.mark.asyncio
async def test_stale_flag_enables_adoption_fail_open():
    outcome, adopted, aborted, sent = await _run(True, fail_open=True)
    print("WITH stale + fail_open:", outcome, "adopted=", adopted, "aborted=", aborted)


@pytest.mark.asyncio
async def test_without_stale_flag():
    outcome, adopted, aborted, sent = await _run(False)
    print("WITHOUT stale item.created:", outcome, "adopted=", adopted, "aborted=", aborted)
    print("sent:", [e["type"] for e in sent])
