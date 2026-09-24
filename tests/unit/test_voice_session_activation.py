from __future__ import annotations

from dataclasses import replace

import pytest

from main_logic.voice_input.activation import (
    ActivationGeneration,
    ActivationState,
    AudioFrame,
    BoundedAudioFrameBuffer,
    CandidateWindow,
    FrameRangeUnavailable,
    OutputCommit,
    OutputOrigin,
    VerificationResultKind,
    VoiceActivationConfig,
    VoiceActivationController,
)


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _generation(*, route: int = 3, profile: int = 5) -> ActivationGeneration:
    return ActivationGeneration(
        session_id="session-a",
        microphone=2,
        route=route,
        profile=profile,
        permission=7,
        input_owner="core_chat",
    )


def _frame(
    sequence: int,
    *,
    generation: ActivationGeneration | None = None,
    captured_at: float | None = None,
    samples: int = 1_600,
    context: object | None = None,
) -> AudioFrame:
    sample_start = sequence * samples
    return AudioFrame(
        sequence=sequence,
        sample_start=sample_start,
        sample_end=sample_start + samples,
        captured_at=(sequence * 0.1 if captured_at is None else captured_at),
        sample_rate=16_000,
        pcm=bytes([sequence % 251]) * (samples * 2),
        generation=generation or _generation(),
        context=context,
    )


def _waiting_controller(
    *,
    clock: _Clock | None = None,
    config: VoiceActivationConfig | None = None,
) -> VoiceActivationController:
    controller = VoiceActivationController(config, clock=clock or _Clock())
    assert (
        controller.start(_generation(), enabled=True).state is ActivationState.PREPARING
    )
    assert controller.mark_ready(_generation()).state is ActivationState.WAITING
    return controller


def _ingest_range(
    controller: VoiceActivationController,
    start: int,
    end: int,
    *,
    voiced: set[int] | None = None,
) -> None:
    voiced = voiced or set()
    for sequence in range(start, end + 1):
        controller.ingest(
            _frame(sequence),
            voice_activity=sequence in voiced,
        )


def _activate(
    controller: VoiceActivationController,
    *,
    candidate_start: int = 1,
    candidate_end: int = 2,
):
    decision = controller.request_verification(
        candidate_start_sequence=candidate_start,
        candidate_end_sequence=candidate_end,
    )
    request = decision.verification_request
    assert request is not None
    assert controller.claim_verification_input(request) is not None
    activated = controller.apply_verification_result(
        request,
        VerificationResultKind.OWNER,
    )
    assert activated.state is ActivationState.REPLAYING
    return request, activated


def _drain(controller: VoiceActivationController) -> list[tuple[int, OutputOrigin]]:
    frames: list[tuple[int, OutputOrigin]] = []
    while (lease := controller.claim_output()) is not None:
        frames.append((lease.frame.sequence, lease.origin))
        controller.complete_output(lease, OutputCommit.TRANSPORT_WRITTEN)
    return frames


def test_bounded_buffer_enforces_time_and_byte_limits() -> None:
    buffer = BoundedAudioFrameBuffer(max_seconds=0.3, max_bytes=9_600)

    evictions = []
    for sequence in range(5):
        result = buffer.append(_frame(sequence))
        evictions.extend(result.evicted_sequences)

    assert buffer.total_seconds == pytest.approx(0.3)
    assert buffer.total_bytes == 9_600
    assert buffer.oldest_sequence == 2
    assert buffer.latest_sequence == 4
    assert evictions == [0, 1]
    with pytest.raises(FrameRangeUnavailable):
        buffer.get_range(1, 4)


def test_default_controller_buffer_is_exactly_eight_seconds_and_256kb() -> None:
    controller = _waiting_controller()
    _ingest_range(controller, 0, 80)

    assert controller.buffered_seconds == pytest.approx(8.0)
    assert controller.buffered_bytes == 256_000
    assert (
        controller.request_verification(
            candidate_start_sequence=0,
            candidate_end_sequence=1,
        ).reason
        == "candidate_unavailable"
    )


