"""Registry-owned retry evidence must outlive transient failures and cancellation."""
from __future__ import annotations

import asyncio
from collections import deque

import pytest

from app.main_server import voice_identity_runtime as registry_module
from app.main_server.voice_identity_runtime import OwnerVoiceRuntimeRegistry
from main_logic.asr_client import VoiceIdentityActivationResult as Result
from main_logic.voice_input.interception import InterceptionInstallationState as State
from tests.unit.runtime.test_registry_registration_order import Manager
from tests.unit.runtime.test_voice_identity_runtime import _profile
from tests.unit.voice_identity_service.test_interception_lifecycle_regressions import (
    RetiringTse, make_factory, process,
)
from tests.unit.voice_identity_service.test_interception_runtime import _Tse
from tests.unit.asr_runtime.test_active_session_interception import _frame

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


class RetryManager(Manager):
    def __init__(self, failures=()):
        super().__init__()
        self.failures = deque(failures)
        self.failed_retry_entered = asyncio.Event()
        self.failure_release = asyncio.Event()
        self.suppress_entered = asyncio.Event()
        self.suppress_release = asyncio.Event()
        self.block_suppression = False

    def active_session_interception_policy_token(self):
        return self.core.active_session_interception_policy_token()

    async def set_voice_session_activation_factory(self, factory, **kwargs):
        if self.failures:
            outcome = self.failures.popleft()
            if outcome == "timeout":
                self.failed_retry_entered.set()
                await self.failure_release.wait()
            elif outcome == "error":
                self.failed_retry_entered.set()
                raise RuntimeError("injected transient authority setter error")
            else:
                self.failed_retry_entered.set()
                return Result.RUNTIME_DEGRADED
        return await super().set_voice_session_activation_factory(factory, **kwargs)

    async def set_voice_input_suppressed(self, reason, *, suppressed):
        if suppressed and self.block_suppression:
            self.suppress_entered.set()
            await self.suppress_release.wait()
        await super().set_voice_input_suppressed(reason, suppressed=suppressed)


async def _assert_live_owner_output(manager):
    bridge = manager.core._active_session_interception_bridge
    assert bridge is not None
    results = [await process(bridge) for _ in range(8)]
    assert any(result.pcm16 for result in results)


async def _assert_interception_closed(manager):
    assert manager.core._active_session_interception_bridge is None
    assert manager.core._active_session_interception_required
    generation = manager.core._capture_voice_session_activation_generation()
    frame = _frame(manager.core, generation, b"\x01\x00" * 400)
    assert await manager.core._intercept_active_session_frame(frame, generation, frame.context) is None


@pytest.mark.parametrize("with_profile", [False, True])
@pytest.mark.parametrize("failure", ["false", "error", "timeout"])
async def test_authority_watchdog_transient_failure_keeps_owned_interception_retry(with_profile, failure, monkeypatch):
    monkeypatch.setattr(registry_module, "_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS", .1)
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01,
                                         restore_retry_timeout_seconds=1)
    manager = RetryManager(("false", failure))
    profile = _profile("retry-profile") if with_profile else None
    factory = make_factory(lambda _: _Tse())
    try:
        await registry.activate(profile, "authority", activation_required=True)
        await registry.set_voice_interception_factory(factory)
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        first = registry._interception_installations[manager]
        assert first.state is State.INSTALLED
        task = registry._attach_retry_task if with_profile else registry._detach_retry_task
        assert task is not None
        await asyncio.wait_for(task, 2)
        retry = registry._interception_retry_task
        if retry is not None:
            await asyncio.wait_for(retry, 2)
        assert not manager.failures
        assert not registry._attach_pending and not registry._detach_pending
        assert factory.is_available
        assert registry._interception_installations[manager].state is State.INSTALLED
        assert await registry.register_manager(manager) is Result.READY
        await _assert_live_owner_output(manager)
    finally:
        manager.failure_release.set()
        await registry.close()
        if profile is not None:
            profile.close()


