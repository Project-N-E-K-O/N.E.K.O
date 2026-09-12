from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.main_server.voice_identity_runtime as runtime_module
from app.main_server.voice_identity_runtime import OwnerVoiceRuntimeRegistry
from main_logic.asr_client import VoiceIdentityActivationResult
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.voice_identity_service.preference_store import (
    VoiceIdentityPreferenceStore,
)
from main_logic.voice_identity_service.profile_store import (
    SecureStorageUnavailableError,
    VoiceIdentityProfileCorruptError,
    VoiceIdentityProfileIncompatibleError,
    VoiceIdentityProfileStore,
)
from main_logic.voice_identity_service.service import VoiceIdentityService
from main_logic.voice_input.suppression import VoiceInputSuppressionController
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus


@dataclass
class _Factory:
    runtime: object
    profile: SpeakerProfile
    activation_generation: str
    enforce: bool
    closed: bool = False

    def __init__(
        self,
        runtime: object,
        profile: SpeakerProfile,
        *,
        activation_generation: str,
        enforce: bool,
        noise_reduction_enabled: bool | None = None,
    ) -> None:
        self.runtime = runtime
        self.profile = profile
        self.activation_generation = activation_generation
        self.enforce = enforce
        self.noise_reduction_enabled = noise_reduction_enabled
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Manager:
    def __init__(self) -> None:
        self._asr_runtime = object()
        self.verifier_calls: list[tuple[_Factory | None, str]] = []
        self.verifier_outcomes: list[
            bool | VoiceIdentityActivationResult | BaseException
        ] = []
        self.suppression_calls: list[tuple[str, bool]] = []
        self.restore_failures = 0
        self.cancel_restore = False
        self.cancel_suppress = False
        self.suppress_failure = False
        self.activation_required = False
        self.activation_degraded = False
        self.require_calls: list[str] = []
        self.activation_policy_revision = 0

    def require_voice_session_activation(
        self,
        *,
        activation_generation: str,
    ) -> int:
        self.activation_policy_revision += 1
        self.activation_required = True
        self.activation_degraded = True
        self.require_calls.append(activation_generation)
        return self.activation_policy_revision

    def voice_session_activation_policy_token(self) -> int:
        return self.activation_policy_revision

    async def set_speaker_verifier_factory(
        self,
        factory: _Factory | None,
        *,
        activation_generation: str,
    ) -> bool | VoiceIdentityActivationResult:
        self.verifier_calls.append((factory, activation_generation))
        outcome = self.verifier_outcomes.pop(0) if self.verifier_outcomes else True
        if isinstance(outcome, BaseException):
            if factory is not None:
                factory.close()
            raise outcome
        if not outcome and factory is not None:
            factory.close()
        return outcome

    async def set_voice_session_activation_factory(
        self,
        factory: _Factory | None,
        *,
        activation_generation: str,
        activation_required: bool = False,
        expected_policy_revision: int | None = None,
    ) -> bool | VoiceIdentityActivationResult:
        if (
            expected_policy_revision is not None
            and expected_policy_revision != self.activation_policy_revision
        ):
            return VoiceIdentityActivationResult.RUNTIME_DEGRADED
        self.activation_required = activation_required
        result = await self.set_speaker_verifier_factory(
            factory,
            activation_generation=activation_generation,
        )
        if result:
            self.activation_degraded = False
        return result

    async def set_voice_input_suppressed(
        self,
        reason: str,
        *,
        suppressed: bool,
    ) -> None:
        self.suppression_calls.append((reason, suppressed))
        if suppressed and self.cancel_suppress:
            raise asyncio.CancelledError
        if suppressed and self.suppress_failure:
            raise RuntimeError("suppression failed")
        if not suppressed and self.cancel_restore:
            raise asyncio.CancelledError
        if not suppressed and self.restore_failures:
            self.restore_failures -= 1
            raise RuntimeError("transient restore failure")


def _profile(generation: str) -> SpeakerProfile:
    reference = SpeakerReference(
        SpeakerModelIdentity("model", "revision", 2),
        [1.0, 0.0],
    )
    try:
        return SpeakerProfile(generation, reference)
    finally:
        reference.close()


