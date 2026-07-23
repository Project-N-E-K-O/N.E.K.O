from __future__ import annotations

from unittest.mock import AsyncMock

import numpy as np
import pytest

from main_logic.voice_identity.contracts import SpeakerShadowObservation
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.runtime import VoiceIdentitySession


pytestmark = pytest.mark.asyncio


class _Verifier:
    def __init__(self) -> None:
        self.close = AsyncMock()

    def submit(self, _pcm16: bytes, *, sample_rate_hz: int, candidate) -> bool:
        return bool(sample_rate_hz and candidate is not None)

    def finish_candidate(self, _candidate) -> bool:
        return True

    async def reset(self) -> None:
        return None

    def snapshot(self) -> dict[str, int]:
        return {}


def _profile(revision: int) -> SpeakerProfile:
    return SpeakerProfile(
        np.eye(4, dtype=np.float32)[revision % 4],
        profile_revision=revision,
        model_id="test-model",
        model_revision="v1",
        embedding_dimension=4,
    )


async def test_session_is_zero_work_without_profile_and_closes_old_revision() -> None:
    created: list[_Verifier] = []
    callbacks = []
    observations: list[SpeakerShadowObservation] = []

    def build(_profile_snapshot, callback):
        verifier = _Verifier()
        created.append(verifier)
        callbacks.append(callback)
        return verifier

    session = VoiceIdentitySession(
        runtime_builder=build,
        on_observation=observations.append,
    )

    assert session.create_runtime() is None
    assert created == []

    await session.set_profile(_profile(1))
    first = session.create_runtime()
    assert first is created[0]
    first_observation = SpeakerShadowObservation((1, 1), 0.8, (), 1_500)
    callbacks[0](first_observation)
    assert observations == [first_observation]

    await session.set_profile(_profile(2))
    created[0].close.assert_awaited_once_with()
    callbacks[0](SpeakerShadowObservation((1, 2), 0.1, (), 1_500))
    assert observations == [first_observation]

    second = session.create_runtime()
    assert second is created[1]
    await session.close()
    created[1].close.assert_awaited_once_with()
    assert session.create_runtime() is None


async def test_profile_revision_must_advance() -> None:
    session = VoiceIdentitySession(runtime_builder=lambda _profile, _callback: _Verifier())
    await session.set_profile(_profile(3))

    with pytest.raises(ValueError, match="advance"):
        await session.set_profile(_profile(3))

    await session.close()
