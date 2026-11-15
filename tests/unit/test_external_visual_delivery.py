import asyncio
import base64
import json
import time
from pathlib import Path
from types import MethodType
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.omni_realtime_client import _responses as responses_module
from main_logic.omni_realtime_client import (
    ImageStageResult,
    MultimodalTurnDelivery,
    OmniRealtimeClient,
    TurnDetectionMode,
)



_REAL_FIT_IMAGES = responses_module.fit_images_to_turn_budget

DUMMY_IMAGE_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsL"
    "DBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
    "2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QA"
    "HwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAFBABAAAAAAAAAAAAAA"
    "AAAAAACf/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AE0A/9k="
)


def _make_client(
    api_type: str,
    model: str,
    *,
    turn_admission_lock: asyncio.Lock | None = None,
) -> OmniRealtimeClient:
    return OmniRealtimeClient(
        base_url="wss://test.example.invalid/realtime",
        api_key="test-key",
        model=model,
        api_type=api_type,
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        turn_admission_lock=turn_admission_lock,
    )


def _wire_completed_response_transport(client: OmniRealtimeClient) -> list[dict]:
    sent: list[dict] = []

    async def send_event(_self, event, **_kwargs):
        copied = dict(event)
        sent.append(copied)
        arbiter = _self._response_arbiter
        if event["type"] == "conversation.item.create":
            arbiter.notify_item_created(
                {
                    "type": "conversation.item.created",
                    "item": {
                        "id": event["item"]["id"],
                        "type": "message",
                        "role": "user",
                    },
                }
            )
        elif event["type"] == "response.create":
            arbiter.notify_response_created({"type": "response.created"})
            arbiter.notify_response_terminal({"type": "response.done"})
        return True

    client.send_event = MethodType(send_event, client)
    client._response_arbiter._send_event = client.send_event
    return sent