async def _wait_until(predicate, *, timeout_seconds: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition was not satisfied before timeout")
        await asyncio.sleep(0.01)


@pytest.fixture(autouse=True)
def _fake_composition_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime_module,
        "OwnerVoiceSessionActivationFactory",
        _Factory,
    )
    monkeypatch.setattr(runtime_module, "_runtime_registry", None)
    monkeypatch.setattr(runtime_module, "_service", None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_activation_updates_current_and_future_managers() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    current = _Manager()
    future = _Manager()
    await registry.register_manager(current)
    borrowed = _profile("profile-a")
    try:
        assert await registry.activate(borrowed, "generation-a")
    finally:
        borrowed.close()

    assert current.verifier_calls[-1][1] == "generation-a"
    current_factory = current.verifier_calls[-1][0]
    assert current_factory is not None
    assert current_factory.enforce

    assert await registry.register_manager(future)
    assert future.verifier_calls[-1][1] == "generation-a"
    assert future.verifier_calls[-1][0] is not current_factory


@pytest.mark.unit
@pytest.mark.asyncio
async def test_activation_preserves_unsupported_route_result() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = _Manager()
    manager.verifier_outcomes.append(
        VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE
    )
    await registry.register_manager(manager)
    profile = _profile("profile")
    try:
        result = await registry.activate(profile, "generation")
    finally:
        profile.close()

    assert result is VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE
    assert registry._activation is not None  # type: ignore[attr-defined]
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registry_prefers_session_activation_over_legacy_utterance_filter() -> None:
    class SessionActivationManager(_Manager):
        def __init__(self) -> None:
            super().__init__()
            self.session_activation_calls: list[tuple[object | None, str]] = []

        async def set_voice_session_activation_factory(
            self,
            factory,
            *,
            activation_generation: str,
            activation_required: bool = False,
            expected_policy_revision: int | None = None,
        ) -> VoiceIdentityActivationResult:
            del expected_policy_revision
            self.activation_required = activation_required
            self.session_activation_calls.append((factory, activation_generation))
            return VoiceIdentityActivationResult.READY

    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = SessionActivationManager()
    await registry.register_manager(manager)
    profile = _profile("profile-session")
    try:
        assert (
            await registry.activate(profile, "generation-session")
            is VoiceIdentityActivationResult.READY
        )
    finally:
        profile.close()

    assert len(manager.session_activation_calls) == 1
    assert manager.session_activation_calls[0][1] == "generation-session"
    assert manager.verifier_calls == []
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_legacy_manager_does_not_receive_incompatible_session_factory() -> None:
    class LegacyManager:
        def __init__(self) -> None:
            self._asr_runtime = object()
            self._asr_route_mode = "independent"
            self.legacy_setter = AsyncMock(return_value=True)

        async def set_speaker_verifier_factory(self, *args, **kwargs):
            return await self.legacy_setter(*args, **kwargs)

        async def set_voice_input_suppressed(
            self,
            reason: str,
            *,
            suppressed: bool,
        ) -> None:
            del reason, suppressed

    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = LegacyManager()
    assert (
        await registry.register_manager(manager)
        is VoiceIdentityActivationResult.READY
    )
    profile = _profile("legacy-profile")
    try:
        assert (
            await registry.activate(profile, "legacy-generation")
            is VoiceIdentityActivationResult.RUNTIME_DEGRADED
        )
    finally:
        profile.close()

    manager.legacy_setter.assert_not_awaited()
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_late_registration_preserves_unsupported_route_result() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()
    manager = _Manager()
    manager.verifier_outcomes.append(
        VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE
    )

    result = await registry.register_manager(manager)

    assert result is VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_activation_status_tracks_live_route_and_runtime_degradation() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = _Manager()
    manager._asr_route_mode = "independent"  # type: ignore[attr-defined]
    manager._asr_runtime = SimpleNamespace(_speaker_verifier_degraded=False)
    await registry.register_manager(manager)
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()

    assert registry.activation_status() is VoiceIdentityActivationResult.READY
    registry._restore_pending.add(manager)  # type: ignore[attr-defined]
    assert (
        registry.activation_status()
        is VoiceIdentityActivationResult.RUNTIME_DEGRADED
    )
    registry._restore_pending.discard(manager)  # type: ignore[attr-defined]
    manager._asr_route_mode = "native"  # type: ignore[attr-defined]
    assert (
        registry.activation_status()
        is VoiceIdentityActivationResult.READY
    )
    manager._asr_route_mode = "independent"  # type: ignore[attr-defined]
    manager._asr_runtime._speaker_verifier_degraded = True
    assert (
        registry.activation_status()
        is VoiceIdentityActivationResult.RUNTIME_DEGRADED
    )
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inactive_blocked_managers_do_not_override_active_route_status() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    active = _Manager()
    active.is_active = True  # type: ignore[attr-defined]
    active._asr_route_mode = "independent"  # type: ignore[attr-defined]
    inactive = _Manager()
    inactive.is_active = False  # type: ignore[attr-defined]
    inactive._asr_route_mode = "blocked"  # type: ignore[attr-defined]
    inactive._asr_runtime = SimpleNamespace(_speaker_verifier_degraded=True)
    await registry.register_manager(active)
    await registry.register_manager(inactive)
    inactive.verifier_outcomes.append(
        VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE
    )
    profile = _profile("profile")
    try:
        result = await registry.activate(profile, "generation")
    finally:
        profile.close()

    assert result is VoiceIdentityActivationResult.READY
    assert registry.activation_status() is VoiceIdentityActivationResult.READY

    late_inactive = _Manager()
    late_inactive.is_active = False  # type: ignore[attr-defined]
    late_inactive._asr_route_mode = "blocked"  # type: ignore[attr-defined]
    late_inactive._asr_runtime = SimpleNamespace(_speaker_verifier_degraded=True)
    late_inactive.verifier_outcomes.append(
        VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE
    )
    assert (
        await registry.register_manager(late_inactive)
        is VoiceIdentityActivationResult.READY
    )
    assert registry.activation_status() is VoiceIdentityActivationResult.READY

    active._asr_route_mode = "blocked"  # type: ignore[attr-defined]
    assert (
        registry.activation_status()
        is VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE
    )
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registration_attachment_failure_retries_current_activation() -> None:
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=1.0,
    )
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()
    manager = _Manager()
    manager.verifier_outcomes.extend([False, True])

    assert not await registry.register_manager(manager)
    assert manager in registry._attach_pending  # type: ignore[attr-defined]
    await _wait_until(
        lambda: manager not in registry._attach_pending  # type: ignore[attr-defined]
    )

    assert len(manager.verifier_calls) == 2
    assert manager.verifier_calls[-1][1] == "generation"
    await _wait_until(
        lambda: registry._attach_retry_task is None  # type: ignore[attr-defined]
    )
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reregistration_invalidates_stale_detach_before_attach_retry() -> None:
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=1.0,
    )
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()
    manager = _Manager()
    assert await registry.register_manager(manager)
    manager.verifier_outcomes.extend([False, False, True])

    await registry.unregister_manager(manager)
    assert manager in registry._detach_pending  # type: ignore[attr-defined]
    calls_before_registration = len(manager.verifier_calls)

    assert not await registry.register_manager(manager)
    assert manager not in registry._detach_pending  # type: ignore[attr-defined]
    assert manager in registry._attach_pending  # type: ignore[attr-defined]
    await _wait_until(
        lambda: manager not in registry._attach_pending  # type: ignore[attr-defined]
    )

    registration_calls = manager.verifier_calls[calls_before_registration:]
    assert len(registration_calls) == 2
    assert all(factory is not None for factory, _generation in registration_calls)
    assert all(generation == "generation" for _factory, generation in registration_calls)
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_activation_rolls_changed_managers_back() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=False)
    managers = [_Manager(), _Manager()]
    for manager in managers:
        await registry.register_manager(manager)
    old_profile = _profile("old")
    new_profile = _profile("new")
    try:
        assert await registry.activate(old_profile, "old-generation")
        ordered = tuple(registry._managers)  # type: ignore[attr-defined]
        ordered[1].verifier_outcomes.append(False)

        assert not await registry.activate(new_profile, "new-generation")

        await _wait_until(
            lambda: all(
                manager
                not in registry._attach_pending  # type: ignore[attr-defined]
                for manager in ordered
            )
        )
        assert ordered[0].verifier_calls[-1][1] == "old-generation"
        assert ordered[1].verifier_calls[-1][1] == "old-generation"
        assert not ordered[0].verifier_calls[-1][0].enforce
    finally:
        old_profile.close()
        new_profile.close()
        await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_activation_retries_prior_verifier_when_rollback_degrades() -> None:
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=1.0,
    )
    managers = [_Manager(), _Manager()]
    for manager in managers:
        await registry.register_manager(manager)
    old_profile = _profile("old")
    new_profile = _profile("new")
    try:
        assert await registry.activate(old_profile, "old-generation")
        ordered = tuple(registry._managers)  # type: ignore[attr-defined]
        ordered[0].verifier_outcomes.extend([True, False, True])
        ordered[1].verifier_outcomes.append(False)

        assert not await registry.activate(new_profile, "new-generation")

        assert ordered[0] in registry._attach_pending  # type: ignore[attr-defined]
        assert ordered[0] not in registry._detach_pending  # type: ignore[attr-defined]
        await _wait_until(
            lambda: ordered[0]
            not in registry._attach_pending  # type: ignore[attr-defined]
        )
        assert ordered[0].verifier_calls[-1][1] == "old-generation"
        assert ordered[0].verifier_calls[-1][0] is not None
    finally:
        old_profile.close()
        new_profile.close()
        await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_activation_hands_blocked_rollback_to_watchdog() -> None:
    class BlockingRollbackManager(_Manager):
        def __init__(self) -> None:
            super().__init__()
            self.block_generation: str | None = None
            self.rollback_started = asyncio.Event()
            self.rollback_release = asyncio.Event()

        async def set_speaker_verifier_factory(
            self,
            factory: _Factory | None,
            *,
            activation_generation: str,
        ) -> bool:
            if activation_generation == self.block_generation:
                self.rollback_started.set()
                # Intentionally ignore cancellation to model a blocking rollback;
                # cleanup must set rollback_release before closing the registry.
                while not self.rollback_release.is_set():
                    try:
                        await self.rollback_release.wait()
                    except asyncio.CancelledError:
                        continue
            return await super().set_speaker_verifier_factory(
                factory,
                activation_generation=activation_generation,
            )

    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=1.0,
    )
    managers = [BlockingRollbackManager(), BlockingRollbackManager()]
    for manager in managers:
        await registry.register_manager(manager)
    old_profile = _profile("old")
    new_profile = _profile("new")
    try:
        assert await registry.activate(old_profile, "old-generation")
        ordered = tuple(registry._managers)  # type: ignore[attr-defined]
        changed = ordered[0]
        failed = ordered[1]
        changed.block_generation = "old-generation"
        failed.verifier_outcomes.append(False)

        loop = asyncio.get_running_loop()
        started_at = loop.time()
        assert not await registry.activate(new_profile, "new-generation")
        assert loop.time() - started_at < 0.5
        assert changed in registry._attach_pending  # type: ignore[attr-defined]

        await asyncio.wait_for(changed.rollback_started.wait(), timeout=0.5)
        changed.rollback_release.set()
        await _wait_until(
            lambda: changed not in registry._attach_pending  # type: ignore[attr-defined]
        )
    finally:
        old_profile.close()
        new_profile.close()
        for manager in managers:
            manager.rollback_release.set()
        await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_attach_closes_unadopted_factory_material() -> None:
    class CancellingManager(_Manager):
        async def set_speaker_verifier_factory(
            self,
            factory: _Factory | None,
            *,
            activation_generation: str,
        ) -> bool:
            self.verifier_calls.append((factory, activation_generation))
            raise asyncio.CancelledError

    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = CancellingManager()
    await registry.register_manager(manager)
    profile = _profile("profile")
    try:
        assert not await registry.activate(profile, "generation")
    finally:
        profile.close()

    factory = manager.verifier_calls[-1][0]
    assert factory is not None and factory.closed


