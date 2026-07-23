from __future__ import annotations

import asyncio
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


class _ControlledCloseVerifier(_Verifier):
    def __init__(self) -> None:
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()

    async def close(self) -> None:
        self.close_started.set()

    async def wait_closed(self) -> None:
        await self.close_release.wait()


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


async def test_profile_validator_rejects_before_replacing_active_revision() -> None:
    def validate(profile: SpeakerProfile) -> None:
        if profile.model_id != "accepted-model":
            raise ValueError("speaker profile model_id mismatch")

    session = VoiceIdentitySession(
        runtime_builder=lambda _profile, _callback: _Verifier(),
        profile_validator=validate,
    )
    accepted = SpeakerProfile(
        np.eye(4, dtype=np.float32)[0],
        profile_revision=1,
        model_id="accepted-model",
        model_revision="v1",
        embedding_dimension=4,
    )
    rejected = SpeakerProfile(
        np.eye(4, dtype=np.float32)[1],
        profile_revision=2,
        model_id="wrong-model",
        model_revision="v1",
        embedding_dimension=4,
    )

    await session.set_profile(accepted)
    with pytest.raises(ValueError, match="model_id"):
        await session.set_profile(rejected)

    assert session.profile_revision == 1
    await session.close()


async def test_profile_update_waits_for_old_runtime_before_exposing_new_revision() -> (
    None
):
    created: list[_ControlledCloseVerifier] = []

    def build(_profile_snapshot, _callback):
        verifier = _ControlledCloseVerifier()
        created.append(verifier)
        return verifier

    session = VoiceIdentitySession(runtime_builder=build)
    await session.set_profile(_profile(1))
    first = session.create_runtime()
    assert first is created[0]

    update = asyncio.create_task(session.set_profile(_profile(2)))
    await asyncio.wait_for(created[0].close_started.wait(), 1)

    assert update.done() is False
    assert session.profile_revision is None
    assert session.create_runtime() is None

    created[0].close_release.set()
    await asyncio.wait_for(update, 1)

    assert session.profile_revision == 2
    await session.close()


async def test_profile_updates_are_serialized_while_old_runtime_closes() -> None:
    created: list[_ControlledCloseVerifier] = []

    def build(_profile_snapshot, _callback):
        verifier = _ControlledCloseVerifier()
        created.append(verifier)
        return verifier

    session = VoiceIdentitySession(runtime_builder=build)
    await session.set_profile(_profile(1))
    session.create_runtime()

    second_update = asyncio.create_task(session.set_profile(_profile(2)))
    await asyncio.wait_for(created[0].close_started.wait(), 1)
    third_update = asyncio.create_task(session.set_profile(_profile(3)))
    await asyncio.sleep(0)

    assert second_update.done() is False
    assert third_update.done() is False
    assert session.profile_revision is None

    created[0].close_release.set()
    await asyncio.wait_for(asyncio.gather(second_update, third_update), 1)

    assert session.profile_revision == 3
    await session.close()


async def test_profile_update_timeout_stays_disabled() -> None:
    created: list[_ControlledCloseVerifier] = []

    def build(_profile_snapshot, _callback):
        verifier = _ControlledCloseVerifier()
        created.append(verifier)
        return verifier

    session = VoiceIdentitySession(
        runtime_builder=build,
        runtime_close_timeout_seconds=0.01,
    )
    await session.set_profile(_profile(1))
    session.create_runtime()

    with pytest.raises(RuntimeError, match="did not close"):
        await session.set_profile(_profile(2))

    assert session.profile_revision is None
    assert session.create_runtime() is None
    with pytest.raises(RuntimeError, match="activation is disabled"):
        await session.set_profile(_profile(3))

    created[0].close_release.set()
    await session.close()
