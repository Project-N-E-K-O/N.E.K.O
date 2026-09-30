from __future__ import annotations

import pytest

from app.main_server.voice_identity_runtime import OwnerVoiceRuntimeRegistry
from main_logic.asr_client import VoiceIdentityActivationResult


class _InterceptionRuntime:
    def __init__(self, *, result: bool = True) -> None:
        self.result = result
        self.calls: list[tuple[object, bool]] = []

    async def set_active_session_interception_factory(
        self,
        factory,
        *,
        interception_required: bool,
    ) -> bool:
        self.calls.append((factory, interception_required))
        return self.result


class _Manager:
    def __init__(self, *, result: bool = True) -> None:
        self._runtime = _InterceptionRuntime(result=result)
        self.revoke_calls = 0

    async def set_active_session_interception_factory(self, factory, *, interception_required):
        return await self._runtime.set_active_session_interception_factory(
            factory,
            interception_required=interception_required,
        )

    def require_active_session_interception(self):
        self.revoke_calls += 1


class _Factory:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


pytestmark = pytest.mark.asyncio


async def test_interception_disabled_preserves_manager_without_setter_call():
    registry = OwnerVoiceRuntimeRegistry(enforce=False)
    manager = _Manager()

    assert await registry.register_manager(manager) is VoiceIdentityActivationResult.READY
    assert manager._runtime.calls == []
    await registry.close()
    assert manager._runtime.calls == []


async def test_required_without_factory_installs_fail_closed_policy_and_reports_degraded():
    registry = OwnerVoiceRuntimeRegistry(enforce=False)
    manager = _Manager()

    assert not await registry.set_voice_interception_factory(
        None,
        interception_required=True,
    )
    result = await registry.register_manager(manager)
    assert result is VoiceIdentityActivationResult.RUNTIME_DEGRADED
    assert manager._runtime.calls == [(None, True)]
    await registry.close()


async def test_factory_is_installed_and_unregistered_manager_is_retired():
    registry = OwnerVoiceRuntimeRegistry(enforce=False)
    manager = _Manager()
    factory = _Factory()
    await registry.register_manager(manager)

    assert await registry.set_voice_interception_factory(
        factory,
        interception_required=True,
    )
    assert manager._runtime.calls == [(factory, True)]
    await registry.unregister_manager(manager)
    assert manager._runtime.calls[-1] == (None, False)
    await registry.close()
    assert factory.closed


async def test_repeated_register_does_not_replace_same_manager_bridge():
    registry = OwnerVoiceRuntimeRegistry(enforce=False)
    manager = _Manager()
    factory = _Factory()
    await registry.register_manager(manager)
    assert await registry.set_voice_interception_factory(factory)
    assert await registry.register_manager(manager) is VoiceIdentityActivationResult.READY
    assert manager._runtime.calls == [(factory, True)]
    await registry.close()


async def test_revoke_fences_old_factory_before_activation_replacement():
    registry = OwnerVoiceRuntimeRegistry(enforce=False)
    manager = _Manager()
    factory = _Factory()
    await registry.register_manager(manager)
    assert await registry.set_voice_interception_factory(factory)

    registry._revoke_interception_authority()
    assert manager.revoke_calls == 2
    assert registry._interception_factory is None
    assert registry._interception_required is True
    assert factory.closed
    await registry.close()


async def test_failed_setter_is_pending_and_not_reported_ready():
    registry = OwnerVoiceRuntimeRegistry(enforce=False)
    manager = _Manager(result=False)
    factory = _Factory()
    await registry.register_manager(manager)

    assert not await registry.set_voice_interception_factory(
        factory,
        interception_required=True,
    )
    assert manager in registry._interception_pending
    await registry.close()


async def test_disable_failure_is_pending_for_detach_retry():
    registry = OwnerVoiceRuntimeRegistry(enforce=False)
    manager = _Manager()
    factory = _Factory()
    await registry.register_manager(manager)
    assert await registry.set_voice_interception_factory(factory)

    manager._runtime.result = False
    assert not await registry.set_voice_interception_factory(
        None,
        interception_required=False,
    )
    assert manager in registry._interception_pending
    await registry.close()