@pytest.mark.unit
@pytest.mark.asyncio
async def test_attach_watchdog_propagates_inflight_cancellation() -> None:
    class BlockingRetryManager(_Manager):
        def __init__(self) -> None:
            super().__init__()
            self.retry_started = asyncio.Event()

        async def set_speaker_verifier_factory(
            self,
            factory: _Factory | None,
            *,
            activation_generation: str,
        ) -> bool:
            self.verifier_calls.append((factory, activation_generation))
            if factory is None:
                return True
            if len(self.verifier_calls) == 1:
                factory.close()
                return False
            self.retry_started.set()
            await asyncio.Event().wait()
            return True

    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=1.0,
    )
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()
    manager = BlockingRetryManager()
    assert not await registry.register_manager(manager)
    await asyncio.wait_for(manager.retry_started.wait(), 1)

    watchdog = registry._attach_retry_task  # type: ignore[attr-defined]
    assert watchdog is not None
    watchdog.cancel()
    with pytest.raises(asyncio.CancelledError):
        await watchdog

    factory = manager.verifier_calls[-1][0]
    assert factory is not None and factory.closed
    assert registry._attach_retry_task is None  # type: ignore[attr-defined]
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_detach_clears_current_and_future_factory() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    current = _Manager()
    await registry.register_manager(current)
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "active-generation")
    finally:
        profile.close()

    assert await registry.activate(None, "detach-generation")
    assert current.verifier_calls[-1] == (None, "detach-generation")
    future = _Manager()
    assert await registry.register_manager(future)
    assert future.verifier_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_required_unavailable_authority_reaches_current_and_future_managers() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    current = _Manager()
    await registry.register_manager(current)

    assert (
        await registry.activate(
            None,
            "required-unavailable",
            activation_required=True,
        )
        is VoiceIdentityActivationResult.READY
    )
    assert current.verifier_calls[-1] == (None, "required-unavailable")
    assert current.activation_required is True

    future = _Manager()
    assert await registry.register_manager(future)
    assert future.verifier_calls[-1][0] is None
    assert future.activation_required is True

    assert await registry.activate(
        None,
        "explicitly-disabled",
        activation_required=False,
    )
    assert current.activation_required is False
    assert future.activation_required is False
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_type",
    [
        SecureStorageUnavailableError,
        VoiceIdentityProfileIncompatibleError,
        VoiceIdentityProfileCorruptError,
    ],
)
@pytest.mark.parametrize("route_mode", ["native", "independent"])
async def test_startup_profile_failure_blocks_actual_core_downstream_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_type,
    route_mode: str,
) -> None:
    from tests.unit.test_core_independent_asr import _Runtime
    from tests.unit.voice_identity_service.test_profile_store import (
        _TestKeyProtector,
    )

    profile_store = VoiceIdentityProfileStore(
        tmp_path / "profile.bin",
        key_protector=_TestKeyProtector(),
    )

    async def fail_load():
        raise failure_type("profile unavailable")

    monkeypatch.setattr(profile_store, "aload", fail_load)

    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    preference_store = VoiceIdentityPreferenceStore(tmp_path / "preference.json")
    await preference_store.asave(True)
    suppression = VoiceInputSuppressionController(
        registry.suppress,
        registry.restore,
        default_ttl_seconds=30.0,
        hard_ttl_seconds=60.0,
    )
    service = VoiceIdentityService(
        profile_store,
        preference_store,
        suppression,
        lambda: object(),  # Enrollment is outside this startup regression.
        registry.activate,
        runtime_mode="enforce",
    )
    runtime = _Runtime()
    runtime.session.stream_audio = AsyncMock()
    runtime._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
    )
    try:
        status = await service.initialize()
        assert status.state.requested_enabled is True
        assert status.state.effective_enabled is False
        assert await registry.register_manager(runtime)
        runtime._set_microphone_route(route_mode)

        assert await runtime._route_microphone_audio(
            b"\x01\x00" * 160,
            sample_rate_hz=16_000,
        )

        runtime.session.stream_audio.assert_not_awaited()
        runtime._asr_runtime.submit.assert_not_awaited()
        assert runtime._voice_session_activation_required is True
        assert runtime._voice_session_activation_factory is None

        await service.set_filter(False)
        assert runtime._voice_session_activation_required is False
        assert await runtime._route_microphone_audio(
            b"\x02\x00" * 160,
            sample_rate_hz=16_000,
        )
        if route_mode == "native":
            runtime.session.stream_audio.assert_awaited_once()
            runtime._asr_runtime.submit.assert_not_awaited()
        else:
            runtime.session.stream_audio.assert_not_awaited()
            runtime._asr_runtime.submit.assert_awaited_once()
    finally:
        await service.close()
        await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("route_mode", ["native", "independent"])
