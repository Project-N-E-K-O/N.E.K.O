import time
import pytest
from main_logic.asr_client.lifecycle import VoiceTurnToken

from tests.support.core_asr_harness import (
    _seal_utterance,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


@pytest.mark.unit
async def test_out_of_order_frame_still_joins_the_turn_sample() -> None:
    """A frame that validates late must not be dropped by the latest-frame guard."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=91)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]
    base = record.started_at

    assert runtime._stage_independent_visual_frame(
        "later-frame",
        source="screen",
        request_id="screen-later",
        captured_at=base + 1.0,
    )
    # Captured earlier but validated later: it cannot replace the latest-frame cache, but must enter this turn's sample.
    assert runtime._stage_independent_visual_frame(
        "earlier-frame",
        source="camera",
        request_id="camera-earlier",
        captured_at=base + 0.1,
    )
    assert runtime._latest_independent_visual_frame.image_b64 == "later-frame"

    turn = runtime._snapshot_core_multimodal_turn(turn_id, "what is that")

    assert turn is not None
    assert turn.images == ("earlier-frame", "later-frame")


@pytest.mark.unit
async def test_post_endpoint_cache_frame_cannot_seed_an_empty_turn() -> None:
    """The empty-record fallback must respect the endpoint cutoff too."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=94)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    _seal_utterance(runtime)
    runtime._mark_independent_asr_endpoint_if_sealed()
    runtime._stage_independent_visual_frame(
        "post-endpoint-frame",
        source="screen",
        request_id="screen-post",
        captured_at=record.endpoint_at + 0.5,
    )
    # The cache has this frame for proactive observation, but this turn received none.
    assert runtime._latest_independent_visual_frame.image_b64 == "post-endpoint-frame"
    assert record.last_frame is None

    assert runtime._snapshot_core_multimodal_turn(turn_id, "what is that") is None


@pytest.mark.unit
async def test_frame_validated_during_lifecycle_notification_joins_the_turn() -> None:
    """Speech onset, not record creation, is the ownership boundary."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    onset = time.monotonic()
    # Set only _asr_turn_onset_at deliberately: both production paths set _asr_turn_audio_started_at
    # after delivery; using it as onset was the fixed defect, so this test must not rely on it.
    runtime._asr_turn_onset_at = onset

    # Speech is confirmed while Core is stuck delivering _send_asr_lifecycle_state; this frame shows the utterance's
    # beginning and what the user pointed at, captured before record creation.
    assert runtime._stage_independent_visual_frame(
        "onset-frame",
        source="screen",
        request_id="screen-onset",
        captured_at=onset + 0.01,
    )

    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=98)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)

    turn = runtime._snapshot_core_multimodal_turn(turn_id, "what is that")

    assert turn is not None
    assert turn.images == ("onset-frame",)


@pytest.mark.unit
async def test_frame_captured_before_the_onset_is_still_a_prior_turn_frame() -> None:
    """Widening the window to the onset must not reach into the previous turn."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    onset = time.monotonic()

    assert runtime._stage_independent_visual_frame(
        "prior-turn-frame",
        source="screen",
        request_id="screen-prior",
        captured_at=onset - 1.0,
    )
    runtime._asr_turn_onset_at = onset

    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=99)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)

    assert runtime._snapshot_core_multimodal_turn(turn_id, "new question") is None


