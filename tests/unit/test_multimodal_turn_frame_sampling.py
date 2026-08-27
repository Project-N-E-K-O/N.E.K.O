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

    assert client._pending_images == [
        f"frame{i}" for i in range(MAX_MULTIMODAL_TURN_IMAGES)
    ]


async def test_offline_submit_still_accepts_a_bare_string():
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = []
    client.stream_text = AsyncMock()

    assert await client.submit_multimodal_turn(
        "look",
        "single-frame",
        turn_id="turn-1",
    ) is True

    assert client._pending_images == ["single-frame"]


async def test_offline_submit_rolls_back_every_staged_frame_on_failure():
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = ["earlier-attachment"]
    client.stream_text = AsyncMock(side_effect=RuntimeError("vlm down"))

    with pytest.raises(RuntimeError, match="vlm down"):
        await client.submit_multimodal_turn(
            "look",
            ("a", "b", "c"),
            turn_id="turn-1",
        )

    # 整段回滚：留下半段会让下一轮凭空多出这一轮的帧。
    assert client._pending_images == ["earlier-attachment"]


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
