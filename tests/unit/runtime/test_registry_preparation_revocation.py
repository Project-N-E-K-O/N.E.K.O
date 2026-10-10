"""A preparation cannot adopt external revocation or erase independent Owner retry."""
from __future__ import annotations

import asyncio

import pytest

from app.main_server import voice_identity_runtime as registry_module
from app.main_server.voice_identity_runtime import OwnerVoiceRuntimeRegistry
from main_logic.asr_client import VoiceIdentityActivationResult as Result
from main_logic.voice_input.interception import InterceptionInstallationState as State
from tests.unit.runtime.test_registry_recovery_retry import (
    RetryManager, _assert_interception_closed, _assert_live_owner_output,
)
from tests.unit.runtime.test_voice_identity_runtime import _profile
from tests.unit.voice_identity_service.test_interception_lifecycle_regressions import make_factory
from tests.unit.voice_identity_service.test_interception_runtime import _Tse

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


class PreparationManager(RetryManager):
    def __init__(self):
        super().__init__()
        self.suppression_error = False
        self.retry_outcome = None
        self.retry_entered = asyncio.Event()
        self.retry_release = asyncio.Event()
        self.authority_successes = 0

    async def set_voice_input_suppressed(self, reason, *, suppressed):
        await super().set_voice_input_suppressed(reason, suppressed=suppressed)
        if suppressed and self.suppression_error:
            raise RuntimeError("injected suppression failure after external revoke")

    async def set_voice_session_activation_factory(self, factory, **kwargs):
        if self.retry_outcome is not None:
            outcome = self.retry_outcome
            self.retry_outcome = None
            self.retry_entered.set()
            await self.retry_release.wait()
            if outcome == "error":
                raise RuntimeError("injected authority retry failure")
            if outcome == "timeout":
                await asyncio.Event().wait()
            if outcome == "false":
                return Result.RUNTIME_DEGRADED
        result = await super().set_voice_session_activation_factory(factory, **kwargs)
        if result:
            self.authority_successes += 1
        return result


def _external_revoke(manager, revoke):
    if revoke == "interception":
        manager.core.require_active_session_interception()
    elif revoke == "owner":
        manager.core.require_voice_session_activation(activation_generation="external-owner")
    return (manager.voice_session_activation_policy_token(),
            manager.active_session_interception_policy_token())


async def _join_retries(registry):
    for task in (registry._attach_retry_task, registry._detach_retry_task,
                 registry._interception_retry_task):
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 2)


def _assert_owner_installed(manager, with_profile):
    assert manager.authority_successes > 0
    assert manager.core._voice_session_activation_authority_generation != "external-owner"
    assert manager.core._voice_session_activation_required
    assert (manager.core._voice_session_activation_factory is not None) is with_profile


@pytest.mark.parametrize("with_profile", [False, True])
@pytest.mark.parametrize("revoke", ["none", "interception", "owner"])
async def test_first_registration_suppression_does_not_adopt_external_policy(with_profile, revoke):
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01,
                                         restore_retry_timeout_seconds=1)
    manager = PreparationManager()
    profile = _profile("preparation-profile") if with_profile else None
    factory = make_factory(lambda _: _Tse())
    task = None
    try:
        await registry.activate(profile, "authority", activation_required=True)
        await registry.suppress("voice_identity_enrollment")
        await registry.set_voice_interception_factory(factory)
        manager.block_suppression = True
        task = asyncio.create_task(registry.register_manager(manager))
        await asyncio.wait_for(manager.suppress_entered.wait(), 1)
        assert manager not in registry._interception_installations
        external_tokens = _external_revoke(manager, revoke)
        manager.suppress_release.set()
        result = await asyncio.wait_for(task, 1)
        await _join_retries(registry)
        await registry.restore("voice_identity_enrollment")
        if revoke == "none":
            assert result is Result.READY
            _assert_owner_installed(manager, with_profile)
            await _assert_live_owner_output(manager)
        else:
            assert result is Result.RUNTIME_DEGRADED
            await _assert_interception_closed(manager)
            assert registry._interception_installations[manager].state is State.REVOKED
            assert manager not in registry._interception_pending
            if revoke == "owner":
                assert manager.voice_session_activation_policy_token() == external_tokens[0]
                assert manager.core._voice_session_activation_authority_generation == "external-owner"
            else:
                _assert_owner_installed(manager, with_profile)
                assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
                await _assert_interception_closed(manager)
                assert registry._interception_installations[manager].state is State.REVOKED
                assert await registry.set_voice_interception_factory(factory)
                assert await registry.register_manager(manager) is Result.READY
                await _assert_live_owner_output(manager)
    finally:
        manager.suppress_release.set()
        manager.retry_release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await registry.close()
        if profile is not None:
            profile.close()