@pytest.mark.parametrize(
    ("api_type", "model", "expected"),
    [
        ("openai", "gpt-4o-realtime", MultimodalTurnDelivery.DIRECT_ATOMIC),
        ("gpt", "gpt-4o-realtime", MultimodalTurnDelivery.DIRECT_ATOMIC),
        ("gemini", "gemini-2.5-flash-native-audio", MultimodalTurnDelivery.DIRECT_ATOMIC),
        ("qwen", "qwen3-omni-flash-realtime", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("glm", "glm-realtime", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("step", "step-audio-2-mini", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("grok", "grok-realtime", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("free", "free-model", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("local", "custom-realtime", MultimodalTurnDelivery.HANDOFF_REQUIRED),
    ],
)
def test_multimodal_turn_delivery_is_provider_adapter_capability(
    api_type,
    model,
    expected,
):
    client = _make_client(api_type, model)

    assert client.get_multimodal_turn_delivery() is expected


@pytest.mark.asyncio
async def test_gpt_multimodal_turn_is_one_atomic_user_item():
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    sent = _wire_completed_response_transport(client)
    client._analyze_image_with_vision_model = AsyncMock()

    await client.prepare_external_voice_turn(turn_id="turn-gpt")
    ticket = await client.submit_multimodal_turn(
        "图片里是什么？",
        DUMMY_IMAGE_B64,
        turn_id="turn-gpt",
    )

    assert ticket is not None
    assert [event["type"] for event in sent] == [
        "conversation.item.create",
        "response.create",
    ]
    content = sent[0]["item"]["content"]
    assert content == [
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64," + DUMMY_IMAGE_B64,
        },
        {"type": "input_text", "text": "图片里是什么？"},
    ]
    client._analyze_image_with_vision_model.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_gpt_multimodal_turn_carries_the_sampled_span_in_one_item():
    """The sampled span shares one user item and still triggers one reply."""
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    sent = _wire_completed_response_transport(client)

    await client.prepare_external_voice_turn(turn_id="turn-span")
    await client.submit_multimodal_turn(
        "这是什么？",
        (DUMMY_IMAGE_B64, DUMMY_IMAGE_B64, DUMMY_IMAGE_B64),
        turn_id="turn-span",
    )

    assert [event["type"] for event in sent] == [
        "conversation.item.create",
        "response.create",
    ]
    content = sent[0]["item"]["content"]
    assert [part["type"] for part in content] == [
        "input_image",
        "input_image",
        "input_image",
        "input_text",
    ]
    await client.close()


@pytest.mark.asyncio
async def test_gpt_multimodal_turn_caps_frames_at_the_per_turn_budget():
    """Provider-side floor: an item written into the conversation is final."""
    from config import MAX_MULTIMODAL_TURN_IMAGES

    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    sent = _wire_completed_response_transport(client)

    await client.prepare_external_voice_turn(turn_id="turn-flood")
    await client.submit_multimodal_turn(
        "这是什么？",
        tuple(DUMMY_IMAGE_B64 for _ in range(12)),
        turn_id="turn-flood",
    )

    content = sent[0]["item"]["content"]
    images = [part for part in content if part["type"] == "input_image"]
    assert len(images) == MAX_MULTIMODAL_TURN_IMAGES
    await client.close()


@pytest.mark.asyncio
async def test_new_external_turn_rejects_superseded_multimodal_ticket():
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    client.handle_interruption = AsyncMock()
    _wire_completed_response_transport(client)
    arbiter = client._response_arbiter
    real_enqueue = arbiter.enqueue
    old_ticket_queued = asyncio.Event()
    release_enqueue_return = asyncio.Event()

    async def enqueue_then_pause_return(*args, **kwargs):
        ticket = await real_enqueue(*args, **kwargs)
        old_ticket_queued.set()
        await release_enqueue_return.wait()
        return ticket

    arbiter.enqueue = enqueue_then_pause_return
    await client.prepare_external_voice_turn(turn_id="turn-old")
    old_submit = asyncio.create_task(
        client.submit_multimodal_turn(
            "旧问题",
            DUMMY_IMAGE_B64,
            turn_id="turn-old",
        )
    )
    await old_ticket_queued.wait()

    await client.prepare_external_voice_turn(turn_id="turn-new")
    release_enqueue_return.set()

    with pytest.raises(RuntimeError, match="admission rejected"):
        await old_submit
    client.abandon_external_voice_turn("turn-new")
    await client.close()


@pytest.mark.asyncio
async def test_gemini_multimodal_turn_is_one_content_with_image_and_text():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock()

    await client.prepare_external_voice_turn(turn_id="turn-gemini")
    result = await client.submit_multimodal_turn(
        "看一下这张图",
        DUMMY_IMAGE_B64,
        turn_id="turn-gemini",
    )

    assert result is None
    session.send_client_content.assert_awaited_once()
    kwargs = session.send_client_content.await_args.kwargs
    assert kwargs["turn_complete"] is True
    assert len(kwargs["turns"]) == 1
    content = kwargs["turns"][0]
    assert content.role == "user"
    assert len(content.parts) == 2
    assert bytes(content.parts[0].inline_data.data)
    assert content.parts[0].inline_data.mime_type == "image/jpeg"
    assert content.parts[1].text == "看一下这张图"
    client._analyze_image_with_vision_model.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_gemini_turn_drops_frames_lost_while_fitting_the_image_budget():
    """Losing visual ownership during compression degrades to text-only.

    Fitting an over-budget turn runs the compressor on a worker thread, which
    is a real yield point. A successor utterance can complete
    _begin_core_multimodal_turn (which synchronously invalidates this record),
    prepare_external_voice_turn and handle_interruption inside that window --
    the two paths do not exclude each other, since neither takes the other's
    lock. Sending the frames anyway hands the provider images that no longer
    belong to this turn, and Core never learns about it, so the existing
    "degrade to text-only" exit is never reached.

    The sentence still goes out: dropping frames may only downgrade a turn to
    text-only, never discard what the user said.
    """
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock()

    owned = [True]

    async def _fit_and_lose_ownership(images, _budget):
        # Compression genuinely yields here; the next utterance takes visual ownership.
        owned[0] = False
        return list(images), {
            "original_count": len(images),
            "final_count": len(images),
            "sampled": False,
            "compressed": True,
            "dropped": 0,
        }

    responses_module.fit_images_to_turn_budget = _fit_and_lose_ownership
    try:
        await client.prepare_external_voice_turn(turn_id="turn-lost")
        await client.submit_multimodal_turn(
            "看一下这张图",
            DUMMY_IMAGE_B64,
            turn_id="turn-lost",
            visual_still_owned=lambda: owned[0],
        )
    finally:
        responses_module.fit_images_to_turn_budget = _REAL_FIT_IMAGES

    session.send_client_content.assert_awaited_once()
    content = session.send_client_content.await_args.kwargs["turns"][0]
    # The frame was dropped...
    assert all(part.inline_data is None for part in content.parts)
    # ...but the user's utterance still sends.
    assert any(part.text == "看一下这张图" for part in content.parts)
    client._analyze_image_with_vision_model.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_openai_turn_drops_frames_lost_while_shrinking_the_item():
    """Dual of the Gemini path for the WebSocket branch.

    An oversized item is recompressed on a worker thread -- a real yield point.
    The successor's _begin_core_multimodal_turn() invalidates this record
    synchronously, but its prepare_external_voice_turn() may still be blocked on
    the shared turn-admission lock and not yet have updated
    _external_voice_turn_pause_id, so the ticket's admission_check still passes
    on the unchanged pause id and superseded frames reach provider history.

    Images are dropped in place rather than letting admission_check reject the
    whole ticket: that rejection happens AFTER the item is committed and needs
    an unconfirmed compensating delete (issue #2982).
    """
    client = _make_client("openai", "gpt-4o-realtime-preview")
    client.ws = AsyncMock()
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock()

    owned = [True]
    captured: list = []

    async def _shrink_and_lose_ownership(_fn, item_event, _payload):
        # Recompression genuinely yields here; the next utterance takes visual ownership.
        owned[0] = False
        return "shrunk"

    async def _fake_enqueue(**kwargs):
        event = kwargs["events_before_response"][0]
        # Faithfully simulate the arbiter: call pre_commit before committing. Ownership was lost during recompression,
        # after pre-enqueue degradation still retained it; pre_commit must cover this path.
        kwargs["pre_commit"](event)
        captured.append(event)
        # Use a completed future rather than asyncio.sleep(0): submit_multimodal_turn awaits only
        # ticket.sent, never the done coroutine, which would produce
        # RuntimeWarning and fail under warnings-as-errors.
        loop = asyncio.get_running_loop()
        sent, done = loop.create_future(), loop.create_future()
        sent.set_result(None)
        done.set_result(None)
        return SimpleNamespace(sent=sent, done=done)

    _arbiter = SimpleNamespace(
        enqueue=_fake_enqueue,
        resume_dispatch=lambda: None,
        pause_dispatch=lambda owner=None: None,
        begin_turn_preparation=lambda owner=None: None,
        end_turn_preparation=lambda _token=None: None,
        # These turns are about visual delivery, not barge-in policy: keep the
        # double reporting a live reply so prepare still cancels, as it did
        # unconditionally when these expectations were written.
        has_live_response=True,
        allow_ticket_while_paused=lambda ticket: None,
        cancel_ticket=AsyncMock(),
        cancel_current=AsyncMock(),
    )
    client._ensure_response_arbiter = lambda: _arbiter
    asyncio_to_thread = asyncio.to_thread
    asyncio.to_thread = _shrink_and_lose_ownership
    _real_limit = responses_module.OMNI_WS_FRAME_LIMIT_BYTES
    # Set frame caps low enough to force recompression, the yield point this case must cover.
    responses_module.OMNI_WS_FRAME_LIMIT_BYTES = 8
    try:
        await client.prepare_external_voice_turn(turn_id="turn-ws")
        assert owned[0] is True
        await client.submit_multimodal_turn(
            "看一下这张图",
            [DUMMY_IMAGE_B64] * 3,
            turn_id="turn-ws",
            visual_still_owned=lambda: owned[0],
        )
    finally:
        asyncio.to_thread = asyncio_to_thread
        responses_module.OMNI_WS_FRAME_LIMIT_BYTES = _real_limit

    assert owned[0] is False, "夹具没走到重压那一步"
    assert captured, "item 没被送进 arbiter"
    content = captured[0]["item"]["content"]
    # Frames removed...
    assert all(part["type"] != "input_image" for part in content)
    # ...but the user's utterance still sends.
    assert any(part.get("text") == "看一下这张图" for part in content)
    await client.close()


@pytest.mark.asyncio
async def test_lost_ownership_downgrades_before_the_oversize_check_fails_the_turn():
    """Frames already forfeited must not fail the whole turn on size.

    The oversize path deliberately raises RealtimeImagePayloadTooLargeError so
    Core can fail the turn closed rather than silently dropping images. But if
    visual ownership is already gone those frames were never going to be sent,
    and raising there kills a turn that should merely have degraded to
    text-only -- the sentence would be lost, which this PR forbids outright.

    So the downgrade runs before the size check.
    """
    client = _make_client("openai", "gpt-4o-realtime-preview")
    client.ws = AsyncMock()
    client.handle_interruption = AsyncMock()
    captured: list = []

    async def _fake_enqueue(**kwargs):
        event = kwargs["events_before_response"][0]
        kwargs["pre_commit"](event)
        captured.append(event)
        loop = asyncio.get_running_loop()
        sent, done = loop.create_future(), loop.create_future()
        sent.set_result(None)
        done.set_result(None)
        return SimpleNamespace(sent=sent, done=done)

    _arbiter = SimpleNamespace(
        enqueue=_fake_enqueue,
        resume_dispatch=lambda: None,
        pause_dispatch=lambda owner=None: None,
        cancel_ticket=AsyncMock(),
        begin_turn_preparation=lambda owner=None: None,
        end_turn_preparation=lambda _token=None: None,
        has_live_response=True,
        allow_ticket_while_paused=lambda ticket: None,
        cancel_current=AsyncMock(),
    )
    client._ensure_response_arbiter = lambda: _arbiter

    # Pin the unshrinkable precondition: the raising path requires _try_shrink_image_payload
    # to return None. Otherwise recompression might succeed by chance, bypassing the intended
    # falsification point, as in version one, which passed even without degradation.
    client._try_shrink_image_payload = lambda *_a: None
    _real_limit = responses_module.OMNI_WS_FRAME_LIMIT_BYTES
    # Choose a limit exceeded with three images but not without images, exercising
    # degradation-before-size-check ordering.
    responses_module.OMNI_WS_FRAME_LIMIT_BYTES = 3 * len(DUMMY_IMAGE_B64)
    try:
        await client.prepare_external_voice_turn(turn_id="turn-oversize")
        await client.submit_multimodal_turn(
            "看一下这张图",
            [DUMMY_IMAGE_B64] * 3,
            turn_id="turn-oversize",
            visual_still_owned=lambda: False,
        )
    finally:
        responses_module.OMNI_WS_FRAME_LIMIT_BYTES = _real_limit

    assert captured, "整轮被超限判死了——降级应当跑在大小检查之前"
    content = captured[0]["item"]["content"]
    assert all(part["type"] != "input_image" for part in content)
    assert any(part.get("text") == "看一下这张图" for part in content)
    await client.close()


@pytest.mark.asyncio
async def test_send_event_downgrades_inside_the_transport_critical_section():
    """The last await before the write is the send semaphore.

    send_event() serializes the payload only after acquiring _send_semaphore,
    so an item queued behind another transport write can lose visual ownership
    while waiting. Running the downgrade inside that critical section, before
    json.dumps, means the mutation is picked up without re-serializing.

    Asserted on what actually reached the socket, not on the dict we passed in:
    a hook that ran after serialization would leave the images on the wire.
    """
    client = _make_client("openai", "gpt-4o-realtime-preview")
    written: list = []

    class _Sock:
        async def send(self, payload):
            written.append(payload)

        async def close(self):
            pass

    client.ws = _Sock()

    event = {
        "type": "conversation.item.create",
        "item": {
            "role": "user",
            "content": [
                {"type": "input_image", "image_url": "data:image/jpeg;base64,x"},
                {"type": "input_text", "text": "look"},
            ],
        },
    }

    def _strip_images(ev):
        item = ev["item"]
        item["content"] = [
            part for part in item["content"] if part.get("type") != "input_image"
        ]

    assert await client.send_event(event, pre_send=_strip_images) is True

    assert written, "什么都没写出去"
    payload = json.loads(written[0])
    content = payload["item"]["content"]
    assert all(part["type"] != "input_image" for part in content)
    assert any(part.get("text") == "look" for part in content)
    await client.close()


@pytest.mark.asyncio
async def test_openai_turn_drops_frames_lost_between_enqueue_and_dispatch():
    """The pre-enqueue check cannot cover the arbiter's own waits.

    Between enqueue() and the transport write the arbiter still waits on an
    active response and on the send semaphore. A successor onset invalidates
    the record synchronously while its prepare_external_voice_turn() is blocked
    on the Core swap/admission lock, so the ticket's admission_check -- which
    reads only _external_voice_turn_pause_id -- still passes and the superseded
    images get committed.

    The pre_commit hook runs immediately before _worker_send, which is the
    actual transport boundary. It downgrades the item rather than rejecting the
    ticket: a rejection happens AFTER the item is committed and needs an
    unconfirmed compensating delete (issue #2982).
    """
    client = _make_client("openai", "gpt-4o-realtime-preview")
    client.ws = AsyncMock()
    client.handle_interruption = AsyncMock()

    owned = [True]
    captured: list = []

    enqueue_kwargs: list = []

    async def _fake_enqueue(**kwargs):
        # Simulate arbiter waiting after enqueue and before commit; the next turn takes
        # visual ownership within this window.
        enqueue_kwargs.append(kwargs)
        event = kwargs["events_before_response"][0]
        owned[0] = False
        kwargs["pre_commit"](event)
        captured.append(event)
        loop = asyncio.get_running_loop()
        sent, done = loop.create_future(), loop.create_future()
        sent.set_result(None)
        done.set_result(None)
        return SimpleNamespace(sent=sent, done=done)

    _arbiter = SimpleNamespace(
        enqueue=_fake_enqueue,
        resume_dispatch=lambda: None,
        pause_dispatch=lambda owner=None: None,
        cancel_ticket=AsyncMock(),
        begin_turn_preparation=lambda owner=None: None,
        end_turn_preparation=lambda _token=None: None,
        has_live_response=True,
        allow_ticket_while_paused=lambda ticket: None,
        cancel_current=AsyncMock(),
    )
    client._ensure_response_arbiter = lambda: _arbiter

    await client.prepare_external_voice_turn(turn_id="turn-late")
    # Prove ownership remains when entering the arbiter, or this tests the preceding window instead.
    assert owned[0] is True
    await client.submit_multimodal_turn(
        "看一下这张图",
        DUMMY_IMAGE_B64,
        turn_id="turn-late",
        visual_still_owned=lambda: owned[0],
    )

    assert captured, "item 没被送进 arbiter"
    content = captured[0]["item"]["content"]
    assert all(part["type"] != "input_image" for part in content)
    assert any(part.get("text") == "看一下这张图" for part in content)

    # Also prove the **third** gate is wired: ticket.event_sender must pass the same degradation
    # function into send_event's critical section, where semaphore waits may flip ownership.
    # Checking only pre_commit cannot detect disconnected wiring there.
    sender = enqueue_kwargs[0].get("event_sender")
    assert sender is not None, "ticket 没带 event_sender，传输临界区那道闸门没接上"
    written: list = []

    class _Sock:
        async def send(self, payload):
            written.append(payload)

        async def close(self):
            pass

    client.ws = _Sock()
    await sender({
        "type": "conversation.item.create",
        "item": {
            "role": "user",
            "content": [
                {"type": "input_image", "image_url": "data:image/jpeg;base64,x"},
                {"type": "input_text", "text": "看一下这张图"},
            ],
        },
    })
    assert written, "event_sender 没把事件写出去"
    sent_content = json.loads(written[0])["item"]["content"]
    assert all(part["type"] != "input_image" for part in sent_content)
    await client.close()


@pytest.mark.asyncio
async def test_gemini_turn_drops_frames_lost_during_the_quarantine_wait():
    """Ownership can still flip after the budget fit, inside the quarantine wait.

    _submit_external_gemini_turn() awaits _await_gemini_external_quarantine()
    when a previous turn's token is still outstanding, and a successor can
    invalidate this record inside that wait. Checking only after the budget fit
    therefore misses it -- and checking there at all only ran when the budget
    actually trimmed something. The check belongs immediately before the SDK
    send.

    The sentence still goes out; only the frames are dropped.
    """
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock()

    owned = [True]
    quarantine_waits = [0]

    async def _lose_ownership_on_the_submit_wait():
        # Quarantine wait runs twice: in prepare_external_voice_turn,
        # then at _submit_external_gemini_turn entry. Lose ownership only on the **second**;
        # otherwise it is gone before submit_multimodal_turn starts, and this case passes regardless
        # of recheck placement, failing to pin its intended window.
        quarantine_waits[0] += 1
        if quarantine_waits[0] >= 2:
            owned[0] = False

    client._await_gemini_external_quarantine = _lose_ownership_on_the_submit_wait

    await client.prepare_external_voice_turn(turn_id="turn-quarantine")
    # Enter the quarantine-wait branch at _submit_external_gemini_turn entry.
    #
    # ⚠ Install **after prepare**. prepare's
    # _start_gemini_external_submit_quarantine() launches background quarantine for an existing token;
    # scheduling it settles the token to None, making the second quarantine-wait branch
    # unreachable. Installing before prepare used to pass only because submit happened to contain
    # no yield allowing background work to run, an accident rather than a criterion. Image budgeting now always
    # uses asyncio.to_thread to normalize each image to model tier; that additional await lets
    # background work finish first and breaks the test. Install after prepare to guarantee the precondition
    # independently of awaits on the tested path.
    client._gemini_external_outcome_token = object()
    # Prove ownership remains on entering the tested function.
    assert owned[0] is True
    await client.submit_multimodal_turn(
        "看一下这张图",
        DUMMY_IMAGE_B64,
        turn_id="turn-quarantine",
        visual_still_owned=lambda: owned[0],
    )

    # Prove ownership is lost on the **second**, internal-submit wait.
    assert quarantine_waits[0] >= 2
    assert owned[0] is False
    session.send_client_content.assert_awaited_once()
    content = session.send_client_content.await_args.kwargs["turns"][0]
    assert all(part.inline_data is None for part in content.parts)
    assert any(part.text == "看一下这张图" for part in content.parts)
    await client.close()


@pytest.mark.asyncio
async def test_gemini_multimodal_turn_carries_the_sampled_span_in_one_content():
    """The sampled span shares one Content and still triggers one reply."""
    from config import MAX_MULTIMODAL_TURN_IMAGES

    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()

    await client.prepare_external_voice_turn(turn_id="turn-gemini-span")
    await client.submit_multimodal_turn(
        "看一下这张图",
        tuple(DUMMY_IMAGE_B64 for _ in range(9)),
        turn_id="turn-gemini-span",
    )

    session.send_client_content.assert_awaited_once()
    kwargs = session.send_client_content.await_args.kwargs
    assert len(kwargs["turns"]) == 1
    parts = kwargs["turns"][0].parts
    # Provider-side independent fallback discards extras.
    assert len(parts) == MAX_MULTIMODAL_TURN_IMAGES + 1
    assert all(part.inline_data is not None for part in parts[:-1])
    assert parts[-1].text == "看一下这张图"
    await client.close()


@pytest.mark.parametrize(
    ("api_type", "model"),
    [
        ("qwen", "qwen3-omni-flash-realtime"),
        ("glm", "glm-realtime"),
        ("step", "step-audio-2-mini"),
        ("grok", "grok-realtime"),
        ("free", "free-model"),
        ("local", "custom-realtime"),
    ],
)
@pytest.mark.asyncio
async def test_unsupported_realtime_multimodal_turn_fails_closed_without_send(
    api_type,
    model,
):
    client = _make_client(api_type, model)
    client.ws = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock()

    with pytest.raises(RuntimeError, match="requires VLM handoff"):
        await client.submit_multimodal_turn(
            "不要退化成纯文本",
            DUMMY_IMAGE_B64,
            turn_id="turn-handoff",
        )

    client.ws.send.assert_not_awaited()
    client._analyze_image_with_vision_model.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_multimodal_turn_rejects_invalid_image_before_provider_send():
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()

    with pytest.raises(ValueError, match="valid base64"):
        await client.submit_multimodal_turn(
            "这张图坏了",
            "not-base64!",
            turn_id="turn-invalid",
        )

    client.ws.send.assert_not_awaited()
    await client.close()


def test_stage_multimodal_frame_only_updates_raw_cache():
    client = _make_client("qwen", "qwen3-omni-flash-realtime")
    client._analyze_image_with_vision_model = AsyncMock()

    result = client.stage_multimodal_frame(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="screen-1",
        captured_at=10.0,
    )

    assert result == ImageStageResult(
        accepted=True,
        mode="staged",
        generation=1,
    )
    assert client._latest_image_b64 == DUMMY_IMAGE_B64
    assert client._latest_image_captured_at == 10.0
    assert client._latest_image_source == "screen"
    assert client._latest_image_request_id == "screen-1"
    client._analyze_image_with_vision_model.assert_not_called()


def test_stage_multimodal_frame_rejects_stale_capture():
    client = _make_client("qwen", "qwen3-omni-flash-realtime")
    first = client.stage_multimodal_frame(DUMMY_IMAGE_B64, captured_at=20.0)
    stale = client.stage_multimodal_frame(DUMMY_IMAGE_B64, captured_at=19.0)

    assert first.accepted is True
    assert stale == ImageStageResult(
        accepted=False,
        mode="staged",
        generation=1,
        rejection_reason="stale_frame",
    )
    assert client._latest_image_captured_at == 20.0


@pytest.mark.parametrize(
    ("api_type", "model"),
    [
        ("openai", "gpt-4o-realtime"),
        ("gemini", "gemini-2.5-flash-native-audio"),
    ],
)
@pytest.mark.asyncio
async def test_callback_owned_image_bypasses_raw_frame_fence_only(
    api_type,
    model,
):
    client = _make_client(api_type, model)
    provider = AsyncMock()
    client.ws = provider
    if api_type == "gemini":
        client._gemini_session = provider
    client.block_raw_visual_delivery()

    ambient = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        bypass_rate_limit=True,
    )
    proactive = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="proactive",
        bypass_rate_limit=True,
        cache_latest=False,
    )
    callback_cached = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="callback",
        bypass_rate_limit=True,
    )
    callback = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="callback",
        bypass_rate_limit=True,
        cache_latest=False,
    )

    assert ambient.accepted is False
    assert ambient.rejection_reason == "raw_visual_delivery_blocked"
    assert proactive.accepted is False
    assert proactive.rejection_reason == "raw_visual_delivery_blocked"
    assert callback_cached.accepted is False
    assert callback_cached.rejection_reason == "raw_visual_delivery_blocked"
    assert callback.accepted is True
    assert client._latest_image_b64 is None
    if api_type == "gemini":
        provider.send_realtime_input.assert_awaited_once()
    else:
        provider.send.assert_awaited_once()
    await client.close()


