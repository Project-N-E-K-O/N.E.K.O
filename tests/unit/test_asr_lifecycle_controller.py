from __future__ import annotations

from main_logic.asr_client.lifecycle_contracts import (
    VoiceLifecycleEvent,
    VoiceLifecycleState,
    VoiceRouteMode,
)
from main_logic.asr_client.lifecycle_controller import (
    AudioDisposition,
    VoiceInputLifecycleController,
)
from main_logic.asr_client.provider_policy import resolve_provider_policy


def _pcm(milliseconds: int) -> bytes:
    return b"\x01\x00" * (16_000 * milliseconds // 1_000)


def test_shadow_mode_observes_suppression_without_dropping_independent_asr_audio() -> None:
    controller = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=True,
    )
    controller.open(route_mode=VoiceRouteMode.INDEPENDENT)

    decision = controller.accept_audio(_pcm(100), sample_rate_hz=16_000)

    assert decision.disposition is AudioDisposition.FORWARD
    assert decision.shadow_disposition is AudioDisposition.BUFFER
    assert controller.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    assert controller.metrics.local_audio_ms == 100
    assert controller.metrics.cloud_audio_ms == 0
    assert controller.metrics.shadow_suppressed_audio_ms == 100


def test_enforced_local_listen_buffers_until_speech_is_confirmed() -> None:
    controller = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    controller.open(route_mode=VoiceRouteMode.INDEPENDENT)

    first = controller.accept_audio(_pcm(500), sample_rate_hz=16_000)
    controller.transition(VoiceLifecycleEvent.SOFT_WAKE)
    controller.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    second = controller.accept_audio(_pcm(100), sample_rate_hz=16_000)

    assert first.disposition is AudioDisposition.BUFFER
    assert second.disposition is AudioDisposition.FORWARD_WITH_PRE_ROLL
    assert second.pre_roll == _pcm(600)
    assert controller.snapshot.state is VoiceLifecycleState.ACTIVE


def test_prewarming_uses_eight_second_pending_connect_buffer() -> None:
    controller = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    controller.open(route_mode=VoiceRouteMode.INDEPENDENT)
    controller.accept_audio(_pcm(500), sample_rate_hz=16_000)
    controller.transition(VoiceLifecycleEvent.SOFT_WAKE)
    controller.accept_audio(_pcm(7_800), sample_rate_hz=16_000)

    assert controller.pending_connect_bytes == len(_pcm(8_000))
    assert controller.metrics.buffer_overflow_count == 1

    controller.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    start_audio = controller.drain_active_start_audio()

    assert start_audio == _pcm(200) + _pcm(7_800)
    assert controller.pending_connect_bytes == 0


def test_blocked_route_consumes_audio_without_buffer_or_forward() -> None:
    controller = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("gemini", "manual"),
        shadow_mode=False,
    )
    controller.open(route_mode=VoiceRouteMode.BLOCKED)

    decision = controller.accept_audio(_pcm(100), sample_rate_hz=16_000)

    assert decision.disposition is AudioDisposition.BLOCK
    assert decision.pre_roll == b""
    assert controller.metrics.cloud_audio_ms == 0


def test_turn_identity_is_allocated_when_speech_starts_not_when_final_arrives() -> None:
    controller = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("openai", "provider"),
        shadow_mode=False,
    )
    controller.open(route_mode=VoiceRouteMode.INDEPENDENT)
    idle_identity = controller.identity
    controller.transition(VoiceLifecycleEvent.SOFT_WAKE)
    candidate_identity = controller.identity
    controller.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    identity = controller.identity
    controller.transition(VoiceLifecycleEvent.TURN_SEALED)
    controller.transition(VoiceLifecycleEvent.PROVIDER_FINAL)

    assert controller.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert candidate_identity.turn_id == idle_identity.turn_id + 1
    assert identity.turn_id == candidate_identity.turn_id
    assert controller.identity.turn_id == identity.turn_id
    assert controller.matches(identity) is False


