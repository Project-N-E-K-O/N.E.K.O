import time
from types import SimpleNamespace
import pytest
from main_logic.asr_client.lifecycle import VoiceLifecycleState, VoiceTurnToken

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


@pytest.mark.unit
async def test_previous_turn_seal_in_the_same_tick_is_not_this_turn_cutoff() -> None:
    """A previous turn's seal in the same tick is not this turn's cutoff.

    monotonic is ~15ms coarse on Windows (_begin_core_multimodal_turn in this
    same module already falls back to a generation criterion for exactly this
    reason), so the previous turn's seal and the successor record's
    registration can land in one tick and compare equal. Stamping it onto the
    successor makes every later frame fail accepts(); once the opening frame
    expires, a slightly longer utterance degrades to text-only and the user
    sees "she only caught the instant I started talking".

    The criterion is turn identity, not the timestamp -- see the dual below.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=97)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    # The previous round's closure copy has a timestamp equal to this record's registration (same tick),
    # but belongs to the previous round. The live field is empty because PROVIDER_FINAL cleared it:
    # exactly why the retained copy exists.
    runtime._asr_turn_endpointed_at = None
    runtime._asr_last_turn_endpointed_at = record.registered_at
    runtime._asr_last_turn_endpointed_key = "asr-0-96"
    assert runtime._asr_last_turn_endpointed_key != record.turn_id

    assert runtime._stage_independent_visual_frame(
        "opening-frame",
        source="screen",
        request_id="screen-opening",
        captured_at=record.started_at,
    )
    # Frame captured mid-utterance: accepts() rejects it if the previous closure incorrectly becomes this round's cutoff.
    assert runtime._stage_independent_visual_frame(
        "middle-frame",
        source="screen",
        request_id="screen-middle",
        captured_at=record.registered_at + 1.0,
    )

    assert record.endpoint_at is None, (
        "上一轮的封口被盖到了后继回合上：相等必须归上一轮"
    )
    turn = runtime._snapshot_core_multimodal_turn(turn_id, "这是什么")

    assert turn is not None
    assert "middle-frame" in turn.images


@pytest.mark.unit
async def test_this_turn_seal_in_the_same_tick_is_still_its_cutoff() -> None:
    """The other direction: this turn's own seal must survive a tick collision.

    A very short utterance can seal inside the same ~15ms tick its record was
    registered in; PROVIDER_FINAL then clears the live field, leaving only the
    retained copy. A pure timestamp test is wrong in one direction or the
    other, and this is the half where "equality belongs to the previous turn"
    is wrong: this turn loses its cutoff and post-speech frames get folded into
    its transcript.

    Hence the criterion is turn identity, not the timestamp -- the runtime
    records which turn the retained seal belongs to.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=101)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    # This round's own closure occurs on the same tick as registration.
    runtime._asr_turn_endpointed_at = None
    runtime._asr_last_turn_endpointed_at = record.registered_at
    runtime._asr_last_turn_endpointed_key = record.turn_id

    runtime._stage_independent_visual_frame(
        "opening-frame",
        source="screen",
        request_id="screen-opening",
        captured_at=record.started_at,
    )

    assert record.endpoint_at == record.registered_at, (
        "本轮自己的封口被当成上一轮残值丢掉了：相等时必须靠身份而不是时间戳"
    )