@pytest.mark.asyncio
async def test_callback_fence_bypass_still_rejects_visual_mode_change_before_send():
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    client.block_raw_visual_delivery()
    client._send_semaphore = asyncio.Semaphore(1)
    await client._send_semaphore.acquire()
    sending = asyncio.create_task(
        client.stream_image(
            DUMMY_IMAGE_B64,
            source="callback",
            bypass_rate_limit=True,
            cache_latest=False,
        )
    )
    await asyncio.sleep(0)

    client.set_visual_delivery_mode("external_description")
    client._send_semaphore.release()
    result = await sending

    assert result.accepted is False
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_loud_pcm_publishes_activity_before_provider_admission():
    admission_lock = asyncio.Lock()
    client = _make_client(
        "openai",
        "gpt-4o-realtime",
        turn_admission_lock=admission_lock,
    )
    client.ws = AsyncMock()
    client._resample_uplink = lambda audio: audio
    client._user_recent_activity_time = 0.0
    await admission_lock.acquire()
    loud_pcm = (1_000).to_bytes(2, "little", signed=True) * 512

    streaming = asyncio.create_task(client.stream_audio(loud_pcm))
    await asyncio.sleep(0)

    assert client._user_recent_activity_time > 0.0
    assert not streaming.done()
    client.ws.send.assert_not_awaited()

    admission_lock.release()
    await streaming
    client.ws.send.assert_awaited_once()
    await client.close()


@pytest.mark.asyncio
async def test_step_legacy_one_shot_annotation_remains_outside_asr_routing_scope():
    client = _make_client("step", "step-audio-2-mini")
    client.ws = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock(
        return_value="legacy Step description"
    )

    result = await client.stream_image(
        DUMMY_IMAGE_B64,
        cache_latest=False,
        bypass_rate_limit=True,
        source="callback",
        request_id="callback-1",
    )

    assert result == ImageStageResult(
        accepted=True,
        mode="external_description",
        generation=0,
        description="legacy Step description",
    )
    client._analyze_image_with_vision_model.assert_awaited_once_with(
        DUMMY_IMAGE_B64,
        update_turn_state=False,
    )
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_proactive_prompt_ephemeral_keeps_environment_annotation_exception(
    monkeypatch,
):
    client = _make_client("step", "step-audio-2-mini")
    client.ws = AsyncMock()
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    analyze_image = AsyncMock(return_value="屏幕上显示番茄钟结束提醒")
    monkeypatch.setattr(
        "utils.screenshot_utils.analyze_image_with_vision_model",
        analyze_image,
    )
    real_analyze = client._analyze_image_with_vision_model
    client._analyze_image_with_vision_model = AsyncMock(wraps=real_analyze)
    injected: dict = {}

    async def inject_text(text, **kwargs):
        injected["text"] = text
        injected.update(kwargs)
        kwargs["on_completed"]()
        return object()

    client.inject_text_and_request_response = AsyncMock(side_effect=inject_text)

    staged = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="proactive-screen",
    )
    delivered = await client.prompt_ephemeral("主动看看屏幕")

    assert staged.accepted is True
    assert delivered is True
    assert injected["text"] == "主动看看屏幕"
    visual_event = injected["events_before_text"][0]
    visual_text = visual_event["item"]["content"][0]["text"]
    assert "屏幕上显示番茄钟结束提醒" in visual_text
    client._analyze_image_with_vision_model.assert_awaited_once_with(
        DUMMY_IMAGE_B64
    )
    analyze_image.assert_awaited_once()
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_native_qwen_streaming_remains_audio_buffer_bound():
    client = _make_client("qwen", "qwen3-omni-flash-realtime")
    client.ws = AsyncMock()
    client._audio_in_buffer = True

    result = await client.stream_image(DUMMY_IMAGE_B64)

    assert result.accepted is True
    sent = client.ws.send.await_args.args[0]
    assert "input_image_buffer.append" in sent
    await client.close()


@pytest.mark.asyncio
async def test_new_gemini_turn_cancels_multimodal_sdk_send_before_returning():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()
    send_started = asyncio.Event()
    send_cancelled = asyncio.Event()

    async def send_client_content(*_args, **_kwargs):
        send_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            send_cancelled.set()
            raise

    session.send_client_content.side_effect = send_client_content
    await client.prepare_external_voice_turn(turn_id="turn-old")
    old_submit = asyncio.create_task(
        client.submit_multimodal_turn(
            "旧问题",
            DUMMY_IMAGE_B64,
            turn_id="turn-old",
        )
    )
    await send_started.wait()

    await client.prepare_external_voice_turn(turn_id="turn-new")

    assert send_cancelled.is_set()
    with pytest.raises(asyncio.CancelledError):
        await old_submit
    client.abandon_external_voice_turn("turn-new")
    await client.close()


@pytest.mark.asyncio
async def test_new_gemini_turn_quarantines_accepted_multimodal_turn():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    old_session = AsyncMock()
    old_context = AsyncMock()
    old_context.__aexit__ = AsyncMock()
    client._gemini_session = old_session
    client._gemini_context_manager = old_context
    client.ws = old_session
    client.instructions = "system prompt"
    client._native_audio = True
    client.handle_interruption = AsyncMock()
    replacement_session = AsyncMock()

    async def reconnect(*_args, **_kwargs):
        client._connection_generation += 1
        client._gemini_session = replacement_session
        client.ws = replacement_session

    client.connect = AsyncMock(side_effect=reconnect)
    await client.prepare_external_voice_turn(turn_id="turn-old")
    await client.submit_multimodal_turn(
        "Gemini 已接受",
        DUMMY_IMAGE_B64,
        turn_id="turn-old",
    )

    assert client._gemini_external_submit_task is None
    assert client._gemini_external_outcome_token is not None

    reconnected = await client.prepare_external_voice_turn(turn_id="turn-new")

    old_context.__aexit__.assert_awaited_once_with(None, None, None)
    client.connect.assert_awaited_once_with("system prompt", native_audio=True)
    assert client._gemini_session is replacement_session
    assert client._gemini_external_outcome_token is None
    assert reconnected is True
    client.abandon_external_voice_turn("turn-new")
    await client.close()


