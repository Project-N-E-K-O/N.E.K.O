import asyncio
import time
import pytest
from main_logic.core.multimodal_turn import _MAX_PRERECORD_VISUAL_VALIDATIONS
from main_logic.asr_client.lifecycle import VoiceTurnToken

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


@pytest.mark.unit
async def test_prerecord_validation_task_is_attached_to_the_onset_record() -> None:
    """A frame task created before the record exists must not be dropped."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    onset = time.monotonic()
    runtime._asr_turn_onset_at = onset

    gate = asyncio.Event()

    async def pending_validation() -> None:
        await gate.wait()

    task = asyncio.create_task(pending_validation())
    await asyncio.sleep(0)

    # The record does not exist yet: the old implementation permanently discarded this task here.
    assert runtime._track_independent_visual_validation_task(
        task,
        captured_at=onset + 0.01,
    ) is False

    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=100)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    assert task in record.pending_visual_validations
    assert runtime._prerecord_visual_validations == {}

    gate.set()
    await task


@pytest.mark.unit
async def test_prerecord_validation_stash_is_bounded() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    onset = time.monotonic()
    runtime._asr_turn_onset_at = onset
    gate = asyncio.Event()

    async def pending_validation() -> None:
        await gate.wait()

    tasks = [asyncio.create_task(pending_validation()) for _ in range(40)]
    await asyncio.sleep(0)
    for task in tasks:
        runtime._track_independent_visual_validation_task(
            task,
            captured_at=onset + 0.01,
        )

    assert len(runtime._prerecord_visual_validations) <= 8

    gate.set()
    await asyncio.gather(*tasks)


@pytest.mark.unit
async def test_prerecord_frame_buffer_is_bounded() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    onset = time.monotonic()
    runtime._asr_turn_onset_at = onset

    for index in range(40):
        runtime._stage_independent_visual_frame(
            f"prerecord-{index}",
            source="screen",
            request_id=f"screen-{index}",
            captured_at=onset + 0.001 * (index + 1),
        )

    assert len(runtime._prerecord_visual_frames) <= 8
    # On overflow, drop the most redundant interior point, never the head: the head marks speech onset.
    kept = [frame.image_b64 for frame in runtime._prerecord_visual_frames]
    assert kept[0] == "prerecord-0"
    assert kept[-1] == "prerecord-39"


@pytest.mark.unit
async def test_prerecord_buffer_trims_in_capture_order_not_arrival_order() -> None:
    """Concurrent validation means arrival order is not capture order.

    The cap evicts the most redundant INTERIOR point and keeps both ends. If the
    buffer is held in arrival order, those "ends" are not the temporal first and
    last, so the eviction can drop the actual start of the utterance — the same
    trap already fixed once for the middle-frame candidates.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    onset = time.monotonic()
    runtime._asr_turn_onset_at = onset

    # Feed alternating endpoints in arrival order: 0, 19, 1, 18, 2, 17, ...
    capture_order = [i if i % 2 == 0 else 19 - i for i in range(20)]
    for generation, index in enumerate(capture_order):
        runtime._stage_independent_visual_frame(
            f"f{index}",
            source="screen",
            request_id=f"screen-{generation}",
            captured_at=onset + 0.001 * (index + 1),
        )

    kept = [frame.image_b64 for frame in runtime._prerecord_visual_frames]
    assert len(kept) <= 8
    # Preserve chronological endpoints, rather than the first/last frames to arrive.
    assert kept[0] == "f0"
    assert kept[-1] == f"f{max(capture_order)}"
    captured = [frame.captured_at for frame in runtime._prerecord_visual_frames]
    assert captured == sorted(captured)