@pytest.mark.unit
async def test_all_prerecord_frames_join_the_turn_not_just_the_newest() -> None:
    """Frames validated before the record exists must survive as a span.

    The single-slot cache keeps only the newest frame, and the pending-task
    stash drops a task the moment it completes. If lifecycle delivery is slow
    enough for several validations to land first, keeping only the newest one
    silently loses the actual first/middle frames of the utterance.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    onset = time.monotonic()
    runtime._asr_turn_onset_at = onset

    for index in range(3):
        assert runtime._stage_independent_visual_frame(
            f"prerecord-{index}",
            source="screen",
            request_id=f"screen-{index}",
            captured_at=onset + 0.01 * (index + 1),
        )
    assert len(runtime._prerecord_visual_frames) == 3

    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=103)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)

    turn = runtime._snapshot_core_multimodal_turn(turn_id, "what is that")

    assert turn is not None
    # Beginning / middle / end remain, rather than only the latest frame.
    assert turn.images == ("prerecord-0", "prerecord-1", "prerecord-2")
    # Consumption clears the cache, preventing leakage into the next turn.
    assert runtime._prerecord_visual_frames == []


@pytest.mark.unit
async def test_prerecord_frames_from_a_previous_route_are_not_adopted() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    onset = time.monotonic()
    runtime._asr_turn_onset_at = onset

    assert runtime._stage_independent_visual_frame(
        "prerecord-frame",
        source="screen",
        request_id="screen-0",
        captured_at=onset + 0.01,
    )
    # After route generation changes, this frame no longer belongs to this chain.
    runtime._voice_input_transition_generation += 1

    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=104)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    # Assert whether the frame entered the record. A None snapshot alone is insufficient:
    # accepts() filters again by route_generation on freezing, returning None even if acceptance
    # missed the check. Such a test falsely passes (removing route filtering at acceptance still passed in practice).
    assert record.last_frame is None
    assert record.first_frame is None
    assert runtime._snapshot_core_multimodal_turn(turn_id, "lost") is None


@pytest.mark.unit
async def test_frames_after_a_sealed_turn_are_kept_for_the_successor() -> None:
    """A sealed record is done taking frames, so it must not block the buffer.

    Between the endpoint and the provider final, the record is sealed but not
    yet invalidated -- the successor cannot be prepared until that final lands.
    Frames captured in that window fail the sealed record's ``accepts()``
    (they are past its endpoint), so if it still counts as the active record
    they are neither attached nor retained: the successor turn loses its
    opening and middle frames and keeps only the latest-frame cache.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=901)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    # This turn has finished speaking and is closed, but remains valid until provider final arrives.
    record.endpoint_at = record.started_at + 1.0
    assert not record.invalidated.is_set()
    assert runtime._active_multimodal_turn_record() is None

    # The successor begins; this frame was captured after closure.
    assert runtime._stage_independent_visual_frame(
        "successor-opening-frame",
        source="screen",
        request_id="screen-successor",
        captured_at=record.endpoint_at + 0.5,
    )

    # It cannot enter the closed record, but must remain available for the successor.
    assert [f.image_b64 for f in runtime._prerecord_visual_frames] == [
        "successor-opening-frame"
    ]


@pytest.mark.unit
async def test_overlap_prerecord_trims_against_the_pending_turn_onset() -> None:
    """During an overlap the successor's boundary lives in the pending slot.

    Speech that starts while the previous turn is still DRAINING records its
    onset in ``_asr_pending_turn_onset_at``; it is only copied into
    ``_asr_turn_onset_at`` once the previous provider final activates that turn.
    Reading only the latter means the whole overlap window is judged against the
    PRECEDING turn's onset, so frames from after its endpoint still count as
    "this turn's" and fill the bounded buffer, evicting the successor's real
    opening and middle views.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    now = time.monotonic()

    # The previous turn's onset is early; its successor begins before it ends, recording the boundary in the pending slot.
    runtime._asr_runtime._asr_turn_onset_at = now - 40.0
    runtime._asr_runtime._asr_pending_turn_onset_at = now - 2.0

    # Frame between the previous turn's closure and successor onset.
    for i in range(3):
        assert runtime._stage_independent_visual_frame(
            f"between-{i}",
            source="screen",
            request_id=f"screen-between-{i}",
            captured_at=now - 30.0 + i,
        )
    # The successor's own frame.
    for i in range(2):
        assert runtime._stage_independent_visual_frame(
            f"successor-{i}",
            source="screen",
            request_id=f"screen-successor-{i}",
            captured_at=now - 1.5 + i * 0.3,
        )

    # Retain only the successor's own frame; intervening frames occupy no slots.
    assert [f.image_b64 for f in runtime._prerecord_visual_frames] == [
        "successor-0",
        "successor-1",
    ]