@pytest.mark.asyncio
async def test_retired_gemini_handler_cannot_settle_replacement_external_turn():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    release_old_handler = asyncio.Event()

    class OldSession:
        def receive(self):
            async def responses():
                await release_old_handler.wait()
                raise RuntimeError("closed")
                yield None

            return responses()

    client._gemini_session = OldSession()
    old_handler = asyncio.create_task(client._handle_messages_gemini())
    await asyncio.sleep(0)

    client._connection_generation += 1
    replacement_token = object()
    client._gemini_external_outcome_token = replacement_token
    release_old_handler.set()
    await old_handler

    assert client._gemini_external_outcome_token is replacement_token
    client._gemini_external_outcome_token = None
    await client.close()


@pytest.mark.asyncio
async def test_gemini_text_only_external_asr_stays_text_only():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session

    await client.submit_external_voice_turn("只有转写", turn_id="turn-text")

    kwargs = session.send_client_content.await_args.kwargs
    content = kwargs["turns"][0]
    assert len(content.parts) == 1
    assert content.parts[0].text == "只有转写"
    await client.close()


@pytest.mark.asyncio
async def test_proactive_nudge_still_speaks_while_raw_frames_are_fenced():
    """A fenced raw route means no visual, not no nudge.

    Independent ASR arms the session's raw-frame fence but keeps the
    latest-frame cache warm for proactive observation. Treating that cached
    frame as deliverable makes every proactive turn fail its native image
    inject and return without sending even its text -- and a screen share
    keeps rearming the cache, so she stays silent for the whole session.
    """
    client = _make_client("openai", "gpt-4o-realtime-preview")
    client.ws = AsyncMock()
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    injected: dict = {}

    async def inject_text(text, **kwargs):
        injected["text"] = text
        injected.update(kwargs)
        kwargs["on_completed"]()
        return object()

    client.inject_text_and_request_response = AsyncMock(side_effect=inject_text)
    assert client._supports_native_image is True

    # Core owns the frames while independent ASR runs: the cache is kept warm
    # without the frame ever being allowed onto the provider connection.
    client.block_raw_visual_delivery()
    staged = client.stage_multimodal_frame(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="independent-screen-1",
    )
    assert staged.accepted is True
    assert client._latest_image_b64 == DUMMY_IMAGE_B64

    delivered = await client.prompt_ephemeral("主动看看屏幕")

    assert delivered is True
    assert injected["text"] == "主动看看屏幕"
    assert not injected.get("events_before_text")
    # Frame unconsumed: reusable after the fence lifts.
    assert client._proactive_image_consumed is False
    await client.close()


@pytest.mark.asyncio
async def test_live_gemini_external_turn_counts_as_an_active_response():
    """Gemini owns the turn before its first content event arrives.

    Between the SDK send returning and the first model content, ``_is_responding``
    is still false and the arbiter is idle, yet the provider has already accepted
    the external-ASR turn (`_gemini_external_outcome_token` stays live until
    turn_complete/interrupted). A queued proactive callback that passes every
    busy check in that window submits a second, unscoped Gemini turn.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._is_responding = False
    assert client.is_active_response() is False

    client._gemini_external_outcome_token = object()
    assert client.is_active_response() is True

    # Return idle after the terminal edge lands.
    client._gemini_external_outcome_token = None
    assert client.is_active_response() is False
    await client.close()


@pytest.mark.asyncio
async def test_successive_gemini_external_turns_quarantine_the_live_predecessor():
    """A second external turn must not silently overwrite a live one.

    An overlapping utterance is prepared before the previous turn's dispatcher
    reaches the SDK send, so the prepare-time quarantine finds no outcome token
    to retire. If this path then mints a fresh token over the live one, two
    Gemini turns coexist: their responses can interleave, and the newer turn's
    ownership can be carried off by the older turn's terminal.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    order = []

    async def send_user_turn(_text, *, images_bytes=()):
        order.append("send")

    async def await_quarantine():
        order.append("await_quarantine")
        client._gemini_external_outcome_token = None

    client._gemini_send_user_turn = AsyncMock(side_effect=send_user_turn)
    client._start_gemini_external_submit_quarantine = MagicMock(
        side_effect=lambda *a, **k: order.append("start_quarantine")
    )
    client._await_gemini_external_quarantine = AsyncMock(
        side_effect=await_quarantine
    )

    # Previous round remains pending without its terminal event.
    stale_token = object()
    client._gemini_external_outcome_token = stale_token

    await client._submit_external_gemini_turn("第二句")

    assert order == ["start_quarantine", "await_quarantine", "send"]
    # New round gets its own token, not the overwritten old one.
    assert client._gemini_external_outcome_token is not None
    assert client._gemini_external_outcome_token is not stale_token
    await client.close()


@pytest.mark.asyncio
async def test_cancelled_external_submit_keeps_its_token_and_arms_quarantine():
    """Cancellation only ends our await; Gemini may already have taken the turn.

    TranscriptDispatcher.invalidate_all() propagates cancellation down the
    worker to here, long after send was handed to the SDK. Settling the token
    as "never sent" announces that no turn is in flight, so the next prepare
    starts no quarantine and that turn's late transcript/response bleeds into
    its successor.

    Both assertions are needed. Asserting only "the token survives" would also
    pass for an implementation that pins the token forever and wedges the
    session busy (is_active_response() reads exactly that token); asserting
    only "a quarantine was armed" would miss the premature settle.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    send_started = asyncio.Event()
    release = asyncio.Event()

    async def _stalling_send(*_args, **_kwargs):
        send_started.set()
        await release.wait()

    client._gemini_send_user_turn = AsyncMock(side_effect=_stalling_send)
    quarantine_args = []

    async def _fake_quarantine(submit_task, outcome_token):
        quarantine_args.append((submit_task, outcome_token))

    client._quarantine_gemini_external_submit = _fake_quarantine
    client._gemini_external_outcome_token = None

    submit = asyncio.create_task(client._submit_external_gemini_turn("被打断的一句"))
    await asyncio.wait_for(send_started.wait(), timeout=1)
    token_in_flight = client._gemini_external_outcome_token
    assert token_in_flight is not None

    submit.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submit

    # Not settled as undelivered.
    assert client._gemini_external_outcome_token is token_in_flight
    # Quarantine armed for this exact submit/token.
    assert client._gemini_external_quarantine_task is not None
    await asyncio.wait_for(
        asyncio.shield(client._gemini_external_quarantine_task), timeout=1
    )
    assert quarantine_args == [(submit, token_in_flight)]
    release.set()
    await client.close()


@pytest.mark.asyncio
async def test_synchronous_external_send_failure_still_settles_immediately():
    """Dual: a synchronous refusal really did not send, so settle right away.

    Otherwise a token that can never receive a terminal event pins the session
    busy and she never speaks on her own again.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._gemini_send_user_turn = AsyncMock(
        side_effect=RuntimeError("provider refused")
    )
    client._quarantine_gemini_external_submit = AsyncMock()
    client._gemini_external_outcome_token = None

    with pytest.raises(RuntimeError, match="provider refused"):
        await client._submit_external_gemini_turn("发不出去的一句")

    assert client._gemini_external_outcome_token is None
    client._quarantine_gemini_external_submit.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_first_gemini_external_turn_does_not_pay_for_quarantine():
    """No live predecessor means no connection retirement."""
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._gemini_send_user_turn = AsyncMock()
    client._start_gemini_external_submit_quarantine = MagicMock()
    client._await_gemini_external_quarantine = AsyncMock()
    client._gemini_external_outcome_token = None

    await client._submit_external_gemini_turn("第一句")

    client._start_gemini_external_submit_quarantine.assert_not_called()
    client._await_gemini_external_quarantine.assert_not_awaited()
    client._gemini_send_user_turn.assert_awaited_once()
    await client.close()


@pytest.mark.asyncio
async def test_a_bare_turn_complete_still_settles_the_external_token():
    """A content-free terminal must not strand the token.

    Gemini can end a turn with just ``turn_complete`` -- no ``model_turn``, no
    ``output_transcription`` -- and that path never advances the turn epoch.
    Anything that gates settling on the epoch therefore refuses forever, and
    ``is_active_response()`` reports busy for the rest of the session: she
    stops speaking up on her own. The owed-terminal credit is the only gate.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_cancelled_terminal_pending = False
    # Empty terminal event: epoch never advanced.
    client._current_turn_epoch = 0
    client._turn_epoch = 0
    client._is_responding = True

    bare_terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=bare_terminal, tool_call=None),
        connection_generation=1,
    )

    assert client._gemini_external_outcome_token is None
    await client.close()

@pytest.mark.asyncio
async def test_late_continuation_terminal_cannot_settle_the_external_token():
    """The epoch alone is not enough: late content advances it too.

    A response cancelled by ``handle_interruption()`` can still emit AI content
    before its terminal, and that content bumps ``_current_turn_epoch`` whether
    or not it is a new turn. The owed-terminal credit binds the terminal to the
    response it belongs to instead of to a clock.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_external_token_epoch = 7
    # The cancelled round owes one terminal event.
    client._gemini_cancelled_terminal_pending = True
    # Its late continuation already advanced epoch past the minting point; epoch alone misclassifies it.
    client._current_turn_epoch = 9
    client._is_responding = True

    terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=terminal, tool_call=None),
        connection_generation=1,
    )

    # This terminal event belongs to the old round; retain the token.
    assert client._gemini_external_outcome_token is token
    # Debt is single-use, consumed by this terminal event.
    assert client._gemini_cancelled_terminal_pending is False

    # Only now can the external round's own terminal event settle.
    client._is_responding = True
    await client._process_gemini_response(
        SimpleNamespace(server_content=terminal, tool_call=None),
        connection_generation=1,
    )
    assert client._gemini_external_outcome_token is None
    await client.close()


@pytest.mark.asyncio
async def test_handle_interruption_records_the_owed_terminal():
    """The credit is worthless unless the interruption actually records it.

    Asserting the consumption path alone stays green when nothing ever sets the
    flag -- the call-site blind spot this PR keeps hitting.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._is_responding = True
    client._current_response_id = None
    client._gemini_cancelled_terminal_pending = False

    await client.handle_interruption()

    assert client._gemini_cancelled_terminal_pending is True
    await client.close()


@pytest.mark.asyncio
async def test_a_genuine_new_turn_voids_a_stale_owed_terminal():
    """The credit must not outlive the response it was owed by.

    If the cancelled response never emits its terminal, an un-voided credit
    would eat the NEXT legitimate one, leaving the external token settled by
    nobody -- the session would read busy forever and she would stop speaking
    up on her own.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()
    client._gemini_cancelled_terminal_pending = True
    client._is_responding = False
    client._interrupted = False
    # User spoke after AI's final frame -> necessarily a new turn.
    client._user_recent_activity_time = 200.0
    client._ai_recent_activity_time = 100.0

    content_start = SimpleNamespace(
        model_turn=SimpleNamespace(parts=[]),
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=False,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=content_start, tool_call=None),
        connection_generation=1,
    )

    assert client._gemini_cancelled_terminal_pending is False
    await client.close()


