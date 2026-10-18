"""One ASR utterance keeps at most first/middle/last, never the whole stream.

Screen/camera frames arrive at roughly 1 fps for as long as the user keeps
talking, and an utterance is a span rather than an instant. Keeping every frame
would hand the answering model an unbounded image list — and a frame written
into a Realtime provider's conversation cannot be deleted afterwards. Keeping
only the newest loses what the user was pointing at when they started.

These tests lock the sampling contract:

* the retained sample is capped at ``MAX_MULTIMODAL_TURN_IMAGES``;
* the candidate buffer never grows with the utterance length;
* the middle sample tracks the centre of the span, not its head;
* both provider submit paths hold the same cap independently of Core.
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from config import MAX_MULTIMODAL_TURN_IMAGES
from main_logic.core.multimodal_turn import (
    _MAX_MIDDLE_CANDIDATES,
    _CoreMultimodalTurnRecord,
    _IndependentVisualFrame,
)
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.omni_realtime_client import MultimodalTurnDelivery


def _record():
    return _CoreMultimodalTurnRecord(
        turn_id="turn-1",
        session_epoch=1,
        route_generation=1,
        start_image_generation=0,
        started_at=0.0,
    )


def _feed(record, count):
    for index in range(count):
        record.observe(
            _IndependentVisualFrame(
                image_b64=f"f{index}",
                session_epoch=1,
                route_generation=1,
                generation=index,
                captured_at=float(index),
                source="screen",
                request_id=None,
            )
        )
    return [frame.image_b64 for frame in record.sampled_frames()]


def test_per_turn_cap_is_three():
    # Guard for derived assertions: cases below calculate expectations using this number.
    assert MAX_MULTIMODAL_TURN_IMAGES == 3


@pytest.mark.parametrize("count", [1, 2, 3, 5, 13, 60, 600])
def test_sample_never_exceeds_the_per_turn_cap(count):
    assert len(_feed(_record(), count)) <= MAX_MULTIMODAL_TURN_IMAGES


@pytest.mark.parametrize("count", [4, 17, 60, 600])
def test_sample_keeps_the_first_and_the_last_frame(count):
    sampled = _feed(_record(), count)

    assert sampled[0] == "f0"
    assert sampled[-1] == f"f{count - 1}"


@pytest.mark.parametrize("count", [17, 60, 600])
def test_middle_sample_tracks_the_centre_of_the_span(count):
    sampled = _feed(_record(), count)

    middle_index = int(sampled[1][1:])
    # Keeping only the last frame loses speech-onset visuals; keeping only the first two loses the middle. Sampling must
    # really land near the utterance midpoint; allow ±25% without pinning a sampling step.
    assert abs(middle_index - (count - 1) / 2) <= count * 0.25


def test_candidate_buffer_does_not_grow_with_the_utterance():
    record = _record()
    _feed(record, 600)

    # Key: avoid accumulating throughout, rather than trimming to three only at the end; the candidate set itself is bounded.
    assert len(record.middle_candidates) <= _MAX_MIDDLE_CANDIDATES


def test_sample_drops_duplicate_slots_for_a_short_utterance():
    assert _feed(_record(), 1) == ["f0"]
    assert _feed(_record(), 2) == ["f0", "f1"]


def test_adopting_a_late_frame_resets_the_sample():
    record = _record()
    _feed(record, 9)
    late = _IndependentVisualFrame(
        image_b64="late",
        session_epoch=1,
        route_generation=1,
        generation=99,
        captured_at=99.0,
        source="screen",
        request_id=None,
    )

    record.adopt_single_frame(late)

    assert [frame.image_b64 for frame in record.sampled_frames()] == ["late"]


async def test_offline_submit_holds_the_per_turn_cap():
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = []
    client.stream_text = AsyncMock()

    assert await client.submit_multimodal_turn(
        "look",
        tuple(f"frame{i}" for i in range(8)),
        turn_id="turn-1",
    ) is True

    assert client.stream_text.await_args.kwargs["turn_images"] == tuple(
        f"frame{i}" for i in range(MAX_MULTIMODAL_TURN_IMAGES)
    )


async def test_offline_submit_still_accepts_a_bare_string():
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = []
    client.stream_text = AsyncMock()

    assert await client.submit_multimodal_turn(
        "look",
        "single-frame",
        turn_id="turn-1",
    ) is True

    assert client.stream_text.await_args.kwargs["turn_images"] == ("single-frame",)


async def test_asr_turn_takes_a_snapshot_and_leaves_later_attachments_alone():
    """Attachments already queued ride this turn; ones arriving mid-turn do not."""
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = ["earlier-attachment"]

    async def attach_while_streaming(*_args, **_kwargs):
        # Simulate another user-dragged image arriving before this round actually consumes stream_text input.
        await OmniOfflineClient.stream_image(client, "late-attachment")

    client.stream_text = AsyncMock(side_effect=attach_while_streaming)

    assert await client.submit_multimodal_turn(
        "look",
        ("a", "b", "c"),
        turn_id="turn-1",
    ) is True

    # Attachments delivered before speech starts belong to this utterance and accompany its frames.
    # Order: sampled frames first, attachments last. Downstream fit/trim discards oldest and always preserves
    # the last image; putting explicit user attachments first kills them before ambient frames.
    assert client.stream_text.await_args.kwargs["turn_images"] == (
        "a",
        "b",
        "c",
        "earlier-attachment",
    )
    # Images arriving during this turn wait for the next, unconsumed here; none of this round's frames enter the queue.
    assert client._pending_images == ["late-attachment"]


async def test_user_attachments_outlive_ambient_frames_under_the_budget():
    """A user's own attachment must not be the first thing the budget drops.

    Everything downstream (fit_images_to_turn_budget, trim_images_to_turn_budget)
    trims from the OLDEST and unconditionally keeps the last image, so position
    decides survival. Putting attachments first means the picture the user
    deliberately dragged in dies while ambient screen/camera samples live -- the
    user sees "she ignored what I showed her and talked about my screen instead".

    The contract matches the one spelled out in _streaming.py: whatever sits
    closest to the text is what the turn is about, and an attachment is exactly
    the image the user picked for this sentence.
    """
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = ["user-attachment"]
    client.stream_text = AsyncMock()

    assert await client.submit_multimodal_turn(
        "look",
        ("frame-first", "frame-mid", "frame-last"),
        turn_id="turn-1",
    ) is True

    sent = client.stream_text.await_args.kwargs["turn_images"]
    assert sent[-1] == "user-attachment", (
        "附件必须排在最后：下游从最旧丢并保住最后一张，排前面它会先被丢掉"
    )
    assert sent.index("user-attachment") > sent.index("frame-last")


async def test_the_budget_actually_keeps_the_attachment_and_drops_a_frame():
    """Position is the means; survival after trimming is the actual claim.

    The test above only asserts the attachment sits last, which is a necessary
    condition, not the conclusion. This one feeds the order
    submit_multimodal_turn really hands over into the real
    trim_images_to_turn_budget, with a budget it is bound to exceed, and
    asserts that what got dropped is the oldest ambient frame while the image
    the user dragged in survives.

    Only the two together prove "a user attachment must not be the first thing
    the budget drops".
    """
    from main_logic import proactive_delivery as pd

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    # Each uses half the budget -> three exceed it, requiring at least the oldest to be trimmed.
    half = pd.TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES // 2
    frame_a = "a" * (half * 4 // 3)
    frame_b = "b" * (half * 4 // 3)
    attachment = "u" * (half * 4 // 3)
    client._pending_images = [attachment]
    client.stream_text = AsyncMock()

    assert await client.submit_multimodal_turn(
        "look",
        (frame_a, frame_b),
        turn_id="turn-1",
    ) is True

    ordered = list(client.stream_text.await_args.kwargs["turn_images"])
    # Each of three uses just over a third of budget -> the oldest must be trimmed.
    kept, dropped = pd.trim_images_to_turn_budget(ordered)

    assert dropped >= 1, "夹具没造出超额，这条用例测不到裁剪"
    assert attachment in kept, (
        "用户拖进来的附件被裁掉了——顺序反了才会这样"
    )
    assert frame_a not in kept, "被裁掉的应该是最旧的那张环境帧"


async def test_failed_asr_turn_returns_the_attachment_to_the_queue():
    """A failed turn must not eat an image the user deliberately sent."""
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = ["user-attachment"]
    client.stream_text = AsyncMock(side_effect=RuntimeError("vlm down"))

    with pytest.raises(RuntimeError, match="vlm down"):
        await client.submit_multimodal_turn(
            "look",
            ("a",),
            turn_id="turn-1",
        )

    assert client._pending_images == ["user-attachment"]


def test_realtime_submit_holds_the_per_turn_cap():
    from main_logic.omni_realtime_client import OmniRealtimeClient

    staged, decoded = OmniRealtimeClient._normalize_multimodal_turn_images(
        tuple("aGVsbG8=" for _ in range(9))
    )

    assert len(staged) == MAX_MULTIMODAL_TURN_IMAGES
    assert len(decoded) == MAX_MULTIMODAL_TURN_IMAGES


def test_realtime_submit_rejects_an_empty_sample():
    from main_logic.omni_realtime_client import OmniRealtimeClient

    with pytest.raises(ValueError, match="at least one image"):
        OmniRealtimeClient._normalize_multimodal_turn_images(())


def test_out_of_order_arrival_keeps_the_earliest_capture_as_first():
    record = _record()
    later = _IndependentVisualFrame(
        image_b64="later",
        session_epoch=1,
        route_generation=1,
        generation=1,
        captured_at=5.0,
        source="screen",
        request_id=None,
    )
    earlier = _IndependentVisualFrame(
        image_b64="earlier",
        session_epoch=1,
        route_generation=1,
        generation=2,
        captured_at=1.0,
        source="screen",
        request_id=None,
    )

    # Validation runs concurrently: an earlier capture may finish later.
    record.observe(later)
    record.observe(earlier)

    sampled = [frame.image_b64 for frame in record.sampled_frames()]
    assert sampled[0] == "earlier"
    assert sampled[-1] == "later"


def _jpeg_b64(width: int, height: int) -> str:
    import base64 as _b64
    from io import BytesIO

    from PIL import Image as PILImage

    # Noise image prevents tiny JPEG compression from bypassing the over-budget path.
    img = PILImage.frombytes(
        "RGB",
        (width, height),
        bytes((i * 37 + i // 7) % 256 for i in range(width * height * 3)),
    )
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return _b64.b64encode(buf.getvalue()).decode()


def _multi_image_item(images):
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                *(
                    {
                        "type": "input_image",
                        "image_url": "data:image/jpeg;base64," + image,
                    }
                    for image in images
                ),
                {"type": "input_text", "text": "这是什么？"},
            ],
        },
    }


def test_oversized_multi_image_item_is_shrunk_not_dropped():
    import json

    from main_logic.omni_realtime_client._transport import _TransportMixin

    images = [_jpeg_b64(600, 600) for _ in range(MAX_MULTIMODAL_TURN_IMAGES)]
    event = _multi_image_item(images)
    payload = json.dumps(event)
    assert len(payload) > _TransportMixin._WS_FRAME_LIMIT

    shrunk = _TransportMixin._try_shrink_image_payload(event, payload)

    # Key: do not discard the entire item, which would put neither image nor transcript into history
    # while the arbiter still sends response.create.
    assert shrunk is not None
    assert len(shrunk) <= _TransportMixin._WS_FRAME_LIMIT
    rebuilt = json.loads(shrunk)
    assert rebuilt["item"]["content"][-1]["text"] == "这是什么？"
    kept = [
        part for part in rebuilt["item"]["content"]
        if part["type"] == "input_image"
    ]
    assert 1 <= len(kept) <= MAX_MULTIMODAL_TURN_IMAGES


def test_single_image_item_still_shrinks_through_the_same_path():
    import json

    from main_logic.omni_realtime_client._transport import _TransportMixin

    event = _multi_image_item([_jpeg_b64(900, 900)])
    payload = json.dumps(event)
    assert len(payload) > _TransportMixin._WS_FRAME_LIMIT

    shrunk = _TransportMixin._try_shrink_image_payload(event, payload)

    assert shrunk is not None
    assert len(shrunk) <= _TransportMixin._WS_FRAME_LIMIT


def test_middle_is_picked_by_capture_time_not_staging_order():
    record = _record()
    # Concurrent validation: completion order 0,4,1,3,2, capture order 0..4.
    for generation, captured_at in enumerate([0.0, 4.0, 1.0, 3.0, 2.0]):
        record.observe(
            _IndependentVisualFrame(
                image_b64=f"t{captured_at:.0f}",
                session_epoch=1,
                route_generation=1,
                generation=generation,
                captured_at=captured_at,
                source="screen",
                request_id=None,
            )
        )

    sampled = [frame.image_b64 for frame in record.sampled_frames()]

    # Completion-order midpoint gives t1; capture-time midpoint gives t2.
    assert sampled == ["t0", "t2", "t4"]


class _StopBeforeLLM(Exception):
    """Escape stream_text right after the user message is assembled."""


def _stream_text_client():
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = []
    client._conversation_history = []
    client._proactive_image_to_inject = None
    client._proactive_image_staged_at = 0.0
    client._proactive_image_history_len = 0
    client.vision_model = None
    client.model = "test-model"
    client.on_response_discarded = None
    client.on_input_transcript = None
    client._begin_reasoning_stream = lambda: None
    return client


async def _assemble_user_message(client, text="hello", **kwargs):
    async def _boom(_text):
        raise _StopBeforeLLM()

    with pytest.raises(_StopBeforeLLM):
        await OmniOfflineClient.stream_text(
            client, text, input_transcript_callback=_boom, **kwargs
        )
    return client._conversation_history[-1]


async def test_stream_text_with_turn_images_does_not_drain_the_queue():
    client = _stream_text_client()
    # Attachments arriving during this round's await must never be consumed by this round.
    client._pending_images = ["late-attachment"]

    message = await _assemble_user_message(client, turn_images=("a", "b"))

    urls = [
        part["image_url"]["url"].rsplit(",", 1)[-1]
        for part in message.content
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    assert urls == ["a", "b"]
    assert client._pending_images == ["late-attachment"]


async def test_stream_text_without_turn_images_still_consumes_attachments():
    """An ordinary text turn keeps its contract: attachments ride and dequeue."""
    client = _stream_text_client()
    client._pending_images = ["attachment"]

    message = await _assemble_user_message(client)

    urls = [
        part["image_url"]["url"].rsplit(",", 1)[-1]
        for part in message.content
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    assert urls == ["attachment"]
    assert client._pending_images == []


async def test_concurrent_text_turns_do_not_send_the_same_attachment_twice():
    """The attachment snapshot must be taken and removed without an await between."""
    client = _stream_text_client()
    client._pending_images = ["attachment"]
    switching = asyncio.Event()
    release = asyncio.Event()
    client.vision_model = "vision-model"
    client.model = "text-model"

    async def block_model_switch(*_args, **_kwargs):
        switching.set()
        await release.wait()

    client.switch_model = block_model_switch

    first = asyncio.create_task(_assemble_user_message(client, "first"))
    await asyncio.wait_for(switching.wait(), 1.0)

    # First round is still in switch_model; the queue must already be empty.
    assert client._pending_images == []

    release.set()
    message = await first

    urls = [
        part["image_url"]["url"].rsplit(",", 1)[-1]
        for part in message.content
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    assert urls == ["attachment"]


def _feed_out_of_order(record, capture_times):
    for generation, captured_at in enumerate(capture_times):
        record.observe(
            _IndependentVisualFrame(
                image_b64=f"t{captured_at}",
                session_epoch=1,
                route_generation=1,
                generation=generation,
                captured_at=float(captured_at),
                source="screen",
                request_id=None,
            )
        )
    return [frame.image_b64 for frame in record.sampled_frames()]


def test_candidate_thinning_runs_in_capture_order():
    # Alternating endpoints in completion order makes completion-based thinning first discard the true temporal midpoint frames;
    # sorting afterward cannot recover them.
    sampled = _feed_out_of_order(_record(), [0, 9, 1, 8, 2, 7, 3, 6, 4, 5])

    assert sampled[0] == "t0"
    assert sampled[-1] == "t9"
    middle_index = int(sampled[1][1:])
    # True midpoint is 4.5; any completion-order step biases toward an endpoint (earlier completion-order
    # thinning degraded to 2, while completion-position admission can bias to the other end).
    assert 3 <= middle_index <= 6


def test_out_of_order_thinning_keeps_a_bounded_candidate_set():
    record = _record()
    _feed_out_of_order(record, [i if i % 2 == 0 else 999 - i for i in range(200)])

    assert len(record.middle_candidates) <= _MAX_MIDDLE_CANDIDATES


def test_heavily_shuffled_arrival_still_lands_the_middle_near_the_centre():
    # Fill an entire segment alternating endpoints: completion position is nearly orthogonal to capture position.
    capture_times = [i if i % 2 == 0 else 199 - i for i in range(200)]
    sampled = _feed_out_of_order(_record(), capture_times)

    low, high = min(capture_times), max(capture_times)
    middle_index = int(sampled[1][1:])
    centre = (low + high) / 2
    assert abs(middle_index - centre) <= (high - low) * 0.25


async def test_failed_vision_switch_returns_attachments_to_the_queue():
    """The dequeue is atomic, so the only await before commit must roll back."""
    client = _stream_text_client()
    client._pending_images = ["attachment-a", "attachment-b"]
    client.vision_model = "vision-model"
    client.model = "text-model"

    async def failing_switch(*_args, **_kwargs):
        raise RuntimeError("vision endpoint down")

    client.switch_model = failing_switch

    with pytest.raises(RuntimeError, match="vision endpoint down"):
        await OmniOfflineClient.stream_text(client, "look at this")

    # The user's newly selected image was not sent and cannot simply disappear.
    assert client._pending_images == ["attachment-a", "attachment-b"]


async def test_failed_vision_switch_also_rolls_back_an_image_free_asr_turn():
    """submit_external_voice_turn goes through the same unprotected dequeue."""
    client = _stream_text_client()
    client._pending_images = ["attachment"]
    client.vision_model = "vision-model"
    client.model = "text-model"
    client._multimodal_submit_lock = asyncio.Lock()
    client._external_voice_submit_task = None

    async def failing_switch(*_args, **_kwargs):
        raise RuntimeError("vision endpoint down")

    client.switch_model = failing_switch

    with pytest.raises(RuntimeError, match="vision endpoint down"):
        await client.submit_external_voice_turn("说点什么", turn_id="turn-1")

    assert client._pending_images == ["attachment"]


async def test_unshrinkable_multimodal_item_fails_the_turn_instead_of_the_item():
    """Dropping the item would lose the transcript while a response still fires."""
    from main_logic.omni_realtime_client import (
        OmniRealtimeClient,
        RealtimeImagePayloadTooLargeError,
    )

    client = OmniRealtimeClient.__new__(OmniRealtimeClient)

    def _unshrinkable(_event, _payload):
        return None

    client._try_shrink_image_payload = _unshrinkable
    client._is_gemini = False
    client._fatal_error_occurred = False
    client.ws = object()
    client.get_multimodal_turn_delivery = lambda: MultimodalTurnDelivery.DIRECT_ATOMIC
    client._decode_multimodal_turn_image = (
        OmniRealtimeClient._decode_multimodal_turn_image
    )

    with pytest.raises(RealtimeImagePayloadTooLargeError):
        await OmniRealtimeClient.submit_multimodal_turn(
            client,
            "这是什么？",
            (_jpeg_b64(900, 900),),
            turn_id="turn-oversized",
        )


async def test_cancelled_after_history_commit_does_not_requeue_attachments():
    """History cannot be rolled back, so the attachments must not come back."""
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = ["attachment"]
    client._conversation_history = []

    async def commit_then_cancel(*_args, on_turn_committed=None, **_kwargs):
        # stream_text already appended the image-bearing HumanMessage (and called this invocation's
        # commit marker), then the next utterance interrupted it.
        client._conversation_history.append(object())
        on_turn_committed()
        raise asyncio.CancelledError()

    client.stream_text = commit_then_cancel

    assert await client.submit_multimodal_turn(
        "look",
        ("frame",),
        turn_id="turn-1",
    ) is False

    # The attachment is already in context; requeuing sends it again next round with an unrelated transcript.
    assert client._pending_images == []


async def test_cancelled_before_history_commit_still_requeues_attachments():
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = ["attachment"]
    client._conversation_history = []

    async def cancel_before_commit(*_args, **_kwargs):
        raise asyncio.CancelledError()

    client.stream_text = cancel_before_commit

    assert await client.submit_multimodal_turn(
        "look",
        ("frame",),
        turn_id="turn-1",
    ) is False

    assert client._pending_images == ["attachment"]


async def test_concurrent_history_growth_does_not_block_the_attachment_rollback():
    """Another request appending is not evidence that THIS turn committed."""
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = ["attachment"]
    client._conversation_history = []

    async def unrelated_append_then_fail(*_args, on_turn_committed=None, **_kwargs):
        # Another concurrent text request / ending response lengthened history, but **this invocation** failed
        # before appending its own HumanMessage.
        client._conversation_history.append(object())
        raise RuntimeError("vlm down")

    client.stream_text = unrelated_append_then_fail

    with pytest.raises(RuntimeError, match="vlm down"):
        await client.submit_multimodal_turn(
            "look",
            ("frame",),
            turn_id="turn-1",
        )

    # Explicit user images must not be consumed merely because someone else wrote history.
    assert client._pending_images == ["attachment"]
