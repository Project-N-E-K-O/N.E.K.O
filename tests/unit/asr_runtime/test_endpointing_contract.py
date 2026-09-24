import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, call
import pytest
from main_logic.asr_client.lifecycle import VoiceLifecycleEvent, VoiceTurnToken
from main_logic.asr_client.endpointing.detector import BoundDetectorTurn, CoreDetectorEventEnvelope, DetectorCandidateKey, DetectorIngressIdentity, DetectorTurnEvent

from tests.support.core_asr_harness import (
    _QueuedSmartTurnDetector,
    _install_ready_lifecycle,
    _install_replacement_runtime_generation,
    _seal_utterance,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


async def test_old_detector_endpoint_cannot_seal_replacement_runtime() -> None:
    runtime = _Runtime()
    old_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    detector = _QueuedSmartTurnDetector()
    detector.detector_epoch = 1
    runtime._asr_detector = detector
    runtime._asr_runtime._asr_current_ingress_token = runtime._capture_ingress_token()
    turn_token = runtime._asr_runtime._capture_turn_token(lifecycle)
    detector._token = turn_token
    candidate = DetectorCandidateKey(detector.detector_epoch, 1)
    envelope = CoreDetectorEventEnvelope(
        event=DetectorTurnEvent(
            ingress=DetectorIngressIdentity(
                ingress_token=turn_token.ingress,
                detector_epoch=detector.detector_epoch,
                sequence_no=1,
            ),
            bound_turn=BoundDetectorTurn(
                candidate=candidate,
                turn_token=turn_token,
            ),
            kind="complete",
        ),
        detector_ref=detector,
        lifecycle_ref=lifecycle,
        session_epoch=runtime._asr_session_epoch,
    )
    draining_started = asyncio.Event()
    release_draining = asyncio.Event()

    async def block_old_lifecycle(payload: str) -> None:
        status = json.loads(payload)
        if (
            status.get("code") == "ASR_LIFECYCLE_STATE"
            and status.get("details", {}).get("state") == "draining"
        ):
            draining_started.set()
            await release_draining.wait()

    runtime.send_status.side_effect = block_old_lifecycle
    endpoint_task = asyncio.create_task(
        runtime._asr_runtime._dispatch_asr_detector_event(envelope)
    )
    await asyncio.wait_for(draining_started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    release_draining.set()
    await asyncio.wait_for(endpoint_task, 1)

    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    new_session.close.assert_not_awaited()
    assert runtime._asr_route_mode == "independent"
    statuses = [
        json.loads(call.args[0]).get("code")
        for call in runtime.send_status.await_args_list
    ]
    assert "ASR_AUDIO_ORDERING_FAILED" not in statuses


@pytest.mark.unit
async def test_frames_captured_after_the_endpoint_are_not_folded_in() -> None:
    """Screen state from after the user stopped talking is not this turn."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=92)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    assert runtime._stage_independent_visual_frame(
        "spoken-frame",
        source="screen",
        request_id="screen-spoken",
        captured_at=record.started_at,
    )

    _seal_utterance(runtime)
    runtime._mark_independent_asr_endpoint_if_sealed()
    assert record.endpoint_at is not None
    runtime._stage_independent_visual_frame(
        "post-endpoint-frame",
        source="screen",
        request_id="screen-post",
        captured_at=record.endpoint_at + 0.5,
    )

    turn = runtime._snapshot_core_multimodal_turn(turn_id, "what is that")

    assert turn is not None
    assert turn.images == ("spoken-frame",)


@pytest.mark.unit
async def test_frame_captured_before_the_endpoint_survives_late_validation() -> None:
    """Validation finishing after DRAINING must not discard a spoken-window frame."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=93)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]
    captured_while_speaking = record.started_at

    # 端点先到，这帧的校验任务才跑完 —— 拍摄时它还在说话，必须留下。
    _seal_utterance(runtime)
    runtime._mark_independent_asr_endpoint_if_sealed()
    assert runtime._stage_independent_visual_frame(
        "late-validated-frame",
        source="screen",
        request_id="screen-late",
        captured_at=captured_while_speaking,
    )

    turn = runtime._snapshot_core_multimodal_turn(turn_id, "what is that")

    assert turn is not None
    assert turn.images == ("late-validated-frame",)


@pytest.mark.unit
async def test_endpoint_cutoff_uses_the_recorded_seal_instant() -> None:
    """A frame captured in the gap before Core looks must still be excluded."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=95)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    assert runtime._stage_independent_visual_frame(
        "spoken-frame",
        source="screen",
        request_id="screen-spoken",
        captured_at=record.started_at,
    )

    # ASR 在这一刻封口，但 Core 要到下一帧 staging 才会去看。
    sealed_at = record.started_at + 1.0
    runtime._asr_turn_endpointed_at = sealed_at
    _seal_utterance(runtime)

    # 这帧拍摄于封口之后、Core 观察之前——按观察时刻当截止值它会被放行。
    runtime._stage_independent_visual_frame(
        "gap-frame",
        source="screen",
        request_id="screen-gap",
        captured_at=sealed_at + 0.5,
    )

    assert record.endpoint_at == sealed_at
    turn = runtime._snapshot_core_multimodal_turn(turn_id, "what is that")

    assert turn is not None
    assert turn.images == ("spoken-frame",)


@pytest.mark.unit
async def test_live_seal_between_onset_and_registration_still_binds() -> None:
    """The live field floors on started_at, not registered_at.

    started_at is rolled back to the speech onset (an overlapping successor can
    even predate the previous turn's seal), so a real window exists between the
    seal and the registration: a very short utterance can be sealed by ASR
    before its record is built. Flooring the live field on registered_at would
    leave such a turn without a cutoff forever, folding everything captured
    after the user stopped talking into this turn.

    Found by mutation: flipping the live branch to registered_at turned nothing
    red in this whole file before this case existed.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    # 把语音起点回拨，制造 started_at < registered_at 的真实窗口。
    onset = time.monotonic() - 0.5
    runtime._asr_turn_onset_at = onset
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=99)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]
    assert record.started_at < record.registered_at, "夹具没造出那段窗口"

    # 在飞字段：封口发生在开口之后、record 建立之前。
    sealed_at = record.started_at + 0.1
    assert sealed_at < record.registered_at
    runtime._asr_turn_endpointed_at = sealed_at

    runtime._stage_independent_visual_frame(
        "post-seal-frame",
        source="screen",
        request_id="screen-post-seal",
        captured_at=sealed_at + 0.05,
    )

    assert record.endpoint_at == sealed_at


@pytest.mark.unit
async def test_a_seal_after_this_record_registered_still_becomes_its_cutoff() -> None:
    """Dual: a retained seal that really belongs to this turn still binds.

    Guards against over-tightening the gate into "never trust a retained copy".
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=98)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    sealed_at = record.registered_at + 1.0
    runtime._asr_turn_endpointed_at = None
    runtime._asr_last_turn_endpointed_at = sealed_at
    runtime._asr_last_turn_endpointed_key = record.turn_id

    runtime._stage_independent_visual_frame(
        "late-frame",
        source="screen",
        request_id="screen-late",
        captured_at=sealed_at + 0.5,
    )

    assert record.endpoint_at == sealed_at


@pytest.mark.unit
async def test_overlapping_successor_is_not_sealed_by_its_predecessor() -> None:
    """The successor's onset predates the predecessor's seal — by design.

    A provider-VAD successor utterance begins while the previous turn is still
    ACTIVE, so its recorded onset is EARLIER than the previous turn's endpoint.
    Comparing the retained seal against ``started_at`` would therefore bind the
    predecessor's endpoint to the successor and reject every frame it captures.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    successor_onset = time.monotonic() - 1.0
    predecessor_seal = successor_onset + 0.3
    runtime._asr_turn_onset_at = successor_onset
    runtime._asr_turn_endpointed_at = None
    runtime._asr_last_turn_endpointed_at = predecessor_seal

    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=105)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]
    assert record.started_at < predecessor_seal

    assert runtime._stage_independent_visual_frame(
        "successor-frame",
        source="screen",
        request_id="screen-successor",
        captured_at=predecessor_seal + 0.4,
    )
    assert record.endpoint_at is None

    turn = runtime._snapshot_core_multimodal_turn(turn_id, "and this?")

    assert turn is not None
    assert turn.images == ("successor-frame",)