@pytest.mark.asyncio
async def test_external_submit_establishes_a_new_user_turn():
    """An external ASR turn IS a user turn; the client has to be told.

    The audio never reaches the provider, so none of the transport-side points
    that advance ``_user_recent_activity_time`` fire for it. Left stale, the
    turn's first content is judged a late continuation: it stays suppressed by
    ``_interrupted``, the owed-terminal credit is never voided, and that turn's
    own terminal spends the credit instead of settling its token -- which pins
    ``is_active_response()`` busy for the rest of the session.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._gemini_send_user_turn = AsyncMock()
    client._start_gemini_external_submit_quarantine = MagicMock()
    client._await_gemini_external_quarantine = AsyncMock()
    client._gemini_external_outcome_token = None
    # AI just spoke; user activity remains earlier.
    client._ai_recent_activity_time = time.time() - 10.0
    client._user_recent_activity_time = client._ai_recent_activity_time - 10.0

    await client._submit_external_gemini_turn("用户插话")

    # Later user speech makes _is_new_turn true, allowing debt invalidation.
    assert client._user_recent_activity_time > client._ai_recent_activity_time
    await client.close()


@pytest.mark.asyncio
async def test_one_event_with_both_terminal_flags_spends_the_debt_once():
    """The debt is per EVENT, not per branch.

    Gemini can report an interrupted completion with ``turn_complete`` and
    ``interrupted`` both set, and both terminal branches run for that single
    event. Consuming separately, the first takes the debt and the second --
    finding none -- settles the freshly minted token using the OLD response's
    terminal, which is precisely what the debt exists to prevent.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_cancelled_terminal_pending = True
    client._is_responding = True

    both_flags = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=True,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=both_flags, tool_call=None),
        connection_generation=1,
    )

    # This event cancels only one debt; retain the token.
    assert client._gemini_external_outcome_token is token
    assert client._gemini_cancelled_terminal_pending is False
    await client.close()


@pytest.mark.asyncio
async def test_late_content_from_the_cancelled_turn_keeps_the_debt():
    """The cancelled turn's own late content must not void its debt.

    Voiding on ``_is_new_turn`` alone is satisfied by that content: the user
    already spoke after the AI's last frame, which is exactly what the
    cancellation means. The debt then dies before the terminal it was owed,
    and that terminal settles the freshly minted external token -- the turn
    reads idle while it is still in flight.

    The line below this one declares the new turn under the stricter
    ``_is_new_turn and _can_clear_interrupted``; voiding has to use the same
    definition of "the old turn is over".
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_cancelled_terminal_pending = True
    # State after handle_interruption(): this round stopped and no longer responds.
    client._is_responding = False
    client._interrupted = True
    # External turn refreshed user activity after AI's final frame, still within 3s.
    client._ai_recent_activity_time = time.time()
    client._user_recent_activity_time = client._ai_recent_activity_time + 0.1

    late_content = SimpleNamespace(
        model_turn=SimpleNamespace(parts=[]),
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=False,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=late_content, tool_call=None),
        connection_generation=1,
    )
    # Late cancelled-round content does not prove a new turn began.
    assert client._gemini_cancelled_terminal_pending is True

    real_terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=real_terminal, tool_call=None),
        connection_generation=1,
    )
    # That terminal event belongs to the cancelled old turn, not the newly minted token.
    assert client._gemini_external_outcome_token is token
    assert client._gemini_cancelled_terminal_pending is False
    await client.close()


@pytest.mark.asyncio
async def test_voiding_the_debt_also_drops_its_deadline():
    """The two fields are one piece of state; retiring one orphans the other.

    The consume and connection-replacement paths already clear them together.
    A deadline left behind by the turn-start void is inert only because the one
    arming site always overwrites it -- a second arming site would inherit it.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()
    client._gemini_cancelled_terminal_pending = True
    client._gemini_cancelled_terminal_deadline = time.monotonic() + 60.0
    client._is_responding = False
    # External ASR sends text; providers return no input_transcription, leaving the interrupted flag
    # set. Use production state, not _interrupted=False to bypass the criterion. This relies on
    # AI silence exceeding the window (_can_clear_interrupted's third disjunct).
    client._interrupted = True
    client._gemini_user_transcript_after_interrupt = False
    client._user_recent_activity_time = 200.0
    client._ai_recent_activity_time = 100.0

    content_start = SimpleNamespace(
        model_turn=SimpleNamespace(parts=[]),
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=False,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=content_start, tool_call=None),
        connection_generation=1,
    )

    assert client._gemini_cancelled_terminal_pending is False
    assert client._gemini_cancelled_terminal_deadline is None
    await client.close()


@pytest.mark.asyncio
async def test_a_slow_handoff_restarts_the_debt_deadline_at_the_gemini_send():
    """The clock starts when the provider is actually interrupted.

    Gemini has no ``response.cancel``: ``handle_interruption`` only marks local
    state, and the provider learns of the barge-in when the successor's content
    lands. Timing the debt from the interruption means a slow ASR handoff or a
    heavy multimodal send burns the whole window before the provider has even
    been told, so the cancelled turn's terminal is judged current and settles
    the successor token -- the regression this change exists to prevent.

    Arming still happens at the interruption so every early return carries a
    deadline; the send re-stamps it, once.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()
    client.note_user_turn_started = MagicMock()

    sent = []
    client._gemini_session = SimpleNamespace(
        send_client_content=AsyncMock(side_effect=lambda **kw: sent.append(kw)),
    )

    # Interruption occurred, but ASR handoff + compression exceeded TTL: the deadline elapsed.
    client._gemini_cancelled_terminal_pending = True
    client._gemini_cancelled_terminal_awaiting_delivery = True
    client._gemini_cancelled_terminal_id = object()
    client._gemini_cancelled_terminal_deadline = time.monotonic() - 1.0

    await client._gemini_send_user_turn("successor")

    assert sent, "the successor content must actually be sent"
    deadline = client._gemini_cancelled_terminal_deadline
    assert deadline is not None and deadline > time.monotonic()
    # Renew once only; renewing every subsequent send extends unconsumed debt forever.
    assert client._gemini_cancelled_terminal_awaiting_delivery is False
    client._gemini_cancelled_terminal_deadline = time.monotonic() - 1.0
    await client._gemini_send_user_turn("another")
    assert client._gemini_cancelled_terminal_deadline < time.monotonic()

    # Behavior: after renewal, cancelled-round terminal events still settle debt without touching successor tokens.
    client._gemini_cancelled_terminal_deadline = time.monotonic() + 60.0
    token = object()
    client._gemini_external_outcome_token = token
    client._is_responding = True
    client._interrupted = True
    terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=terminal, tool_call=None),
        connection_generation=1,
    )
    assert client._gemini_external_outcome_token is token
    await client.close()


@pytest.mark.asyncio
async def test_the_debt_does_not_expire_before_the_interrupt_is_delivered():
    """The clock cannot run while the provider has not been interrupted.

    ``send_client_content()`` puts the successor on the wire before it returns,
    and the re-stamp happens after that await. The receive loop can deliver the
    cancelled turn's terminal inside that gap, where the deadline is still the
    one stamped at the interruption and may already be spent. Expiring there
    hands the terminal to a successor whose own turn has not started.

    Until that send lands nothing else has been submitted, so the first
    terminal can only belong to the cancelled turn.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_cancelled_terminal_pending = True
    # Handoff exceeded TTL before interrupt delivery; timestamp refresh has not run yet.
    client._gemini_cancelled_terminal_deadline = time.monotonic() - 1.0
    client._gemini_cancelled_terminal_awaiting_delivery = True
    client._is_responding = True
    client._interrupted = True

    terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=terminal, tool_call=None),
        connection_generation=1,
    )

    # This terminal event settles debt, not successor tokens.
    assert client._gemini_external_outcome_token is token
    assert client._gemini_cancelled_terminal_pending is False
    assert client._gemini_cancelled_terminal_awaiting_delivery is False
    await client.close()


@pytest.mark.asyncio
async def test_only_the_send_that_delivered_the_debt_restarts_its_clock():
    """A send restamps the debt it delivered, not whatever is armed on return.

    An earlier send can still be inside ``send_client_content`` when a barge-in
    arms a fresh debt. Reading the flags after that await lets the earlier send
    claim the new debt, lowering awaiting-delivery and starting the TTL while
    the successor's content has not been sent at all -- so a slow handoff
    afterwards spends the window before the provider is interrupted.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client.note_user_turn_started = MagicMock()

    armed = {}

    async def _arm_midflight(**_kw):
        # While this send is in flight, user interjection arms a **new** debt.
        client._gemini_cancelled_terminal_pending = True
        client._gemini_cancelled_terminal_awaiting_delivery = True
        client._gemini_cancelled_terminal_id = object()
        client._gemini_cancelled_terminal_deadline = time.monotonic() + 60.0
        armed["id"] = client._gemini_cancelled_terminal_id

    client._gemini_session = SimpleNamespace(
        send_client_content=AsyncMock(side_effect=_arm_midflight),
    )
    # No debt existed when this send started.
    client._gemini_cancelled_terminal_pending = False
    client._gemini_cancelled_terminal_awaiting_delivery = False
    client._gemini_cancelled_terminal_id = None

    await client._gemini_send_user_turn("earlier turn")

    # This send did not deliver that debt; it must still await its own delivery.
    assert client._gemini_cancelled_terminal_id is armed["id"]
    assert client._gemini_cancelled_terminal_awaiting_delivery is True
    await client.close()


@pytest.mark.asyncio
async def test_an_expired_debt_does_not_eat_a_legitimate_terminal():
    """A debt that outlived its window is spent but not honoured.

    The turn-start void only runs when AI content arrives before the terminal.
    A bare ``turn_complete`` -- which this provider does emit -- skips it, so
    without a deadline a stale debt waits indefinitely and absorbs a terminal
    that belongs to someone else. That leaves an external token nobody settles
    and a session pinned busy, which is the worse of the two failure
    directions.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_cancelled_terminal_pending = True
    client._gemini_cancelled_terminal_deadline = time.monotonic() - 0.001
    client._is_responding = True
    client._interrupted = True

    bare_terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=bare_terminal, tool_call=None),
        connection_generation=1,
    )

    # Expired debt is consumed without claiming the event, which settles the current turn normally.
    assert client._gemini_cancelled_terminal_pending is False
    assert client._gemini_cancelled_terminal_deadline is None
    assert client._gemini_external_outcome_token is None
    await client.close()


