"""Session-scoped Owner voice composition for independent ASR."""

from __future__ import annotations

import asyncio
import copy
import threading
from collections.abc import Callable

from main_logic.asr_client.candidate_control import CandidateRejectionRequest
from main_logic.asr_client.runtime import IndependentAsrRuntime
from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
)
from main_logic.asr_client.speaker_shadow.campplus import (
    CAMPPLUS_EMBEDDING_DIM,
    CampPlusBackendFactory,
)
from main_logic.asr_client.speaker_shadow.contracts import (
    SpeakerShadowBackendFactory,
    SpeakerShadowConfig,
    SpeakerShadowObservation,
)
from main_logic.asr_client.speaker_shadow.runtime import SpeakerShadowRuntime

from .contracts import SpeakerModelIdentity
from .policy import OwnerVoiceBetaPolicy, OwnerVoicePolicyDecision
from .profile import SpeakerProfile
from .reference import SpeakerReference


_BackendFactoryBuilder = Callable[
    [SpeakerReference],
    SpeakerShadowBackendFactory,
]


def _build_campplus_backend_factory(
    reference: SpeakerReference,
) -> SpeakerShadowBackendFactory:
    expected_identity = SpeakerModelIdentity(
        CAMPPLUS_MODEL_ID,
        CAMPPLUS_MODEL_REVISION,
        CAMPPLUS_EMBEDDING_DIM,
    )
    if reference.model_identity != expected_identity:
        raise ValueError("speaker profile model identity does not match CAM++")
    embedding = reference.copy_embedding()
    try:
        return CampPlusBackendFactory(embedding)
    finally:
        embedding.fill(0.0)


class OwnerVoiceAsrCompositionFactory:
    """Create one fixed-generation Owner voice observer per ASR session.

    The factory owns a clone of the supplied profile. Calling it captures a
    fresh reference and the immutable generation for exactly one ASR session;
    there is deliberately no setter or runtime profile replacement path.
    """

    __slots__ = (
        "_backend_factory_builder",
        "_closed",
        "_lock",
        "_profile",
        "_runtime",
    )

    def __init__(
        self,
        runtime: IndependentAsrRuntime,
        profile: SpeakerProfile,
        *,
        backend_factory_builder: _BackendFactoryBuilder | None = None,
    ) -> None:
        if not isinstance(runtime, IndependentAsrRuntime):
            raise TypeError("runtime must be IndependentAsrRuntime")
        if type(profile) is not SpeakerProfile:
            raise TypeError("profile must be SpeakerProfile")
        self._runtime = runtime
        self._profile = copy.copy(profile)
        self._backend_factory_builder = (
            backend_factory_builder or _build_campplus_backend_factory
        )
        self._lock = threading.Lock()
        self._closed = False

    def __call__(self) -> SpeakerShadowRuntime:
        with self._lock:
            if self._closed:
                raise RuntimeError("Owner voice ASR composition factory is closed")
            profile_generation = self._profile.generation
            reference = self._profile.clone_reference()
        try:
            backend_factory = self._backend_factory_builder(reference)
        finally:
            reference.close()

        policy = OwnerVoiceBetaPolicy()
        runtime = self._runtime

        async def on_observation(observation: SpeakerShadowObservation) -> None:
            try:
                checkpoint_ms = observation.checkpoint_ms
                if checkpoint_ms not in {
                    policy.FIRST_CHECKPOINT_MS,
                    policy.SECOND_CHECKPOINT_MS,
                }:
                    return
                detector = runtime._asr_detector
                lifecycle = runtime._asr_lifecycle
                if detector is None or lifecycle is None:
                    return
                candidate = await detector.correlate_speaker_shadow_candidate(
                    observation.candidate
                )
                if candidate is None:
                    return
                result = policy.observe(
                    detector_epoch=candidate.detector_epoch,
                    candidate_generation=candidate.candidate_generation,
                    profile_generation=profile_generation,
                    active_detector_epoch=candidate.detector_epoch,
                    active_candidate_generation=candidate.candidate_generation,
                    active_profile_generation=profile_generation,
                    checkpoint_ms=checkpoint_ms,
                    similarity=observation.similarity,
                )
                if result.decision != OwnerVoicePolicyDecision.HYPOTHETICAL_REJECT:
                    return
                snapshot = lifecycle.snapshot
                request = CandidateRejectionRequest(
                    session_epoch=runtime._asr_session_epoch,
                    audio_generation=runtime._asr_audio_generation,
                    transport_generation=snapshot.transport_generation,
                    turn_id=snapshot.turn_id,
                    candidate=candidate,
                    profile_generation=profile_generation,
                    filter_generation=policy.VERSION,
                )
                await runtime._reject_candidate(
                    request,
                    active_profile_generation=profile_generation,
                    active_filter_generation=policy.VERSION,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Composition is advisory. Every correlation, policy, or
                # transaction failure preserves the independent ASR route.
                return

        return SpeakerShadowRuntime(
            backend_factory=backend_factory,
            config=SpeakerShadowConfig(
                enabled=True,
                similarity_thresholds=(OwnerVoiceBetaPolicy.SIMILARITY_THRESHOLD,),
                minimum_audio_ms=OwnerVoiceBetaPolicy.FIRST_CHECKPOINT_MS,
                maximum_audio_ms=OwnerVoiceBetaPolicy.SECOND_CHECKPOINT_MS,
                observation_checkpoints_ms=(
                    OwnerVoiceBetaPolicy.FIRST_CHECKPOINT_MS,
                    OwnerVoiceBetaPolicy.SECOND_CHECKPOINT_MS,
                ),
            ),
            on_observation=on_observation,
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._profile.close()