def test_draining_audio_is_isolated_for_the_next_turn_until_old_final() -> None:
    controller = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    controller.open(route_mode=VoiceRouteMode.INDEPENDENT)
    controller.transition(VoiceLifecycleEvent.SOFT_WAKE)
    controller.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    old_turn = controller.identity.turn_id
    controller.transition(VoiceLifecycleEvent.TURN_SEALED)

    decision = controller.accept_audio(_pcm(400), sample_rate_hz=16_000)
    controller.mark_pending_turn_speech()

    assert decision.disposition is AudioDisposition.BUFFER
    assert controller.pending_turn_bytes == len(_pcm(400))
    assert controller.identity.turn_id == old_turn

    controller.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    pending = controller.begin_pending_turn()

    assert controller.snapshot.state is VoiceLifecycleState.ACTIVE
    assert controller.identity.turn_id == old_turn + 1
    assert pending == _pcm(400)
    assert controller.pending_turn_bytes == 0


def test_pending_turn_overflow_discards_entire_candidate() -> None:
    controller = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("openai", "provider"),
        shadow_mode=False,
    )
    controller.open(route_mode=VoiceRouteMode.INDEPENDENT)
    controller.transition(VoiceLifecycleEvent.SOFT_WAKE)
    controller.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    controller.transition(VoiceLifecycleEvent.TURN_SEALED)

    controller.mark_pending_turn_speech()
    decision = controller.accept_audio(_pcm(9_000), sample_rate_hz=16_000)

    assert decision.disposition is AudioDisposition.BLOCK
    assert decision.backpressure is True
    assert controller.pending_turn_bytes == 0
    assert controller.has_pending_turn is False
    assert controller.metrics.buffer_overflow_count == 1


def test_stop_clears_audio_and_invalidates_all_async_identity() -> None:
    controller = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    controller.open(route_mode=VoiceRouteMode.INDEPENDENT)
    controller.accept_audio(_pcm(100), sample_rate_hz=16_000)
    identity = controller.identity

    controller.stop()

    assert controller.snapshot.state is VoiceLifecycleState.OFF
    assert controller.matches(identity) is False
    assert controller.pre_roll_bytes == 0
    assert controller.pending_turn_bytes == 0


def test_detector_failure_fails_open_only_to_continuous_independent_asr() -> None:
    controller = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    controller.open(route_mode=VoiceRouteMode.INDEPENDENT)
    buffered = controller.accept_audio(_pcm(100), sample_rate_hz=16_000)

    controller.enable_independent_asr_fail_open()
    first = controller.accept_audio(_pcm(20), sample_rate_hz=16_000)
    second = controller.accept_audio(_pcm(20), sample_rate_hz=16_000)

    assert buffered.disposition is AudioDisposition.BUFFER
    assert first.disposition is AudioDisposition.FORWARD_WITH_PRE_ROLL
    assert first.pre_roll == _pcm(120)
    assert second.disposition is AudioDisposition.FORWARD
    assert controller.snapshot.route_mode is VoiceRouteMode.INDEPENDENT


def test_game_takeover_suspends_active_turn_and_clears_audio() -> None:
    controller = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    controller.open(route_mode=VoiceRouteMode.INDEPENDENT)
    controller.accept_audio(_pcm(100), sample_rate_hz=16_000)
    controller.transition(VoiceLifecycleEvent.SOFT_WAKE)
    controller.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    old_identity = controller.identity

    controller.transition(VoiceLifecycleEvent.GAME_TAKEOVER)
    blocked = controller.accept_audio(_pcm(20), sample_rate_hz=16_000)

    assert controller.snapshot.state is VoiceLifecycleState.SUSPENDED
    assert controller.pre_roll_bytes == 0
    assert blocked.disposition is AudioDisposition.BLOCK
    assert controller.matches(old_identity) is False

    controller.transition(VoiceLifecycleEvent.GAME_RELEASED)
    assert controller.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