@pytest.mark.asyncio
async def test_a_replacement_connection_does_not_inherit_the_debt():
    """The debt belongs to the connection that armed it.

    The replacement's first terminal is its own. Absorbed by the predecessor's
    debt, the turn that produced it never settles its token.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._gemini_cancelled_terminal_pending = True
    client._gemini_cancelled_terminal_deadline = time.monotonic() + 60.0

    client._on_connection_attached()

    assert client._gemini_cancelled_terminal_pending is False
    assert client._gemini_cancelled_terminal_deadline is None
    await client.close()


@pytest.mark.unit
def test_arming_the_cancellation_debt_always_sets_a_deadline():
    """The expiry fails open on a missing deadline, so arming must set one.

    ``_consume_cancelled_terminal`` treats ``deadline is None`` as "never
    expires" so that state built directly by tests keeps the old semantics. A
    production arming site that forgot the deadline would silently opt out of
    the bound.
    """
    source = (
        Path(__file__).resolve().parents[2]
        / "main_logic"
        / "omni_realtime_client"
        / "_transport.py"
    ).read_text(encoding="utf-8").split(chr(10))

    arming = [
        index
        for index, line in enumerate(source)
        if line.strip() == "self._gemini_cancelled_terminal_pending = True"
    ]
    assert arming, "the arming site moved; update this guard"
    for index in arming:
        # The window covers both assignments and their explanatory comments.
        window = chr(10).join(source[index : index + 16])
        assert "self._gemini_cancelled_terminal_deadline = (" in window, (
            f"line {index + 1}: arming the debt must also set its deadline, "
            f"got: {window!r}"
        )
        assert (
            "self._gemini_cancelled_terminal_awaiting_delivery = True" in window
        ), (
            f"line {index + 1}: arming the debt must mark it as awaiting "
            f"delivery, or the deadline starts before the provider is "
            f"interrupted; got: {window!r}"
        )


@pytest.mark.asyncio
async def test_non_terminal_content_does_not_spend_the_cancellation_debt():
    """Only a terminal event pays off the debt.

    A cancelled response keeps emitting content before it ends. Spending the
    debt on one of those events leaves nothing to absorb the real terminal that
    follows, so that terminal settles the freshly minted external token and the
    session reads idle while its response is still live.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_cancelled_terminal_pending = True
    client._is_responding = True
    client._interrupted = True
    # Brief silence without more user speech -> late continuation, not a new turn that invalidates debt.
    client._user_recent_activity_time = 100.0
    client._ai_recent_activity_time = time.time()

    late_content = SimpleNamespace(
        model_turn=SimpleNamespace(parts=[]),
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=False,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=late_content, tool_call=None),
        connection_generation=1,
    )
    # Nonterminal events must not affect debt.
    assert client._gemini_cancelled_terminal_pending is True

    # The old turn's actual terminal event alone settles debt, without settling the new token.
    real_terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=real_terminal, tool_call=None),
        connection_generation=1,
    )
    assert client._gemini_external_outcome_token is token
    assert client._gemini_cancelled_terminal_pending is False
    await client.close()

# ---------------------------------------------------------------------------
# Independent-ASR multimodal turn -> plugin frames bus (P8 fourth delivery point).
# ---------------------------------------------------------------------------
#
# Every case closes client.close() in finally. This file's manual-close convention meant
# failed assertions leaked live realtime sessions, hanging the **next** test forever instead of merely
# failing this one. Mutation verification specifically makes tests fail.


def _capture_published_frames(monkeypatch) -> list[dict]:
    """Record every frame handed to the plugin-bus publisher."""

    from main_logic import agent_event_bus

    published: list[dict] = []

    async def _publish(lanlan_name, **kwargs):
        published.append({"lanlan_name": lanlan_name, **kwargs})
        return True

    monkeypatch.setattr(
        agent_event_bus,
        "publish_provider_frame_observed_best_effort",
        _publish,
    )
    return published


async def _wait_for_published(published: list, count: int = 1) -> None:
    """The copy is fire-and-forget; give its task a chance to run."""

    for _ in range(50):
        if len(published) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(
        f"expected {count} published frame(s), saw {len(published)}"
    )


async def _assert_nothing_published(published: list) -> None:
    """Nothing reached the bus -- after the background task had its turns.

    Asserting an empty list the instant a turn returns would pass no matter
    what, now that the publish is scheduled rather than awaited. Drain first,
    then assert.
    """

    for _ in range(20):
        await asyncio.sleep(0)
    assert published == [], f"frame published from a non-delivery: {published}"


def _fake_arbiter(captured: list, *, sent_cancelled: bool = False):
    """An arbiter stub that dispatches the way the real worker does.

    Faithful on the two points this delivery point rests on: ``pre_commit``
    runs before the write, and ``_send_queued_event`` awaits the per-ticket
    sender while ignoring what it returns.
    """

    async def _enqueue(**kwargs):
        event = kwargs["events_before_response"][0]
        kwargs["pre_commit"](event)
        await kwargs["event_sender"](event)
        captured.append(event)
        loop = asyncio.get_running_loop()
        sent, done = loop.create_future(), loop.create_future()
        if sent_cancelled:
            sent.cancel()
        else:
            sent.set_result(None)
        done.set_result(None)
        return SimpleNamespace(sent=sent, done=done)

    return SimpleNamespace(
        enqueue=_enqueue,
        resume_dispatch=lambda: None,
        pause_dispatch=lambda owner=None: None,
        cancel_ticket=AsyncMock(),
        begin_turn_preparation=lambda owner=None: None,
        end_turn_preparation=lambda _token=None: None,
        has_live_response=True,
        allow_ticket_while_paused=lambda ticket: None,
        cancel_current=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_delivered_multimodal_turn_copies_its_frames_onto_the_plugin_bus(
    monkeypatch,
):
    """The independent-ASR turn is the only frame channel this mode has.

    Its frames never pass through stream_image: transcript and sampled frames
    reach the provider as ONE item. Without this copy a plugin sees nothing at
    all while the user is talking to a description-mode provider.
    """
    client = _make_client("openai", "gpt-4o-realtime")
    try:
        client.ws = AsyncMock()
        sent = _wire_completed_response_transport(client)
        client._analyze_image_with_vision_model = AsyncMock()
        client._latest_image_source = "screen"
        published = _capture_published_frames(monkeypatch)

        await client.prepare_external_voice_turn(turn_id="turn-frames")
        ticket = await client.submit_multimodal_turn(
            "看一下这张图",
            [DUMMY_IMAGE_B64, DUMMY_IMAGE_B64],
            turn_id="turn-frames",
        )

        assert ticket is not None
        wire_images = [
            part["image_url"]
            for part in sent[0]["item"]["content"]
            if part["type"] == "input_image"
        ]
        assert len(wire_images) == 2, "前提没成立：这一轮本来就没送出两张图"
        await _wait_for_published(published, 2)
        # Bus payloads are **plain base64**, not data URLs: records have a separate mime field; prefixes
        # would force every plugin to strip them again.
        assert [frame["image_base64"] for frame in published] == [
            url.split(",", 1)[1] for url in wire_images
        ]
        for frame in published:
            assert frame["mime"] == "image/jpeg"
            assert frame["turn_id"] == "turn-frames"
            # Preserve the capture source recorded by staging (screen / camera).
            assert frame["source"] == "screen"
            # Realtime clients do not know their character; forwarding may omit it.
            assert frame["lanlan_name"] is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_turn_that_lost_its_frames_publishes_nothing(monkeypatch):
    """Frames stripped by the downgrade were never sent, so they never appear.

    Ownership flips between enqueue and dispatch here: the pre-enqueue
    downgrade already ran while the turn still owned its frames, so the images
    are removed by ``pre_commit`` -- inside the arbiter, after the caller has
    stopped looking. Reading the item back after ``ticket.sent`` is what makes
    that visible; publishing anything captured earlier would put pictures on
    the bus that the provider never received.
    """
    client = _make_client("openai", "gpt-4o-realtime")
    try:
        client.ws = AsyncMock()
        sent = _wire_completed_response_transport(client)
        client._analyze_image_with_vision_model = AsyncMock()
        published = _capture_published_frames(monkeypatch)

        owned = [True]
        arbiter = client._ensure_response_arbiter()
        _real_resume = arbiter.resume_dispatch

        def _resume_and_lose_ownership():
            # Flip when the dispatch gate opens: pre-enqueue degradation already ran with ownership retained,
            # so only arbiter-internal pre_commit can remove the image.
            owned[0] = False
            _real_resume()

        arbiter.resume_dispatch = _resume_and_lose_ownership

        await client.prepare_external_voice_turn(turn_id="turn-lost-frames")
        await client.submit_multimodal_turn(
            "看一下这张图",
            DUMMY_IMAGE_B64,
            turn_id="turn-lost-frames",
            visual_still_owned=lambda: owned[0],
        )

        assert owned[0] is False, "夹具没走到派发那一步"
        content = sent[0]["item"]["content"]
        # pre_commit removed the frame...
        assert all(part["type"] != "input_image" for part in content), (
            "前提没成立：这一轮的图根本没被摘掉"
        )
        # ...the user's utterance still sends...
        assert any(part.get("text") == "看一下这张图" for part in content)
        # ...and no images reach the bus.
        await _assert_nothing_published(published)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_turn_whose_item_never_reached_the_socket_publishes_nothing(
    monkeypatch,
):
    """``ticket.sent`` resolving is not proof the bytes went out.

    ``_send_queued_event`` awaits the per-ticket sender and ignores what it
    returns, so a refused write (retired ws, earlier fatal error -> send_event
    returns False) still lets the dispatch run to completion and resolve
    ``ticket.sent``. Those frames reached no provider.
    """
    client = _make_client("openai", "gpt-4o-realtime")
    try:
        client.ws = AsyncMock()
        published = _capture_published_frames(monkeypatch)

        async def _refuse_the_write(_event, **_kwargs):
            return False

        client.send_event = _refuse_the_write
        captured: list = []
        client._ensure_response_arbiter = lambda: _fake_arbiter(captured)

        await client.prepare_external_voice_turn(turn_id="turn-refused")
        await client.submit_multimodal_turn(
            "看一下这张图",
            DUMMY_IMAGE_B64,
            turn_id="turn-refused",
            visual_still_owned=lambda: True,
        )

        assert captured, "item 没被送进 arbiter"
        assert any(
            part["type"] == "input_image"
            for part in captured[0]["item"]["content"]
        ), "前提没成立：这一轮的图在提交前就没了"
        await _assert_nothing_published(published)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_bus_gets_the_bytes_the_transport_rewrote(monkeypatch):
    """Publish what the wire carried, not what the caller staged.

    An oversized item is recompressed in place inside send_event, and frames it
    still cannot fit are dropped from the item's content. Publishing the staged
    copies would put a bigger, different picture on the bus than the provider
    saw -- and one picture more than it saw.
    """
    client = _make_client("openai", "gpt-4o-realtime")
    try:
        client.ws = AsyncMock()
        published = _capture_published_frames(monkeypatch)
        shrunk_b64 = DUMMY_IMAGE_B64[:40]

        async def _shrink_and_drop_on_send(event, **_kwargs):
            # _shrink_multi_image_item shape: rewrite the retained image in place and delete
            # the oldest image that cannot fit from content.
            content = event["item"]["content"]
            images = [part for part in content if part["type"] == "input_image"]
            content.remove(images[0])
            images[-1]["image_url"] = "data:image/jpeg;base64," + shrunk_b64
            return True

        client.send_event = _shrink_and_drop_on_send
        captured: list = []
        client._ensure_response_arbiter = lambda: _fake_arbiter(captured)

        await client.prepare_external_voice_turn(turn_id="turn-shrunk")
        await client.submit_multimodal_turn(
            "看一下这张图",
            [DUMMY_IMAGE_B64, DUMMY_IMAGE_B64],
            turn_id="turn-shrunk",
            visual_still_owned=lambda: True,
        )

        await _wait_for_published(published, 1)
        assert [frame["image_base64"] for frame in published] == [shrunk_b64]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_cancelled_multimodal_turn_publishes_nothing(monkeypatch):
    """Cancellation ends the turn; the bus copy is not a consolation prize.

    The write itself succeeded here -- the point is that a turn the caller
    cancelled unwinds through ``arbiter.cancel_ticket`` and must publish
    nothing on the way out, which only holds while the publish sits after the
    await rather than around it.
    """
    client = _make_client("openai", "gpt-4o-realtime")
    try:
        client.ws = AsyncMock()
        published = _capture_published_frames(monkeypatch)

        async def _accept_the_write(_event, **_kwargs):
            return True

        client.send_event = _accept_the_write
        captured: list = []
        arbiter = _fake_arbiter(captured, sent_cancelled=True)
        client._ensure_response_arbiter = lambda: arbiter

        await client.prepare_external_voice_turn(turn_id="turn-cancelled")
        with pytest.raises(asyncio.CancelledError):
            await client.submit_multimodal_turn(
                "看一下这张图",
                DUMMY_IMAGE_B64,
                turn_id="turn-cancelled",
                visual_still_owned=lambda: True,
            )

        arbiter.cancel_ticket.assert_awaited_once()
        await _assert_nothing_published(published)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_only_well_formed_image_parts_are_copied_and_keep_their_mime(
    monkeypatch,
):
    """The record describes the part it came from, and nothing else.

    ``mime`` is read back off the data URL instead of assumed: recompression
    rewrites the payload but keeps whatever prefix the part was built with, so
    a hardcoded ``image/jpeg`` would eventually describe the wrong bytes.
    Malformed parts are skipped rather than published as empty frames.
    """
    client = _make_client("openai", "gpt-4o-realtime")
    try:
        client._latest_image_source = "camera"
        published = _capture_published_frames(monkeypatch)

        item_event = {
            "item": {
                "content": [
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64," + DUMMY_IMAGE_B64,
                    },
                    # Not a data URL: this layer cannot retrieve it.
                    {
                        "type": "input_image",
                        "image_url": "https://example.invalid/x",
                    },
                    # Empty payload.
                    {"type": "input_image", "image_url": "data:image/jpeg;base64,"},
                    {"type": "input_text", "text": "看一下这张图"},
                ]
            }
        }
        copied = await client._publish_turn_frames(
            client._delivered_multimodal_frames(item_event),
            turn_id="turn-mime",
            source=client._latest_image_source,
        )

        assert copied == 1
        assert [frame["mime"] for frame in published] == ["image/png"]
        assert published[0]["image_base64"] == DUMMY_IMAGE_B64
        assert published[0]["source"] == "camera"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_publisher_failure_does_not_fail_the_delivered_turn(monkeypatch):
    """The turn is already delivered by the time the copy runs.

    A bus that is absent or down must not turn a good turn into a failed one,
    so the publish is caught and the turn still returns its ticket.
    """
    from main_logic import agent_event_bus

    client = _make_client("openai", "gpt-4o-realtime")
    try:
        client.ws = AsyncMock()
        _wire_completed_response_transport(client)
        client._analyze_image_with_vision_model = AsyncMock()

        async def _explode(_lanlan_name, **_kwargs):
            raise RuntimeError("message plane is down")

        monkeypatch.setattr(
            agent_event_bus,
            "publish_provider_frame_observed_best_effort",
            _explode,
        )

        await client.prepare_external_voice_turn(turn_id="turn-bus-down")
        ticket = await client.submit_multimodal_turn(
            "看一下这张图",
            DUMMY_IMAGE_B64,
            turn_id="turn-bus-down",
        )

        assert ticket is not None
        # Failure occurs inside a background task. Finish it to prove that layer swallows the exception,
        # rather than producing an unretrieved task exception.
        for _ in range(20):
            await asyncio.sleep(0)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_stalled_frames_bus_does_not_hold_up_the_delivered_turn(
    monkeypatch,
):
    """A turn the provider already took may not wait on the bus copy.

    ``publish_session_event_threadsafe`` forwards a cross-thread publish to the
    bridge's owner loop through an un-timed ``run_coroutine_threadsafe``, so a
    blocked or slow owner loop blocks whoever awaits it. Awaiting the copy on
    the turn's return path would make that the user's latency, for a frame the
    provider already has.
    """
    from main_logic import agent_event_bus

    entered = asyncio.Event()
    released = asyncio.Event()

    async def _stall(_lanlan_name, **_kwargs):
        entered.set()
        await released.wait()
        return True

    monkeypatch.setattr(
        agent_event_bus,
        "publish_provider_frame_observed_best_effort",
        _stall,
    )

    client = _make_client("openai", "gpt-4o-realtime")
    try:
        client.ws = AsyncMock()
        _wire_completed_response_transport(client)
        client._analyze_image_with_vision_model = AsyncMock()

        await client.prepare_external_voice_turn(turn_id="turn-stalled-bus")
        # If publish still blocks the return path, this waits until wait_for times out,
        # making regressions fail rather than merely slowing silently.
        ticket = await asyncio.wait_for(
            client.submit_multimodal_turn(
                "看一下这张图",
                DUMMY_IMAGE_B64,
                turn_id="turn-stalled-bus",
            ),
            timeout=5,
        )

        assert ticket is not None
        # Precondition: publish really was scheduled and is blocked inside the bus.
        await asyncio.wait_for(entered.wait(), timeout=5)
    finally:
        released.set()
        for _ in range(20):
            await asyncio.sleep(0)
        await client.close()


