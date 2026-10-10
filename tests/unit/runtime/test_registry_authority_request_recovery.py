"""Requested authority that never commits cannot strand the current Owner."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.main_server import voice_identity_runtime as registry_module
from app.main_server.voice_identity_runtime import OwnerVoiceRuntimeRegistry
from main_logic.asr_client import VoiceIdentityActivationResult as Result
from tests.unit.runtime.test_registry_preparation_revocation import PreparationManager
from tests.unit.runtime.test_voice_identity_runtime import _profile

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


class RequestObservedRegistry(OwnerVoiceRuntimeRegistry):
    def __init__(self):
        super().__init__(enforce=True, restore_retry_interval_seconds=.01,
                         restore_retry_timeout_seconds=1)
        self.request_waiting = asyncio.Event()

    @asynccontextmanager
    async def _activation_request_lock(self, request_revision):
        if request_revision > 1:
            self.request_waiting.set()
        async with super()._activation_request_lock(request_revision):
            yield


async def _join_owner_recovery(registry):
    for task in (registry._attach_retry_task, registry._detach_retry_task):
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 2)


def _assert_owner_authorized(manager, generation):
    assert manager.core._voice_session_activation_factory is not None
    assert manager.core._voice_session_activation_authority_generation == generation
    assert not manager.core._voice_session_activation_degraded


async def _assert_owner_input_closed(manager):
    assert manager.core._voice_session_activation_required
    assert manager.core._voice_session_activation_factory is None
    sent = AsyncMock()
    manager.core._route_microphone_audio_unfiltered = sent
    await manager.core._route_microphone_audio(b"\x01\x00" * 400, sample_rate_hz=16000)
    sent.assert_not_awaited()


@pytest.mark.parametrize("request_exit", ["cancel", "construction_error"])
@pytest.mark.parametrize("registration_exit", ["continue", "cancel", "error"])
async def test_uncommitted_optional_request_preserves_required_owner_recovery(request_exit, registration_exit, monkeypatch):
    registry = RequestObservedRegistry()
    manager = PreparationManager()
    profile = _profile("committed-owner-profile")
    register_task = activation_task = None
    try:
        await registry.activate(profile, "committed-owner", activation_required=True)
        committed = registry._activation
        await registry.suppress("voice_identity_enrollment")
        manager.block_suppression = True
        register_task = asyncio.create_task(registry.register_manager(manager))
        await asyncio.wait_for(manager.suppress_entered.wait(), 1)
        activation_task = asyncio.create_task(registry.activate(profile, "optional-request"))
        await asyncio.wait_for(registry.request_waiting.wait(), 1)
        assert registry._activation is committed
        assert registry._authority_request_revision == 2
        if request_exit == "cancel":
            activation_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await activation_task
        else:
            def fail_clone(*args, **kwargs):
                raise RuntimeError("injected uncommitted profile clone failure")

            monkeypatch.setattr(registry_module._OwnerActivation, "from_borrowed", fail_clone)
        if registration_exit == "cancel":
            register_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await register_task
        elif registration_exit == "error":
            manager.suppression_error = True
            manager.suppress_release.set()
            with pytest.raises(RuntimeError, match="injected suppression failure"):
                await register_task
        else:
            manager.suppress_release.set()
            await asyncio.wait_for(register_task, 1)
        if request_exit == "construction_error":
            assert await asyncio.wait_for(activation_task, 1) is Result.RUNTIME_DEGRADED
        assert registry._activation is committed
        manager.suppression_error = False
        manager.suppress_release.set()
        await registry.restore("voice_identity_enrollment")
        await _join_owner_recovery(registry)
        # Readiness must be backed by actual Core authority, including when
        # registration is repeated after its first attempt was interrupted.
        assert await registry.register_manager(manager) is Result.READY
        _assert_owner_authorized(manager, "committed-owner")
        assert manager not in registry._attach_pending and manager not in registry._detach_pending
    finally:
        manager.suppress_release.set()
        manager.retry_release.set()
        for task in (register_task, activation_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (register_task, activation_task) if task is not None), return_exceptions=True)
        await registry.close()
        profile.close()


@pytest.mark.parametrize("registration_exit", ["continue", "cancel", "error"])
async def test_committed_successor_supersedes_old_registration_recovery(registration_exit):
    registry = RequestObservedRegistry()
    manager = PreparationManager()
    profile = _profile("successor-owner-profile")
    register_task = activation_task = None
    try:
        await registry.activate(profile, "old-owner", activation_required=True)
        await registry.suppress("voice_identity_enrollment")
        manager.block_suppression = True
        register_task = asyncio.create_task(registry.register_manager(manager))
        await asyncio.wait_for(manager.suppress_entered.wait(), 1)
        activation_task = asyncio.create_task(registry.activate(profile, "successor-owner", activation_required=True))
        await asyncio.wait_for(registry.request_waiting.wait(), 1)
        if registration_exit == "cancel":
            register_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await register_task
        elif registration_exit == "error":
            manager.suppression_error = True
            manager.suppress_release.set()
            with pytest.raises(RuntimeError, match="injected suppression failure"):
                await register_task
        else:
            manager.suppress_release.set()
            assert await register_task is Result.RUNTIME_DEGRADED
        manager.suppression_error = False
        manager.suppress_release.set()
        assert await asyncio.wait_for(activation_task, 1) is Result.READY
        await registry.restore("voice_identity_enrollment")
        await _join_owner_recovery(registry)
        _assert_owner_authorized(manager, "successor-owner")
        assert await registry.register_manager(manager) is Result.READY
        _assert_owner_authorized(manager, "successor-owner")
    finally:
        manager.suppress_release.set()
        for task in (register_task, activation_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (register_task, activation_task) if task is not None), return_exceptions=True)
        await registry.close()
        profile.close()


@pytest.mark.parametrize("registration_exit", ["continue", "cancel", "error"])
async def test_uncommitted_request_cannot_recover_external_owner_revocation(registration_exit):
    registry = RequestObservedRegistry()
    manager = PreparationManager()
    profile = _profile("externally-revoked-owner-profile")
    register_task = activation_task = None
    try:
        await registry.activate(profile, "old-owner", activation_required=True)
        await registry.suppress("voice_identity_enrollment")
        manager.block_suppression = True
        register_task = asyncio.create_task(registry.register_manager(manager))
        await asyncio.wait_for(manager.suppress_entered.wait(), 1)
        activation_task = asyncio.create_task(registry.activate(profile, "optional-request"))
        await asyncio.wait_for(registry.request_waiting.wait(), 1)
        activation_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await activation_task
        manager.core.require_voice_session_activation(activation_generation="external-owner")
        external_token = manager.voice_session_activation_policy_token()
        if registration_exit == "cancel":
            register_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await register_task
        elif registration_exit == "error":
            manager.suppression_error = True
            manager.suppress_release.set()
            with pytest.raises(RuntimeError, match="injected suppression failure"):
                await register_task
        else:
            manager.suppress_release.set()
            assert await register_task is Result.RUNTIME_DEGRADED
        manager.suppression_error = False
        manager.suppress_release.set()
        await registry.restore("voice_identity_enrollment")
        await _join_owner_recovery(registry)
        assert manager.voice_session_activation_policy_token() == external_token
        assert manager.core._voice_session_activation_authority_generation == "external-owner"
        assert manager not in registry._attach_pending and manager not in registry._detach_pending
        await _assert_owner_input_closed(manager)
    finally:
        manager.suppress_release.set()
        for task in (register_task, activation_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (register_task, activation_task) if task is not None), return_exceptions=True)
        await registry.close()
        profile.close()