@pytest.mark.parametrize("state", [State.PENDING, State.INVALIDATED])
async def test_current_pending_or_invalidated_installation_survives_owned_authority_preparation(state):
    # Keep automatic retries asleep while supported setters construct the case;
    # repeated registration itself must preserve and complete this recovery.
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=10,
                                         restore_retry_timeout_seconds=10)
    manager = RetryManager(("false",))
    old_worker = RetiringTse(can_stop=False)
    old = make_factory(lambda _: old_worker)
    current = make_factory(lambda _: _Tse())
    try:
        await registry.activate(None, "authority", activation_required=True)
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        assert manager in registry._detach_pending
        assert await registry.set_voice_interception_factory(old)
        if state is State.PENDING:
            async with registry._lock:
                await process(manager.core._active_session_interception_bridge)
            assert not await registry.set_voice_interception_factory(current)
            old_worker.can_stop = True
        else:
            current.close()
            current = old
            # Actual Core temporary route invalidation reports to the exact
            # registry receipt and starts its provider-neutral retry owner.
            manager.core._invalidate_active_session_interception_now("route_binding_changed", recoverable=True)
        first = registry._interception_installations[manager]
        assert first.state is state
        assert registry._interception_factory is current and current.is_available
        assert await registry.register_manager(manager) is Result.READY
        retry = registry._interception_installations[manager]
        assert retry is not first and retry.state is State.INSTALLED
        await _assert_live_owner_output(manager)
    finally:
        old_worker.can_stop = True
        await registry.close()
        old.close()
        current.close()


@pytest.mark.parametrize("where", ["suppression", "empty_authority"])
async def test_cancelled_first_registration_without_activation_keeps_interception_recovery(where):
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01,
                                         restore_retry_timeout_seconds=1)
    manager = RetryManager()
    factory = make_factory(lambda _: _Tse())
    task = None
    try:
        if where == "suppression":
            await registry.suppress("voice_identity_enrollment")
            manager.block_suppression = True
        else:
            await registry.activate(None, "authority", activation_required=True)
            manager.block_authority = True
        assert registry._activation is None
        await registry.set_voice_interception_factory(factory)
        task = asyncio.create_task(registry.register_manager(manager))
        entered = manager.suppress_entered if where == "suppression" else manager.authority_entered
        await asyncio.wait_for(entered.wait(), 1)
        assert manager.core._active_session_interception_required
        assert manager.core._active_session_interception_bridge is None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert manager in registry._managers
        assert manager in registry._interception_pending
        assert registry._interception_pending[manager]["factory"] is factory
        manager.suppress_release.set()
        manager.authority_release.set()
        if where == "suppression":
            await registry.restore("voice_identity_enrollment")
        for retry in (registry._detach_retry_task, registry._interception_retry_task):
            if retry is not None:
                await asyncio.wait_for(retry, 2)
        assert registry._interception_installations[manager].state is State.INSTALLED
        assert await registry.register_manager(manager) is Result.READY
        await _assert_live_owner_output(manager)
    finally:
        manager.suppress_release.set()
        manager.authority_release.set()
        manager.failure_release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await registry.close()


@pytest.mark.parametrize("initial_state", [State.INSTALLED, State.PENDING, State.INVALIDATED, State.REVOKED])
async def test_independent_interception_revoke_during_registration_blocks_recovery(initial_state):
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=10,
                                         restore_retry_timeout_seconds=10)
    manager = RetryManager(("false",))
    old_worker = RetiringTse(can_stop=False)
    old = make_factory(lambda _: old_worker)
    current = make_factory(lambda _: _Tse())
    task = None
    try:
        await registry.activate(None, "authority", activation_required=True)
        await registry.set_voice_interception_factory(old)
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        if initial_state is State.PENDING:
            async with registry._lock:
                await process(manager.core._active_session_interception_bridge)
            assert not await registry.set_voice_interception_factory(current)
            old_worker.can_stop = True
        else:
            current.close()
            current = old
            if initial_state is State.INVALIDATED:
                manager.core._invalidate_active_session_interception_now("route_binding_changed", recoverable=True)
            elif initial_state is State.REVOKED:
                manager.core.require_active_session_interception()
        installation = registry._interception_installations[manager]
        assert installation.state is initial_state
        manager.authority_entered.clear()
        manager.block_authority = True
        task = asyncio.create_task(registry.register_manager(manager))
        await asyncio.wait_for(manager.authority_entered.wait(), 1)
        owner_token = manager.voice_session_activation_policy_token()
        manager.core.require_active_session_interception()
        assert manager.voice_session_activation_policy_token() == owner_token
        manager.authority_release.set()
        assert await task is Result.RUNTIME_DEGRADED
        assert registry._interception_installations[manager] is installation
        assert installation.state is State.REVOKED
        assert manager not in registry._interception_pending
        assert manager not in registry._interception_manager_factories
        await _assert_interception_closed(manager)
    finally:
        old_worker.can_stop = True
        manager.authority_release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await registry.close()
        old.close()
        current.close()