@pytest.mark.asyncio
async def test_the_bus_record_is_read_at_delivery_not_after_the_turn(
    monkeypatch,
):
    """The whole record is snapshotted on the turn; the task only publishes it.

    Now that the copy runs on a later loop iteration, nothing it describes is
    still this turn's to read: the item gets rewritten in place by the next
    turn's downgrade and recompression, and ``_latest_image_source`` is
    overwritten by the next staged frame. Reading either inside the task would
    publish whatever the session had moved on to, and "only what the provider
    got" would stop being true with every other test in this file still green.
    """
    client = _make_client("openai", "gpt-4o-realtime")
    try:
        client.ws = AsyncMock()
        sent = _wire_completed_response_transport(client)
        client._analyze_image_with_vision_model = AsyncMock()
        client._latest_image_source = "screen"
        published = _capture_published_frames(monkeypatch)

        await client.prepare_external_voice_turn(turn_id="turn-snapshot")
        await client.submit_multimodal_turn(
            "看一下这张图",
            DUMMY_IMAGE_B64,
            turn_id="turn-snapshot",
        )

        content = sent[0]["item"]["content"]
        delivered = [
            part["image_url"].split(",", 1)[1]
            for part in content
            if part["type"] == "input_image"
        ]
        assert len(delivered) == 1, "前提没成立：这一轮本来就没送出图"
        # The turn returned before publish ran once: precisely the window where successor turns mutate
        # these structures. Establish the criterion here or subsequent mutation proves nothing.
        assert published == [], "publish 没被挪出回合，本用例的判据落空"

        for part in content:
            if part["type"] == "input_image":
                part["image_url"] = "data:image/jpeg;base64,bmV4dC10dXJu"
        # Capture source is live session state too: the next staged frame overwrites it.
        client._latest_image_source = "camera"

        await _wait_for_published(published, 1)
        assert [frame["image_base64"] for frame in published] == delivered
        assert [frame["source"] for frame in published] == ["screen"]
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Independent-ASR multimodal turn -> plugin frames bus: Gemini half.
# ---------------------------------------------------------------------------
#
# WebSocket inspects outgoing items, but Gemini constructs no wire event and gives raw bytes
# directly to the SDK. Test the actual tuple handed to the SDK.
# Symmetry matters: independent ASR arms raw-visual fences, making stream_image reject every
# ambient frame; this turn is the session's only visual channel.


def _gemini_sdk_frames(session) -> list[str]:
    """The base64 of every image part the SDK was actually handed."""

    content = session.send_client_content.await_args.kwargs["turns"][0]
    return [
        base64.b64encode(bytes(part.inline_data.data)).decode("ascii")
        for part in content.parts
        if part.inline_data is not None
    ]