@pytest.mark.unit
async def test_prerecord_task_stash_keeps_the_earliest_validation() -> None:
    """Evicting the oldest task drops the opening frame of the utterance.

    The router registers validation tasks in capture order, so the oldest entry
    is the earliest capture. If a short utterance reaches final before that
    evicted task completes, the final freeze cannot wait for it and the record
    is abandoned before the opening frame lands.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    onset = time.monotonic()
    runtime._asr_turn_onset_at = onset
    gate = asyncio.Event()

    async def pending_validation() -> None:
        await gate.wait()

    tasks = [asyncio.create_task(pending_validation()) for _ in range(30)]
    await asyncio.sleep(0)
    for index, task in enumerate(tasks):
        runtime._track_independent_visual_validation_task(
            task,
            captured_at=onset + 0.001 * index,
        )

    stash = runtime._prerecord_visual_validations
    assert len(stash) <= 8
    kept = sorted(stash.values())
    # Both chronological endpoints must survive; eviction is allowed only in the middle.
    assert kept[0] == onset
    assert kept[-1] == onset + 0.001 * 29

    gate.set()
    await asyncio.gather(*tasks)


@pytest.mark.unit
async def test_validation_tracking_picks_the_active_record_not_a_retained_one() -> None:
    """Retained records exist only so an in-flight final keeps its transcript.

    They are invalidated; the active turn is the newest live one. Selecting
    "whichever record happens to be first" binds new frame validations to a
    superseded turn.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    first = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=301)
    first_id = f"asr-{first.ingress.session_epoch}-{first.turn_id}"
    runtime._begin_core_multimodal_turn(first_id, first)
    first_record = runtime._core_multimodal_turns[first_id]

    second = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=302)
    second_id = f"asr-{second.ingress.session_epoch}-{second.turn_id}"
    runtime._begin_core_multimodal_turn(second_id, second)
    second_record = runtime._core_multimodal_turns[second_id]

    gate = asyncio.Event()

    async def pending_validation() -> None:
        await gate.wait()

    task = asyncio.create_task(pending_validation())
    await asyncio.sleep(0)
    assert runtime._track_independent_visual_validation_task(
        task,
        captured_at=second_record.started_at,
    ) is True

    assert task in second_record.pending_visual_validations
    assert task not in first_record.pending_visual_validations

    gate.set()
    await task


@pytest.mark.unit
async def test_invalidated_record_does_not_hand_over_its_frames() -> None:
    """A superseded turn keeps its words but not the successor's frames."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    first = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=303)
    first_id = f"asr-{first.ingress.session_epoch}-{first.turn_id}"
    runtime._begin_core_multimodal_turn(first_id, first)
    record = runtime._core_multimodal_turns[first_id]
    assert runtime._stage_independent_visual_frame(
        "first-turn-frame",
        source="screen",
        request_id="screen-first",
        captured_at=record.started_at,
    )
    assert runtime._snapshot_core_multimodal_turn(first_id, "first") is not None

    second = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=304)
    runtime._begin_core_multimodal_turn(
        f"asr-{second.ingress.session_epoch}-{second.turn_id}", second
    )

    # Keep the speech record, but visual ownership has moved to the successor turn: submit text only.
    assert runtime._core_multimodal_turns.get(first_id) is record
    assert runtime._snapshot_core_multimodal_turn(first_id, "first") is None


@pytest.mark.unit
async def test_prerecord_stash_still_arms_while_older_records_are_retained() -> None:
    """The dict is no longer empty between turns, so 'no records' is the wrong test."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    first = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=305)
    first_id = f"asr-{first.ingress.session_epoch}-{first.turn_id}"
    runtime._begin_core_multimodal_turn(first_id, first)
    runtime._core_multimodal_turns[first_id].invalidated.set()

    onset = time.monotonic()
    runtime._asr_turn_onset_at = onset
    assert runtime._stage_independent_visual_frame(
        "between-turns-frame",
        source="screen",
        request_id="screen-between",
        captured_at=onset,
    )

    # The current turn does not exist yet; buffer this frame until it does.
    assert [f.image_b64 for f in runtime._prerecord_visual_frames] == [
        "between-turns-frame"
    ]


@pytest.mark.unit
async def test_idle_frames_do_not_consume_the_prerecord_budget() -> None:
    """Frames from before the user spoke are not this turn's to keep.

    Screen sharing fills the eight-slot buffer while nobody is talking. The
    sampler deliberately preserves widely spaced endpoints, so those idle
    frames hold their slots; the few captured between speech confirmation and
    record creation then get sampled together with the whole idle history, and
    the onset filter at record creation discards all of them -- leaving only
    the newest frame and losing this turn's opening and middle views.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    now = time.monotonic()

    # Sharing with no speech: idle frames fill the buffer.
    runtime._asr_runtime._asr_turn_onset_at = None
    for i in range(_MAX_PRERECORD_VISUAL_VALIDATIONS):
        assert runtime._stage_independent_visual_frame(
            f"idle-{i}",
            source="screen",
            request_id=f"screen-idle-{i}",
            captured_at=now - 60.0 + i * 5.0,
        )
    assert len(runtime._prerecord_visual_frames) == _MAX_PRERECORD_VISUAL_VALIDATIONS

    # User starts speaking; three more frames arrive between confirmation and registration.
    onset = now - 2.0
    runtime._asr_runtime._asr_turn_onset_at = onset
    for i in range(3):
        assert runtime._stage_independent_visual_frame(
            f"speech-{i}",
            source="screen",
            request_id=f"screen-speech-{i}",
            captured_at=onset + 0.2 * (i + 1),
        )

    kept = [f.image_b64 for f in runtime._prerecord_visual_frames]
    # Pre-speech frames consume no slots; all three frames from this turn remain.
    assert kept == ["speech-0", "speech-1", "speech-2"]