@pytest.mark.parametrize("with_profile", [False, True])
async def test_independent_interception_revoke_during_authority_watchdog_blocks_recovery(with_profile):
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01,
                                         restore_retry_timeout_seconds=1)
    manager = RetryManager(("false",))
    profile = _profile("independent-revoke-profile") if with_profile else None
    factory = make_factory(lambda _: _Tse())
    task = None
    try:
        await registry.activate(profile, "authority", activation_required=True)
        await registry.set_voice_interception_factory(factory)
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        installation = registry._interception_installations[manager]
        assert installation.state is State.INSTALLED
        manager.authority_entered.clear()
        manager.block_authority = True
        task = registry._attach_retry_task if with_profile else registry._detach_retry_task
        await asyncio.wait_for(manager.authority_entered.wait(), 1)
        owner_token = manager.voice_session_activation_policy_token()
        manager.core.require_active_session_interception()
        assert manager.voice_session_activation_policy_token() == owner_token
        manager.authority_release.set()
        await asyncio.wait_for(task, 2)
        retry = registry._interception_retry_task
        if retry is not None:
            await asyncio.wait_for(retry, 2)
        assert registry._interception_installations[manager] is installation
        assert installation.state is State.REVOKED
        assert manager not in registry._interception_pending
        assert manager not in registry._interception_manager_factories
        await _assert_interception_closed(manager)
    finally:
        manager.authority_release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await registry.close()
        if profile is not None:
            profile.close()


async def test_independent_interception_revoke_during_cancelled_suppression_blocks_install_retry():
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01,
                                         restore_retry_timeout_seconds=1)
    manager = RetryManager()
    factory = make_factory(lambda _: _Tse())
    task = None
    try:
        await registry.suppress("voice_identity_enrollment")
        manager.block_suppression = True
        await registry.set_voice_interception_factory(factory)
        task = asyncio.create_task(registry.register_manager(manager))
        await asyncio.wait_for(manager.suppress_entered.wait(), 1)
        owner_token = manager.voice_session_activation_policy_token()
        manager.core.require_active_session_interception()
        assert manager.voice_session_activation_policy_token() == owner_token
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert manager not in registry._interception_pending
        manager.suppress_release.set()
        await registry.restore("voice_identity_enrollment")
        retry = registry._interception_retry_task
        if retry is not None:
            await asyncio.wait_for(retry, 2)
        assert manager not in registry._interception_pending
        assert manager not in registry._interception_manager_factories
        await _assert_interception_closed(manager)
    finally:
        manager.suppress_release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await registry.close()


