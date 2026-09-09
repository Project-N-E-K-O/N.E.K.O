"""Bind one Owner profile activation to one independent-ASR evidence sink."""

from __future__ import annotations

import copy
from dataclasses import replace
import threading
import weakref
from typing import Protocol

from main_logic.asr_client.speaker_verifier_contracts import (
    SpeakerVerifierAuthority,
    SpeakerVerifierInstallIdentity,
    SpeakerVerifierHealthEvent,
)

from main_logic.asr_client.admission.contracts import (
    CaptureClosed,
    SpeakerCheckpointKind,
    SpeakerHigh,
    SpeakerLow,
    SpeakerUnavailable,
    SpeakerUnavailableReason,
)
from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
)
from main_logic.asr_client.speaker_shadow.campplus import (
    CAMPPLUS_EMBEDDING_DIM,
    CAMPPLUS_EXECUTABLE_MINIMUM_SAMPLES,
    CampPlusBackendFactory,
)
from main_logic.asr_client.speaker_shadow.contracts import (
    MAX_SPEAKER_SHADOW_CANDIDATE_AUDIO_MS,
    SpeakerShadowCompletion,
    SpeakerShadowConfig,
    SpeakerShadowEvidenceEvent,
    SpeakerShadowObservation,
)
from main_logic.asr_client.speaker_shadow.runtime import SpeakerShadowRuntime
from main_logic.asr_client.speaker_shadow.shared_host import (
    HostGenerationReceipt,
    SharedHostIdentityError,
    SharedSpeakerScoringHostManager,
    SpeakerScoringLane,
    SpeakerScoringMode,
)
from main_logic.asr_client.speaker_shadow.diagnostics import (
    SpeakerScoreDiagnosticConfiguration,
    SpeakerShadowDiagnostic,
)
from main_logic.asr_client.speaker_diagnostics import diagnostic_value_ref
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile
from .calibration import (
    CalibrationOutcome,
    CalibrationPackage,
    CalibrationProtocol,
    RegisteredCalibration,
)
from .policy import OwnerVoiceClassification, OwnerVoicePolicy
from .pvad_policy import (
    FIRST_CHECKPOINT_SAMPLES,
    MINIMUM_SHORT_SAMPLES,
    PVAD_FRAME_SAMPLES,
    PVAD_OBSERVATION_SCOPES,
    PvadEvidenceKind,
    PvadMode,
    classify_pvad_observation,
)
from .pvad_backend import (
    PvadBackendFactory,
    PvadSpeakerBackend,
    is_compatible_activity_reference,
)
from .shared_campplus_host import SharedCampPlusCompositionBinding


class _OwnerVoiceEvidenceSink(Protocol):
    """One-way bridge; it has no transcript or reservation operations."""

    def _accept_speaker_diagnostic(
        self,
        event: SpeakerShadowDiagnostic,
        *,
        activation_generation: str,
        source: object,
    ) -> None: ...

    def _accept_speaker_evidence_fact(
        self,
        fact: SpeakerLow | SpeakerHigh | SpeakerUnavailable,
        *,
        activation_generation: str,
        enforce: bool,
    ) -> bool: ...

    def _close_speaker_evidence(
        self,
        closed: CaptureClosed,
        *,
        activation_generation: str,
        enforce: bool,
        evidence_complete: bool,
    ) -> bool: ...

    def _mark_speaker_evidence_backend_degraded(
        self,
        *,
        activation_generation: str,
    ) -> None: ...

    def _mark_speaker_evidence_backend_healthy(
        self,
        *,
        activation_generation: str,
    ) -> None: ...


