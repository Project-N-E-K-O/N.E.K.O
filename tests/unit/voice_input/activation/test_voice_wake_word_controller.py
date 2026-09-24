from dataclasses import replace

import pytest

from main_logic.voice_input.activation import (
    ActivationGeneration,
    ActivationState,
    AudioFrame,
    OutputCommit,
    VerificationResultKind,
    VoiceActivationConfig,
    VoiceActivationController,
    WakeWordDetection,
)


GENERATION = ActivationGeneration("wake", 1, 2, 3, 4, "microphone")


def frame(sequence, captured_at=None):
    return AudioFrame(
        sequence,
        sequence * 1600,
        (sequence + 1) * 1600,
        sequence / 10 if captured_at is None else captured_at,
        16000,
        bytes([sequence % 251]) * 3200,
        GENERATION,
    )


def waiting(config=None):
    controller = VoiceActivationController(config, clock=lambda: 1.0)
    controller.start(GENERATION, enabled=True)
    controller.mark_ready(GENERATION)
    return controller


def detection(controller, start=1600, end=4000):
    return WakeWordDetection(
        "test keyword", GENERATION, controller.standby_epoch, start, end
    )


def drain(controller, now=1.0):
    sent = []
    while (lease := controller.claim_output()) is not None:
        sent.append(lease.frame)
        controller.complete_output(lease, OutputCommit.TRANSPORT_WRITTEN, now=now)
    return sent


def test_short_keyword_without_vad_replays_whole_utterance_once():
    controller = waiting()
    for i in range(8):
        controller.ingest(frame(i), voice_activity=False)
    hit = detection(controller)
    decision = controller.apply_wake_word(hit, now=1.0)
    assert decision.reason == "wake_word_detected"
    assert controller.last_voice_at == pytest.approx(0.25)
    assert controller.apply_wake_word(hit, now=1.0).reason == "stale_wake_word"
    assert drain(controller) == [frame(i) for i in range(8)]
    assert controller.state is ActivationState.ACTIVE
    assert controller.tick(30.249).state is ActivationState.ACTIVE
    assert controller.tick(30.25).state is ActivationState.WAITING


def test_keyword_does_not_move_newer_voice_activity_backwards():
    controller = waiting()
    for i in range(8):
        controller.ingest(frame(i), voice_activity=True)
    controller.apply_wake_word(detection(controller), now=1.0)
    assert controller.last_voice_at == pytest.approx(0.8)


@pytest.mark.parametrize("kind", list(VerificationResultKind))
def test_keyword_revokes_inflight_speaker_authority(kind):
    controller = waiting()
    for i in range(8):
        controller.ingest(frame(i), voice_activity=True)
    request = controller.request_verification(
        candidate_start_sequence=0, candidate_end_sequence=7
    ).verification_request
    controller.claim_verification_input(request)
    controller.apply_wake_word(detection(controller), now=1.0)
    assert (
        controller.apply_verification_result(request, kind, now=1.0).reason
        == "stale_verification_result"
    )
    assert len(drain(controller)) == 8


@pytest.mark.parametrize(
    "kind", [VerificationResultKind.NOT_OWNER, VerificationResultKind.INSUFFICIENT]
)
def test_normal_speaker_rejection_keeps_same_keyword_round(kind):
    controller = waiting()
    for i in range(8):
        controller.ingest(frame(i), voice_activity=True)
    hit = detection(controller)
    request = controller.request_verification(
        candidate_start_sequence=0, candidate_end_sequence=7
    ).verification_request
    controller.claim_verification_input(request)
    controller.apply_verification_result(request, kind, now=1.0)
    assert controller.apply_wake_word(hit, now=1.0).reason == "wake_word_detected"


def test_old_round_and_permission_hits_cannot_activate_new_round():
    controller = waiting()
    for i in range(8):
        controller.ingest(frame(i), voice_activity=False)
    old = detection(controller)
    controller.apply_wake_word(old, now=1.0)
    drain(controller)
    controller.tick(31.0)
    controller.ingest(frame(8, 31.0), voice_activity=False)
    assert controller.apply_wake_word(old, now=31.1).reason == "stale_wake_word"
    hit = detection(controller, 12800, 14400)
    assert (
        controller.apply_wake_word(
            replace(hit, generation=replace(GENERATION, permission=5)), now=31.1
        ).reason
        == "stale_wake_word"
    )
    assert controller.apply_wake_word(hit, now=31.1).reason == "wake_word_detected"


@pytest.mark.parametrize("state", ["preparing", "unavailable", "closed"])
def test_keyword_never_restores_disallowed_states(state):
    controller = waiting()
    controller.ingest(frame(0), voice_activity=False)
    hit = detection(controller, 0, 1600)
    if state == "preparing":
        controller.start(GENERATION, enabled=True)
    elif state == "unavailable":
        controller.mark_unavailable(GENERATION, "model_failed")
    else:
        controller.close()
    before = controller.state
    controller.apply_wake_word(hit, now=1.0)
    assert controller.state is before
    assert controller.claim_output() is None


def test_expired_or_evicted_keyword_cannot_extend_activity():
    controller = waiting(VoiceActivationConfig(buffer_seconds=0.3))
    for i in range(8):
        controller.ingest(frame(i), voice_activity=False)
    assert (
        controller.apply_wake_word(detection(controller), now=1.0).reason
        == "wake_word_source_unavailable"
    )
    assert (
        controller.apply_wake_word(detection(controller, 11200, 12800), now=31.0).reason
        == "wake_word_expired"
    )
    assert controller.last_voice_at is None
    assert controller.claim_output() is None