@pytest.mark.parametrize("revoke", ["interception", "owner"])
async def test_queued_interception_retry_cannot_outlive_external_policy_revocation(revoke):
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01,
                                         restore_retry_timeout_seconds=1)
    manager = RetryManager()
    factory = make_factory(lambda _: _Tse())
    task = None
    retry = None
    try:
        await registry.suppress("voice_identity_enrollment")
        manager.block_suppression = True
        await registry.set_voice_interception_factory(factory)
        task = asyncio.create_task(registry.register_manager(manager))
        await asyncio.wait_for(manager.suppress_entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Acquire synchronously before the retry's next lock admission. The
        # actual queue entry exists, but no installer can adopt it until release.
        async with registry._lock:
            grant = registry._interception_pending[manager]
            assert grant["factory"] is factory
            retry = registry._interception_retry_task
            assert retry is not None and not retry.done()
            owner_token = manager.voice_session_activation_policy_token()
            if revoke == "interception":
                manager.core.require_active_session_interception()
                assert manager.voice_session_activation_policy_token() == owner_token
            else:
                manager.core.require_voice_session_activation(activation_generation="external-owner")
                assert manager.voice_session_activation_policy_token() != owner_token
            assert registry._interception_pending[manager] is grant
        await asyncio.wait_for(retry, 2)
        manager.suppress_release.set()
        await registry.restore("voice_identity_enrollment")
        assert manager not in registry._interception_pending
        assert manager not in registry._interception_manager_factories
        assert registry._interception_installations[manager].state is State.REVOKED
        assert factory.is_available and not factory._runtimes
        await _assert_interception_closed(manager)
    finally:
        manager.suppress_release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await registry.close()


@pytest.mark.parametrize("with_profile", [False, True])
async def test_new_required_intent_during_interception_retirement_keeps_successor_pending(with_profile):
    registry = OwnerVoiceRuntimeRegistry(enforce=True, restore_retry_interval_seconds=.01,
                                         restore_retry_timeout_seconds=1)
    manager = RetryManager(("false",))
    profile = _profile("retirement-owner-profile") if with_profile else None
    worker = RetiringTse(wait=True)
    factory = make_factory(lambda _: worker)
    observer_entered = asyncio.Event()
    observer_release = asyncio.Event()
    task = None
    observer_task = None
    activation_task = None
    original_require = manager.require_voice_session_activation

    def require_with_unconfirmed_new_intent(**kwargs):
        token = original_require(**kwargs)
        return None if kwargs["activation_generation"] == "next-authority" else token

    async def observe_before_new_activation_adopts_lock():
        async with registry._lock:
            observer_entered.set()
            await observer_release.wait()

    try:
        await registry.activate(profile, "authority", activation_required=True)
        await registry.set_voice_interception_factory(factory)
        assert await registry.register_manager(manager) is Result.RUNTIME_DEGRADED
        task = registry._attach_retry_task if with_profile else registry._detach_retry_task
        assert task is not None
        async with registry._lock:
            await process(manager.core._active_session_interception_bridge)
            manager.interception_entered.clear()
        await asyncio.wait_for(worker.closing.wait(), 1)
        await asyncio.wait_for(manager.interception_entered.wait(), 1)
        # Authority has already succeeded; the actual Core interception setter
        # now owns a new PENDING attempt and awaits the old physical DSP owner.
        pending_installation = registry._interception_installations[manager]
        assert pending_installation.state is State.PENDING
        observer_task = asyncio.create_task(observe_before_new_activation_adopts_lock())
        await asyncio.sleep(0)
        manager.require_voice_session_activation = require_with_unconfirmed_new_intent
        activation_task = asyncio.create_task(
            registry.activate(None, "next-authority", activation_required=True),
        )
        await asyncio.sleep(0)
        assert registry._required_intent_generation == "next-authority"
        assert registry._detach_pending[manager] == "next-authority"
        assert pending_installation.state is State.REVOKED
        worker.release.set()
        await asyncio.wait_for(observer_entered.wait(), 1)
        assert registry._detach_pending[manager] == "next-authority"
        if with_profile:
            assert manager in registry._attach_pending
        assert manager.core._voice_session_activation_authority_generation == "next-authority"
        assert manager not in registry._interception_pending
        assert manager not in registry._interception_manager_factories
        await _assert_interception_closed(manager)
    finally:
        worker.release.set()
        observer_release.set()
        for pending in (task, observer_task, activation_task):
            if pending is not None and not pending.done():
                pending.cancel()
        await asyncio.gather(*(pending for pending in (task, observer_task, activation_task) if pending is not None), return_exceptions=True)
        await registry.close()
        if profile is not None:
            profile.close()
