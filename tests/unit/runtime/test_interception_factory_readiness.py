"""Installation must reject known retired authority without allocating models."""

from __future__ import annotations

import asyncio

import pytest

from app.main_server.voice_identity_runtime import OwnerVoiceRuntimeRegistry
from main_logic.asr_client import VoiceIdentityActivationResult
from main_logic.voice_input.interception import ActiveSessionInterceptionBridge, InterceptionDecision
from tests.unit.voice_identity_service.test_interception_lifecycle_regressions import (
    CoreHarness,
    RegistryManager,
    RetiringTse,
    make_factory,
    process,
)
from tests.unit.voice_identity_service.test_interception_runtime import _Tse
from tests.unit.voice_input.test_interception_bridge import _Factory

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_declared_unavailable_factory_never_reports_installed():
    factory = _Factory()
    factory.is_available = False
    core = CoreHarness()
    try:
        assert not await core.set_active_session_interception_factory(factory)
        assert core._active_session_interception_bridge is None
        assert core._active_session_interception_required
        assert not factory.created
    finally:
        await core.set_active_session_interception_factory(None, interception_required=False)


@pytest.mark.parametrize("availability", [None, 1, "ready"])
async def test_declared_availability_must_be_a_boolean(availability):
    factory = _Factory()
    factory.is_available = availability
    with pytest.raises(TypeError, match="is_available"):
        ActiveSessionInterceptionBridge(factory)
    assert not factory.created


async def test_legacy_injection_without_availability_is_still_supported():
    factory = _Factory()
    bridge = ActiveSessionInterceptionBridge(factory)
    try:
        result = await bridge.process(b"\x02\x00" * 10, sample_rate_hz=16000,
                                      generation="g", ingress_token="i", captured_at=None)
        assert result.decision is InterceptionDecision.KEEP
        assert len(factory.created) == 1
    finally:
        await bridge.close()


async def test_closed_real_factory_is_not_cached_ready():
    registry = OwnerVoiceRuntimeRegistry(enforce=False)
    manager = RegistryManager()
    factory = make_factory(lambda _: _Tse())
    factory.close()
    try:
        await registry.register_manager(manager)
        assert not await registry.set_voice_interception_factory(factory)
        assert manager.core._active_session_interception_bridge is None
        assert await registry.register_manager(manager) is VoiceIdentityActivationResult.RUNTIME_DEGRADED
        assert manager not in registry._interception_manager_factories
        assert not factory._runtimes
    finally:
        await registry.close()


async def test_profile_revocation_retires_factory_and_requires_fresh_authority():
    registry = OwnerVoiceRuntimeRegistry(enforce=False)
    manager = RegistryManager()
    old = make_factory(lambda _: _Tse())
    new = make_factory(lambda _: _Tse())
    try:
        await registry.register_manager(manager)
        assert await registry.set_voice_interception_factory(old)
        await process(manager.core._active_session_interception_bridge)
        registry._revoke_interception_authority()
        assert not old.is_available
        assert not await registry.set_voice_interception_factory(old)
        assert manager.core._active_session_interception_bridge is None
        assert await registry.set_voice_interception_factory(new)
        assert await registry.register_manager(manager) is VoiceIdentityActivationResult.READY
        assert (await process(manager.core._active_session_interception_bridge)).decision is InterceptionDecision.PENDING
    finally:
        await registry.close()
        new.close()


async def test_installation_does_not_allocate_before_first_capture():
    allocated = []

    def tse_factory(stream):
        allocated.append(stream)
        return _Tse()

    registry = OwnerVoiceRuntimeRegistry(enforce=False)
    manager = RegistryManager()
    factory = make_factory(tse_factory)
    try:
        await registry.register_manager(manager)
        assert await registry.set_voice_interception_factory(factory)
        assert await registry.register_manager(manager) is VoiceIdentityActivationResult.READY
        assert not allocated and not factory._runtimes
        await process(manager.core._active_session_interception_bridge)
        assert len(allocated) == len(factory._runtimes) == 1
    finally:
        await registry.close()


async def test_factory_availability_is_checked_after_previous_owner_retires():
    core = CoreHarness()
    old_worker = RetiringTse(wait=True)
    old = make_factory(lambda _: old_worker)
    new = make_factory(lambda _: _Tse())
    task = None
    try:
        assert await core.set_active_session_interception_factory(old)
        await process(core._active_session_interception_bridge)
        task = asyncio.create_task(core.set_active_session_interception_factory(new))
        await asyncio.wait_for(old_worker.closing.wait(), 1)
        new.close()
        old_worker.release.set()
        assert not await task
        assert core._active_session_interception_bridge is None
        assert core._active_session_interception_required
        assert not new._runtimes
    finally:
        old_worker.release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await core.set_active_session_interception_factory(None, interception_required=False)
        old.close()
        new.close()
