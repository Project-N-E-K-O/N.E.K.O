from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import numpy as np
import pytest

from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
)
from main_logic.asr_client.speaker_shadow.campplus import CAMPPLUS_EMBEDDING_DIM
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.voice_identity_service import (
    session_activation_factory as factory_module,
)
from main_logic.voice_identity_service.activation_scoring import ActivationScoreStatus
from main_logic.voice_identity_service.session_activation_factory import (
    OwnerVoiceSessionActivationFactory,
)
from main_logic.voice_input.activation import (
    ActivationGeneration,
    ActivationState,
    AudioFrame,
    OutputCommit,
)


def _profile(
    *,
    generation: str = "profile-1",
    model_id: str = CAMPPLUS_MODEL_ID,
) -> SpeakerProfile:
    identity = SpeakerModelIdentity(
        model_id,
        CAMPPLUS_MODEL_REVISION,
        CAMPPLUS_EMBEDDING_DIM,
    )
    reference = SpeakerReference(
        identity,
        np.arange(1, CAMPPLUS_EMBEDDING_DIM + 1, dtype=np.float32),
    )
    try:
        return SpeakerProfile(generation, reference)
    finally:
        reference.close()


def _generation(*, route: int = 1) -> ActivationGeneration:
    return ActivationGeneration(
        session_id="session-a",
        microphone=2,
        route=route,
        profile=3,
        permission=4,
        input_owner="core_chat",
    )


def _frame(generation: ActivationGeneration) -> AudioFrame:
    return AudioFrame(
        sequence=0,
        sample_start=0,
        sample_end=1_600,
        captured_at=0.0,
        sample_rate=16_000,
        pcm=b"\x01\x00" * 1_600,
        generation=generation,
        context={"source": "factory-test"},
    )


class _Scorer:
    instances: list[_Scorer] = []

    def __init__(
        self,
        profile: SpeakerProfile,
        *,
        scorer_generation: int,
    ) -> None:
        self.profile_generation = profile.generation
        self.scorer_generation = scorer_generation
        self.prepare_calls = 0
        self.closed = False
        self.instances.append(self)

    async def prepare(self) -> ActivationScoreStatus:
        self.prepare_calls += 1
        return ActivationScoreStatus.READY

    async def score(self, *args: object, **kwargs: object):
        raise AssertionError("disabled runtime must not score")

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_scorer(monkeypatch: pytest.MonkeyPatch):
    _Scorer.instances = []
    monkeypatch.setattr(factory_module, "CampPlusActivationScorer", _Scorer)
    return _Scorer


@pytest.mark.asyncio
async def test_factory_creates_repeatable_independent_runtimes(
    fake_scorer,
) -> None:
    profile = _profile()
    factory = OwnerVoiceSessionActivationFactory(
        object(),
        profile,
        activation_generation="activation-1",
        enforce=True,
    )
    first = factory.create(_generation(route=1), AsyncMock())
    second = factory.create(_generation(route=2), AsyncMock())

    assert first is not second
    assert first.generation == _generation(route=1)
    assert second.generation == _generation(route=2)
    assert [scorer.scorer_generation for scorer in fake_scorer.instances] == [1, 2]
    assert [scorer.profile_generation for scorer in fake_scorer.instances] == [
        "profile-1",
        "profile-1",
    ]

    await first.close()
    assert fake_scorer.instances[0].closed is True
    assert fake_scorer.instances[1].closed is False
    await second.close()
    factory.close()
    profile.close()


def test_factory_owns_profile_clone_and_close_wipes_only_its_copy(
    fake_scorer,
) -> None:
    del fake_scorer
    profile = _profile()
    source_embedding = profile._reference._embedding
    factory = OwnerVoiceSessionActivationFactory(
        object(),
        profile,
        activation_generation="activation-2",
        enforce=True,
    )
    factory_profile = factory._profile
    factory_embedding = factory_profile._reference._embedding

    profile.close()
    assert profile.closed is True
    assert not np.any(source_embedding)
    assert factory_profile.closed is False
    assert np.any(factory_embedding)

    runtime = factory.create(_generation(), AsyncMock())
    factory.close()
    factory.close()
    assert factory_profile.closed is True
    assert not np.any(factory_embedding)
    assert runtime._scorer.closed is False


@pytest.mark.asyncio
async def test_enforce_false_bypasses_without_preparing_or_scoring(
    fake_scorer,
) -> None:
    profile = _profile()
    output = AsyncMock(return_value=OutputCommit.TRANSPORT_WRITTEN)
    factory = OwnerVoiceSessionActivationFactory(
        object(),
        profile,
        activation_generation="activation-shadow",
        enforce=False,
    )
    runtime = factory.create(_generation(), output)

    assert runtime.state is ActivationState.DISABLED
    assert (await runtime.prepare()).state is ActivationState.DISABLED
    decision = await runtime.feed(_frame(_generation()), voice_activity=False)
    assert decision.state is ActivationState.DISABLED
    for _ in range(10):
        await asyncio.sleep(0)
        if output.await_count:
            break

    output.assert_awaited_once()
    sent_frame = output.await_args.args[0]
    assert sent_frame.context == {"source": "factory-test"}
    assert fake_scorer.instances[0].prepare_calls == 0
    await runtime.close()
    assert fake_scorer.instances[0].closed is True
    factory.close()
    profile.close()


def test_incompatible_profile_rejects_create_and_closes_temporary_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(model_id="incompatible-model")
    factory = OwnerVoiceSessionActivationFactory(
        object(),
        profile,
        activation_generation="activation-incompatible",
        enforce=True,
    )
    captured: list[SpeakerProfile] = []
    original_copy = SpeakerProfile.__copy__

    def capture_copy(owner: SpeakerProfile) -> SpeakerProfile:
        clone = original_copy(owner)
        captured.append(clone)
        return clone

    monkeypatch.setattr(SpeakerProfile, "__copy__", capture_copy)

    with pytest.raises(ValueError, match=r"does not match CAM\+\+"):
        factory.create(_generation(), AsyncMock())

    assert len(captured) == 1
    temporary = captured[0]
    temporary_embedding = temporary._reference._embedding
    assert temporary.closed is True
    assert not np.any(temporary_embedding)
    factory.close()
    profile.close()


def test_closed_factory_and_closed_source_profile_reject_creation(
    fake_scorer,
) -> None:
    profile = _profile()
    factory = OwnerVoiceSessionActivationFactory(
        object(),
        profile,
        activation_generation="activation-closed",
        enforce=True,
    )
    factory.close()

    with pytest.raises(RuntimeError, match="factory is closed"):
        factory.create(_generation(), AsyncMock())
    assert fake_scorer.instances == []

    profile.close()
    with pytest.raises(RuntimeError, match="speaker profile is closed"):
        OwnerVoiceSessionActivationFactory(
            object(),
            profile,
            activation_generation="activation-closed-source",
            enforce=True,
        )
