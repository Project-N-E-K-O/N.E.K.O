from __future__ import annotations

import pytest

from main_logic.voice_identity_service.state import (
    VoiceIdentityEffectiveReason,
    VoiceIdentityState,
)


@pytest.mark.unit
def test_effective_state_has_stable_json_representation() -> None:
    state = VoiceIdentityState(
        requested_enabled=True,
        effective_enabled=True,
        effective_reason=VoiceIdentityEffectiveReason.READY,
        has_profile=True,
    )

    assert state.as_dict() == {
        "requested_enabled": True,
        "effective_enabled": True,
        "effective_reason": "ready",
        "has_profile": True,
    }


@pytest.mark.unit
def test_degraded_state_preserves_user_request() -> None:
    state = VoiceIdentityState(
        requested_enabled=True,
        effective_enabled=False,
        effective_reason=VoiceIdentityEffectiveReason.MODEL_UNAVAILABLE,
        has_profile=True,
    )

    assert state.requested_enabled
    assert not state.effective_enabled


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "requested_enabled": False,
            "effective_enabled": True,
            "effective_reason": VoiceIdentityEffectiveReason.READY,
            "has_profile": True,
        },
        {
            "requested_enabled": True,
            "effective_enabled": True,
            "effective_reason": VoiceIdentityEffectiveReason.READY,
            "has_profile": False,
        },
        {
            "requested_enabled": True,
            "effective_enabled": False,
            "effective_reason": VoiceIdentityEffectiveReason.READY,
            "has_profile": True,
        },
    ],
)
def test_state_rejects_impossible_effective_combinations(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        VoiceIdentityState(**kwargs)  # type: ignore[arg-type]


@pytest.mark.unit
def test_state_requires_exact_public_types() -> None:
    with pytest.raises(TypeError, match="requested_enabled"):
        VoiceIdentityState(
            requested_enabled=1,  # type: ignore[arg-type]
            effective_enabled=False,
            effective_reason=VoiceIdentityEffectiveReason.DISABLED,
            has_profile=False,
        )

    with pytest.raises(TypeError, match="effective_reason"):
        VoiceIdentityState(
            requested_enabled=False,
            effective_enabled=False,
            effective_reason="disabled",  # type: ignore[arg-type]
            has_profile=False,
        )