@pytest.mark.unit
async def test_stale_seal_instant_from_a_previous_turn_is_not_this_turn_cutoff() -> None:
    """A leftover timestamp predates this record and must not seal it early."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    runtime._asr_turn_endpointed_at = time.monotonic() - 30.0
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=96)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    assert runtime._stage_independent_visual_frame(
        "spoken-frame",
        source="screen",
        request_id="screen-spoken",
        captured_at=record.started_at,
    )

    assert record.endpoint_at is None
    turn = runtime._snapshot_core_multimodal_turn(turn_id, "what is that")

    assert turn is not None
    assert turn.images == ("spoken-frame",)


@pytest.mark.unit
async def test_pending_turn_does_not_inherit_the_previous_turn_endpoint() -> None:
    """A turn started while the previous one drained must not be sealed by it.

    ``_asr_turn_onset_at`` survives a normal turn end (only close/abort/error
    clear it), and ``_asr_last_turn_endpointed_at`` is never cleared. If the
    pending-turn activation forgets to re-stamp the onset, Core takes the
    PREVIOUS turn's onset as this record's ``started_at``, the previous seal
    then satisfies ``sealed_at >= started_at``, and every frame captured for
    the new utterance is rejected as post-endpoint — a silent text-only turn.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    previous_onset = time.monotonic() - 2.0
    previous_seal = previous_onset + 1.0
    runtime._asr_turn_onset_at = previous_onset          # Left over from the previous round; never cleared.
    runtime._asr_turn_endpointed_at = None               # Cleared by PROVIDER_FINAL.
    runtime._asr_last_turn_endpointed_at = previous_seal  # Never cleared.
    # A pending turn is marked during previous-round draining, then activated later.
    runtime._asr_turn_onset_at = previous_seal + 0.2

    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=101)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    # Claim the cutoff only on first staging (or final freeze), so feed a frame before asserting.
    assert runtime._stage_independent_visual_frame(
        "new-utterance-frame",
        source="screen",
        request_id="screen-new",
        captured_at=record.started_at + 0.1,
    )
    assert record.endpoint_at is None, (
        "the previous turn's seal must not become this turn's cutoff"
    )
    turn = runtime._snapshot_core_multimodal_turn(turn_id, "and this one?")

    assert turn is not None
    assert turn.images == ("new-utterance-frame",)


@pytest.mark.unit
async def test_retained_seal_predating_registration_is_not_this_turn_cutoff() -> None:
    """Second line of defence behind the onset stamp.

    A retained seal survives across turns, so "is it >= started_at" cannot tell
    whether it belongs to this turn — an overlapping successor's onset is even
    recorded BEFORE the predecessor sealed. The floor for the retained copy is
    therefore the moment the record was registered: the previous turn's seal
    necessarily happened before that.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    previous_onset = time.monotonic() - 1.0
    previous_seal = previous_onset + 0.5
    # Simulate missing onset completion when activating a pending turn: retain the previous onset.
    runtime._asr_turn_onset_at = previous_onset
    runtime._asr_turn_endpointed_at = None
    runtime._asr_last_turn_endpointed_at = previous_seal

    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=102)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    assert runtime._stage_independent_visual_frame(
        "post-seal-frame",
        source="screen",
        request_id="screen-post",
        captured_at=previous_seal + 0.5,
    )
    # Even if onset retains a previous-round value (first defense failed), the previous closure cannot become this
    # round's cutoff, because it occurred before record registration.
    assert record.endpoint_at is None
    turn = runtime._snapshot_core_multimodal_turn(turn_id, "survives")

    assert turn is not None
    assert turn.images == ("post-seal-frame",)


@pytest.mark.unit
async def test_live_endpoint_still_seals_its_own_turn() -> None:
    """The live field only ever describes the in-flight turn, so keep it loose."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=106)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    assert runtime._stage_independent_visual_frame(
        "spoken-frame",
        source="screen",
        request_id="screen-spoken",
        captured_at=record.started_at,
    )
    # Very short utterance: closure can precede record registration; the live field must still bind.
    sealed_at = record.started_at
    runtime._asr_turn_endpointed_at = sealed_at
    runtime._mark_independent_asr_endpoint_if_sealed()

    assert record.endpoint_at == sealed_at


@pytest.mark.unit
async def test_endpoint_cutoff_survives_provider_final_clearing_the_live_field() -> None:
    """PROVIDER_FINAL clears the live timestamp before Core freezes the turn."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=97)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    assert runtime._stage_independent_visual_frame(
        "spoken-frame",
        source="screen",
        request_id="screen-spoken",
        captured_at=record.started_at,
    )

    # Closure -> provider final: runtime cleared the live field and lifecycle left
    # DRAINING, leaving only the copy preserved across final.
    sealed_at = record.started_at + 1.0
    runtime._asr_turn_endpointed_at = None
    runtime._asr_last_turn_endpointed_at = sealed_at
    runtime._asr_lifecycle = SimpleNamespace(
        snapshot=SimpleNamespace(state=VoiceLifecycleState.WARM_IDLE)
    )

    # A post-endpoint frame finishes validation only during final dispatch.
    runtime._stage_independent_visual_frame(
        "post-endpoint-frame",
        source="screen",
        request_id="screen-post",
        captured_at=sealed_at + 0.5,
    )

    turn = runtime._snapshot_core_multimodal_turn(turn_id, "what is that")

    assert record.endpoint_at == sealed_at
    assert turn is not None
    assert turn.images == ("spoken-frame",)
