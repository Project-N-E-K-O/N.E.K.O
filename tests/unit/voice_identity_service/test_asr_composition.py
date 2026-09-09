from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pytest

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
from main_logic.asr_client.speaker_shadow.campplus import CAMPPLUS_EMBEDDING_DIM
from main_logic.asr_client.speaker_shadow.contracts import (
    SpeakerShadowCandidateKey,
    SpeakerShadowCompletion,
    SpeakerShadowObservation,
)
from main_logic.asr_client.speaker_shadow.shared_host import (
    HostGenerationReceipt,
    SharedHostIdentityError,
    SharedSpeakerScoringHostManager,
    SpeakerHostIdentity,
    SpeakerScoringLane,
    SpeakerScoringMode,
)
from main_logic.asr_client.speaker_verifier_contracts import (
    SpeakerVerifierInstallIdentity,
)
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.voice_identity_service.asr_composition import (
    OwnerVoiceAsrCompositionFactory,
)
from main_logic.voice_identity_service.shared_campplus_host import (
    SharedCampPlusCompositionBinding,
)
from main_logic.voice_identity_service.calibration import (
    CALIBRATION_MODEL_FEATURE_ORDER,
    CalibrationPackage,
    CalibrationProtocol,
    RegisteredCalibration,
    calibration_package_artifact_sha256,
    register_calibration_package,
)


@dataclass
class _EvidenceSink:
    events: list[SpeakerLow | SpeakerHigh | SpeakerUnavailable | CaptureClosed] = field(
        default_factory=list
    )
    degraded_generations: list[str] = field(default_factory=list)
    healthy_generations: list[str] = field(default_factory=list)

    def _accept_speaker_evidence_fact(
        self,
        fact: SpeakerLow | SpeakerHigh | SpeakerUnavailable,
        *,
        activation_generation: str,
        enforce: bool,
    ) -> bool:
        assert activation_generation == "activation-1"
        assert enforce is True
        self.events.append(fact)
        return True

    def _close_speaker_evidence(
        self,
        closed: CaptureClosed,
        *,
        activation_generation: str,
        enforce: bool,
        evidence_complete: bool,
    ) -> bool:
        assert activation_generation == "activation-1"
        assert enforce is True
        self.events.append(closed)
        return True

    def _mark_speaker_evidence_backend_degraded(
        self,
        *,
        activation_generation: str,
    ) -> None:
        self.degraded_generations.append(activation_generation)

    def _mark_speaker_evidence_backend_healthy(
        self,
        *,
        activation_generation: str,
    ) -> None:
        self.healthy_generations.append(activation_generation)


@dataclass(slots=True)
class _LeaseToken:
    generation: HostGenerationReceipt
    lane: SpeakerScoringLane
    mode: SpeakerScoringMode
    timeout_seconds: float
    close_count: int = 0

    async def close(self, *, timeout_seconds: float = 1.0) -> bool:
        del timeout_seconds
        self.close_count += 1
        return True


class _SharedManagerStub(SharedSpeakerScoringHostManager):
    def __init__(self, current: HostGenerationReceipt) -> None:
        self._current = current
        self.leases: list[_LeaseToken] = []
        self.close_count = 0

    @property
    def current_generation(self) -> HostGenerationReceipt:
        return self._current

    def is_generation_current(self, receipt: HostGenerationReceipt) -> bool:
        return receipt == self._current

    def lease(
        self,
        generation: HostGenerationReceipt,
        *,
        lane: SpeakerScoringLane,
        mode: SpeakerScoringMode,
        timeout_seconds: float,
    ) -> _LeaseToken:
        if not self.is_generation_current(generation):
            raise SharedHostIdentityError("host_generation_stale")
        lease = _LeaseToken(generation, lane, mode, timeout_seconds)
        self.leases.append(lease)
        return lease

    async def close(self) -> None:
        self.close_count += 1