def test_lifecycle_is_fail_closed_and_disabled_mode_bypasses() -> None:
    controller = VoiceActivationController()
    assert controller.state is ActivationState.DISABLED
    assert (
        controller.start(_generation(), enabled=True).state is ActivationState.PREPARING
    )
    assert (
        controller.mark_unavailable(_generation(), "model_missing").state
        is ActivationState.UNAVAILABLE
    )
    assert controller.ingest(_frame(0), voice_activity=True).output_ready is False
    assert controller.mark_ready(_generation()).state is ActivationState.WAITING
    assert controller.buffered_bytes == 0

    controller.disable()
    bypass = controller.ingest(_frame(1), voice_activity=False)
    assert bypass.state is ActivationState.DISABLED
    lease = controller.claim_output()
    assert lease is not None
    assert lease.origin is OutputOrigin.BYPASS

    assert controller.close().state is ActivationState.CLOSED
    assert controller.close().reason == "already_closed"
    assert controller.claim_output() is None


def test_waiting_buffers_without_output_and_exposes_read_only_scoring_pcm() -> None:
    controller = _waiting_controller()
    _ingest_range(controller, 0, 4, voiced={1, 2, 3})

    decision = controller.request_verification(
        candidate_start_sequence=1,
        candidate_end_sequence=3,
    )
    request = decision.verification_request
    assert decision.state is ActivationState.VERIFYING
    assert request is not None
    assert request.candidate == CandidateWindow(1, 3)
    assert request.replay_start_sequence == 0
    assert controller.claim_output() is None

    verification_input = controller.claim_verification_input(request)
    assert verification_input is not None
    assert verification_input.sample_rate == 16_000
    assert verification_input.sample_start == 1_600
    assert verification_input.sample_end == 6_400
    assert verification_input.pcm == b"".join(_frame(seq).pcm for seq in range(1, 4))
    assert controller.claim_verification_input(request) is None

    controller.apply_verification_result(
        request,
        VerificationResultKind.NOT_OWNER,
    )
    assert controller.claim_verification_input(request) is None
    assert controller.state is ActivationState.WAITING


def test_verification_is_single_flight_with_one_bounded_waiter() -> None:
    controller = _waiting_controller()
    _ingest_range(controller, 0, 5, voiced={1, 2, 4, 5})
    first = controller.request_verification(
        candidate_start_sequence=1,
        candidate_end_sequence=2,
    ).verification_request
    assert first is not None
    assert controller.claim_verification_input(first) is not None

    queued = controller.request_verification(
        candidate_start_sequence=4,
        candidate_end_sequence=5,
    )
    assert queued.reason == "verification_queued"
    assert controller.verification_queued is True
    full = controller.request_verification(
        candidate_start_sequence=3,
        candidate_end_sequence=5,
    )
    assert full.reason == "verification_queue_full"

    next_decision = controller.apply_verification_result(
        first,
        VerificationResultKind.NOT_OWNER,
    )
    assert next_decision.state is ActivationState.VERIFYING
    assert next_decision.verification_request is not None
    assert next_decision.verification_request.candidate == CandidateWindow(4, 5)
    assert controller.verification_queued is False


def test_owner_result_freezes_cutoff_and_serializes_replay_before_live() -> None:
    controller = _waiting_controller()
    contexts = [object() for _ in range(5)]
    for sequence in range(4):
        controller.ingest(
            _frame(sequence, context=contexts[sequence]),
            voice_activity=sequence in {1, 2, 3},
        )

    request, decision = _activate(controller, candidate_start=1, candidate_end=2)
    assert decision.replay_cutoff_sequence == 3
    assert request.replay_start_sequence == 0

    controller.ingest(_frame(4, context=contexts[4]), voice_activity=True)
    delivered: list[tuple[int, OutputOrigin]] = []
    delivered_contexts: list[object | None] = []
    while (lease := controller.claim_output()) is not None:
        delivered.append((lease.frame.sequence, lease.origin))
        delivered_contexts.append(lease.frame.context)
        controller.complete_output(lease, OutputCommit.TRANSPORT_WRITTEN)
    assert delivered == [
        (0, OutputOrigin.REPLAY),
        (1, OutputOrigin.REPLAY),
        (2, OutputOrigin.REPLAY),
        (3, OutputOrigin.REPLAY),
        (4, OutputOrigin.LIVE),
    ]
    assert delivered_contexts == contexts
    assert controller.state is ActivationState.ACTIVE

    controller.ingest(_frame(5), voice_activity=False)
    assert _drain(controller) == [(5, OutputOrigin.LIVE)]


