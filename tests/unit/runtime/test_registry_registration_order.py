"""Real Core checks for independent interception and Owner registration."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.main_server.voice_identity_runtime import OwnerVoiceRuntimeRegistry
from main_logic.asr_client import VoiceIdentityActivationResult as Result
from main_logic.voice_input.interception import InterceptionInstallationState
from tests.unit.runtime.test_voice_identity_runtime import _profile
from tests.unit.voice_identity_service.test_interception_lifecycle_regressions import (
    CoreHarness, RetiringTse, make_factory, process,
)
from tests.unit.voice_identity_service.test_interception_runtime import _Tse

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


class Manager:
    def __init__(self):
        self.core = CoreHarness()
        self._asr_runtime = object()
        self.require_calls = []
        self.authority_calls = []
        self.suppression_calls = []
        self.fail_authority_once = False
        self.authority_entered = asyncio.Event()
        self.interception_entered = asyncio.Event()
        self.authority_release = asyncio.Event()
        self.block_authority = False
        self.block_after_authority = False
        self.authority_committed = asyncio.Event()

    def require_active_session_interception(self):
        return self.core.require_active_session_interception()

    async def set_active_session_interception_factory(self, factory, **kwargs):
        self.interception_entered.set()
        return await self.core.set_active_session_interception_factory(factory, **kwargs)

    def require_voice_session_activation(self, **kwargs):
        self.require_calls.append(kwargs)
        return self.core.require_voice_session_activation(**kwargs)

    def voice_session_activation_policy_token(self):
        return self.core.voice_session_activation_policy_token()

    def active_session_interception_policy_token(self):
        return self.core.active_session_interception_policy_token()

    async def set_voice_session_activation_factory(self, factory, **kwargs):
        self.authority_calls.append((factory, kwargs))
        self.authority_entered.set()
        if self.block_authority:
            await self.authority_release.wait()
        if self.fail_authority_once:
            self.fail_authority_once = False
            return Result.RUNTIME_DEGRADED
        result = await self.core.set_voice_session_activation_factory(factory, **kwargs)
        if self.block_after_authority:
            self.authority_committed.set()
            await self.authority_release.wait()
        return result

    async def set_voice_input_suppressed(self, reason, *, suppressed):
        self.suppression_calls.append((reason, suppressed))
        self.core._voice_input_suppressed = suppressed


async def test_required_first_registration_installs_after_all_authority_revocations():
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = Manager()
    factory = make_factory(lambda _: _Tse())
    try:
        await registry.activate(None, "authority", activation_required=True)
        assert await registry.set_voice_interception_factory(factory)
        assert await registry.register_manager(manager) is Result.READY
        installation = registry._interception_installations[manager]
        bridge = manager.core._active_session_interception_bridge
        assert installation.state is InterceptionInstallationState.INSTALLED
        assert bridge is not None
        assert manager.require_calls and manager.authority_calls
        assert manager.core._voice_session_activation_required
        assert factory.is_available and not factory._runtimes
        assert await registry.register_manager(manager) is Result.READY
        assert manager.core._active_session_interception_bridge is bridge
    finally:
        await registry.close()


@pytest.mark.parametrize("suppressed", [False, True])
async def test_missing_interception_does_not_skip_required_authority_or_suppression(suppressed):
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = Manager()
    sent = AsyncMock()
    manager.core._route_microphone_audio_unfiltered = sent
    try:
        await registry.activate(None, "authority", activation_required=True)
        if suppressed:
            await registry.suppress("voice_identity_enrollment")
        assert not await registry.set_voice_interception_factory(None, interception_required=True)
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        assert manager.require_calls and manager.authority_calls
        assert manager.core._voice_session_activation_required
        assert manager.suppression_calls == ([('voice_identity_enrollment', True)] if suppressed else [])
        assert await registry.set_voice_interception_factory(None, interception_required=False)
        await manager.core._route_microphone_audio(b"\x01\x00" * 400, sample_rate_hz=16000)
        sent.assert_not_awaited()
        assert manager.core._voice_session_activation_required
    finally:
        await registry.close()


async def test_initial_missing_interception_still_schedules_detach_watchdog():
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01)
    manager = Manager()
    manager.fail_authority_once = True
    try:
        await registry.activate(None, "authority", activation_required=True)
        await registry.set_voice_interception_factory(None, interception_required=True)
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        assert manager in registry._detach_pending
        assert registry._detach_retry_task is not None
        task = registry._detach_retry_task
        await asyncio.wait_for(task, 1)
        assert manager not in registry._detach_pending
        assert len(manager.authority_calls) == 2
        assert manager.core._voice_session_activation_required
    finally:
        manager.authority_release.set()
        await registry.close()


async def test_required_intent_registration_is_gated_before_authority_await():
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = Manager()
    manager.block_authority = True
    task = None
    activation_task = None
    try:
        await registry.set_voice_interception_factory(None, interception_required=True)
        await registry._lock.acquire()
        task = asyncio.create_task(registry.register_manager(manager))
        activation_task = asyncio.create_task(registry.activate(None, "new-required", activation_required=True))
        # activate publishes intent synchronously before waiting on the held lock.
        await asyncio.sleep(0)
        assert registry._required_intent_revision is not None
        registry._lock.release()
        await asyncio.wait_for(manager.authority_entered.wait(), 1)
        assert manager.require_calls
        assert manager.core._voice_session_activation_required
        assert manager.core._active_session_interception_required
        manager.authority_release.set()
        assert await task is Result.RUNTIME_DEGRADED
        await activation_task
    finally:
        manager.authority_release.set()
        if registry._lock.locked() and (task is None or not manager.authority_entered.is_set()):
            registry._lock.release()
        for pending in (task, activation_task):
            if pending is not None and not pending.done():
                pending.cancel()
        await asyncio.gather(*(item for item in (task, activation_task) if item is not None), return_exceptions=True)
        await registry.close()


async def test_same_profile_application_retires_old_factory_and_needs_fresh_publication():
    registry = OwnerVoiceRuntimeRegistry(enforce=False)
    manager = Manager()
    profile = _profile("same-profile")
    old, fresh = (make_factory(lambda _: _Tse()) for _ in range(2))
    try:
        await registry.register_manager(manager)
        await registry.activate(profile, profile.generation)
        assert await registry.set_voice_interception_factory(old)
        await registry.activate(profile, profile.generation, allow_partial=True)
        assert registry._activation.profile.generation == profile.generation
        assert not old.is_available
        assert registry._interception_factory is None and registry._interception_required
        assert not await registry.set_voice_interception_factory(old)
        assert manager.core._active_session_interception_bridge is None
        assert await registry.set_voice_interception_factory(fresh)
        assert manager.core._active_session_interception_bridge is not None
    finally:
        await registry.close()
        old.close()
        fresh.close()
        profile.close()


@pytest.mark.parametrize("with_profile", [False, True])
async def test_authority_watchdog_reinstalls_only_its_own_revoked_current_installation(with_profile):
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01)
    manager = Manager()
    profile = _profile("watchdog-profile") if with_profile else None
    factory = make_factory(lambda _: _Tse())
    try:
        await registry.activate(profile, "authority", activation_required=True)
        await registry.set_voice_interception_factory(factory)
        manager.fail_authority_once = True
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        installation = registry._interception_installations[manager]
        assert installation.state is InterceptionInstallationState.INSTALLED
        task = registry._attach_retry_task if with_profile else registry._detach_retry_task
        assert task is not None
        await asyncio.wait_for(task, 1)
        current = registry._interception_installations[manager]
        assert installation.state is InterceptionInstallationState.REVOKED
        assert current is not installation
        assert current.state is InterceptionInstallationState.INSTALLED
        assert manager.core._active_session_interception_bridge is not None
        assert factory.is_available
        assert not registry._attach_pending and not registry._detach_pending
        assert await registry.register_manager(manager) is Result.READY
    finally:
        await registry.close()
        if profile is not None:
            profile.close()


async def test_external_revoke_before_registration_cannot_reuse_installed_factory():
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = Manager()
    factory = make_factory(lambda _: _Tse())
    try:
        await registry.activate(None, "authority", activation_required=True)
        await registry.set_voice_interception_factory(factory)
        assert await registry.register_manager(manager) is Result.READY
        installation = registry._interception_installations[manager]
        manager.core.require_voice_session_activation(activation_generation="external")
        assert installation.state is InterceptionInstallationState.REVOKED
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        assert registry._interception_installations[manager] is installation
        assert manager.core._active_session_interception_bridge is None
        assert factory.is_available
    finally:
        await registry.close()


async def test_external_core_revoke_during_authority_await_blocks_first_install():
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = Manager()
    manager.block_authority = True
    factory = make_factory(lambda _: _Tse())
    task = None
    try:
        await registry.activate(None, "authority", activation_required=True)
        await registry.set_voice_interception_factory(factory)
        task = asyncio.create_task(registry.register_manager(manager))
        await asyncio.wait_for(manager.authority_entered.wait(), 1)
        prepared_token = manager.voice_session_activation_policy_token()
        manager.core.require_voice_session_activation(activation_generation="external")
        assert manager.voice_session_activation_policy_token() != prepared_token
        manager.authority_release.set()
        assert await task is Result.RUNTIME_DEGRADED
        assert manager.core._active_session_interception_bridge is None
        assert manager not in registry._interception_manager_factories
        assert manager not in registry._interception_pending
        assert manager not in registry._attach_pending and manager not in registry._detach_pending
        assert manager.core._voice_session_activation_authority_generation == "external"
    finally:
        manager.authority_release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await registry.close()


@pytest.mark.parametrize("with_profile", [False, True])
async def test_external_core_revoke_during_watchdog_cannot_reinstall_current_factory(with_profile):
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01)
    manager = Manager()
    profile = _profile("watchdog-profile") if with_profile else None
    factory = make_factory(lambda _: _Tse())
    task = None
    try:
        await registry.activate(profile, "authority", activation_required=True)
        await registry.set_voice_interception_factory(factory)
        manager.fail_authority_once = True
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        installation = registry._interception_installations[manager]
        assert installation.state is InterceptionInstallationState.INSTALLED
        manager.authority_entered.clear()
        manager.block_authority = True
        task = registry._attach_retry_task if with_profile else registry._detach_retry_task
        await asyncio.wait_for(manager.authority_entered.wait(), 1)
        prepared = manager.voice_session_activation_policy_token()
        manager.core.require_voice_session_activation(activation_generation="external")
        assert manager.voice_session_activation_policy_token() != prepared
        manager.authority_release.set()
        await asyncio.wait_for(task, 1)
        assert manager.core._active_session_interception_bridge is None
        assert registry._interception_installations[manager] is installation
        assert installation.state is InterceptionInstallationState.REVOKED
        assert manager.core._voice_session_activation_authority_generation == "external"
        assert manager not in registry._attach_pending and manager not in registry._detach_pending
        assert manager not in registry._interception_pending
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
    finally:
        manager.authority_release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await registry.close()
        if profile is not None:
            profile.close()


@pytest.mark.parametrize("invalid", [None, -1, "invalid", "raise"])
async def test_invalid_require_token_cannot_drop_real_core_policy_fence(invalid):
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = Manager()
    factory = make_factory(lambda _: _Tse())
    original = manager.require_voice_session_activation

    def invalid_require(**kwargs):
        original(**kwargs)
        if invalid == "raise":
            raise RuntimeError("require failed after revocation")
        return invalid

    manager.require_voice_session_activation = invalid_require
    try:
        await registry.activate(None, "authority", activation_required=True)
        await registry.set_voice_interception_factory(factory)
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        assert manager.core._voice_session_activation_required
        assert manager.core._active_session_interception_bridge is None
        assert manager not in registry._interception_manager_factories
    finally:
        await registry.close()


async def test_external_revoke_during_physical_interception_retirement_blocks_installation():
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01)
    manager = Manager()
    worker = RetiringTse(wait=True)
    factory = make_factory(lambda _: worker)
    task = None
    try:
        await registry.activate(None, "authority", activation_required=True)
        await registry.set_voice_interception_factory(factory)
        manager.fail_authority_once = True
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        task = registry._detach_retry_task
        assert task is not None
        async with registry._lock:
            await process(manager.core._active_session_interception_bridge)
            manager.interception_entered.clear()
        await asyncio.wait_for(worker.closing.wait(), 1)
        await asyncio.wait_for(manager.interception_entered.wait(), 1)
        pending = registry._interception_installations[manager]
        # The replacement setter is now waiting on the real old runtime owner.
        manager.core.require_voice_session_activation(activation_generation="external")
        worker.release.set()
        await asyncio.wait_for(task, 1)
        assert manager.core._active_session_interception_bridge is None
        assert pending.state is InterceptionInstallationState.REVOKED
        assert manager not in registry._interception_manager_factories
        assert manager not in registry._interception_pending
        assert manager.core._voice_session_activation_authority_generation == "external"
    finally:
        worker.release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await registry.close()


@pytest.mark.parametrize("with_profile", [False, True])
async def test_old_watchdog_cannot_remove_pending_owned_by_new_required_intent(with_profile):
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01)
    manager = Manager()
    profile = _profile("watchdog-profile") if with_profile else None
    factory = make_factory(lambda _: _Tse())
    snapshot_entered = asyncio.Event()
    snapshot_release = asyncio.Event()
    snapshot_task = None
    activation_task = None
    task = None

    async def observe_after_watchdog():
        async with registry._lock:
            snapshot_entered.set()
            await snapshot_release.wait()

    original_require = manager.require_voice_session_activation

    def require_with_unconfirmed_new_intent(**kwargs):
        token = original_require(**kwargs)
        return None if kwargs["activation_generation"] == "next-authority" else token

    try:
        await registry.activate(profile, "authority", activation_required=True)
        await registry.set_voice_interception_factory(factory)
        manager.fail_authority_once = True
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        manager.block_after_authority = True
        task = registry._attach_retry_task if with_profile else registry._detach_retry_task
        await asyncio.wait_for(manager.authority_committed.wait(), 1)
        # Queue this observation ahead of the new activation's lock wait.
        snapshot_task = asyncio.create_task(observe_after_watchdog())
        await asyncio.sleep(0)
        manager.require_voice_session_activation = require_with_unconfirmed_new_intent
        activation_task = asyncio.create_task(
            registry.activate(None, "next-authority", activation_required=True),
        )
        await asyncio.sleep(0)
        assert registry._required_intent_generation == "next-authority"
        assert registry._detach_pending[manager] == "next-authority"
        manager.authority_release.set()
        await asyncio.wait_for(snapshot_entered.wait(), 1)
        assert registry._detach_pending[manager] == "next-authority"
        if with_profile:
            assert manager in registry._attach_pending
        assert manager.core._active_session_interception_bridge is None
        assert manager.core._voice_session_activation_authority_generation == "next-authority"
    finally:
        manager.authority_release.set()
        snapshot_release.set()
        for pending in (snapshot_task, activation_task, task):
            if pending is not None and not pending.done():
                pending.cancel()
        await asyncio.gather(*(pending for pending in (snapshot_task, activation_task, task) if pending is not None), return_exceptions=True)
        await registry.close()
        if profile is not None:
            profile.close()