@pytest.mark.unit
async def test_endpoint_marking_skips_invalidated_records() -> None:
    """A successor's seal has no business landing on a superseded record."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    first = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=401)
    first_id = f"asr-{first.ingress.session_epoch}-{first.turn_id}"
    runtime._begin_core_multimodal_turn(first_id, first)
    retained = runtime._core_multimodal_turns[first_id]

    second = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=402)
    second_id = f"asr-{second.ingress.session_epoch}-{second.turn_id}"
    runtime._begin_core_multimodal_turn(second_id, second)
    active = runtime._core_multimodal_turns[second_id]

    runtime._asr_turn_endpointed_at = time.monotonic()
    runtime._mark_independent_asr_endpoint_if_sealed()

    assert active.endpoint_at is not None
    assert retained.endpoint_at is None


@pytest.mark.unit
async def test_a_late_registration_still_adopts_its_real_onset() -> None:
    """Waiting behind a provider final does not make an onset stale.

    An overlapping utterance registers only after the previous turn's final
    lands, and that provider timeout reaches 40s in the registry. Judging the
    onset by the FRAME freshness window (5s) rejects it, resets ``started_at``
    to registration time and drops every frame captured since the user actually
    started speaking -- the turn goes text-only while the screen was streaming
    the whole time. Frame freshness is enforced separately, at freeze time.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    now = time.monotonic()
    real_onset = now - 30.0  # 排在一个 provider final 后面，远超帧的 5s TTL
    runtime._asr_runtime._asr_turn_onset_at = real_onset

    frame_at = real_onset + 1.0
    assert runtime._stage_independent_visual_frame(
        "frame-from-the-real-onset",
        source="screen",
        request_id="screen-late",
        captured_at=frame_at,
    )

    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=902)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    # 起点是真实开口时刻，不是注册时刻。
    assert record.started_at == pytest.approx(real_onset, abs=0.01)
    # 那一刻以来的帧被采纳了，而不是整轮退化成纯文本。
    assert [f.image_b64 for f in record.sampled_frames()] == [
        "frame-from-the-real-onset"
    ]
