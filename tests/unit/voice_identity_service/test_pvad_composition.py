from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import pickle

import numpy as np
import pytest

from main_logic.asr_client.admission.contracts import (
    CaptureClosed,
    SpeakerHigh,
    SpeakerLow,
    SpeakerUnavailable,
    SpeakerUnavailableReason,
)
from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
)
from main_logic.asr_client.speaker_shadow.contracts import (
    SpeakerShadowCandidateKey,
    SpeakerShadowCompletion,
    SpeakerShadowObservation,
)
from main_logic.asr_client.speaker_shadow.shared_host import (
    HostGenerationReceipt,
    SharedSpeakerScoringHostManager,
    SpeakerHostIdentity,
    SpeakerScoringMode,
)
from main_logic.asr_client.speaker_verifier_contracts import (
    SpeakerVerifierAuthority,
    SpeakerVerifierInstallIdentity,
)
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import (
    SpeakerActivityReferenceContract,
    SpeakerProfile,
)
from main_logic.voice_identity.pvad.assets import (
    ECAPA_IDENTITY,
    ECAPA_PREPROCESSING_REVISION,
    ECAPA_REFERENCE_METHOD,
    ECAPA_RESOURCE_REVISION,
)
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.voice_identity_service.asr_composition import (
    OwnerVoiceAsrCompositionFactory,
    PvadBackendFactory,
    PvadSpeakerBackend,
)
from main_logic.voice_identity_service.pvad_policy import PvadEvidenceKind, PvadMode
from main_logic.voice_identity_service.shared_campplus_host import (
    SharedCampPlusCompositionBinding,
)


class _Scorer:
    def __init__(self, score=0.1, *, available=True, error=False):
        self.result, self.available, self.error = score, available, error
        self.calls = []
        self.closed = False

    def load(self):
        return self.available

    def score(self, pcm, rate):
        self.calls.append((pcm, rate))
        if self.error:
            raise RuntimeError("model_failed")
        return self.result

    def close(self):
        self.closed = True


class _ModeScorer(_Scorer):
    def score_with_mode(self, pcm, rate, *, mode):
        self.calls.append((pcm, rate, mode))
        return self.result


class _ProcessFactory:
    """Real worker process/queue, deterministic model boundary for routing tests."""

    def __call__(self):
        return PvadSpeakerBackend(_Scorer(0.7), _Scorer(0.1))

    def close(self):
        pass


class _UnavailablePvadFactory(_ProcessFactory):
    def __call__(self):
        return PvadSpeakerBackend(_Scorer(0.7), _Scorer(available=False))


class _SharedLeaseManager(SharedSpeakerScoringHostManager):
    def __init__(self, generation: HostGenerationReceipt) -> None:
        self.generation = generation
        self.leases = []

    def is_generation_current(self, generation: HostGenerationReceipt) -> bool:
        return generation == self.generation

    def lease(self, generation, **kwargs):
        lease = object()
        self.leases.append((generation, kwargs, lease))
        return lease


@dataclass
class _Sink:
    events: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)
    current_installation: object = None

    def _accept_speaker_evidence_fact(self, fact, **kwargs):
        self.events.append(fact)
        return True

    def _close_speaker_evidence(self, closed, **kwargs):
        self.events.append(closed)
        return True

    def _accept_speaker_diagnostic(self, event, **kwargs):
        self.diagnostics.append(event)

    def _mark_speaker_evidence_backend_degraded(self, **kwargs):
        pass

    def _mark_speaker_evidence_backend_healthy(self, **kwargs):
        pass

    def speaker_verifier_installation_permits_evidence(self, identity):
        return identity == self.current_installation


@pytest.fixture
def profile():
    camp = SpeakerReference(
        SpeakerModelIdentity(CAMPPLUS_MODEL_ID, CAMPPLUS_MODEL_REVISION, 192),
        np.ones(192),
    )
    activity = SpeakerReference(ECAPA_IDENTITY, np.ones(192))
    contract = SpeakerActivityReferenceContract(
        ECAPA_RESOURCE_REVISION,
        ECAPA_PREPROCESSING_REVISION,
        ECAPA_REFERENCE_METHOD,
        16_000,
        True,
    )
    profile = SpeakerProfile(
        "profile",
        camp,
        activity_reference=activity,
        activity_reference_contract=contract,
    )
    camp.close()
    activity.close()
    yield profile
    profile.close()


