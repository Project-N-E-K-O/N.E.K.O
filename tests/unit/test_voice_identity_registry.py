from __future__ import annotations

import numpy as np
import pytest

from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.registry import (
    EnrollmentLeaseBusy,
    VoiceIdentityProfileRegistry,
    get_voice_identity_profile_registry,
)


def _profile(revision: int) -> SpeakerProfile:
    return SpeakerProfile(
        np.eye(4, dtype=np.float32)[0],
        profile_revision=revision,
        model_id="test-model",
        model_revision="v1",
        embedding_dimension=4,
    )


def test_registry_owns_one_profile_filter_and_enrollment_lease() -> None:
    registry = VoiceIdentityProfileRegistry()
    profile = _profile(2)

    with pytest.raises(RuntimeError):
        registry.set_filter_enabled(True)
    with pytest.raises(TypeError):
        registry.set_filter_enabled(1)
    with pytest.raises(ValueError):
        registry.begin_enrollment("")

    registry.install_profile(profile)
    snapshot = registry.snapshot_profile()
    assert snapshot is not None
    assert snapshot.profile_revision == 2
    assert registry.next_profile_revision() == 3
    registry.set_filter_enabled(True)
    assert registry.filter_enabled is True

    registry.begin_enrollment("session-a")
    assert registry.enrollment_owned_by("session-a") is True
    assert registry.end_enrollment("other") is False
    with pytest.raises(EnrollmentLeaseBusy):
        registry.begin_enrollment("session-b")
    assert registry.end_enrollment("session-a") is True

    registry.clear_profile()
    assert registry.profile_revision is None
    assert registry.next_profile_revision() == 3
    assert registry.filter_enabled is False
    registry.close()
    profile.close()
    snapshot.close()


def test_global_registry_accessor_is_stable() -> None:
    assert (
        get_voice_identity_profile_registry()
        is get_voice_identity_profile_registry()
    )