@pytest.mark.asyncio
async def test_a_delivered_gemini_external_turn_copies_its_frames_onto_the_bus(
    monkeypatch,
):
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    try:
        session = AsyncMock()
        client._gemini_session = session
        client.ws = session
        client.handle_interruption = AsyncMock()
        client._analyze_image_with_vision_model = AsyncMock()
        client._latest_image_source = "screen"
        published = _capture_published_frames(monkeypatch)

        await client.prepare_external_voice_turn(turn_id="turn-gemini-frames")
        result = await client.submit_multimodal_turn(
            "看一下这张图",
            [DUMMY_IMAGE_B64, DUMMY_IMAGE_B64],
            turn_id="turn-gemini-frames",
        )

        assert result is None
        wire = _gemini_sdk_frames(session)
        assert len(wire) == 2, "前提没成立：这一轮本来就没送出两张图"
        await _wait_for_published(published, 2)
        assert [frame["image_base64"] for frame in published] == wire
        content = session.send_client_content.await_args.kwargs["turns"][0]
        sdk_mime = next(
            part.inline_data.mime_type
            for part in content.parts
            if part.inline_data is not None
        )
        for frame in published:
            # Tags must match the mime actually received by the SDK, not independent literals.
            assert frame["mime"] == sdk_mime
            assert frame["turn_id"] == "turn-gemini-frames"
            assert frame["source"] == "screen"
            assert frame["lanlan_name"] is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_gemini_bus_gets_the_bytes_the_budget_ladder_produced(
    monkeypatch,
):
    """The ladder re-encodes; the bus must carry what the SDK got, not the input.

    Gemini is the one route with no aggregate gate of its own, so
    ``fit_images_to_turn_budget`` runs right here and rewrites the turn's
    frames. Publishing the caller's staged images would put a bigger, different
    picture on the bus than the provider ever received.
    """
    fitted_b64 = base64.b64encode(b"fitted-frame-bytes").decode("ascii")
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    try:
        session = AsyncMock()
        client._gemini_session = session
        client.ws = session
        client.handle_interruption = AsyncMock()
        client._analyze_image_with_vision_model = AsyncMock()
        published = _capture_published_frames(monkeypatch)

        async def _fit(images, _budget):
            return [fitted_b64], {
                "original_count": len(images),
                "final_count": 1,
                "normalized": True,
                "sampled": False,
                "compressed": True,
                "dropped": 0,
                "user_visible": False,
            }

        monkeypatch.setattr(
            responses_module, "fit_images_to_turn_budget", _fit
        )

        await client.prepare_external_voice_turn(turn_id="turn-gemini-fitted")
        await client.submit_multimodal_turn(
            "看一下这张图",
            DUMMY_IMAGE_B64,
            turn_id="turn-gemini-fitted",
        )

        assert _gemini_sdk_frames(session) == [fitted_b64]
        await _wait_for_published(published, 1)
        assert [frame["image_base64"] for frame in published] == [fitted_b64]
        assert DUMMY_IMAGE_B64 not in [
            frame["image_base64"] for frame in published
        ]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_gemini_turn_that_lost_its_frames_publishes_nothing(monkeypatch):
    """The ownership recheck empties the tuple before the SDK send.

    Those frames were never handed to the provider, so nothing may appear on
    the bus -- while the sentence still goes out, as everywhere else here.
    """
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    try:
        session = AsyncMock()
        client._gemini_session = session
        client.ws = session
        client.handle_interruption = AsyncMock()
        client._analyze_image_with_vision_model = AsyncMock()
        published = _capture_published_frames(monkeypatch)

        owned = [True]

        async def _fit_and_lose_ownership(images, _budget):
            owned[0] = False
            return list(images), {
                "original_count": len(images),
                "final_count": len(images),
                "normalized": False,
                "sampled": False,
                "compressed": True,
                "dropped": 0,
                "user_visible": False,
            }

        monkeypatch.setattr(
            responses_module,
            "fit_images_to_turn_budget",
            _fit_and_lose_ownership,
        )

        await client.prepare_external_voice_turn(turn_id="turn-gemini-lost")
        await client.submit_multimodal_turn(
            "看一下这张图",
            DUMMY_IMAGE_B64,
            turn_id="turn-gemini-lost",
            visual_still_owned=lambda: owned[0],
        )

        assert owned[0] is False, "夹具没走到丢所有权那一步"
        assert _gemini_sdk_frames(session) == [], (
            "前提没成立：这一轮的图根本没被摘掉"
        )
        await _assert_nothing_published(published)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_refused_gemini_send_publishes_nothing(monkeypatch):
    """A provider that rejects the turn outright never received the frames."""

    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    try:
        session = AsyncMock()
        session.send_client_content.side_effect = RuntimeError("refused")
        client._gemini_session = session
        client.ws = session
        client.handle_interruption = AsyncMock()
        client._analyze_image_with_vision_model = AsyncMock()
        published = _capture_published_frames(monkeypatch)

        await client.prepare_external_voice_turn(turn_id="turn-gemini-refused")
        with pytest.raises(RuntimeError):
            await client.submit_multimodal_turn(
                "看一下这张图",
                DUMMY_IMAGE_B64,
                turn_id="turn-gemini-refused",
            )

        await _assert_nothing_published(published)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_cancelled_gemini_turn_publishes_nothing(monkeypatch):
    """Cancellation leaves the turn unconfirmed, so it stays off the bus.

    The provider may well have taken it -- that is exactly why the quarantine
    exists -- but "may have" is not the frames contract, and re-raising before
    the publish is what keeps that decision in one place.
    """
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    try:
        session = AsyncMock()
        session.send_client_content.side_effect = asyncio.CancelledError()
        client._gemini_session = session
        client.ws = session
        client.handle_interruption = AsyncMock()
        client._analyze_image_with_vision_model = AsyncMock()
        published = _capture_published_frames(monkeypatch)

        await client.prepare_external_voice_turn(turn_id="turn-gemini-cancel")
        # Run in its own task, matching existing cancellation tests:
        # quarantine cancels the submit task; directly awaiting it
        # would cancel the test itself.
        submit = asyncio.create_task(
            client.submit_multimodal_turn(
                "看一下这张图",
                DUMMY_IMAGE_B64,
                turn_id="turn-gemini-cancel",
            )
        )
        with pytest.raises(asyncio.CancelledError):
            await submit

        await _assert_nothing_published(published)
        quarantine = getattr(client, "_gemini_external_quarantine_task", None)
        if quarantine is not None:
            await asyncio.gather(quarantine, return_exceptions=True)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_publisher_failure_does_not_fail_the_delivered_gemini_turn(
    monkeypatch,
):
    from main_logic import agent_event_bus

    async def _boom(_lanlan_name, **_kwargs):
        raise RuntimeError("frames bus is down")

    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    try:
        session = AsyncMock()
        client._gemini_session = session
        client.ws = session
        client.handle_interruption = AsyncMock()
        client._analyze_image_with_vision_model = AsyncMock()
        monkeypatch.setattr(
            agent_event_bus,
            "publish_provider_frame_observed_best_effort",
            _boom,
        )

        await client.prepare_external_voice_turn(turn_id="turn-gemini-boom")
        result = await client.submit_multimodal_turn(
            "看一下这张图",
            DUMMY_IMAGE_B64,
            turn_id="turn-gemini-boom",
        )

        assert result is None
        session.send_client_content.assert_awaited_once()
        # Failure is in a background task, matching the WebSocket criterion.
        for _ in range(20):
            await asyncio.sleep(0)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_stalled_frames_bus_does_not_hold_up_the_gemini_turn(
    monkeypatch,
):
    """Gemini's half owes the same guarantee: the copy is off the turn.

    Same un-timed cross-loop hop, same conclusion. Kept as its own case because
    this route publishes from the SDK byte tuple rather than from an outgoing
    item, so nothing about the WebSocket test constrains it.
    """
    from main_logic import agent_event_bus

    entered = asyncio.Event()
    released = asyncio.Event()

    async def _stall(_lanlan_name, **_kwargs):
        entered.set()
        await released.wait()
        return True

    monkeypatch.setattr(
        agent_event_bus,
        "publish_provider_frame_observed_best_effort",
        _stall,
    )

    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    try:
        session = AsyncMock()
        client._gemini_session = session
        client.ws = session
        client.handle_interruption = AsyncMock()
        client._analyze_image_with_vision_model = AsyncMock()

        await client.prepare_external_voice_turn(turn_id="turn-gemini-stalled")
        await asyncio.wait_for(
            client.submit_multimodal_turn(
                "看一下这张图",
                DUMMY_IMAGE_B64,
                turn_id="turn-gemini-stalled",
            ),
            timeout=5,
        )

        session.send_client_content.assert_awaited_once()
        await asyncio.wait_for(entered.wait(), timeout=5)
    finally:
        released.set()
        for _ in range(20):
            await asyncio.sleep(0)
        await client.close()


# ---------------------------------------------------------------------------
# Source drift during intra-turn awaits.
# ---------------------------------------------------------------------------
#
# The preceding case mutates _latest_image_source only **after submit returns**, covered by sampling once
# when scheduling. The uncovered window is **inside submit**: trimming, arbiter queuing, and SDK send
# all await; another source staging a frame overwrites it. Images still belong to this turn,
# but source changes to the session's later channel. Plugins use source to distinguish user-shared
# visuals from plugin-provided media, making wrong labels worse than absent labels.


@pytest.mark.asyncio
async def test_ws_turn_keeps_its_channel_when_another_stages_during_the_send(
    monkeypatch,
):
    """A frame staged mid-send must not relabel the turn already in flight."""

    client = _make_client("openai", "gpt-4o-realtime")
    try:
        client.ws = AsyncMock()
        sent = _wire_completed_response_transport(client)
        client._analyze_image_with_vision_model = AsyncMock()
        client._latest_image_source = "screen"
        published = _capture_published_frames(monkeypatch)

        # Camera stages a frame **during** this turn's send. _transport writes this field on every
        # staged frame, reproducing real-session mutation.
        _inner = client.send_event

        async def _send_and_stage_another_channel(event, **kwargs):
            result = await _inner(event, **kwargs)
            client._latest_image_source = "camera"
            return result

        client.send_event = _send_and_stage_another_channel
        client._response_arbiter._send_event = client.send_event

        await client.prepare_external_voice_turn(turn_id="turn-drift-ws")
        await client.submit_multimodal_turn(
            "看一下这张图",
            DUMMY_IMAGE_B64,
            turn_id="turn-drift-ws",
            source="screen",
        )

        delivered = [
            part["image_url"].split(",", 1)[1]
            for part in sent[0]["item"]["content"]
            if part["type"] == "input_image"
        ]
        assert len(delivered) == 1, "前提没成立：这一轮本来就没送出图"
        assert client._latest_image_source == "camera", (
            "前提没成立：漂移没发生，本用例什么都没在测"
        )

        await _wait_for_published(published, 1)
        assert [frame["image_base64"] for frame in published] == delivered
        assert [frame["source"] for frame in published] == ["screen"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_gemini_turn_keeps_its_channel_when_another_stages_during_the_send(
    monkeypatch,
):
    """The Gemini half of the same window — its send is an SDK await."""

    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    try:
        session = AsyncMock()

        async def _send_and_stage_another_channel(*_args, **_kwargs):
            client._latest_image_source = "camera"

        session.send_client_content.side_effect = _send_and_stage_another_channel
        client._gemini_session = session
        client.ws = session
        client.handle_interruption = AsyncMock()
        client._analyze_image_with_vision_model = AsyncMock()
        client._latest_image_source = "screen"
        published = _capture_published_frames(monkeypatch)

        await client.prepare_external_voice_turn(turn_id="turn-drift-gemini")
        await client.submit_multimodal_turn(
            "看一下这张图",
            DUMMY_IMAGE_B64,
            turn_id="turn-drift-gemini",
            source="screen",
        )

        assert len(_gemini_sdk_frames(session)) == 1, (
            "前提没成立：这一轮本来就没送出图"
        )
        assert client._latest_image_source == "camera", (
            "前提没成立：漂移没发生，本用例什么都没在测"
        )

        await _wait_for_published(published, 1)
        assert [frame["source"] for frame in published] == ["screen"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_turns_own_channel_beats_live_session_state(monkeypatch):
    """``MultimodalTurn.source`` wins even when the live field disagrees.

    The caller freezes the channel with the frames; the session field is only
    a fallback for entry points that have no turn. A test that let both say
    the same thing would pass with the argument ignored entirely.
    """

    client = _make_client("openai", "gpt-4o-realtime")
    try:
        client.ws = AsyncMock()
        _wire_completed_response_transport(client)
        client._analyze_image_with_vision_model = AsyncMock()
        client._latest_image_source = "screen"
        published = _capture_published_frames(monkeypatch)

        await client.prepare_external_voice_turn(turn_id="turn-frozen")
        await client.submit_multimodal_turn(
            "看一下这张图",
            DUMMY_IMAGE_B64,
            turn_id="turn-frozen",
            source="camera",
        )

        await _wait_for_published(published, 1)
        assert [frame["source"] for frame in published] == ["camera"]
    finally:
        await client.close()