class _ConstructedShadow:
    def bind_score_diagnostic_configuration(self, configuration: object) -> None:
        self.configuration = configuration


def _shared_generation(
    *,
    profile_generation: str = "profile-generation",
    host_generation: int = 7,
) -> HostGenerationReceipt:
    return HostGenerationReceipt(
        "manager-1",
        host_generation,
        SpeakerHostIdentity(
            profile_generation,
            "campplus-model-generation",
            "speaker-config-generation",
        ),
    )


def _profile(identity: SpeakerModelIdentity | None = None) -> SpeakerProfile:
    identity = identity or SpeakerModelIdentity(
        CAMPPLUS_MODEL_ID, CAMPPLUS_MODEL_REVISION, CAMPPLUS_EMBEDDING_DIM
    )
    embedding = np.ones(identity.embedding_dimension, dtype=np.float32)
    reference = SpeakerReference(identity, embedding)
    embedding.fill(0.0)
    try:
        return SpeakerProfile("profile-generation", reference)
    finally:
        reference.close()


def _calibration_package(*, release_status: str) -> CalibrationPackage:
    size = len(CALIBRATION_MODEL_FEATURE_ORDER)
    return CalibrationPackage(
        schema_version=1,
        package_revision="terminal-short-test",
        protocol=CalibrationProtocol(
            model_id=CAMPPLUS_MODEL_ID,
            model_revision=CAMPPLUS_MODEL_REVISION,
            embedding_dimension=CAMPPLUS_EMBEDDING_DIM,
            preprocessing_contract_id="desktop-pcm16-test",
            preprocessing_revision=1,
            noise_reduction_enabled=True,
        ),
        feature_order=CALIBRATION_MODEL_FEATURE_ORDER,
        center=(0.0,) * size,
        scale=(1.0,) * size,
        weights=(1.0,) + (0.0,) * (size - 1),
        intercept=0.0,
        nonowner_boundary=-0.5,
        owner_boundary=0.5,
        dataset_digest="0" * 64,
        split_group_counts=(1, 1, 1),
        release_status=release_status,
    )


def _allowlisted(package: CalibrationPackage) -> RegisteredCalibration:
    return register_calibration_package(
        package,
        expected_digest=calibration_package_artifact_sha256(package),
    )