@pytest.mark.parametrize("count", [3_199, 3_200, 3_201, 23_999, 24_000, 24_001])
def test_pcm_sample_boundary_dispatch_and_unpadded_coverage(count):
    camp, pvad = _Scorer(0.7), _Scorer(0.8)
    backend = PvadSpeakerBackend(camp, pvad)
    assert backend.load()
    pcm = b"\x01\x02" * count
    try:
        if count < 3_200:
            with pytest.raises(ValueError, match="pvad_unsupported_samples"):
                backend.score(pcm, 16_000)
            assert not camp.calls and not pvad.calls
        elif count < 24_000:
            assert backend.score(pcm, 16_000) == 0.8
            assert pvad.calls == [(pcm[: count // 160 * 160 * 2], 16_000)]
            evidence = backend.observe_short_candidate(pcm, 16_000)
            assert evidence.kind is PvadEvidenceKind.OWNER_ACTIVITY
            assert evidence.input_sample_count == count
            assert evidence.covered_end_sample == count // 160 * 160
            assert evidence.uncovered_tail_samples == count % 160
            assert not camp.calls
        else:
            assert backend.score(pcm, 16_000) == 0.7
            assert camp.calls == [(pcm, 16_000)]
            assert not pvad.calls
    finally:
        backend.close()
    assert camp.closed and pvad.closed


def test_shared_modes_keep_prewire_on_campplus_and_shadow_short_on_pvad():
    camp, pvad = _ModeScorer(0.7), _Scorer(0.8)
    backend = PvadSpeakerBackend(camp, pvad)
    assert backend.load()
    short_pcm = b"\x01\x02" * 3_200
    long_pcm = b"\x01\x02" * 24_000
    try:
        assert backend.score_with_mode(
            short_pcm,
            16_000,
            mode=SpeakerScoringMode.SHORT_PROBE.value,
        ) == 0.7
        assert backend.score_with_mode(
            short_pcm,
            16_000,
            mode=SpeakerScoringMode.STANDARD.value,
        ) == 0.8
        assert backend.score_with_mode(
            long_pcm,
            16_000,
            mode=SpeakerScoringMode.STANDARD.value,
        ) == 0.7
        assert camp.calls == [
            (short_pcm, 16_000, "short_probe"),
            (long_pcm, 16_000, "standard"),
        ]
        assert pvad.calls == [(short_pcm, 16_000)]
    finally:
        backend.close()


@pytest.mark.parametrize(
    "pvad", [_Scorer(available=False), _Scorer(error=True), _Scorer(float("nan"))]
)
def test_pvad_failure_cannot_disable_long_camplus_or_become_a_low_score(pvad):
    camp = _Scorer(0.7)
    backend = PvadSpeakerBackend(camp, pvad)
    assert backend.load()
    try:
        evidence = backend.observe_short_candidate(b"\x01\x02" * 3_200, 16_000)
        assert evidence.kind is PvadEvidenceKind.UNAVAILABLE
        with pytest.raises(ValueError):
            backend.score(b"\x01\x02" * 3_200, 16_000)
        assert backend.score(b"\x01\x02" * 24_000, 16_000) == 0.7
    finally:
        backend.close()
    with pytest.raises(RuntimeError, match="pvad_backend_closed"):
        backend.score(b"\x01\x02" * 3_200, 16_000)
    assert not backend.load()


@pytest.mark.parametrize(
    "pcm,rate", [(b"x", 16_000), (b"xx" * 3_200, 8_000), (b"xx" * 3_200, True)]
)
def test_invalid_pcm_never_reaches_the_model(pcm, rate):
    pvad = _Scorer()
    backend = PvadSpeakerBackend(_Scorer(), pvad)
    backend.load()
    assert (
        backend.observe_short_candidate(pcm, rate).kind is PvadEvidenceKind.UNAVAILABLE
    )
    assert not pvad.calls
    backend.close()


@pytest.mark.parametrize("scope", ["provider_candidate", "smart_turn_turn"])
@pytest.mark.parametrize("score", [0.0, 0.499, 0.5, 1.0, float("nan")])
async def test_default_observation_never_emits_owner_or_nonowner_authority(
    profile, scope, score
):
    sink = _Sink()
    factory = OwnerVoiceAsrCompositionFactory(
        sink, profile, activation_generation="activation", enforce=True
    )
    shadow = factory()
    candidate = SpeakerShadowCandidateKey(1, 1, scope)
    try:
        assert shadow._config.terminal_short_evaluation_scopes == (
            "provider_candidate",
            "smart_turn_turn",
        )
        assert shadow._config.terminal_short_minimum_samples == 3_200
        shadow._on_evidence(
            SpeakerShadowObservation(
                candidate,
                score,
                (),
                200,
                observation_kind="terminal_short",
                sequence_no=1,
            )
        )
        shadow._on_evidence(SpeakerShadowCompletion(candidate, "scored", None, 1, True))
        assert len(sink.events) == 2
        assert isinstance(sink.events[0], SpeakerUnavailable)
        assert isinstance(sink.events[1], CaptureClosed)
        assert not any(
            isinstance(event, (SpeakerHigh, SpeakerLow)) for event in sink.events
        )
        diagnostics = factory.diagnostics_snapshot()
        assert diagnostics["low_checkpoint_count"] == 0
        assert diagnostics["terminal_short_owner_count"] == 0
        assert diagnostics["terminal_short_nonowner_count"] == 0
        assert (
            sum(
                value
                for key, value in diagnostics.items()
                if key.startswith("pvad_") and key.endswith("_observed_count")
            )
            == 1
        )
    finally:
        await shadow.close()
        factory.close()


async def test_off_restores_existing_short_behavior_and_enforce_cannot_bypass_release_gate(
    profile,
):
    sink = _Sink()
    with pytest.raises(ValueError, match="pvad_enforcement_not_validated"):
        OwnerVoiceAsrCompositionFactory(
            sink,
            profile,
            activation_generation="activation",
            enforce=True,
            pvad_mode=PvadMode.ENFORCE,
        )
    with pytest.raises(TypeError, match="pvad_mode"):
        OwnerVoiceAsrCompositionFactory(
            sink,
            profile,
            activation_generation="activation",
            enforce=True,
            pvad_mode="enforce",
        )
    factory = OwnerVoiceAsrCompositionFactory(
        sink,
        profile,
        activation_generation="activation",
        enforce=True,
        pvad_mode=PvadMode.OFF,
    )
    shadow = factory()
    try:
        assert shadow._config.terminal_short_evaluation_scopes == ()
        assert not isinstance(shadow._backend_factory, PvadBackendFactory)
    finally:
        await shadow.close()
        factory.close()


def test_shared_campplus_path_does_not_spawn_a_second_private_backend(
    monkeypatch: pytest.MonkeyPatch,
    profile: SpeakerProfile,
) -> None:
    import main_logic.voice_identity_service.asr_composition as module

    constructed = []

    class _Shadow:
        def bind_score_diagnostic_configuration(self, configuration):
            self.configuration = configuration

    def construct_shadow(**kwargs):
        constructed.append(kwargs)
        return _Shadow()

    def reject_private_backend(*_args, **_kwargs):
        raise AssertionError("shared path must not construct a private CAM++/pVAD host")

    generation = HostGenerationReceipt(
        "manager",
        1,
        SpeakerHostIdentity(profile.generation, "campplus", "config"),
    )
    manager = _SharedLeaseManager(generation)
    monkeypatch.setattr(module, "SpeakerShadowRuntime", construct_shadow)
    monkeypatch.setattr(module, "CampPlusBackendFactory", reject_private_backend)
    factory = OwnerVoiceAsrCompositionFactory(
        _Sink(),
        profile,
        activation_generation="activation",
        enforce=True,
        shared_scoring_manager=manager,
        shared_host_generation=generation,
    )
    try:
        shadow = factory()
        assert constructed[0]["backend_factory"] is None
        assert constructed[0]["shared_backend_lease"] is manager.leases[0][2]
        assert constructed[0]["config"].terminal_short_evaluation_scopes == ()
        assert shadow.configuration is not None
    finally:
        factory.close()


def test_shared_pvad_binding_enables_observation_without_private_backend(
    monkeypatch: pytest.MonkeyPatch,
    profile: SpeakerProfile,
) -> None:
    import main_logic.voice_identity_service.asr_composition as module

    constructed = []

    class _Shadow:
        def bind_score_diagnostic_configuration(self, configuration):
            self.configuration = configuration

    def construct_shadow(**kwargs):
        constructed.append(kwargs)
        return _Shadow()

    def reject_private_backend(*_args, **_kwargs):
        raise AssertionError("shared path must not construct a private backend")

    generation = HostGenerationReceipt(
        "manager",
        1,
        SpeakerHostIdentity(profile.generation, "campplus+pvad", "config"),
    )
    manager = _SharedLeaseManager(generation)
    binding = SharedCampPlusCompositionBinding(
        manager,
        generation,
        pvad_observe_enabled=True,
    )
    monkeypatch.setattr(module, "SpeakerShadowRuntime", construct_shadow)
    monkeypatch.setattr(module, "CampPlusBackendFactory", reject_private_backend)

    factory = OwnerVoiceAsrCompositionFactory(
        _Sink(),
        profile,
        activation_generation="activation",
        enforce=True,
        shared_host_binding=binding,
    )
    try:
        factory()
        assert constructed[0]["backend_factory"] is None
        assert constructed[0]["shared_backend_lease"] is manager.leases[0][2]
        assert constructed[0]["config"].terminal_short_evaluation_scopes == (
            "provider_candidate",
            "smart_turn_turn",
        )
        assert constructed[0]["config"].terminal_short_minimum_samples == 3_200
        assert manager.leases[0][1]["mode"] is SpeakerScoringMode.STANDARD
    finally:
        factory.close()


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("resource_revision", "other-resource"),
        ("preprocessing_revision", "other-preprocessing"),
        ("reference_method", "other-method"),
        ("sample_rate_hz", 8_000),
    ],
)
async def test_incompatible_activity_reference_does_not_install_pvad(
    profile, field_name, value
):
    camp = profile.clone_reference()
    activity = profile.clone_activity_reference()
    mismatched = SpeakerProfile(
        "new-profile",
        camp,
        activity_reference=activity,
        activity_reference_contract=replace(
            profile.activity_reference_contract, **{field_name: value}
        ),
    )
    camp.close()
    activity.close()
    factory = OwnerVoiceAsrCompositionFactory(
        _Sink(), mismatched, activation_generation="activation", enforce=True
    )
    shadow = factory()
    try:
        assert shadow._config.terminal_short_evaluation_scopes == ()
        assert not isinstance(shadow._backend_factory, PvadBackendFactory)
    finally:
        await shadow.close()
        factory.close()
        mismatched.close()


@pytest.mark.parametrize("retirement", ["authority", "installation", "factory"])
async def test_late_activity_after_retirement_cannot_emit_facts(profile, retirement):
    identity = SpeakerVerifierInstallIdentity(
        1, 2, 3, 4, 5, 6, "activation", "installation"
    )
    sink = _Sink(current_installation=identity)
    authority = SpeakerVerifierAuthority()
    assert authority.commit()
    factory = OwnerVoiceAsrCompositionFactory(
        sink,
        profile,
        activation_generation="activation",
        enforce=True,
        authority=authority,
        installation_identity=identity,
    )
    shadow = factory()
    candidate = SpeakerShadowCandidateKey(1, 1, "provider_candidate")
    try:
        if retirement == "authority":
            authority.revoke()
        elif retirement == "installation":
            sink.current_installation = replace(
                identity, installation_id="new-installation"
            )
        else:
            factory.close()
        shadow._on_evidence(
            SpeakerShadowObservation(
                candidate,
                0.0,
                (),
                200,
                observation_kind="terminal_short",
                sequence_no=1,
            )
        )
        shadow._on_evidence(SpeakerShadowCompletion(candidate, "scored", None, 1, True))
        assert sink.events == []
        assert factory.diagnostics_snapshot()["speaker_completion_stale_count"] == 1
    finally:
        await shadow.close()
        factory.close()


@pytest.mark.parametrize("scope", ["provider_candidate", "smart_turn_turn"])
@pytest.mark.parametrize("count", [3_199, 3_200, 23_999, 24_000])
async def test_real_worker_terminal_routing_respects_boundaries(profile, scope, count):
    sink = _Sink()
    factory = OwnerVoiceAsrCompositionFactory(
        sink, profile, activation_generation="activation", enforce=True
    )
    shadow = factory()
    # Keep real worker queues, process IPC, candidate state and completion; only
    # replace the expensive model boundary with deterministic synthetic scores.
    shadow._backend_factory.close()
    shadow._backend_factory = _ProcessFactory()
    candidate = SpeakerShadowCandidateKey(1, 1, scope)
    try:
        pcm = b"\x01\x20" * count
        for offset in range(0, len(pcm), 640):
            assert shadow.submit(
                pcm[offset : offset + 640], sample_rate_hz=16_000, candidate=candidate
            )
        assert shadow.finish_candidate(candidate)
        await asyncio.wait_for(shadow.wait_idle(), timeout=15)
        assert isinstance(sink.events[-1], CaptureClosed)
        facts = sink.events[:-1]
        if count < 24_000:
            assert len(facts) == 1
            assert isinstance(facts[0], SpeakerUnavailable)
            if count < 3_200:
                assert facts[0].reason is SpeakerUnavailableReason.UNSUPPORTED
            else:
                assert facts[0].reason is SpeakerUnavailableReason.INSUFFICIENT_EVIDENCE
        else:
            assert len(facts) == 1
            assert isinstance(facts[0], SpeakerHigh)
        if count == 23_999:
            score_diagnostics = [
                item
                for item in sink.diagnostics
                if item.score_checkpoint_kind == "terminal_short"
            ]
            assert score_diagnostics
            assert all(
                item.model_version == "firered_pvad_v1" for item in score_diagnostics
            )
            assert all(
                item.score_input_sample_count == 23_999 for item in score_diagnostics
            )
            assert all(
                item.score_window_end_sample == 23_840 for item in score_diagnostics
            )
        settled = list(sink.events)
        shadow.finish_candidate(candidate)
        await asyncio.wait_for(shadow.wait_idle(), timeout=3)
        assert sink.events == settled
    finally:
        await shadow.close()
        factory.close()


async def test_real_worker_pvad_unavailable_then_camplus_candidate_still_scores(
    profile,
):
    sink = _Sink()
    factory = OwnerVoiceAsrCompositionFactory(
        sink, profile, activation_generation="activation", enforce=True
    )
    shadow = factory()
    shadow._backend_factory.close()
    shadow._backend_factory = _UnavailablePvadFactory()
    try:
        for number, count in [(1, 3_200), (2, 24_000)]:
            candidate = SpeakerShadowCandidateKey(1, number, "provider_candidate")
            pcm = b"\x01\x20" * count
            for offset in range(0, len(pcm), 640):
                assert shadow.submit(
                    pcm[offset : offset + 640],
                    sample_rate_hz=16_000,
                    candidate=candidate,
                )
            assert shadow.finish_candidate(candidate)
            await asyncio.wait_for(shadow.wait_idle(), timeout=15)
        assert [type(event) for event in sink.events] == [
            SpeakerUnavailable,
            CaptureClosed,
            SpeakerHigh,
            CaptureClosed,
        ]
        assert sink.events[0].reason is SpeakerUnavailableReason.FAILURE
        assert sink.events[2].candidate == SpeakerShadowCandidateKey(
            1, 2, "provider_candidate"
        )
    finally:
        await shadow.close()
        factory.close()


def test_composition_factory_is_spawn_pickleable_and_retires_references():
    factory = PvadBackendFactory(_ProcessFactory(), np.ones(192))
    restored = pickle.loads(pickle.dumps(factory))
    factory.close()
    restored.close()
    assert not factory._reference.any()
    assert not restored._reference.any()
    with pytest.raises(RuntimeError, match="pvad_factory_closed"):
        factory()