@pytest.mark.parametrize("initial_nr", [True, False])
async def test_failed_dsp_construction_blocks_pcm_until_successful_retry(
    monkeypatch, route_mode: str, initial_nr: bool,
) -> None:
    import main_logic.core.asr_runtime as core_runtime
    import main_routers.config_router.preferences as preferences
    from main_logic.voice_input.activation import ActivationState
    from tests.unit.test_core_independent_asr import _CoreActivationFactory, _Runtime

    # Use real Core, Registry and settings sequencing. Only model inference and
    # the pipeline constructor failure are injected.
    def factory_for(_manager, _profile, *, activation_generation, enforce,
                    noise_reduction_enabled=None):
        factory = _CoreActivationFactory()
        factory.activation_generation = activation_generation
        factory.noise_reduction_enabled = noise_reduction_enabled
        return factory

    monkeypatch.setattr(runtime_module, "OwnerVoiceSessionActivationFactory", factory_for)
    runtime = _Runtime()
    runtime.is_active = True
    runtime._set_microphone_route(route_mode)
    runtime.session.stream_audio = AsyncMock()
    runtime._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED),
    )
    await runtime.apply_voice_input_noise_reduction(initial_nr)
    original = runtime._voice_input_audio_pipeline
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    await registry.register_manager(runtime)
    profile = _profile("profile")
    reconciled: list[VoiceIdentityActivationResult] = []
    requested_nr = not initial_nr

    async def prepare(enabled: bool) -> bool:
        assert enabled is requested_nr
        return bool(await registry.activate(
            None, "dsp-transition", activation_required=True,
        ))

    async def reconcile(enabled: bool, *, runtime_ready: bool) -> None:
        assert runtime_ready is True
        reconciled.append(await registry.activate(
            profile, "new-authority", activation_required=True,
            noise_reduction_enabled=enabled, allow_partial=True,
        ))

    monkeypatch.setattr(preferences, "get_session_manager", lambda: {"test": runtime})
    monkeypatch.setattr(
        preferences, "aload_global_conversation_settings_snapshot",
        AsyncMock(return_value=SimpleNamespace(
            settings={"noiseReductionEnabled": requested_nr},
        )),
    )
    preferences.configure_voice_identity_audio_contract_callbacks(
        prepare=prepare, reconcile=reconcile,
    )
    try:
        assert await registry.activate(
            profile, "old-authority", activation_required=True,
            noise_reduction_enabled=initial_nr,
        )
        with monkeypatch.context() as failing:
            from unittest.mock import Mock

            failing.setattr(
                core_runtime, "VoiceInputAudioPipeline",
                Mock(side_effect=RuntimeError("pipeline construction failed")),
            )
            await preferences._apply_noise_reduction_if_current(requested_nr)

        assert reconciled == [VoiceIdentityActivationResult.RUNTIME_DEGRADED]
        assert runtime._voice_input_audio_pipeline is original
        assert runtime._voice_input_noise_reduction_enabled is initial_nr
        assert runtime._voice_session_activation_required is True
        assert runtime._voice_session_activation_factory is None
        assert runtime in registry._attach_pending
        # More than a full activation checkpoint must still produce no output.
        for _ in range(20):
            await runtime._route_microphone_audio(
                b"\xd0\x07" * 1_600, sample_rate_hz=16_000,
            )
        runtime.session.stream_audio.assert_not_awaited()
        runtime._asr_runtime.submit.assert_not_awaited()
        assert runtime._voice_session_activation_runtime is None

        # The same persisted preference can be retried; reopening starts with
        # empty evidence rather than forwarding any PCM seen during failure.
        await preferences._apply_noise_reduction_if_current(requested_nr)
        assert reconciled[-1] is VoiceIdentityActivationResult.READY
        assert runtime._voice_input_audio_pipeline.nr_enabled is requested_nr
        assert runtime._voice_input_noise_reduction_enabled is requested_nr
        assert runtime not in registry._attach_pending
        with pytest.raises(RuntimeError, match="VOICE_AUDIO_PIPELINE_CLOSED"):
            await original.process(b"\x01\x00" * 160, sample_rate_hz=16_000)
        # This direct post-DSP input models a successful retry, including the
        # actual native capability evidence when the new contract requires it.
        await runtime._route_microphone_audio(
            bytes(320), sample_rate_hz=16_000,
            rnnoise_available=requested_nr,
        )
        await _wait_until(lambda: (
            runtime._voice_session_activation_runtime is not None
            and runtime._voice_session_activation_runtime.state is ActivationState.WAITING
        ))
        factory = runtime._voice_session_activation_factory
        assert factory.scorers[0].calls == 0
        runtime.session.stream_audio.assert_not_awaited()
        runtime._asr_runtime.submit.assert_not_awaited()
    finally:
        preferences.configure_voice_identity_audio_contract_callbacks()
        await registry.close()
        profile.close()
        await runtime._voice_input_audio_pipeline.close()
        await asyncio.gather(*runtime._core_asr_cleanup_tasks, return_exceptions=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dsp_reconcile_restores_ready_manager_and_keeps_mismatch_blocked() -> None:
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=1.0,
    )
    ready = _Manager()
    failed = _Manager()
    ready._voice_input_noise_reduction_enabled = True
    failed._voice_input_noise_reduction_enabled = True
    await registry.register_manager(ready)
    await registry.register_manager(failed)
    profile = _profile("profile")
    try:
        assert await registry.activate(
            profile,
            "old-authority",
            activation_required=True,
            noise_reduction_enabled=True,
        )
        assert await registry.activate(
            None,
            "dsp-transition",
            activation_required=True,
        )

        ready._voice_input_noise_reduction_enabled = False
        result = await registry.activate(
            profile,
            "new-authority",
            activation_required=True,
            noise_reduction_enabled=False,
            allow_partial=True,
        )

        assert result is VoiceIdentityActivationResult.RUNTIME_DEGRADED
        assert ready.verifier_calls[-1][1] == "new-authority"
        assert ready.verifier_calls[-1][0] is not None
        assert ready.activation_required is True
        assert failed.verifier_calls[-1] == (None, "dsp-transition")
        assert failed.activation_required is True
        assert ready not in registry._attach_pending  # type: ignore[attr-defined]
        assert failed in registry._attach_pending  # type: ignore[attr-defined]

        failed._voice_input_noise_reduction_enabled = False
        await _wait_until(
            lambda: failed not in registry._attach_pending  # type: ignore[attr-defined]
        )
        assert failed.verifier_calls[-1][1] == "new-authority"
        assert failed.verifier_calls[-1][0] is not None
    finally:
        profile.close()
        await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_initial_contract_mismatch_is_synchronously_fail_closed() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = _Manager()
    manager._voice_input_noise_reduction_enabled = True
    await registry.register_manager(manager)
    profile = _profile("profile")
    try:
        result = await registry.activate(
            profile,
            "mismatched-authority",
            activation_required=True,
            noise_reduction_enabled=False,
        )

        assert result is VoiceIdentityActivationResult.RUNTIME_DEGRADED
        assert manager.require_calls == ["mismatched-authority"]
        assert manager.activation_required is True
        assert manager.activation_degraded is True
        assert all(factory is None for factory, _generation in manager.verifier_calls)
    finally:
        profile.close()
        await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_required_registration_keeps_new_manager_fail_closed() -> None:
    class BlockingManager(_Manager):
        def __init__(self) -> None:
            super().__init__()
            self.block_once = True

        async def set_voice_session_activation_factory(
            self,
            factory,
            *,
            activation_generation: str,
            activation_required: bool = False,
            expected_policy_revision: int | None = None,
        ):
            if self.block_once:
                self.block_once = False
                await asyncio.Event().wait()
            return await super().set_voice_session_activation_factory(
                factory,
                activation_generation=activation_generation,
                activation_required=activation_required,
                expected_policy_revision=expected_policy_revision,
            )

    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    assert await registry.activate(
        None,
        "required-empty",
        activation_required=True,
    )
    manager = BlockingManager()
    registration = asyncio.create_task(registry.register_manager(manager))
    await asyncio.sleep(0)
    registration.cancel()
    with pytest.raises(asyncio.CancelledError):
        await registration

    assert manager.activation_required is True
    assert manager.activation_degraded is True
    assert manager in registry._detach_pending  # type: ignore[attr-defined]
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_new_required_intent_fences_older_factory_commit() -> None:
    class BlockingManager(_Manager):
        def __init__(self) -> None:
            super().__init__()
            self.first_attach_entered = asyncio.Event()
            self.release_first_attach = asyncio.Event()
            self.block_once = True

        async def set_voice_session_activation_factory(
            self,
            factory,
            *,
            activation_generation: str,
            activation_required: bool = False,
            expected_policy_revision: int | None = None,
        ):
            if factory is not None and self.block_once:
                self.block_once = False
                self.first_attach_entered.set()
                await self.release_first_attach.wait()
            return await super().set_voice_session_activation_factory(
                factory,
                activation_generation=activation_generation,
                activation_required=activation_required,
                expected_policy_revision=expected_policy_revision,
            )

    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = BlockingManager()
    await registry.register_manager(manager)
    old_profile = _profile("old-profile")
    new_profile = _profile("new-profile")
    try:
        old_activation = asyncio.create_task(
            registry.activate(
                old_profile,
                "old-authority",
                activation_required=True,
            )
        )
        await manager.first_attach_entered.wait()
        new_activation = asyncio.create_task(
            registry.activate(
                new_profile,
                "new-authority",
                activation_required=True,
            )
        )
        await asyncio.sleep(0)
        manager.release_first_attach.set()

        assert (
            await old_activation
            is VoiceIdentityActivationResult.RUNTIME_DEGRADED
        )
        assert await new_activation is VoiceIdentityActivationResult.READY
        assert all(
            generation != "old-authority"
            for _factory, generation in manager.verifier_calls
        )
        assert manager.verifier_calls[-1][1] == "new-authority"
        assert manager.activation_degraded is False
    finally:
        old_profile.close()
        new_profile.close()
        await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_attach_watchdog_cannot_restore_old_factory_over_new_intent() -> None:
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=1.0,
    )
    manager = _Manager()
    await registry.register_manager(manager)
    old_profile = _profile("old-profile")
    new_profile = _profile("new-profile")
    try:
        assert await registry.activate(
            old_profile,
            "old-authority",
            activation_required=True,
        )
        manager.verifier_calls.clear()
        await registry._lock.acquire()  # type: ignore[attr-defined]
        registry._attach_pending.add(manager)  # type: ignore[attr-defined]
        registry._ensure_attach_watchdog()  # type: ignore[attr-defined]
        await asyncio.sleep(0.02)
        replacement = asyncio.create_task(
            registry.activate(
                new_profile,
                "new-authority",
                activation_required=True,
            )
        )
        await asyncio.sleep(0)
        registry._lock.release()  # type: ignore[attr-defined]

        assert await replacement is VoiceIdentityActivationResult.READY
        assert all(
            generation != "old-authority"
            for _factory, generation in manager.verifier_calls
        )
        assert manager.verifier_calls[-1][1] == "new-authority"
    finally:
        if registry._lock.locked():  # type: ignore[attr-defined]
            registry._lock.release()  # type: ignore[attr-defined]
        old_profile.close()
        new_profile.close()
        await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_partial_reconcile_commits_authority_and_retries_pending() -> None:
    class BlockingManager(_Manager):
        def __init__(self) -> None:
            super().__init__()
            self.attach_started = asyncio.Event()
            self.block_once = True

        async def set_voice_session_activation_factory(
            self,
            factory,
            *,
            activation_generation: str,
            activation_required: bool = False,
            expected_policy_revision: int | None = None,
        ):
            if factory is not None and self.block_once:
                self.block_once = False
                self.attach_started.set()
                await asyncio.Event().wait()
            return await super().set_voice_session_activation_factory(
                factory,
                activation_generation=activation_generation,
                activation_required=activation_required,
                expected_policy_revision=expected_policy_revision,
            )

    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=1.0,
    )
    manager = BlockingManager()
    manager._voice_input_noise_reduction_enabled = False
    await registry.register_manager(manager)
    profile = _profile("profile")
    try:
        reconciliation = asyncio.create_task(
            registry.activate(
                profile,
                "new-authority",
                activation_required=True,
                noise_reduction_enabled=False,
                allow_partial=True,
            )
        )
        await manager.attach_started.wait()
        reconciliation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reconciliation

        activation = registry._activation  # type: ignore[attr-defined]
        assert activation is not None
        assert activation.generation == "new-authority"
        assert manager in registry._attach_pending  # type: ignore[attr-defined]
        await _wait_until(
            lambda: manager not in registry._attach_pending  # type: ignore[attr-defined]
        )
        assert manager.verifier_calls[-1][1] == "new-authority"
        assert manager.verifier_calls[-1][0] is not None
    finally:
        profile.close()
        await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_detach_never_restores_old_activation() -> None:
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=0.1,
    )
    manager = _Manager()
    await registry.register_manager(manager)
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "active-generation")
    finally:
        profile.close()
    old_activation = registry._activation  # type: ignore[attr-defined]
    manager.verifier_outcomes.append(False)

    assert not await registry.activate(None, "detach-generation")

    assert registry._activation is None  # type: ignore[attr-defined]
    assert old_activation.profile.closed
    assert manager.verifier_calls[-1] == (None, "detach-generation")
    assert registry._detach_pending[manager] == "detach-generation"  # type: ignore[attr-defined]
    future = _Manager()
    assert await registry.register_manager(future)
    assert future.verifier_calls == []
    await _wait_until(
        lambda: manager
        not in registry._detach_pending  # type: ignore[attr-defined]
    )
    assert all(
        generation != "active-generation"
        for _factory, generation in manager.verifier_calls[1:]
    )
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_detach_defers_current_and_remaining_managers() -> None:
    class BlockingDetachManager(_Manager):
        def __init__(self) -> None:
            super().__init__()
            self.detach_started = asyncio.Event()
            self.detach_release = asyncio.Event()

        async def set_speaker_verifier_factory(
            self,
            factory: _Factory | None,
            *,
            activation_generation: str,
        ) -> bool:
            if factory is None:
                self.verifier_calls.append((factory, activation_generation))
                self.detach_started.set()
                await self.detach_release.wait()
            return await super().set_speaker_verifier_factory(
                factory,
                activation_generation=activation_generation,
            )

    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=10.0,
    )
    managers = [BlockingDetachManager(), BlockingDetachManager()]
    for manager in managers:
        await registry.register_manager(manager)
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "active-generation")
    finally:
        profile.close()
    ordered = tuple(registry._managers)  # type: ignore[attr-defined]

    detach_task = asyncio.create_task(
        registry.activate(None, "detach-generation")
    )
    await asyncio.wait_for(ordered[0].detach_started.wait(), 1.0)
    detach_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(detach_task, 1.0)
    assert not ordered[1].detach_started.is_set()
    assert registry._detach_pending == {  # type: ignore[attr-defined]
        ordered[0]: "detach-generation",
        ordered[1]: "detach-generation",
    }
    for manager in ordered:
        manager.detach_release.set()
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_suppression_and_restore_apply_to_all_managers() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    managers = [_Manager(), _Manager()]
    for manager in managers:
        await registry.register_manager(manager)

    await registry.suppress("voice_identity_enrollment")
    await registry.suppress("voice_identity_enrollment")
    await registry.restore("voice_identity_enrollment")

    for manager in managers:
        assert manager.suppression_calls == [
            ("voice_identity_enrollment", True),
            ("voice_identity_enrollment", False),
        ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transient_restore_failure_never_leaves_registry_gate() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    failing = _Manager()
    failing.restore_failures = 1
    await registry.register_manager(failing)
    await registry.suppress("voice_identity_enrollment")

    await registry.restore("voice_identity_enrollment")

    assert failing.suppression_calls[-2:] == [
        ("voice_identity_enrollment", False),
        ("voice_identity_enrollment", False),
    ]
    assert not registry._suppressed  # type: ignore[attr-defined]
    assert not registry._restore_pending  # type: ignore[attr-defined]
    replacement = _Manager()
    await registry.register_manager(replacement)
    assert replacement.suppression_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_restore_watchdog_retries_pending_manager() -> None:
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=2.0,
    )
    failing = _Manager()
    failing.restore_failures = 3
    await registry.register_manager(failing)
    await registry.suppress("voice_identity_enrollment")

    await registry.restore("voice_identity_enrollment")
    assert registry._restore_pending  # type: ignore[attr-defined]
    await _wait_until(
        lambda: (
            not registry._restore_pending  # type: ignore[attr-defined]
            and registry._restore_retry_task is None
        )  # type: ignore[attr-defined]
    )

    assert not registry._restore_pending  # type: ignore[attr-defined]
    assert registry._restore_retry_task is None  # type: ignore[attr-defined]
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bounded_restore_allows_second_attempt_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS",
        0.2,
    )

    class FirstAttemptBlocks(_Manager):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def set_voice_input_suppressed(
            self,
            reason: str,
            *,
            suppressed: bool,
        ) -> None:
            self.calls += 1
            self.suppression_calls.append((reason, suppressed))
            if self.calls == 1:
                await asyncio.Event().wait()

    manager = FirstAttemptBlocks()

    restored = await OwnerVoiceRuntimeRegistry._restore_manager_bounded(
        manager,
        "voice_identity_enrollment",
    )

    assert restored
    assert manager.calls == 2
    assert manager.suppression_calls == [
        ("voice_identity_enrollment", False),
        ("voice_identity_enrollment", False),
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_restore_cannot_gate_replacement_manager() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    cancelled = _Manager()
    await registry.register_manager(cancelled)
    await registry.suppress("voice_identity_enrollment")
    cancelled.cancel_restore = True

    with pytest.raises(asyncio.CancelledError):
        await registry.restore("voice_identity_enrollment")

    assert not registry._suppressed  # type: ignore[attr-defined]
    replacement = _Manager()
    await registry.register_manager(replacement)
    assert replacement.suppression_calls == []
    cancelled.cancel_restore = False
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manager_replacement_gets_current_state_and_old_is_detached() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    old = _Manager()
    await registry.register_manager(old)
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()
    await registry.suppress("voice_identity_enrollment")

    replacement = _Manager()
    assert await registry.register_manager(replacement)
    assert replacement.suppression_calls == [("voice_identity_enrollment", True)]
    assert replacement.verifier_calls[-1][1] == "generation"

    await registry.unregister_manager(old)
    assert old.verifier_calls[-1][0] is None
    assert old.suppression_calls[-1] == ("voice_identity_enrollment", False)

    new_profile = _profile("new")
    try:
        assert await registry.activate(new_profile, "new-generation")
    finally:
        new_profile.close()
    assert replacement.verifier_calls[-1][1] == "new-generation"
    assert old.verifier_calls[-1][1] != "new-generation"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unregister_cancellation_records_cleanup_before_propagating() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = _Manager()
    await registry.register_manager(manager)
    await registry.suppress("voice_identity_enrollment")
    manager.verifier_outcomes.append(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await registry.unregister_manager(manager)

    assert manager in registry._detach_pending  # type: ignore[attr-defined]
    assert manager.suppression_calls[-1] == (
        "voice_identity_enrollment",
        False,
    )
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unregister_bounds_pending_restore_and_starts_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS",
        0.02,
    )
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.5,
        restore_retry_timeout_seconds=1.0,
    )
    manager = _Manager()
    await registry.register_manager(manager)
    registry._restore_pending.add(manager)  # type: ignore[attr-defined]
    restore_started = asyncio.Event()

    async def never_restore(reason: str, *, suppressed: bool) -> None:
        manager.suppression_calls.append((reason, suppressed))
        if not suppressed:
            restore_started.set()
            await asyncio.Event().wait()

    manager.set_voice_input_suppressed = never_restore  # type: ignore[method-assign]

    await asyncio.wait_for(registry.unregister_manager(manager), timeout=0.2)

    assert restore_started.is_set()
    assert manager in registry._restore_pending  # type: ignore[attr-defined]
    assert registry._restore_retry_task is not None  # type: ignore[attr-defined]
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_register_bounds_pending_restore_and_starts_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS",
        0.02,
    )
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.5,
        restore_retry_timeout_seconds=1.0,
    )
    manager = _Manager()
    registry._restore_pending.add(manager)  # type: ignore[attr-defined]
    restore_started = asyncio.Event()

    async def never_restore(reason: str, *, suppressed: bool) -> None:
        manager.suppression_calls.append((reason, suppressed))
        if not suppressed:
            restore_started.set()
            await asyncio.Event().wait()

    manager.set_voice_input_suppressed = never_restore  # type: ignore[method-assign]

    result = await asyncio.wait_for(registry.register_manager(manager), timeout=0.2)

    assert result is VoiceIdentityActivationResult.RUNTIME_DEGRADED
    assert restore_started.is_set()
    assert manager in registry._restore_pending  # type: ignore[attr-defined]
    assert registry._restore_retry_task is not None  # type: ignore[attr-defined]
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_attach_failure_keeps_manager_registered_for_recovery() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    active_profile = _profile("profile")
    try:
        assert await registry.activate(active_profile, "generation")
    finally:
        active_profile.close()
    await registry.suppress("voice_identity_enrollment")
    manager = _Manager()
    manager.verifier_outcomes.append(False)

    assert not await registry.register_manager(manager)

    assert manager in registry._managers  # type: ignore[attr-defined]
    assert manager.suppression_calls == [
        ("voice_identity_enrollment", True),
    ]
    replacement_profile = _profile("replacement")
    try:
        assert await registry.activate(replacement_profile, "replacement-generation")
    finally:
        replacement_profile.close()
    assert manager.verifier_calls[-1][1] == "replacement-generation"
    await registry.restore("voice_identity_enrollment")
    assert manager.suppression_calls[-1] == (
        "voice_identity_enrollment",
        False,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_suppression_rollback_gets_watchdog_retry() -> None:
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=2.0,
    )
    changed = _Manager()
    failing = _Manager()
    await registry.register_manager(changed)
    await registry.register_manager(failing)
    ordered = tuple(registry._managers)  # type: ignore[attr-defined]
    ordered[1].suppress_failure = True
    ordered[0].restore_failures = 3

    with pytest.raises(RuntimeError, match="suppression failed"):
        await registry.suppress("voice_identity_enrollment")

    assert registry._restore_retry_task is not None  # type: ignore[attr-defined]
    await _wait_until(
        lambda: (
            not registry._restore_pending  # type: ignore[attr-defined]
            and registry._restore_retry_task is None
        )  # type: ignore[attr-defined]
    )
    assert not registry._restore_pending  # type: ignore[attr-defined]
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_watchdog_exhaustion_emits_restore_and_detach_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(
        runtime_module.logger,
        "warning",
        lambda message, *_args, **_kwargs: warnings.append(message),
    )
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=0.1,
    )
    restore_manager = _Manager()
    detach_manager = _Manager()
    await registry.register_manager(restore_manager)
    await registry.register_manager(detach_manager)
    await registry.suppress("voice_identity_enrollment")

    async def never_restore(reason: str, *, suppressed: bool) -> None:
        restore_manager.suppression_calls.append((reason, suppressed))
        if not suppressed:
            raise RuntimeError("restore remains unavailable")

    restore_manager.set_voice_input_suppressed = never_restore  # type: ignore[method-assign]
    await registry.restore("voice_identity_enrollment")
    restore_task = registry._restore_retry_task  # type: ignore[attr-defined]
    assert restore_task is not None

    async def never_detach(_factory, *, activation_generation: str) -> bool:
        del activation_generation
        return False

    detach_manager.set_speaker_verifier_factory = never_detach  # type: ignore[method-assign]
    await registry.unregister_manager(detach_manager)
    detach_task = registry._detach_retry_task  # type: ignore[attr-defined]
    assert detach_task is not None

    await asyncio.gather(restore_task, detach_task)

    assert any("restore watchdog exhausted" in message for message in warnings)
    assert any("detach watchdog exhausted" in message for message in warnings)
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("watchdog_kind", ["attach", "restore", "detach"])
async def test_watchdog_bounds_never_returning_manager_call(
    watchdog_kind: str,
) -> None:
    class BlockingManager(_Manager):
        def __init__(self) -> None:
            super().__init__()
            self.block_attach = False
            self.block_restore = False
            self.block_detach = False
            self.call_started = asyncio.Event()

        async def set_speaker_verifier_factory(
            self,
            factory: _Factory | None,
            *,
            activation_generation: str,
        ) -> bool:
            if (factory is None and self.block_detach) or (
                factory is not None and self.block_attach
            ):
                self.call_started.set()
                await asyncio.Event().wait()
            return await super().set_speaker_verifier_factory(
                factory,
                activation_generation=activation_generation,
            )

        async def set_voice_input_suppressed(
            self,
            reason: str,
            *,
            suppressed: bool,
        ) -> None:
            if not suppressed and self.block_restore:
                self.call_started.set()
                await asyncio.Event().wait()
            await super().set_voice_input_suppressed(reason, suppressed=suppressed)

    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=0.05,
    )
    manager = BlockingManager()

    if watchdog_kind == "attach":
        profile = _profile("profile")
        try:
            assert await registry.activate(profile, "generation")
        finally:
            profile.close()
        manager.verifier_outcomes.append(False)
        assert not await registry.register_manager(manager)
        manager.block_attach = True
        watchdog = registry._attach_retry_task  # type: ignore[attr-defined]
    elif watchdog_kind == "restore":
        await registry.register_manager(manager)
        await registry.suppress("voice_identity_enrollment")
        manager.restore_failures = 2
        await registry.restore("voice_identity_enrollment")
        manager.block_restore = True
        watchdog = registry._restore_retry_task  # type: ignore[attr-defined]
    else:
        await registry.register_manager(manager)
        manager.verifier_outcomes.append(False)
        await registry.unregister_manager(manager)
        manager.block_detach = True
        watchdog = registry._detach_retry_task  # type: ignore[attr-defined]

    assert watchdog is not None
    await asyncio.wait_for(manager.call_started.wait(), 0.5)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    await asyncio.wait_for(watchdog, 0.5)
    assert loop.time() - started_at < 0.2
    assert watchdog.done()

    manager.block_attach = False
    manager.block_restore = False
    manager.block_detach = False
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_late_registration_timeout_transfers_to_attach_watchdog() -> None:
    class BlockingAttachManager(_Manager):
        async def set_speaker_verifier_factory(
            self,
            factory: _Factory | None,
            *,
            activation_generation: str,
        ) -> bool | VoiceIdentityActivationResult:
            if factory is not None:
                await asyncio.Event().wait()
            return await super().set_speaker_verifier_factory(
                factory,
                activation_generation=activation_generation,
            )

    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=0.05,
    )
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()
    manager = BlockingAttachManager()

    result = await registry.register_manager(manager)

    assert result is VoiceIdentityActivationResult.RUNTIME_DEGRADED
    assert manager in registry._attach_pending  # type: ignore[attr-defined]
    assert registry._attach_retry_task is not None  # type: ignore[attr-defined]

    async def verifier_success(
        factory: _Factory | None,
        *,
        activation_generation: str,
    ) -> bool:
        del factory, activation_generation
        return True

    manager.set_speaker_verifier_factory = verifier_success  # type: ignore[method-assign]
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unregister_timeout_transfers_to_detach_watchdog() -> None:
    class BlockingDetachManager(_Manager):
        async def set_speaker_verifier_factory(
            self,
            factory: _Factory | None,
            *,
            activation_generation: str,
        ) -> bool | VoiceIdentityActivationResult:
            if factory is None:
                await asyncio.Event().wait()
            return await super().set_speaker_verifier_factory(
                factory,
                activation_generation=activation_generation,
            )

    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=0.05,
    )
    manager = BlockingDetachManager()
    await registry.register_manager(manager)

    await registry.unregister_manager(manager)

    assert manager in registry._detach_pending  # type: ignore[attr-defined]
    assert registry._detach_retry_task is not None  # type: ignore[attr-defined]

    async def verifier_success(
        factory: _Factory | None,
        *,
        activation_generation: str,
    ) -> bool:
        del factory, activation_generation
        return True

    manager.set_speaker_verifier_factory = verifier_success  # type: ignore[method-assign]
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("watchdog_kind", ["attach", "restore", "detach"])
async def test_watchdog_retries_manager_originated_cancellation(
    watchdog_kind: str,
) -> None:
    class CancellingManager(_Manager):
        def __init__(self) -> None:
            super().__init__()
            self.cancel_attach = False
            self.cancel_restore_watchdog = False
            self.cancel_detach = False
            self.cancelled_calls = 0
            self.call_started = asyncio.Event()

        async def set_speaker_verifier_factory(
            self,
            factory: _Factory | None,
            *,
            activation_generation: str,
        ) -> bool:
            if (factory is not None and self.cancel_attach) or (
                factory is None and self.cancel_detach
            ):
                self.cancelled_calls += 1
                self.call_started.set()
                raise asyncio.CancelledError("manager-originated")
            return await super().set_speaker_verifier_factory(
                factory,
                activation_generation=activation_generation,
            )

        async def set_voice_input_suppressed(
            self,
            reason: str,
            *,
            suppressed: bool,
        ) -> None:
            if not suppressed and self.cancel_restore_watchdog:
                self.cancelled_calls += 1
                self.call_started.set()
                raise asyncio.CancelledError("manager-originated")
            await super().set_voice_input_suppressed(reason, suppressed=suppressed)

    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=0.05,
    )
    manager = CancellingManager()

    if watchdog_kind == "attach":
        profile = _profile("profile")
        try:
            assert await registry.activate(profile, "generation")
        finally:
            profile.close()
        manager.verifier_outcomes.append(False)
        assert not await registry.register_manager(manager)
        manager.cancel_attach = True
        watchdog = registry._attach_retry_task  # type: ignore[attr-defined]
    elif watchdog_kind == "restore":
        await registry.register_manager(manager)
        await registry.suppress("voice_identity_enrollment")
        manager.restore_failures = 2
        await registry.restore("voice_identity_enrollment")
        manager.cancel_restore_watchdog = True
        watchdog = registry._restore_retry_task  # type: ignore[attr-defined]
    else:
        await registry.register_manager(manager)
        manager.verifier_outcomes.append(False)
        await registry.unregister_manager(manager)
        manager.cancel_detach = True
        watchdog = registry._detach_retry_task  # type: ignore[attr-defined]

    assert watchdog is not None
    await asyncio.wait_for(manager.call_started.wait(), 0.5)
    await asyncio.wait_for(watchdog, 0.5)
    assert manager.cancelled_calls >= 2
    assert watchdog.done()

    manager.cancel_attach = False
    manager.cancel_restore_watchdog = False
    manager.cancel_detach = False
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_inflight_suppression_restores_current_manager() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = _Manager()
    manager.cancel_suppress = True
    await registry.register_manager(manager)

    with pytest.raises(asyncio.CancelledError):
        await registry.suppress("voice_identity_enrollment")

    assert manager.suppression_calls == [
        ("voice_identity_enrollment", True),
        ("voice_identity_enrollment", False),
    ]
    assert not registry._suppressed  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registration_suppression_failure_keeps_manager_for_retry() -> None:
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=2.0,
    )
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()
    await registry.suppress("voice_identity_enrollment")
    manager = _Manager()
    manager.suppress_failure = True

    with pytest.raises(RuntimeError, match="suppression failed"):
        await registry.register_manager(manager)

    assert manager in registry._managers  # type: ignore[attr-defined]
    assert manager in registry._attach_pending  # type: ignore[attr-defined]
    assert (
        registry.activation_status()
        is VoiceIdentityActivationResult.RUNTIME_DEGRADED
    )
    manager.suppress_failure = False
    await registry.restore("voice_identity_enrollment")
    await _wait_until(lambda: manager not in registry._attach_pending)  # type: ignore[attr-defined]

    assert registry.activation_status() is VoiceIdentityActivationResult.READY
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registration_suppression_timeout_keeps_manager_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS",
        0.02,
    )
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=0.5,
    )
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()
    await registry.suppress("voice_identity_enrollment")

    class BlockingSuppressManager(_Manager):
        async def set_voice_input_suppressed(
            self,
            reason: str,
            *,
            suppressed: bool,
        ) -> None:
            self.suppression_calls.append((reason, suppressed))
            if suppressed:
                await asyncio.Event().wait()
            await super().set_voice_input_suppressed(
                reason,
                suppressed=suppressed,
            )

    manager = BlockingSuppressManager()

    result = await registry.register_manager(manager)

    assert result is VoiceIdentityActivationResult.RUNTIME_DEGRADED
    assert manager in registry._managers  # type: ignore[attr-defined]
    assert manager in registry._restore_pending  # type: ignore[attr-defined]
    assert manager in registry._attach_pending  # type: ignore[attr-defined]
    assert (
        registry.activation_status()
        is VoiceIdentityActivationResult.RUNTIME_DEGRADED
    )
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_window", ["restore", "attach"])
async def test_cancelled_registration_keeps_attach_pending_for_retry(
    cancel_window: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS",
        1.0,
    )
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.05,
        restore_retry_timeout_seconds=0.5,
    )
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()

    class BlockingManager(_Manager):
        def __init__(self) -> None:
            super().__init__()
            self.block_restore = False
            self.block_attach = False
            self.block_started = asyncio.Event()

        async def set_voice_input_suppressed(
            self,
            reason: str,
            *,
            suppressed: bool,
        ) -> None:
            self.suppression_calls.append((reason, suppressed))
            if not suppressed and self.block_restore:
                self.block_restore = False
                self.block_started.set()
                await asyncio.Event().wait()
            await super().set_voice_input_suppressed(reason, suppressed=suppressed)

        async def set_speaker_verifier_factory(
            self,
            factory: _Factory | None,
            *,
            activation_generation: str,
        ) -> bool | VoiceIdentityActivationResult:
            if factory is not None and self.block_attach:
                self.block_attach = False
                self.block_started.set()
                await asyncio.Event().wait()
            return await super().set_speaker_verifier_factory(
                factory,
                activation_generation=activation_generation,
            )

    manager = BlockingManager()
    if cancel_window == "restore":
        registry._restore_pending.add(manager)  # type: ignore[attr-defined]
        manager.block_restore = True
    else:
        manager.block_attach = True

    task = asyncio.create_task(registry.register_manager(manager))
    await asyncio.wait_for(manager.block_started.wait(), 1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert manager in registry._managers  # type: ignore[attr-defined]
    assert manager in registry._attach_pending  # type: ignore[attr-defined]
    assert registry._attach_retry_task is not None  # type: ignore[attr-defined]
    if cancel_window == "restore":
        assert manager in registry._restore_pending  # type: ignore[attr-defined]
        assert registry._restore_retry_task is not None  # type: ignore[attr-defined]

    await _wait_until(
        lambda: manager not in registry._attach_pending  # type: ignore[attr-defined]
        and manager not in registry._restore_pending  # type: ignore[attr-defined]
    )
    assert registry.activation_status() is VoiceIdentityActivationResult.READY
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_registration_keeps_active_suppression_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS",
        1.0,
    )
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=1.0,
        restore_retry_timeout_seconds=2.0,
    )
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()
    await registry.suppress("voice_identity_enrollment")

    class BlockingAttachManager(_Manager):
        def __init__(self) -> None:
            super().__init__()
            self.attach_started = asyncio.Event()
            self.input_suppressed = False

        async def set_voice_input_suppressed(
            self,
            reason: str,
            *,
            suppressed: bool,
        ) -> None:
            self.input_suppressed = suppressed
            await super().set_voice_input_suppressed(reason, suppressed=suppressed)

        async def set_speaker_verifier_factory(
            self,
            factory: _Factory | None,
            *,
            activation_generation: str,
        ) -> bool | VoiceIdentityActivationResult:
            if factory is not None and not self.attach_started.is_set():
                self.attach_started.set()
                await asyncio.Event().wait()
            return await super().set_speaker_verifier_factory(
                factory,
                activation_generation=activation_generation,
            )

    manager = BlockingAttachManager()
    task = asyncio.create_task(registry.register_manager(manager))
    await asyncio.wait_for(manager.attach_started.wait(), 1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert manager.input_suppressed is True
    assert manager in registry._restore_pending  # type: ignore[attr-defined]
    assert manager in registry._attach_pending  # type: ignore[attr-defined]
    assert manager.suppression_calls == [("voice_identity_enrollment", True)]

    await registry.restore("voice_identity_enrollment")
    assert manager.input_suppressed is False
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_restore_queues_every_manager_before_retry() -> None:
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=2.0,
    )
    first = _Manager()
    second = _Manager()
    await registry.register_manager(first)
    await registry.register_manager(second)
    await registry.suppress("voice_identity_enrollment")
    first.cancel_restore = True
    second.cancel_restore = True

    with pytest.raises(asyncio.CancelledError):
        await registry.restore("voice_identity_enrollment")

    assert first in registry._restore_pending  # type: ignore[attr-defined]
    assert second in registry._restore_pending  # type: ignore[attr-defined]
    first.cancel_restore = False
    second.cancel_restore = False
    await _wait_until(lambda: not registry._restore_pending)  # type: ignore[attr-defined]
    await registry.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registry_close_bounds_blocking_detach() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = _Manager()

    async def never_detach(
        factory: _Factory | None,
        *,
        activation_generation: str,
    ) -> bool:
        del activation_generation
        if factory is None:
            await asyncio.Event().wait()
        return True

    await registry.register_manager(manager)
    manager.set_speaker_verifier_factory = never_detach  # type: ignore[method-assign]

    await asyncio.wait_for(registry.close(), 3.0)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registry_close_bounds_blocking_restore_before_detach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS",
        0.02,
    )
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = _Manager()
    restore_started = asyncio.Event()
    detach_called = asyncio.Event()

    async def blocking_restore(reason: str, *, suppressed: bool) -> None:
        manager.suppression_calls.append((reason, suppressed))
        if not suppressed:
            restore_started.set()
            await asyncio.Event().wait()

    async def bounded_detach(
        factory: _Factory | None,
        *,
        activation_generation: str,
    ) -> bool:
        del activation_generation
        if factory is None:
            detach_called.set()
        return True

    await registry.register_manager(manager)
    manager.set_voice_input_suppressed = blocking_restore  # type: ignore[method-assign]
    manager.set_speaker_verifier_factory = bounded_detach  # type: ignore[method-assign]
    await registry.suppress("voice_identity_enrollment")

    await asyncio.wait_for(registry.close(), 1.0)

    assert restore_started.is_set()
    assert detach_called.is_set()
    assert not registry._managers  # type: ignore[attr-defined]
    assert registry._activation is None  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registry_close_propagates_external_cancellation_and_cleans() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = _Manager()
    detach_started = asyncio.Event()

    async def blocking_detach(
        factory: _Factory | None,
        *,
        activation_generation: str,
    ) -> bool:
        del activation_generation
        if factory is None:
            detach_started.set()
            await asyncio.Event().wait()
        return True

    await registry.register_manager(manager)
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()
    manager.set_speaker_verifier_factory = blocking_detach  # type: ignore[method-assign]
    close_task = asyncio.create_task(registry.close())
    await asyncio.wait_for(detach_started.wait(), 1.0)

    close_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close_task

    assert not registry._managers  # type: ignore[attr-defined]
    assert registry._activation is None  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registry_close_cancellation_during_watchdog_join_still_cleans() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = _Manager()
    await registry.register_manager(manager)
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()

    retry_cancelled = asyncio.Event()
    retry_release = asyncio.Event()

    async def slow_retry_task() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            retry_cancelled.set()
            await retry_release.wait()

    retry_task = asyncio.create_task(slow_retry_task())
    registry._restore_retry_task = retry_task  # type: ignore[attr-defined]
    close_task = asyncio.create_task(registry.close())
    await asyncio.wait_for(retry_cancelled.wait(), 1.0)

    close_task.cancel()
    await asyncio.sleep(0)
    assert not close_task.done()

    retry_release.set()
    with pytest.raises(asyncio.CancelledError):
        await close_task

    assert not registry._managers  # type: ignore[attr-defined]
    assert registry._activation is None  # type: ignore[attr-defined]
    assert registry._restore_retry_task is None  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rejects_unknown_suppression_reason() -> None:
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    with pytest.raises(ValueError, match="unsupported"):
        await registry.suppress("other")


