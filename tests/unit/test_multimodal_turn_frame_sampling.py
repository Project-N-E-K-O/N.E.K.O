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
    # 派生断言护栏：下面的用例按这个数字算期望值。
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
    # 只留最后一张会丢掉开口时的画面；只留开头两张则等于没有中间。抽样要真的
    # 落在这段发声的中段，容差放宽到 ±25%，不锁死具体抽样步长。
    assert abs(middle_index - (count - 1) / 2) <= count * 0.25


def test_candidate_buffer_does_not_grow_with_the_utterance():
    record = _record()
    _feed(record, 600)

    # 关键：不是"最后裁到 3 张"，而是全程都不攒——候选集本身有界。
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
        # 模拟用户在本轮 stream_text 真正消费之前又拖了一张图进来。
        await OmniOfflineClient.stream_image(client, "late-attachment")

    client.stream_text = AsyncMock(side_effect=attach_while_streaming)

    assert await client.submit_multimodal_turn(
        "look",
        ("a", "b", "c"),
        turn_id="turn-1",
    ) is True

    # 用户开口之前投递的附件属于这次发言，跟本轮帧一起送。
    assert client.stream_text.await_args.kwargs["turn_images"] == (
        "earlier-attachment",
        "a",
        "b",
        "c",
    )
    # 本轮进行中到达的那张留给下一轮，不被这一轮吞掉；本轮帧一张都没进队列。
    assert client._pending_images == ["late-attachment"]


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

    # 校验任务并发跑：先拍的那帧可以后落地。
    record.observe(later)
    record.observe(earlier)

    sampled = [frame.image_b64 for frame in record.sampled_frames()]
    assert sampled[0] == "earlier"
    assert sampled[-1] == "later"


def _jpeg_b64(width: int, height: int) -> str:
    import base64 as _b64
    from io import BytesIO

    from PIL import Image as PILImage

    # 噪声图，避免被 JPEG 压到极小而测不到超限路径。
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

    # 关键：整条 item 不能被丢掉——那会让本轮既没有图也没有 transcript 进历史，
    # 而 arbiter 仍然会发 response.create。
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
    # 并发校验：落地顺序 0,4,1,3,2，但拍摄顺序是 0..4。
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

    # 按落地顺序取正中间会拿到 t1；按拍摄时间才是 t2。
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
    # 本轮 await 期间到达的附件：绝不能被这一轮消费掉。
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