def test_each_output_has_one_writer_and_only_proven_unsent_may_retry() -> None:
    controller = VoiceActivationController(
        VoiceActivationConfig(output_queue_bytes=12_800)
    )
    controller.start(_generation(), enabled=False)
    controller.ingest(_frame(0), voice_activity=False)

    first = controller.claim_output()
    assert first is not None
    assert controller.claim_output() is None
    stale = replace(first, lease_id=99)
    assert (
        controller.complete_output(stale, OutputCommit.NOT_SENT).reason
        == "stale_output_lease"
    )

    assert (
        controller.complete_output(first, OutputCommit.NOT_SENT).reason
        == "output_not_sent"
    )
    retry = controller.claim_output()
    assert retry is not None
    assert retry.lease_id != first.lease_id
    assert retry.frame.sequence == first.frame.sequence
    controller.complete_output(retry, OutputCommit.PROVIDER_CONFIRMED)
    assert controller.claim_output() is None


def test_unknown_delivery_fails_closed_without_replay() -> None:
    controller = VoiceActivationController()
    controller.start(_generation(), enabled=False)
    controller.ingest(_frame(0), voice_activity=False)
    controller.ingest(_frame(1), voice_activity=False)
    lease = controller.claim_output()
    assert lease is not None

    decision = controller.complete_output(lease, OutputCommit.UNKNOWN)
    assert decision.state is ActivationState.UNAVAILABLE
    assert decision.reason == "output_delivery_unknown"
    assert controller.pending_output_bytes == 0
    assert controller.claim_output() is None


def test_duplicate_is_ignored_but_timeline_gap_fails_closed() -> None:
    controller = _waiting_controller()
    controller.ingest(_frame(0), voice_activity=True)
    duplicate = controller.ingest(_frame(0), voice_activity=True)
    assert duplicate.reason == "duplicate_or_stale_frame"
    assert controller.buffered_bytes == len(_frame(0).pcm)

    gap = controller.ingest(_frame(2), voice_activity=True)
    assert gap.state is ActivationState.UNAVAILABLE
    assert gap.reason == "capture_timeline_gap"
    assert controller.buffered_bytes == 0


def test_evicted_candidate_and_replay_source_never_activate_partially() -> None:
    config = VoiceActivationConfig(
        buffer_seconds=0.3,
        buffer_bytes=9_600,
        output_queue_bytes=32_000,
    )
    controller = _waiting_controller(config=config)
    _ingest_range(controller, 0, 3, voiced={1, 2})
    assert (
        controller.request_verification(
            candidate_start_sequence=0,
            candidate_end_sequence=1,
        ).reason
        == "candidate_unavailable"
    )

    request = controller.request_verification(
        candidate_start_sequence=1,
        candidate_end_sequence=2,
    ).verification_request
    assert request is not None
    assert controller.claim_verification_input(request) is not None
    controller.ingest(_frame(4), voice_activity=False)

    stale_replay = controller.apply_verification_result(
        request,
        VerificationResultKind.OWNER,
    )
    assert stale_replay.state is ActivationState.WAITING
    assert stale_replay.reason == "replay_source_evicted"
    assert controller.claim_output() is None


def test_candidate_evicted_before_scoring_fails_closed() -> None:
    config = VoiceActivationConfig(
        buffer_seconds=0.3,
        buffer_bytes=9_600,
    )
    controller = _waiting_controller(config=config)
    _ingest_range(controller, 0, 2, voiced={0, 1})
    request = controller.request_verification(
        candidate_start_sequence=0,
        candidate_end_sequence=1,
    ).verification_request
    assert request is not None

    controller.ingest(_frame(3), voice_activity=False)
    assert controller.claim_verification_input(request) is None
    assert controller.state is ActivationState.UNAVAILABLE
    assert controller.buffered_bytes == 0