@pytest.mark.parametrize("with_profile", [False, True])
@pytest.mark.parametrize("exit_boundary", ["cancel", "error"])
@pytest.mark.parametrize("revoke", ["interception", "owner"])
async def test_first_suppression_revoke_survives_cancel_or_exception_and_owner_retry(with_profile, exit_boundary, revoke):
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01,
                                         restore_retry_timeout_seconds=1)
    manager = PreparationManager()
    profile = _profile("cancel-preparation-profile") if with_profile else None
    factory = make_factory(lambda _: _Tse())
    task = None
    try:
        await registry.activate(profile, "authority", activation_required=True)
        await registry.suppress("voice_identity_enrollment")
        await registry.set_voice_interception_factory(factory)
        manager.block_suppression = True
        task = asyncio.create_task(registry.register_manager(manager))
        await asyncio.wait_for(manager.suppress_entered.wait(), 1)
        external_tokens = _external_revoke(manager, revoke)
        if exit_boundary == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            manager.suppression_error = True
            manager.suppress_release.set()
            with pytest.raises(RuntimeError, match="injected suppression failure"):
                await task
        await _join_retries(registry)
        if revoke == "owner":
            assert manager.voice_session_activation_policy_token() == external_tokens[0]
            assert manager.core._voice_session_activation_authority_generation == "external-owner"
            assert manager.authority_successes == 0
            assert registry._interception_installations[manager].state is State.REVOKED
            assert manager not in registry._interception_pending
            await _assert_interception_closed(manager)
            return
        _assert_owner_installed(manager, with_profile)
        assert manager not in registry._attach_pending and manager not in registry._detach_pending
        assert registry._interception_installations[manager].state is State.REVOKED
        assert manager not in registry._interception_pending
        await _assert_interception_closed(manager)
        manager.suppression_error = False
        manager.suppress_release.set()
        await registry.restore("voice_identity_enrollment")
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        await _assert_interception_closed(manager)
        assert await registry.set_voice_interception_factory(factory)
        await _assert_live_owner_output(manager)
    finally:
        manager.suppress_release.set()
        manager.retry_release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await registry.close()
        if profile is not None:
            profile.close()


@pytest.mark.parametrize("with_profile", [False, True])
@pytest.mark.parametrize("failure", ["false", "error", "timeout"])
async def test_interception_only_revoke_during_failed_authority_retry_keeps_owner_recovery(with_profile, failure, monkeypatch):
    monkeypatch.setattr(registry_module, "_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS", .15)
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01,
                                         restore_retry_timeout_seconds=1)
    manager = PreparationManager()
    manager.failures.append("false")
    profile = _profile("failed-retry-profile") if with_profile else None
    factory = make_factory(lambda _: _Tse())
    try:
        await registry.activate(profile, "authority", activation_required=True)
        await registry.set_voice_interception_factory(factory)
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        first = registry._interception_installations[manager]
        assert first.state is State.INSTALLED
        manager.retry_outcome = failure
        await asyncio.wait_for(manager.retry_entered.wait(), 1)
        owner_token = manager.voice_session_activation_policy_token()
        manager.core.require_active_session_interception()
        assert manager.voice_session_activation_policy_token() == owner_token
        manager.retry_release.set()
        await _join_retries(registry)
        _assert_owner_installed(manager, with_profile)
        assert manager not in registry._attach_pending and manager not in registry._detach_pending
        assert registry._interception_installations[manager] is first
        assert first.state is State.REVOKED
        assert manager not in registry._interception_pending
        await _assert_interception_closed(manager)
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        await _assert_interception_closed(manager)
        assert await registry.set_voice_interception_factory(factory)
        await _assert_live_owner_output(manager)
    finally:
        manager.retry_release.set()
        await registry.close()
        if profile is not None:
            profile.close()


