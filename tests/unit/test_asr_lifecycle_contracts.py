from __future__ import annotations

import pytest

from main_logic.asr_client.lifecycle_contracts import (
    FinalKey,
    VoiceIngressToken,
    VoiceLifecycleConfig,
    VoiceLifecycleEvent,
    VoiceLifecycleState,
    VoiceRouteMode,
    VoiceTransportToken,
    VoiceTurnToken,
    next_lifecycle_state,
)


def test_default_config_matches_phase3_contract() -> None:
    config = VoiceLifecycleConfig()

    assert config.pre_roll_ms == 700
    assert config.confirm_speech_ms == 240
    assert config.candidate_pause_ms == 320
    assert config.trailing_audio_ms == 400
    assert config.pending_audio_ms == 8_000
    assert config.smart_turn_warm_ms == 60_000


@pytest.mark.parametrize(
    ("state", "event", "expected"),
    [
        (VoiceLifecycleState.OFF, VoiceLifecycleEvent.MIC_OPENED, VoiceLifecycleState.LOCAL_LISTEN),
        (VoiceLifecycleState.LOCAL_LISTEN, VoiceLifecycleEvent.SOFT_WAKE, VoiceLifecycleState.PREWARMING),
        (VoiceLifecycleState.PREWARMING, VoiceLifecycleEvent.SPEECH_CONFIRMED, VoiceLifecycleState.ACTIVE),
        (VoiceLifecycleState.ACTIVE, VoiceLifecycleEvent.TURN_SEALED, VoiceLifecycleState.DRAINING),
        (VoiceLifecycleState.DRAINING, VoiceLifecycleEvent.PROVIDER_FINAL, VoiceLifecycleState.WARM_IDLE),
        (VoiceLifecycleState.WARM_IDLE, VoiceLifecycleEvent.WARM_EXPIRED, VoiceLifecycleState.DEEP_SLEEP),
        (VoiceLifecycleState.DEEP_SLEEP, VoiceLifecycleEvent.SOFT_WAKE, VoiceLifecycleState.PREWARMING),
        (VoiceLifecycleState.LOCAL_LISTEN, VoiceLifecycleEvent.GAME_TAKEOVER, VoiceLifecycleState.SUSPENDED),
        (VoiceLifecycleState.SUSPENDED, VoiceLifecycleEvent.GAME_RELEASED, VoiceLifecycleState.LOCAL_LISTEN),
    ],
)
def test_legal_state_transitions(state, event, expected) -> None:
    assert next_lifecycle_state(state, event) is expected


def test_stop_wins_from_every_live_state() -> None:
    for state in VoiceLifecycleState:
        if state is VoiceLifecycleState.OFF:
            continue
        assert next_lifecycle_state(state, VoiceLifecycleEvent.STOPPED) is VoiceLifecycleState.OFF


def test_invalid_transition_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="VOICE_LIFECYCLE_INVALID_TRANSITION"):
        next_lifecycle_state(
            VoiceLifecycleState.OFF,
            VoiceLifecycleEvent.PROVIDER_FINAL,
        )


def test_route_mode_has_no_native_or_omni_value() -> None:
    assert {mode.value for mode in VoiceRouteMode} == {"independent", "blocked"}


def test_logical_final_key_does_not_depend_on_transport_generation() -> None:
    ingress = VoiceIngressToken(1, "socket", 2, 3, 4)
    turn = VoiceTurnToken(ingress, 5)

    first = VoiceTransportToken(turn, 6)
    retry = VoiceTransportToken(turn, 7)

    assert FinalKey.from_turn(first.turn) == FinalKey.from_turn(retry.turn)