def test_late_verification_cannot_authorize_new_generation_or_closed_controller() -> (
    None
):
    controller = _waiting_controller()
    _ingest_range(controller, 0, 2, voiced={1, 2})
    old_request = controller.request_verification(
        candidate_start_sequence=1,
        candidate_end_sequence=2,
    ).verification_request
    assert old_request is not None

    next_generation = _generation(route=4)
    controller.start(next_generation, enabled=True)
    controller.mark_ready(next_generation)
    stale = controller.apply_verification_result(
        old_request,
        VerificationResultKind.OWNER,
    )
    assert stale.state is ActivationState.WAITING
    assert stale.reason == "stale_verification_result"
    assert controller.claim_output() is None

    controller.close()
    closed = controller.apply_verification_result(
        old_request,
        VerificationResultKind.OWNER,
    )
    assert closed.state is ActivationState.CLOSED
    assert closed.reason == "stale_verification_result"


def test_local_voice_activity_renews_idle_timeout_but_replay_does_not() -> None:
    clock = _Clock(2.0)
    controller = _waiting_controller(clock=clock)
    _ingest_range(controller, 0, 2, voiced={1, 2})
    _activate(controller)
    _drain(controller)
    assert controller.state is ActivationState.ACTIVE

    clock.now = 30.1
    assert controller.tick().state is ActivationState.ACTIVE
    controller.ingest(_frame(3, captured_at=30.1), voice_activity=True)
    _drain(controller)

    clock.now = 59.9
    assert controller.tick().state is ActivationState.ACTIVE
    clock.now = 60.2
    expired = controller.tick()
    assert expired.state is ActivationState.WAITING
    assert expired.reason == "idle_timeout"


def test_frame_captured_at_deadline_requires_new_activation() -> None:
    clock = _Clock(2.0)
    controller = _waiting_controller(clock=clock)
    _ingest_range(controller, 0, 2, voiced={1, 2})
    _activate(controller)
    _drain(controller)

    deadline_frame = _frame(3, captured_at=30.3)
    decision = controller.ingest(deadline_frame, voice_activity=True)
    assert decision.state is ActivationState.WAITING
    assert decision.output_ready is False
    assert controller.buffered_bytes == len(deadline_frame.pcm)


def test_replay_backlog_overflow_fails_closed_instead_of_truncating() -> None:
    config = VoiceActivationConfig(output_queue_bytes=6_400)
    controller = _waiting_controller(config=config)
    _ingest_range(controller, 0, 2, voiced={1, 2})
    request = controller.request_verification(
        candidate_start_sequence=1,
        candidate_end_sequence=2,
    ).verification_request
    assert request is not None
    assert controller.claim_verification_input(request) is not None

    failed = controller.apply_verification_result(
        request,
        VerificationResultKind.OWNER,
    )
    assert failed.state is ActivationState.UNAVAILABLE
    assert failed.reason == "output_backlog_overflow"
    assert controller.pending_output_bytes == 0
    assert controller.claim_output() is None


def test_close_and_disable_clear_pending_private_audio_and_old_work() -> None:
    controller = _waiting_controller()
    _ingest_range(controller, 0, 2, voiced={1, 2})
    request = controller.request_verification(
        candidate_start_sequence=1,
        candidate_end_sequence=2,
    ).verification_request
    assert request is not None

    assert controller.disable().state is ActivationState.DISABLED
    assert controller.buffered_bytes == 0
    assert controller.claim_verification_input(request) is None
    assert (
        controller.apply_verification_result(
            request,
            VerificationResultKind.OWNER,
        ).reason
        == "stale_verification_result"
    )
    assert controller.claim_output() is None

    controller.start(_generation(profile=6), enabled=True)
    controller.mark_ready(_generation(profile=6))
    controller.ingest(
        _frame(0, generation=_generation(profile=6)),
        voice_activity=True,
    )
    controller.close()
    assert controller.buffered_bytes == 0
    assert controller.pending_output_bytes == 0


def test_stale_frame_cannot_pollute_current_generation() -> None:
    current = _generation(route=4)
    controller = VoiceActivationController()
    controller.start(current, enabled=True)
    controller.mark_ready(current)

    decision = controller.ingest(_frame(0), voice_activity=True)
    assert decision.reason == "stale_frame"
    assert controller.buffered_bytes == 0
    assert controller.state is ActivationState.WAITING