class OwnerVoiceAsrCompositionFactory:
    """Create repeatable observers for one activation generation."""

    def __init__(
        self,
        runtime: _OwnerVoiceEvidenceSink,
        profile: SpeakerProfile,
        *,
        activation_generation: str,
        enforce: bool,
        authority: SpeakerVerifierAuthority | None = None,
        installation_identity: SpeakerVerifierInstallIdentity | None = None,
        calibration_package: CalibrationPackage | None = None,
        registered_calibration: RegisteredCalibration | None = None,
        runtime_calibration_protocol: CalibrationProtocol | None = None,
        shared_host_binding: SharedCampPlusCompositionBinding | None = None,
        shared_scoring_manager: SharedSpeakerScoringHostManager | None = None,
        shared_host_generation: HostGenerationReceipt | None = None,
        pvad_mode: PvadMode = PvadMode.OBSERVE,
    ) -> None:
        required_methods = (
            "_accept_speaker_evidence_fact",
            "_close_speaker_evidence",
            "_mark_speaker_evidence_backend_degraded",
            "_mark_speaker_evidence_backend_healthy",
        )
        if any(not callable(getattr(runtime, name, None)) for name in required_methods):
            raise TypeError("runtime must provide the Owner voice evidence sink")
        if type(profile) is not SpeakerProfile:
            raise TypeError("profile must be SpeakerProfile")
        if type(activation_generation) is not str or not activation_generation.strip():
            raise ValueError("activation_generation must be a non-empty string")
        if type(enforce) is not bool:
            raise TypeError("enforce must be bool")
        if type(pvad_mode) is not PvadMode:
            raise TypeError("pvad_mode must be PvadMode")
        if pvad_mode is PvadMode.ENFORCE:
            # No validated negative-evidence rule or sample-exact authority
            # transport has shipped. A profile/download never opens this gate.
            raise ValueError("pvad_enforcement_not_validated")
        if (
            calibration_package is not None
            and type(calibration_package) is not CalibrationPackage
        ):
            raise TypeError("calibration_package must be CalibrationPackage or None")
        if registered_calibration is not None:
            if type(registered_calibration) is not RegisteredCalibration:
                raise TypeError(
                    "registered_calibration must be RegisteredCalibration or None"
                )
            if (
                calibration_package is None
                or registered_calibration.package != calibration_package
            ):
                raise ValueError(
                    "registered_calibration must match calibration_package"
                )
        if (
            runtime_calibration_protocol is not None
            and type(runtime_calibration_protocol) is not CalibrationProtocol
        ):
            raise TypeError(
                "runtime_calibration_protocol must be CalibrationProtocol or None"
            )
        if (calibration_package is None) != (runtime_calibration_protocol is None):
            raise ValueError(
                "calibration_package and runtime_calibration_protocol must be provided together"
            )
        if shared_host_binding is not None:
            if type(shared_host_binding) is not SharedCampPlusCompositionBinding:
                raise TypeError(
                    "shared_host_binding must be "
                    "SharedCampPlusCompositionBinding or None"
                )
            if shared_scoring_manager is not None or shared_host_generation is not None:
                raise ValueError(
                    "shared_host_binding is mutually exclusive with the legacy "
                    "shared manager/generation pair"
                )
            shared_scoring_manager = shared_host_binding.manager
            shared_host_generation = shared_host_binding.generation
            shared_pvad_observe_enabled = shared_host_binding.pvad_observe_enabled
        else:
            shared_pvad_observe_enabled = False
        if (shared_scoring_manager is None) != (shared_host_generation is None):
            raise ValueError(
                "shared_scoring_manager and shared_host_generation must be provided together"
            )
        if shared_scoring_manager is not None:
            if not isinstance(shared_scoring_manager, SharedSpeakerScoringHostManager):
                raise TypeError(
                    "shared_scoring_manager must be SharedSpeakerScoringHostManager or None"
                )
            if type(shared_host_generation) is not HostGenerationReceipt:
                raise TypeError(
                    "shared_host_generation must be HostGenerationReceipt or None"
                )
            assert shared_host_generation is not None
            self._validate_shared_generation(
                shared_scoring_manager,
                shared_host_generation,
                profile_generation=profile.generation,
            )
        self._runtime = runtime
        self._profile = copy.copy(profile)
        self._activation_generation = activation_generation
        self._enforce = enforce
        self._pvad_mode = pvad_mode
        self._authority = authority
        self._installation_identity = installation_identity
        self._calibration_package = calibration_package
        self._registered_calibration = registered_calibration
        self._runtime_calibration_protocol = runtime_calibration_protocol
        self._shared_scoring_manager = shared_scoring_manager
        self._shared_host_generation = shared_host_generation
        self._shared_pvad_observe_enabled = shared_pvad_observe_enabled
        self._lock = threading.Lock()
        self._closed = False
        self._diagnostics = {
            "observation_count": 0,
            "first_checkpoint_count": 0,
            "second_checkpoint_count": 0,
            "low_checkpoint_count": 0,
            "speaker_first_low_count": 0,
            "speaker_second_low_count": 0,
            "speaker_completion_count": 0,
            "speaker_completion_before_first_checkpoint_count": 0,
            "speaker_completion_after_first_checkpoint_count": 0,
            "speaker_completion_stale_count": 0,
            "terminal_short_observation_count": 0,
            "terminal_short_owner_count": 0,
            "terminal_short_nonowner_count": 0,
            "terminal_short_insufficient_count": 0,
            "terminal_short_unavailable_count": 0,
            "terminal_short_recommended_owner_count": 0,
            "terminal_short_recommended_nonowner_count": 0,
            "terminal_short_recommended_uncertain_count": 0,
            "terminal_short_recommended_unsupported_count": 0,
            "terminal_short_recommended_failure_count": 0,
            "pvad_owner_activity_observed_count": 0,
            "pvad_negative_evidence_observed_count": 0,
            "pvad_insufficient_observed_count": 0,
            "pvad_unavailable_observed_count": 0,
        }

    @property
    def activation_generation(self) -> str:
        return self._activation_generation

    def bind_installation(
        self,
        identity: SpeakerVerifierInstallIdentity,
        authority: SpeakerVerifierAuthority,
    ) -> None:
        """Bind a legacy one-shot factory before any observer is constructed."""
        with self._lock:
            if self._closed:
                raise RuntimeError("composition factory is closed")
            if (
                self._installation_identity is not None
                and self._installation_identity != identity
            ):
                raise RuntimeError(
                    "composition factory already belongs to another installation"
                )
            self._installation_identity = identity
            self._authority = authority

    @property
    def enforces_admission(self) -> bool:
        """Whether this activation may suppress independent-ASR output."""

        return self._enforce

    def diagnostics_snapshot(self) -> dict[str, int]:
        """Return aggregate decision counters without biometric material."""

        with self._lock:
            return dict(self._diagnostics)

    def __call__(self) -> SpeakerShadowRuntime:
        with self._lock:
            if self._closed:
                raise RuntimeError("Owner voice composition factory is closed")
            reference = self._profile.clone_reference()
            shared_scoring_manager = self._shared_scoring_manager
            shared_host_generation = self._shared_host_generation
            shared_pvad_observe_enabled = self._shared_pvad_observe_enabled
        embedding = None
        backend_factory = None
        pvad_enabled = bool(
            shared_scoring_manager is not None
            and shared_pvad_observe_enabled
            and self._pvad_mode is PvadMode.OBSERVE
        )
        try:
            expected_identity = SpeakerModelIdentity(
                CAMPPLUS_MODEL_ID,
                CAMPPLUS_MODEL_REVISION,
                CAMPPLUS_EMBEDDING_DIM,
            )
            if reference.model_identity != expected_identity:
                raise ValueError("speaker profile model identity does not match CAM++")
            if shared_scoring_manager is None:
                embedding = reference.copy_embedding()
                backend_factory = (
                    CampPlusBackendFactory(embedding)
                    if self._calibration_package is None
                    else CampPlusBackendFactory(embedding, allow_short_input=True)
                )
                activity_reference = self._profile.clone_activity_reference()
                if activity_reference is not None:
                    activity_embedding = None
                    activity_contract = self._profile.activity_reference_contract
                    try:
                        if (
                            self._pvad_mode is PvadMode.OBSERVE
                            and is_compatible_activity_reference(
                                activity_reference.model_identity,
                                activity_contract,
                            )
                        ):
                            activity_embedding = activity_reference.copy_embedding()
                            backend_factory = PvadBackendFactory(
                                backend_factory, activity_embedding
                            )
                            pvad_enabled = True
                    finally:
                        if activity_embedding is not None:
                            activity_embedding.fill(0)
                        activity_reference.close()
        except BaseException:
            if backend_factory is not None:
                backend_factory.close()
            raise
        finally:
            if embedding is not None:
                embedding.fill(0.0)
            reference.close()

        runtime = self._runtime
        identity = self._installation_identity
        generation = (
            identity.installation_id
            if identity is not None
            else self._activation_generation
        )
        enforce = self._enforce
        source_ref = None

        shadow_config = SpeakerShadowConfig(
            enabled=True,
            similarity_thresholds=(OwnerVoicePolicy.SIMILARITY_THRESHOLD,),
            minimum_audio_ms=OwnerVoicePolicy.FIRST_CHECKPOINT_MS,
            maximum_audio_ms=MAX_SPEAKER_SHADOW_CANDIDATE_AUDIO_MS,
            observation_checkpoints_ms=(
                OwnerVoicePolicy.FIRST_CHECKPOINT_MS,
                OwnerVoicePolicy.SECOND_CHECKPOINT_MS,
            ),
            completion_confirmation_scopes=(("provider_candidate",) if enforce else ()),
            terminal_short_evaluation_scopes=(
                PVAD_OBSERVATION_SCOPES
                if pvad_enabled
                else ("provider_candidate",)
                if self._calibration_package is not None
                else ()
            ),
            terminal_short_minimum_samples=(
                MINIMUM_SHORT_SAMPLES
                if pvad_enabled
                else CAMPPLUS_EXECUTABLE_MINIMUM_SAMPLES
                if self._calibration_package is not None
                else 1
            ),
            pending_observation_gate_scopes=(
                ("provider_candidate",) if enforce else ()
            ),
            backend_prewarm_scopes=(("provider_candidate",) if enforce else ()),
        )
        shared_backend_lease = None
        if shared_scoring_manager is not None:
            assert shared_host_generation is not None
            self._validate_shared_generation(
                shared_scoring_manager,
                shared_host_generation,
                profile_generation=self._profile.generation,
            )
            shared_backend_lease = shared_scoring_manager.lease(
                shared_host_generation,
                lane=SpeakerScoringLane.SHADOW,
                mode=(
                    SpeakerScoringMode.STANDARD
                    if pvad_enabled
                    else SpeakerScoringMode.SHORT_PROBE
                    if self._calibration_package is not None
                    else SpeakerScoringMode.STANDARD
                ),
                timeout_seconds=shadow_config.backend_score_timeout_seconds,
            )

        def on_diagnostic(event: SpeakerShadowDiagnostic) -> None:
            if pvad_enabled and event.score_checkpoint_kind == "terminal_short":
                # Diagnostic coverage is not an authority channel. Preserve the
                # real input count, but show only complete frames as evaluated.
                covered = (
                    event.score_input_sample_count
                    // PVAD_FRAME_SAMPLES
                    * PVAD_FRAME_SAMPLES
                )
                event = replace(
                    event,
                    model_version="firered_pvad_v1",
                    scoring_rule_version="pvad_activity_observe_v1",
                    score_window_end_sample=(
                        event.score_window_start_sample + covered
                        if event.score_window_start_sample is not None
                        else None
                    ),
                    score_duration_ms=covered * 1_000 // 16_000,
                )
            if source_ref is not None:
                runtime._accept_speaker_diagnostic(
                    event,
                    activation_generation=generation,
                    source=source_ref(),
                )

        def on_evidence(event: SpeakerShadowEvidenceEvent) -> None:
            with self._lock:
                factory_closed = self._closed
            permitted = (
                self._authority is None or self._authority.permits_evidence
            ) and (
                identity is None
                or runtime.speaker_verifier_installation_permits_evidence(identity)
            )
            if factory_closed or not permitted:
                if isinstance(event, SpeakerShadowCompletion):
                    with self._lock:
                        self._diagnostics["speaker_completion_stale_count"] += 1
                return

            if isinstance(event, SpeakerShadowCompletion):
                self._record_completion(event)
                through_sequence_no = event.through_sequence_no
                if not event.evidence_complete:
                    through_sequence_no = max(1, through_sequence_no)
                    runtime._accept_speaker_evidence_fact(
                        SpeakerUnavailable(
                            candidate=event.candidate,
                            sequence_no=through_sequence_no,
                            reason=SpeakerUnavailableReason.FAILURE,
                        ),
                        activation_generation=generation,
                        enforce=enforce,
                    )
                runtime._close_speaker_evidence(
                    CaptureClosed(
                        candidate=event.candidate,
                        through_sequence_no=through_sequence_no,
                    ),
                    activation_generation=generation,
                    enforce=enforce,
                    evidence_complete=event.evidence_complete,
                )
                return

            assert isinstance(event, SpeakerShadowObservation)
            checkpoint_kind = self._checkpoint_kind(event)
            with self._lock:
                self._diagnostics["observation_count"] += 1
                if checkpoint_kind is SpeakerCheckpointKind.FIRST:
                    self._diagnostics["first_checkpoint_count"] += 1
                elif checkpoint_kind is SpeakerCheckpointKind.SECOND:
                    self._diagnostics["second_checkpoint_count"] += 1
                elif checkpoint_kind is SpeakerCheckpointKind.TERMINAL_SHORT:
                    self._diagnostics["terminal_short_observation_count"] += 1

            if pvad_enabled and checkpoint_kind is SpeakerCheckpointKind.TERMINAL_SHORT:
                activity = classify_pvad_observation(event)
                with self._lock:
                    self._diagnostics[f"pvad_{activity.kind.value}_observed_count"] += 1
                # Neither observed target activity nor its absence is a formal
                # Owner/nonowner result. The existing route owns degradation,
                # holding and settlement; no pre-wire KEEP/DROP is emitted.
                reason = (
                    SpeakerUnavailableReason.FAILURE
                    if activity.kind is PvadEvidenceKind.UNAVAILABLE
                    else SpeakerUnavailableReason.INSUFFICIENT_EVIDENCE
                )
                if activity.reason == "pvad_unsupported_samples":
                    reason = SpeakerUnavailableReason.UNSUPPORTED
                runtime._accept_speaker_evidence_fact(
                    SpeakerUnavailable(event.candidate, event.sequence_no, reason),
                    activation_generation=generation,
                    enforce=enforce,
                )
                return

            if not event.evidence_available:
                fact: SpeakerLow | SpeakerHigh | SpeakerUnavailable = (
                    SpeakerUnavailable(
                        event.candidate,
                        event.sequence_no,
                        (
                            SpeakerUnavailableReason.UNSUPPORTED
                            if event.unavailable_reason == "unsupported"
                            else SpeakerUnavailableReason.FAILURE
                        ),
                    )
                )
            else:
                result = OwnerVoicePolicy.classify(
                    checkpoint_ms=event.checkpoint_ms,
                    similarity=event.similarity,
                    observation_kind=event.observation_kind,
                    audio_ms=event.audio_ms,
                    calibration_package=self._calibration_package,
                    registered_calibration=self._registered_calibration,
                    runtime_protocol=self._runtime_calibration_protocol,
                    rms=event.rms,
                    peak=event.peak,
                    near_silence=event.near_silence,
                    clipping=event.clipping,
                )
                if (
                    checkpoint_kind is SpeakerCheckpointKind.TERMINAL_SHORT
                    and result.calibration_outcome is not None
                ):
                    outcome_key = {
                        CalibrationOutcome.OWNER: "owner",
                        CalibrationOutcome.NONOWNER: "nonowner",
                        CalibrationOutcome.UNCERTAIN: "uncertain",
                        CalibrationOutcome.UNSUPPORTED: "unsupported",
                        CalibrationOutcome.FAILURE: "failure",
                    }[result.calibration_outcome]
                    with self._lock:
                        self._diagnostics[
                            f"terminal_short_recommended_{outcome_key}_count"
                        ] += 1
                if (
                    result.classification is OwnerVoiceClassification.LOW
                    and checkpoint_kind is not None
                ):
                    fact = SpeakerLow(
                        event.candidate,
                        event.sequence_no,
                        checkpoint_kind,
                    )
                    with self._lock:
                        self._diagnostics["low_checkpoint_count"] += 1
                        if checkpoint_kind is SpeakerCheckpointKind.FIRST:
                            self._diagnostics["speaker_first_low_count"] += 1
                        elif checkpoint_kind in {
                            SpeakerCheckpointKind.SECOND,
                            SpeakerCheckpointKind.COMPLETION_CONFIRMATION,
                        }:
                            self._diagnostics["speaker_second_low_count"] += 1
                        elif checkpoint_kind is SpeakerCheckpointKind.TERMINAL_SHORT:
                            self._diagnostics["terminal_short_nonowner_count"] += 1
                elif (
                    result.classification is OwnerVoiceClassification.HIGH
                    and checkpoint_kind is not None
                ):
                    fact = SpeakerHigh(
                        event.candidate,
                        event.sequence_no,
                        checkpoint_kind,
                        event.audio_ms,
                    )
                    if checkpoint_kind is SpeakerCheckpointKind.TERMINAL_SHORT:
                        with self._lock:
                            self._diagnostics["terminal_short_owner_count"] += 1
                elif result.classification is OwnerVoiceClassification.INSUFFICIENT:
                    fact = SpeakerUnavailable(
                        event.candidate,
                        event.sequence_no,
                        SpeakerUnavailableReason.INSUFFICIENT_EVIDENCE,
                    )
                    with self._lock:
                        self._diagnostics["terminal_short_insufficient_count"] += 1
                else:
                    reason = (
                        SpeakerUnavailableReason.UNSUPPORTED
                        if result.calibration_outcome is CalibrationOutcome.UNSUPPORTED
                        or result.reason
                        in {
                            "protocol_mismatch",
                            "missing_primary_feature",
                            "calibration_protocol_unavailable",
                        }
                        else SpeakerUnavailableReason.FAILURE
                    )
                    fact = SpeakerUnavailable(
                        event.candidate,
                        event.sequence_no,
                        reason,
                    )
                    if checkpoint_kind is SpeakerCheckpointKind.TERMINAL_SHORT:
                        with self._lock:
                            self._diagnostics["terminal_short_unavailable_count"] += 1
            runtime._accept_speaker_evidence_fact(
                fact,
                activation_generation=generation,
                enforce=enforce,
            )

        def on_backend_degraded() -> None:
            runtime._mark_speaker_evidence_backend_degraded(
                activation_generation=generation,
            )

        def on_backend_recovered() -> None:
            runtime._mark_speaker_evidence_backend_healthy(
                activation_generation=generation,
            )

        def on_health_changed(revision: int, causes: frozenset[str]) -> None:
            with self._lock:
                if self._closed:
                    return
            if identity is not None:
                runtime._accept_speaker_verifier_health(
                    SpeakerVerifierHealthEvent(identity, revision, causes)
                )

        try:
            shadow_kwargs = dict(
                backend_factory=backend_factory,
                config=shadow_config,
                on_evidence=on_evidence,
                on_diagnostic=on_diagnostic,
                on_backend_degraded=on_backend_degraded if identity is None else None,
                on_backend_recovered=on_backend_recovered if identity is None else None,
                on_health_changed=on_health_changed if identity is not None else None,
            )
            if shared_backend_lease is not None:
                shadow_kwargs["shared_backend_lease"] = shared_backend_lease
            shadow = SpeakerShadowRuntime(**shadow_kwargs)
            try:
                shadow.bind_score_diagnostic_configuration(
                    SpeakerScoreDiagnosticConfiguration(
                        profile_generation_ref=diagnostic_value_ref(
                            self._profile.generation, namespace="speaker_profile"
                        ),
                        activation_generation_ref=diagnostic_value_ref(
                            self._activation_generation, namespace="speaker_activation"
                        ),
                        installation_ref=diagnostic_value_ref(
                            identity.installation_id if identity is not None else None,
                            namespace="speaker_installation",
                        ),
                        model_version="campplus_v1_0_0",
                        scoring_rule_version="owner_voice_v1",
                    )
                )
            except Exception:
                # Diagnostic identity must never decide whether scoring installs.
                pass
            source_ref = weakref.ref(shadow)
            return shadow
        except BaseException:
            if backend_factory is not None:
                backend_factory.close()
            raise

    @staticmethod
    def _validate_shared_generation(
        manager: SharedSpeakerScoringHostManager,
        generation: HostGenerationReceipt,
        *,
        profile_generation: str,
    ) -> None:
        if generation.identity.profile_generation != profile_generation:
            raise ValueError(
                "shared_host_generation profile does not match speaker profile"
            )
        if not manager.is_generation_current(generation):
            raise SharedHostIdentityError("shared_host_generation is not current")

    @staticmethod
    def _checkpoint_kind(
        observation: SpeakerShadowObservation,
    ) -> SpeakerCheckpointKind | None:
        if observation.observation_kind == "completion_confirmation":
            return SpeakerCheckpointKind.COMPLETION_CONFIRMATION
        if observation.observation_kind == "terminal_short":
            return SpeakerCheckpointKind.TERMINAL_SHORT
        if observation.checkpoint_ms == OwnerVoicePolicy.FIRST_CHECKPOINT_MS:
            return SpeakerCheckpointKind.FIRST
        if observation.checkpoint_ms == OwnerVoicePolicy.SECOND_CHECKPOINT_MS:
            return SpeakerCheckpointKind.SECOND
        return None

    def _record_completion(self, completion: SpeakerShadowCompletion) -> None:
        with self._lock:
            self._diagnostics["speaker_completion_count"] += 1
            if completion.last_checkpoint_ms is None:
                self._diagnostics[
                    "speaker_completion_before_first_checkpoint_count"
                ] += 1
            else:
                self._diagnostics[
                    "speaker_completion_after_first_checkpoint_count"
                ] += 1

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._profile.close()


__all__ = ["OwnerVoiceAsrCompositionFactory"]