@pytest.mark.parametrize("with_profile", [False, True])
async def test_initial_interception_revoke_is_not_a_fresh_grant_on_next_authority_retry(with_profile):
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01,
                                         restore_retry_timeout_seconds=1)
    manager = PreparationManager()
    manager.failures.append("false")
    profile = _profile("initial-retry-profile") if with_profile else None
    factory = make_factory(lambda _: _Tse())
    task = None
    try:
        await registry.activate(profile, "authority", activation_required=True)
        await registry.suppress("voice_identity_enrollment")
        await registry.set_voice_interception_factory(factory)
        manager.block_suppression = True
        task = asyncio.create_task(registry.register_manager(manager))
        await asyncio.wait_for(manager.suppress_entered.wait(), 1)
        manager.core.require_active_session_interception()
        manager.suppress_release.set()
        assert await task is Result.RUNTIME_DEGRADED
        await _join_retries(registry)
        _assert_owner_installed(manager, with_profile)
        assert registry._interception_installations[manager].state is State.REVOKED
        assert manager not in registry._interception_pending
        await _assert_interception_closed(manager)
        await registry.restore("voice_identity_enrollment")
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        await _assert_interception_closed(manager)
        fresh = make_factory(lambda _: _Tse())
        assert await registry.set_voice_interception_factory(fresh)
        assert not factory.is_available
        await _assert_live_owner_output(manager)
    finally:
        manager.suppress_release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await registry.close()
        if profile is not None:
            profile.close()


@pytest.mark.parametrize("confirmed_token", [False, True])
@pytest.mark.parametrize("exit_boundary", ["continue", "cancel", "error", "timeout"])
async def test_new_required_intent_during_suppression_keeps_successor_authority(confirmed_token, exit_boundary, monkeypatch):
    if exit_boundary == "timeout":
        monkeypatch.setattr(registry_module, "_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS", .15)
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01,
                                         restore_retry_timeout_seconds=1)
    manager = PreparationManager()
    factory = make_factory(lambda _: _Tse())
    observer_queued = asyncio.Event()
    observer_entered = asyncio.Event()
    observer_release = asyncio.Event()
    new_intent_entered = asyncio.Event()
    task = observer_task = activation_task = None
    original_require = manager.require_voice_session_activation

    def require_new_intent(**kwargs):
        token = original_require(**kwargs)
        if kwargs["activation_generation"] == "new-authority":
            new_intent_entered.set()
            return token if confirmed_token else None
        return token

    async def observe_before_successor_applies():
        observer_queued.set()
        async with registry._lock:
            observer_entered.set()
            await observer_release.wait()

    try:
        await registry.activate(None, "old-authority", activation_required=True)
        await registry.suppress("voice_identity_enrollment")
        await registry.set_voice_interception_factory(factory)
        manager.block_suppression = True
        task = asyncio.create_task(registry.register_manager(manager))
        await asyncio.wait_for(manager.suppress_entered.wait(), 1)
        observer_task = asyncio.create_task(observe_before_successor_applies())
        await observer_queued.wait()
        manager.require_voice_session_activation = require_new_intent
        activation_task = asyncio.create_task(registry.activate(None, "new-authority", activation_required=True))
        await asyncio.wait_for(new_intent_entered.wait(), 1)
        successor_token = manager.voice_session_activation_policy_token()
        assert registry._required_intent_generation == "new-authority"
        if not confirmed_token:
            assert registry._detach_pending[manager] == "new-authority"
        if exit_boundary == "cancel":
            task.cancel()
        elif exit_boundary == "error":
            manager.suppression_error = True
            manager.suppress_release.set()
        elif exit_boundary == "continue":
            manager.suppress_release.set()
        await asyncio.wait_for(observer_entered.wait(), 1)
        assert manager.voice_session_activation_policy_token() == successor_token
        assert manager.core._voice_session_activation_authority_generation == "new-authority"
        if not confirmed_token:
            assert registry._detach_pending[manager] == "new-authority"
        assert manager.authority_successes == 0
        if exit_boundary == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        elif exit_boundary == "error":
            with pytest.raises(RuntimeError, match="injected suppression failure"):
                await task
        else:
            assert await task is Result.RUNTIME_DEGRADED
        await _assert_interception_closed(manager)
    finally:
        manager.suppress_release.set()
        observer_release.set()
        for pending in (task, observer_task, activation_task):
            if pending is not None and not pending.done():
                pending.cancel()
        await asyncio.gather(*(pending for pending in (task, observer_task, activation_task) if pending is not None), return_exceptions=True)
        await registry.close()


@pytest.mark.parametrize("getter_failure", ["none", "error"])
async def test_declared_owner_policy_getter_failure_cannot_authorize_first_install(getter_failure):
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    manager = PreparationManager()
    factory = make_factory(lambda _: _Tse())

    def unavailable_owner_policy():
        if getter_failure == "error":
            raise RuntimeError("injected declared Owner getter failure")
        return None

    try:
        await registry.activate(None, "authority", activation_required=True)
        await registry.set_voice_interception_factory(factory)
        manager.voice_session_activation_policy_token = unavailable_owner_policy
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        assert manager.authority_successes == 0
        assert not manager.authority_calls
        assert manager not in registry._interception_pending
        await _assert_interception_closed(manager)
    finally:
        await registry.close()
