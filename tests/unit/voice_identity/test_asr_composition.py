from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from main_logic.asr_client.endpointing.detector import DetectorCandidateKey
from main_logic.asr_client.runtime import AsrRuntimeCallbacks, IndependentAsrRuntime
from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
)
from main_logic.asr_client.speaker_shadow.campplus import (
    CAMPPLUS_EMBEDDING_DIM,
    CampPlusBackendFactory,
)
from main_logic.asr_client.speaker_shadow.contracts import (
    SpeakerShadowCandidateKey,
    SpeakerShadowObservation,
)
from main_logic.voice_identity.asr_composition import (
    OwnerVoiceAsrCompositionFactory,
)
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.policy import OwnerVoiceBetaPolicy
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.reference import SpeakerReference


def _runtime() -> IndependentAsrRuntime:
    return IndependentAsrRuntime(
        AsrRuntimeCallbacks(
            display_name=lambda: "test",
            on_prepare_turn=AsyncMock(return_value=True),
            on_partial=AsyncMock(),
            on_final=AsyncMock(),
            on_turn_abandoned=AsyncMock(),
            on_failure=AsyncMock(),
            on_status=AsyncMock(),
            on_lifecycle=AsyncMock(),
        )
    )


def _profile(
    generation: str = "profile-7",
    *,
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


def _fake_backend_builder(reference: SpeakerReference):
    assert reference.model_identity.model_id == CAMPPLUS_MODEL_ID
    return MagicMock(name="backend_factory")


def test_factory_captures_owned_profile_and_exact_beta_checkpoints() -> None:
    runtime = _runtime()
    profile = _profile()
    factory = OwnerVoiceAsrCompositionFactory(
        runtime,
        profile,
        backend_factory_builder=_fake_backend_builder,
    )
    profile.close()

    observer = factory()

    assert observer.enabled is True
    assert observer._config.minimum_audio_ms == 1_500
    assert observer._config.maximum_audio_ms == 3_000
    assert observer._config.observation_checkpoints_ms == (1_500, 3_000)
    assert observer._worker_task is None
    assert observer._callback_task is None


def test_default_backend_builder_copies_profile_into_campplus_factory() -> None:
    runtime = _runtime()
    profile = _profile()
    factory = OwnerVoiceAsrCompositionFactory(runtime, profile)

    observer = factory()

    assert isinstance(observer._backend_factory, CampPlusBackendFactory)
    profile.close()
    factory.close()


def test_default_backend_rejects_wrong_model_identity_fail_open_at_seam() -> None:
    runtime = _runtime()
    profile = _profile(model_id="other-model")
    factory = OwnerVoiceAsrCompositionFactory(runtime, profile)

    with pytest.raises(ValueError, match=r"does not match CAM\+\+"):
        factory()

    profile.close()
    factory.close()


async def test_two_low_observations_build_detector_authoritative_rejection() -> None:
    runtime = _runtime()
    profile = _profile()
    candidate = DetectorCandidateKey(8, 12)
    detector = SimpleNamespace(
        correlate_speaker_shadow_candidate=AsyncMock(return_value=candidate)
    )
    runtime._asr_detector = detector
    runtime._asr_lifecycle = SimpleNamespace(
        snapshot=SimpleNamespace(transport_generation=4, turn_id=6)
    )
    runtime._asr_session_epoch = 3
    runtime._asr_audio_generation = 5
    runtime._reject_candidate = AsyncMock()
    factory = OwnerVoiceAsrCompositionFactory(
        runtime,
        profile,
        backend_factory_builder=_fake_backend_builder,
    )
    observer = factory()
    callback = observer._on_observation
    shadow_candidate = SpeakerShadowCandidateKey(8, 2, "provider_candidate")

    await callback(
        SpeakerShadowObservation(
            candidate=shadow_candidate,
            similarity=0.39,
            would_block=((0.40, True),),
            audio_ms=1_500,
            checkpoint_ms=1_500,
        )
    )
    runtime._reject_candidate.assert_not_awaited()

    await callback(
        SpeakerShadowObservation(
            candidate=shadow_candidate,
            similarity=0.38,
            would_block=((0.40, True),),
            audio_ms=3_000,
            checkpoint_ms=3_000,
        )
    )

    runtime._reject_candidate.assert_awaited_once()
    request = runtime._reject_candidate.await_args.args[0]
    assert request.session_epoch == 3
    assert request.audio_generation == 5
    assert request.transport_generation == 4
    assert request.turn_id == 6
    assert request.candidate is candidate
    assert request.profile_generation == "profile-7"
    assert request.filter_generation == OwnerVoiceBetaPolicy.VERSION
    assert runtime._reject_candidate.await_args.kwargs == {
        "active_profile_generation": "profile-7",
        "active_filter_generation": OwnerVoiceBetaPolicy.VERSION,
    }
    profile.close()
    factory.close()


async def test_stale_shadow_correlation_and_callback_failure_are_fail_open() -> None:
    runtime = _runtime()
    profile = _profile()
    detector = SimpleNamespace(
        correlate_speaker_shadow_candidate=AsyncMock(return_value=None)
    )
    runtime._asr_detector = detector
    runtime._asr_lifecycle = SimpleNamespace(
        snapshot=SimpleNamespace(transport_generation=1, turn_id=1)
    )
    runtime._reject_candidate = AsyncMock()
    factory = OwnerVoiceAsrCompositionFactory(
        runtime,
        profile,
        backend_factory_builder=_fake_backend_builder,
    )
    callback = factory()._on_observation
    observation = SpeakerShadowObservation(
        candidate=SpeakerShadowCandidateKey(0, 0, "provider_candidate"),
        similarity=0.1,
        would_block=((0.40, True),),
        audio_ms=1_500,
        checkpoint_ms=1_500,
    )

    await callback(observation)
    detector.correlate_speaker_shadow_candidate.side_effect = RuntimeError("race")
    await callback(observation)

    runtime._reject_candidate.assert_not_awaited()
    profile.close()
    factory.close()


async def test_rejection_transaction_survives_observation_callback_cancellation() -> (
    None
):
    runtime = _runtime()
    profile = _profile()
    candidate = DetectorCandidateKey(8, 12)
    runtime._asr_detector = SimpleNamespace(
        correlate_speaker_shadow_candidate=AsyncMock(return_value=candidate)
    )
    runtime._asr_lifecycle = SimpleNamespace(
        snapshot=SimpleNamespace(transport_generation=4, turn_id=6)
    )
    transaction_started = asyncio.Event()
    transaction_release = asyncio.Event()
    transaction_completed = asyncio.Event()

    async def reject_candidate(*_args, **_kwargs) -> None:
        transaction_started.set()
        await transaction_release.wait()
        transaction_completed.set()

    runtime._reject_candidate = AsyncMock(side_effect=reject_candidate)
    factory = OwnerVoiceAsrCompositionFactory(
        runtime,
        profile,
        backend_factory_builder=_fake_backend_builder,
    )
    callback = factory()._on_observation
    shadow_candidate = SpeakerShadowCandidateKey(8, 2, "provider_candidate")

    try:
        await callback(
            SpeakerShadowObservation(
                candidate=shadow_candidate,
                similarity=0.39,
                would_block=((0.40, True),),
                audio_ms=1_500,
                checkpoint_ms=1_500,
            )
        )
        callback_task = asyncio.create_task(
            callback(
                SpeakerShadowObservation(
                    candidate=shadow_candidate,
                    similarity=0.38,
                    would_block=((0.40, True),),
                    audio_ms=3_000,
                    checkpoint_ms=3_000,
                )
            )
        )
        await asyncio.wait_for(transaction_started.wait(), 1)

        callback_task.cancel()
        await asyncio.sleep(0)
        transaction_release.set()
        with pytest.raises(asyncio.CancelledError):
            await callback_task
        await asyncio.wait_for(transaction_completed.wait(), 1)
    finally:
        transaction_release.set()
        profile.close()
        factory.close()


def test_closed_factory_cannot_capture_a_later_session() -> None:
    runtime = _runtime()
    profile = _profile()
    factory = OwnerVoiceAsrCompositionFactory(
        runtime,
        profile,
        backend_factory_builder=_fake_backend_builder,
    )
    factory.close()

    with pytest.raises(RuntimeError, match="factory is closed"):
        factory()

    profile.close()