@pytest.mark.unit
def test_registry_requires_boolean_enforcement_mode() -> None:
    with pytest.raises(TypeError, match="enforce"):
        OwnerVoiceRuntimeRegistry(enforce=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="retry bounds"):
        OwnerVoiceRuntimeRegistry(
            enforce=True,
            restore_retry_interval_seconds=2.0,
            restore_retry_timeout_seconds=1.0,
        )


@pytest.mark.unit
def test_unavailable_profile_store_never_falls_back_to_plaintext(
    tmp_path: Path,
) -> None:
    store = runtime_module._UnavailableProfileStore(tmp_path / "profile")
    profile = _profile("profile")
    try:
        with pytest.raises(RuntimeError, match="secure_storage_unavailable"):
            store.load()
        with pytest.raises(RuntimeError, match="secure_storage_unavailable"):
            store.stage(profile)
        with pytest.raises(RuntimeError, match="secure_storage_unavailable"):
            store.delete()
    finally:
        profile.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_runtime_install_and_wrapper_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    installed: list[object] = []
    callback_configurations: list[tuple[object | None, object | None]] = []

    class FakeProfileStore:
        def __init__(self, _path: Path) -> None:
            raise runtime_module.SecureStorageUnavailableError(
                "secure_storage_unavailable"
            )

    class FakePreferenceStore:
        def __init__(self, path: Path) -> None:
            self.path = path

    class FakeSuppression:
        def __init__(self, suppress, restore, **kwargs) -> None:
            self.suppress = suppress
            self.restore = restore
            self.kwargs = kwargs

    class FakeService:
        def __init__(
            self,
            *args,
            runtime_mode: str,
            runtime_status_callback,
            enrollment_noise_reduction_enabled: bool,
        ) -> None:
            self.args = args
            self.runtime_mode = runtime_mode
            self.runtime_status_callback = runtime_status_callback
            self.enrollment_noise_reduction_enabled = (
                enrollment_noise_reduction_enabled
            )
            self.initialized = 0
            self.closed = 0

        async def initialize(self) -> None:
            self.initialized += 1

        async def close(self) -> None:
            self.closed += 1

    monkeypatch.setattr(runtime_module, "VoiceIdentityProfileStore", FakeProfileStore)
    monkeypatch.setattr(
        runtime_module,
        "VoiceIdentityPreferenceStore",
        FakePreferenceStore,
    )
    monkeypatch.setattr(
        runtime_module,
        "VoiceInputSuppressionController",
        FakeSuppression,
    )
    monkeypatch.setattr(runtime_module, "VoiceIdentityService", FakeService)
    monkeypatch.setattr(
        runtime_module,
        "install_voice_identity_service_for_app",
        installed.append,
    )
    monkeypatch.setattr(
        runtime_module,
        "configure_voice_identity_audio_contract_callbacks",
        lambda *, prepare=None, reconcile=None: callback_configurations.append(
            (prepare, reconcile)
        ),
    )
    monkeypatch.setenv("NEKO_VOICE_IDENTITY_MODE", "invalid-mode")
    config = SimpleNamespace(local_state_dir=tmp_path)

    service = runtime_module.install_voice_identity_runtime(config)
    assert service.runtime_mode == "off"
    assert "Unsupported NEKO_VOICE_IDENTITY_MODE" in caplog.text
    assert isinstance(service.args[0], runtime_module._UnavailableProfileStore)
    assert installed == [service]
    assert runtime_module.install_voice_identity_runtime(config) is service

    await runtime_module.initialize_voice_identity_runtime(config)
    assert service.initialized == 1
    assert callback_configurations == [
        (
            runtime_module.prepare_voice_identity_audio_contract_change,
            runtime_module.reconcile_voice_identity_audio_contract_change,
        )
    ]
    await runtime_module.close_voice_identity_runtime()
    assert service.closed == 1
    assert callback_configurations[-1] == (None, None)

    manager = _Manager()
    assert not await runtime_module.register_voice_identity_manager(manager)
    await runtime_module.unregister_voice_identity_manager(manager)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_runtime_close_always_closes_registry_and_preserves_service_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingService:
        async def close(self) -> None:
            raise RuntimeError("service close failed")

    class Registry:
        closed = False

        async def close(self) -> None:
            self.closed = True

    registry = Registry()
    monkeypatch.setattr(runtime_module, "_service", FailingService())
    monkeypatch.setattr(runtime_module, "_runtime_registry", registry)

    with pytest.raises(RuntimeError, match="service close failed"):
        await runtime_module.close_voice_identity_runtime()

    assert registry.closed


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registration_wrapper_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingRegistry:
        async def register_manager(self, _manager) -> bool:
            raise RuntimeError("registration failed")

        async def unregister_manager(self, _manager) -> None:
            self.unregistered = True

    registry = FailingRegistry()
    monkeypatch.setattr(runtime_module, "_runtime_registry", registry)

    assert not await runtime_module.register_voice_identity_manager(object())
    await runtime_module.unregister_voice_identity_manager(object())
    assert registry.unregistered


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registry_close_cancels_watchdog_and_detaches_managers() -> None:
    registry = OwnerVoiceRuntimeRegistry(
        enforce=True,
        restore_retry_interval_seconds=0.01,
        restore_retry_timeout_seconds=1.0,
    )
    manager = _Manager()
    manager.restore_failures = 100
    await registry.register_manager(manager)
    profile = _profile("profile")
    try:
        assert await registry.activate(profile, "generation")
    finally:
        profile.close()
    await registry.suppress("voice_identity_enrollment")
    await registry.restore("voice_identity_enrollment")
    retry_task = registry._restore_retry_task  # type: ignore[attr-defined]
    assert retry_task is not None

    await registry.close()
    await registry.close()

    assert retry_task.done()
    assert registry._restore_retry_task is None  # type: ignore[attr-defined]
    assert not registry._managers  # type: ignore[attr-defined]
    assert registry._activation is None  # type: ignore[attr-defined]
    assert manager.verifier_calls[-1][0] is None