@pytest.mark.parametrize(
    ("with_calibration", "expected_mode"),
    (
        (False, SpeakerScoringMode.STANDARD),
        (True, SpeakerScoringMode.SHORT_PROBE),
    ),
    ids=("standard", "short-probe"),
)
def test_shared_host_creates_independent_shadow_leases_for_one_generation(
    monkeypatch: pytest.MonkeyPatch,
    with_calibration: bool,
    expected_mode: SpeakerScoringMode,
) -> None:
    import main_logic.voice_identity_service.asr_composition as module

    constructed: list[dict[str, object]] = []

    def construct_shadow(**kwargs: object) -> _ConstructedShadow:
        constructed.append(dict(kwargs))
        return _ConstructedShadow()

    def reject_legacy_backend(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("shared mode must not construct a private backend")

    monkeypatch.setattr(module, "SpeakerShadowRuntime", construct_shadow)
    monkeypatch.setattr(module, "CampPlusBackendFactory", reject_legacy_backend)
    profile = _profile()
    generation = _shared_generation()
    manager = _SharedManagerStub(generation)
    package = (
        _calibration_package(release_status="candidate") if with_calibration else None
    )
    factory = OwnerVoiceAsrCompositionFactory(
        _EvidenceSink(),
        profile,
        activation_generation="activation-1",
        enforce=True,
        calibration_package=package,
        runtime_calibration_protocol=package.protocol if package is not None else None,
        shared_scoring_manager=manager,
        shared_host_generation=generation,
    )
    try:
        first = factory()
        second = factory()

        assert first is not second
        assert len(manager.leases) == 2
        assert manager.leases[0] is not manager.leases[1]
        assert all(lease.generation == generation for lease in manager.leases)
        assert all(lease.lane is SpeakerScoringLane.SHADOW for lease in manager.leases)
        assert all(lease.mode is expected_mode for lease in manager.leases)
        assert all(lease.timeout_seconds == 2.0 for lease in manager.leases)
        assert [item["backend_factory"] for item in constructed] == [None, None]
        assert [item["shared_backend_lease"] for item in constructed] == manager.leases
    finally:
        factory.close()
        profile.close()

    assert manager.close_count == 0


def test_public_shared_host_binding_creates_independent_shadow_leases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main_logic.voice_identity_service.asr_composition as module

    constructed: list[dict[str, object]] = []

    def construct_shadow(**kwargs: object) -> _ConstructedShadow:
        constructed.append(dict(kwargs))
        return _ConstructedShadow()

    monkeypatch.setattr(module, "SpeakerShadowRuntime", construct_shadow)
    profile = _profile()
    generation = _shared_generation()
    manager = _SharedManagerStub(generation)
    binding = SharedCampPlusCompositionBinding(manager, generation)
    factory = OwnerVoiceAsrCompositionFactory(
        _EvidenceSink(),
        profile,
        activation_generation="activation-1",
        enforce=True,
        shared_host_binding=binding,
    )
    try:
        first = factory()
        second = factory()

        assert first is not second
        assert len(manager.leases) == 2
        assert manager.leases[0] is not manager.leases[1]
        assert [item["shared_backend_lease"] for item in constructed] == (
            manager.leases
        )
    finally:
        factory.close()
        profile.close()

    assert manager.close_count == 0


def test_public_shared_host_binding_rejects_stale_and_mixed_dependencies() -> None:
    profile = _profile()
    generation = _shared_generation()
    manager = _SharedManagerStub(generation)
    binding = SharedCampPlusCompositionBinding(manager, generation)
    try:
        with pytest.raises(ValueError, match="mutually exclusive"):
            OwnerVoiceAsrCompositionFactory(
                _EvidenceSink(),
                profile,
                activation_generation="activation-1",
                enforce=True,
                shared_host_binding=binding,
                shared_scoring_manager=manager,
                shared_host_generation=generation,
            )

        stale_generation = _shared_generation(host_generation=8)
        with pytest.raises(SharedHostIdentityError, match="not current"):
            OwnerVoiceAsrCompositionFactory(
                _EvidenceSink(),
                profile,
                activation_generation="activation-1",
                enforce=True,
                shared_host_binding=SharedCampPlusCompositionBinding(
                    manager,
                    stale_generation,
                ),
            )
    finally:
        profile.close()


def test_public_binding_shadow_failure_does_not_close_physical_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main_logic.voice_identity_service.asr_composition as module

    def broken_shadow(**_kwargs: object) -> object:
        raise RuntimeError("injected observer construction failure")

    monkeypatch.setattr(module, "SpeakerShadowRuntime", broken_shadow)
    profile = _profile()
    generation = _shared_generation()
    manager = _SharedManagerStub(generation)
    factory = OwnerVoiceAsrCompositionFactory(
        _EvidenceSink(),
        profile,
        activation_generation="activation-1",
        enforce=True,
        shared_host_binding=SharedCampPlusCompositionBinding(manager, generation),
    )
    try:
        with pytest.raises(RuntimeError, match="injected observer"):
            factory()
        assert len(manager.leases) == 1
        assert manager.close_count == 0
    finally:
        factory.close()
        profile.close()

    assert manager.close_count == 0


def test_shared_host_dependencies_must_be_provided_as_a_current_matching_pair() -> None:
    profile = _profile()
    generation = _shared_generation()
    manager = _SharedManagerStub(generation)
    try:
        with pytest.raises(ValueError, match="must be provided together"):
            OwnerVoiceAsrCompositionFactory(
                _EvidenceSink(),
                profile,
                activation_generation="activation-1",
                enforce=True,
                shared_scoring_manager=manager,
            )

        mismatched = _shared_generation(profile_generation="another-profile")
        mismatched_manager = _SharedManagerStub(mismatched)
        with pytest.raises(ValueError, match="does not match speaker profile"):
            OwnerVoiceAsrCompositionFactory(
                _EvidenceSink(),
                profile,
                activation_generation="activation-1",
                enforce=True,
                shared_scoring_manager=mismatched_manager,
                shared_host_generation=mismatched,
            )

        stale_manager = _SharedManagerStub(_shared_generation(host_generation=8))
        with pytest.raises(SharedHostIdentityError, match="not current"):
            OwnerVoiceAsrCompositionFactory(
                _EvidenceSink(),
                profile,
                activation_generation="activation-1",
                enforce=True,
                shared_scoring_manager=stale_manager,
                shared_host_generation=generation,
            )
    finally:
        profile.close()


def test_shared_generation_is_revalidated_before_each_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main_logic.voice_identity_service.asr_composition as module

    monkeypatch.setattr(
        module, "SpeakerShadowRuntime", lambda **_kwargs: _ConstructedShadow()
    )
    profile = _profile()
    generation = _shared_generation()
    manager = _SharedManagerStub(generation)
    factory = OwnerVoiceAsrCompositionFactory(
        _EvidenceSink(),
        profile,
        activation_generation="activation-1",
        enforce=True,
        shared_scoring_manager=manager,
        shared_host_generation=generation,
    )
    try:
        manager._current = _shared_generation(host_generation=8)
        with pytest.raises(SharedHostIdentityError, match="not current"):
            factory()
        assert manager.leases == []
    finally:
        factory.close()
        profile.close()


def test_shared_shadow_construction_failure_does_not_close_shared_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main_logic.voice_identity_service.asr_composition as module

    def broken_shadow(**_kwargs: object) -> object:
        raise RuntimeError("injected observer construction failure")

    monkeypatch.setattr(module, "SpeakerShadowRuntime", broken_shadow)
    profile = _profile()
    generation = _shared_generation()
    manager = _SharedManagerStub(generation)
    factory = OwnerVoiceAsrCompositionFactory(
        _EvidenceSink(),
        profile,
        activation_generation="activation-1",
        enforce=True,
        shared_scoring_manager=manager,
        shared_host_generation=generation,
    )
    try:
        with pytest.raises(RuntimeError, match="injected observer"):
            factory()
        assert len(manager.leases) == 1
        assert manager.leases[0].close_count == 0
        assert manager.close_count == 0
    finally:
        factory.close()
        profile.close()


def test_legacy_composition_omits_shared_lease_and_builds_private_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main_logic.voice_identity_service.asr_composition as module

    constructed: list[dict[str, object]] = []

    def construct_shadow(**kwargs: object) -> _ConstructedShadow:
        constructed.append(dict(kwargs))
        return _ConstructedShadow()

    monkeypatch.setattr(module, "SpeakerShadowRuntime", construct_shadow)
    profile = _profile()
    factory = OwnerVoiceAsrCompositionFactory(
        _EvidenceSink(),
        profile,
        activation_generation="activation-1",
        enforce=True,
    )
    try:
        factory()
        assert len(constructed) == 1
        assert "shared_backend_lease" not in constructed[0]
        assert constructed[0]["backend_factory"] is not None
    finally:
        factory.close()
        profile.close()


@pytest.mark.parametrize("iteration", range(50))
def test_shadow_constructor_failure_closes_unadopted_backend_factory(
    monkeypatch, iteration
):
    import main_logic.voice_identity_service.asr_composition as module

    created = []
    original = module.CampPlusBackendFactory

    def backend_factory(embedding):
        backend = original(embedding)
        created.append(backend)
        return backend

    def broken_shadow(**kwargs):
        raise RuntimeError("injected observer construction failure")

    monkeypatch.setattr(module, "CampPlusBackendFactory", backend_factory)
    monkeypatch.setattr(module, "SpeakerShadowRuntime", broken_shadow)
    profile = _profile()
    factory = module.OwnerVoiceAsrCompositionFactory(
        _EvidenceSink(), profile, activation_generation="activation-1", enforce=True
    )
    try:
        with pytest.raises(RuntimeError, match="injected observer"):
            factory()
        assert len(created) == 1 and created[0]._closed
    finally:
        factory.close()
        profile.close()


async def test_diagnostic_configuration_failure_cannot_block_shadow_install(
    monkeypatch,
):
    import main_logic.voice_identity_service.asr_composition as module

    def fail_configuration(**_kwargs):
        raise RuntimeError("diagnostic only")

    monkeypatch.setattr(
        module, "SpeakerScoreDiagnosticConfiguration", fail_configuration
    )
    profile = _profile()
    factory = module.OwnerVoiceAsrCompositionFactory(
        _EvidenceSink(), profile, activation_generation="activation-1", enforce=True
    )
    shadow = factory()
    try:
        assert shadow.enabled
        assert not hasattr(shadow, "_score_diagnostic_configuration")
    finally:
        await shadow.close()
        factory.close()
        profile.close()


async def test_installed_shadow_keeps_profile_activation_and_install_refs_distinct():
    profile = _profile()
    identity = SpeakerVerifierInstallIdentity(
        1, 2, 3, 4, 5, 6, "activation-1", "installation-7"
    )
    factory = OwnerVoiceAsrCompositionFactory(
        _EvidenceSink(),
        profile,
        activation_generation="activation-1",
        enforce=True,
        installation_identity=identity,
    )
    shadow = factory()
    try:
        configuration = shadow._score_diagnostic_configuration
        refs = {
            configuration.profile_generation_ref,
            configuration.activation_generation_ref,
            configuration.installation_ref,
        }
        assert None not in refs
        assert len(refs) == 3
        assert all(len(value) == 16 for value in refs)
        assert "profile-generation" not in repr(configuration)
        assert "activation-1" not in repr(configuration)
        assert "installation-7" not in repr(configuration)
    finally:
        await shadow.close()
        factory.close()
        profile.close()


def test_composition_emits_stateless_ordered_low_facts_then_close() -> None:
    sink = _EvidenceSink()
    profile = _profile()
    factory = OwnerVoiceAsrCompositionFactory(
        sink,
        profile,
        activation_generation="activation-1",
        enforce=True,
    )
    assert factory.enforces_admission is True
    shadow = factory()
    candidate = SpeakerShadowCandidateKey(1, 2, "provider_candidate")
    callback = shadow._on_evidence
    assert callback is not None

    callback(
        SpeakerShadowObservation(
            candidate,
            0.2,
            ((0.4, True),),
            1_500,
            1_500,
            sequence_no=1,
        )
    )
    callback(
        SpeakerShadowObservation(
            candidate,
            0.2,
            ((0.4, True),),
            3_000,
            3_000,
            sequence_no=2,
        )
    )
    callback(SpeakerShadowCompletion(candidate, "scored", 3_000, 2, True))

    assert [type(event) for event in sink.events] == [
        SpeakerLow,
        SpeakerLow,
        CaptureClosed,
    ]
    assert [event.sequence_no for event in sink.events[:2]] == [1, 2]
    assert sink.events[-1] == CaptureClosed(candidate, 2)
    diagnostics = factory.diagnostics_snapshot()
    assert diagnostics["speaker_first_low_count"] == 1
    assert diagnostics["speaker_second_low_count"] == 1
    assert "reject_decision_count" not in diagnostics
    factory.close()


@pytest.mark.parametrize(
    ("checkpoint_ms", "audio_ms", "observation_kind", "checkpoint_kind"),
    (
        (3_000, 3_000, "checkpoint", SpeakerCheckpointKind.SECOND),
        (
            1_500,
            2_100,
            "completion_confirmation",
            SpeakerCheckpointKind.COMPLETION_CONFIRMATION,
        ),
    ),
)
def test_composition_preserves_high_checkpoint_window_metadata(
    checkpoint_ms: int,
    audio_ms: int,
    observation_kind: str,
    checkpoint_kind: SpeakerCheckpointKind,
) -> None:
    sink = _EvidenceSink()
    profile = _profile()
    factory = OwnerVoiceAsrCompositionFactory(
        sink,
        profile,
        activation_generation="activation-1",
        enforce=True,
    )
    shadow = factory()
    candidate = SpeakerShadowCandidateKey(1, 2, "provider_candidate")
    callback = shadow._on_evidence
    assert callback is not None

    callback(
        SpeakerShadowObservation(
            candidate,
            0.8,
            ((0.4, False),),
            audio_ms,
            checkpoint_ms,
            observation_kind=observation_kind,
            sequence_no=1,
        )
    )

    assert sink.events == [
        SpeakerHigh(
            candidate,
            1,
            checkpoint_kind,
            audio_ms,
        )
    ]
    factory.close()


def test_incomplete_close_emits_unavailable_before_close() -> None:
    sink = _EvidenceSink()
    profile = _profile()
    factory = OwnerVoiceAsrCompositionFactory(
        sink,
        profile,
        activation_generation="activation-1",
        enforce=True,
    )
    shadow = factory()
    candidate = SpeakerShadowCandidateKey(1, 3, "provider_candidate")
    callback = shadow._on_evidence
    assert callback is not None

    callback(SpeakerShadowCompletion(candidate, "failed", None, 1, False))

    assert sink.events == [
        SpeakerUnavailable(candidate, 1, SpeakerUnavailableReason.FAILURE),
        CaptureClosed(candidate, 1),
    ]
    factory.close()


@pytest.mark.parametrize(
    ("package", "similarity", "expected"),
    [
        (
            _calibration_package(release_status="candidate"),
            -0.8,
            SpeakerUnavailableReason.INSUFFICIENT_EVIDENCE,
        ),
        (
            _calibration_package(release_status="registered"),
            -0.8,
            SpeakerUnavailableReason.INSUFFICIENT_EVIDENCE,
        ),
        (
            _calibration_package(release_status="registered"),
            0.0,
            SpeakerUnavailableReason.INSUFFICIENT_EVIDENCE,
        ),
    ],
    ids=["candidate-observes", "bare-registered-observes", "registered-uncertain"],
)
def test_terminal_short_maps_calibration_without_using_checkpoint_threshold(
    package: CalibrationPackage,
    similarity: float,
    expected: SpeakerUnavailableReason | SpeakerCheckpointKind,
) -> None:
    sink = _EvidenceSink()
    profile = _profile()
    factory = OwnerVoiceAsrCompositionFactory(
        sink,
        profile,
        activation_generation="activation-1",
        enforce=True,
        calibration_package=package,
        runtime_calibration_protocol=package.protocol,
    )
    shadow = factory()
    candidate = SpeakerShadowCandidateKey(1, 4, "provider_candidate")
    callback = shadow._on_evidence
    assert callback is not None

    callback(
        SpeakerShadowObservation(
            candidate,
            similarity,
            ((0.4, similarity < 0.4),),
            900,
            observation_kind="terminal_short",
            sequence_no=1,
        )
    )

    assert shadow._config.terminal_short_evaluation_scopes == ("provider_candidate",)
    assert shadow._backend_factory._allow_short_input is True
    assert shadow._config.terminal_short_minimum_samples == 720
    if isinstance(expected, SpeakerCheckpointKind):
        assert sink.events == [SpeakerLow(candidate, 1, expected)]
    else:
        assert sink.events == [SpeakerUnavailable(candidate, 1, expected)]
    diagnostics = factory.diagnostics_snapshot()
    if similarity == -0.8:
        assert diagnostics["terminal_short_recommended_nonowner_count"] == 1
    factory.close()
    profile.close()


@pytest.mark.parametrize(
    ("similarity", "expected"),
    (
        (0.8, SpeakerCheckpointKind.TERMINAL_SHORT),
        (-0.8, SpeakerCheckpointKind.TERMINAL_SHORT),
        (0.0, SpeakerUnavailableReason.INSUFFICIENT_EVIDENCE),
    ),
    ids=("owner", "nonowner", "uncertain"),
)
def test_only_allowlisted_wrapper_enables_terminal_short_authority(
    similarity: float,
    expected: SpeakerUnavailableReason | SpeakerCheckpointKind,
) -> None:
    sink = _EvidenceSink()
    profile = _profile()
    package = _calibration_package(release_status="candidate")
    factory = OwnerVoiceAsrCompositionFactory(
        sink,
        profile,
        activation_generation="activation-1",
        enforce=True,
        calibration_package=package,
        registered_calibration=_allowlisted(package),
        runtime_calibration_protocol=package.protocol,
    )
    shadow = factory()
    candidate = SpeakerShadowCandidateKey(1, 40, "provider_candidate")
    callback = shadow._on_evidence
    assert callback is not None

    callback(
        SpeakerShadowObservation(
            candidate,
            similarity,
            ((0.4, similarity < 0.4),),
            900,
            observation_kind="terminal_short",
            sequence_no=1,
        )
    )

    if isinstance(expected, SpeakerUnavailableReason):
        assert sink.events == [SpeakerUnavailable(candidate, 1, expected)]
    elif similarity < 0:
        assert sink.events == [SpeakerLow(candidate, 1, expected)]
    else:
        assert sink.events == [SpeakerHigh(candidate, 1, expected, 900)]
    factory.close()
    profile.close()


def test_registered_wrapper_must_match_the_observed_package() -> None:
    profile = _profile()
    package = _calibration_package(release_status="candidate")
    other = _calibration_package(release_status="registered")

    with pytest.raises(ValueError, match="must match calibration_package"):
        OwnerVoiceAsrCompositionFactory(
            _EvidenceSink(),
            profile,
            activation_generation="activation-1",
            enforce=True,
            calibration_package=package,
            registered_calibration=_allowlisted(other),
            runtime_calibration_protocol=package.protocol,
        )

    profile.close()


def test_terminal_short_is_disabled_without_calibration_package() -> None:
    profile = _profile()
    factory = OwnerVoiceAsrCompositionFactory(
        _EvidenceSink(),
        profile,
        activation_generation="activation-1",
        enforce=True,
    )
    shadow = factory()

    assert shadow._config.terminal_short_evaluation_scopes == ()
    assert shadow._backend_factory._allow_short_input is False
    factory.close()
    profile.close()


def test_calibration_package_requires_independent_runtime_protocol() -> None:
    profile = _profile()
    package = _calibration_package(release_status="candidate")

    with pytest.raises(ValueError, match="must be provided together"):
        OwnerVoiceAsrCompositionFactory(
            _EvidenceSink(),
            profile,
            activation_generation="activation-1",
            enforce=True,
            calibration_package=package,
        )

    profile.close()


def test_candidate_package_observes_terminal_short_when_admission_is_not_enforced() -> (
    None
):
    profile = _profile()
    package = _calibration_package(release_status="candidate")
    factory = OwnerVoiceAsrCompositionFactory(
        _EvidenceSink(),
        profile,
        activation_generation="activation-1",
        enforce=False,
        calibration_package=package,
        runtime_calibration_protocol=package.protocol,
    )
    shadow = factory()

    assert shadow._config.terminal_short_evaluation_scopes == ("provider_candidate",)
    assert shadow._backend_factory._allow_short_input is True
    factory.close()
    profile.close()


def test_terminal_short_backend_limit_maps_to_unsupported_fail_open() -> None:
    sink = _EvidenceSink()
    profile = _profile()
    package = _calibration_package(release_status="registered")
    factory = OwnerVoiceAsrCompositionFactory(
        sink,
        profile,
        activation_generation="activation-1",
        enforce=True,
        calibration_package=package,
        runtime_calibration_protocol=package.protocol,
    )
    shadow = factory()
    candidate = SpeakerShadowCandidateKey(1, 5, "provider_candidate")
    callback = shadow._on_evidence
    assert callback is not None

    callback(
        SpeakerShadowObservation(
            candidate,
            0.0,
            (),
            44,
            observation_kind="terminal_short",
            sequence_no=1,
            evidence_available=False,
            unavailable_reason="unsupported",
        )
    )

    assert sink.events == [
        SpeakerUnavailable(candidate, 1, SpeakerUnavailableReason.UNSUPPORTED)
    ]
    factory.close()
    profile.close()


async def test_factory_close_wipes_owned_profile_and_backend_material() -> None:
    sink = _EvidenceSink()
    profile = _profile()
    source_embedding = profile._reference._embedding
    factory = OwnerVoiceAsrCompositionFactory(
        sink,
        profile,
        activation_generation="activation-1",
        enforce=True,
    )
    factory_profile = factory._profile
    factory_profile_embedding = factory_profile._reference._embedding

    profile.close()
    assert profile.closed is True
    assert not np.any(source_embedding)
    assert np.any(factory_profile_embedding)

    shadow = factory()
    backend_factory = shadow._backend_factory
    assert backend_factory is not None
    backend_storage = backend_factory._reference._storage
    assert any(backend_storage)

    factory.close()
    factory.close()
    assert factory_profile.closed is True
    assert not np.any(factory_profile_embedding)
    with pytest.raises(RuntimeError, match="factory is closed"):
        factory()

    await shadow.close()
    assert not any(backend_storage)


async def test_backend_health_uses_generation_scoped_evidence_sink() -> None:
    sink = _EvidenceSink()
    profile = _profile()
    factory = OwnerVoiceAsrCompositionFactory(
        sink,
        profile,
        activation_generation="activation-1",
        enforce=True,
    )
    shadow = factory()

    shadow._mark_backend_degraded()
    shadow._mark_backend_recovered()

    assert sink.degraded_generations == ["activation-1"]
    assert sink.healthy_generations == ["activation-1"]
    await shadow.close()
    factory.close()
    profile.close()


def test_wrong_model_identity_wipes_temporary_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(
        SpeakerModelIdentity(
            "wrong-model",
            "wrong-revision",
            CAMPPLUS_EMBEDDING_DIM,
        )
    )
    sink = _EvidenceSink()
    captured_references: list[SpeakerReference] = []
    captured_embeddings: list[np.ndarray] = []
    original_clone: Callable[[SpeakerProfile], SpeakerReference] = (
        SpeakerProfile.clone_reference
    )

    def capture_clone(owner: SpeakerProfile) -> SpeakerReference:
        reference = original_clone(owner)
        captured_references.append(reference)
        captured_embeddings.append(reference._embedding)
        return reference

    monkeypatch.setattr(SpeakerProfile, "clone_reference", capture_clone)
    factory = OwnerVoiceAsrCompositionFactory(
        sink,
        profile,
        activation_generation="activation-1",
        enforce=True,
    )

    with pytest.raises(ValueError, match=r"model identity does not match CAM\+\+"):
        factory()

    assert len(captured_references) == 1
    assert captured_references[0].closed is True
    assert not np.any(captured_embeddings[0])
    factory.close()
    profile.close()
