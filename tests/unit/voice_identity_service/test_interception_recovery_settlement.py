"""Regression proofs for recovery, local delivery ownership and tail evidence."""

from __future__ import annotations

import asyncio
import itertools

import pytest

from app.main_server.voice_identity_runtime import OwnerVoiceRuntimeRegistry
from main_logic.asr_client import VoiceIdentityActivationResult
from main_logic.voice_identity_service.interception_runtime import InterceptionRuntimeConfig
from main_logic.voice_identity_service.prewire_gate.contracts import PrewireCommitStage
from main_logic.voice_identity_service.prewire_gate.ledger import PrewireTransitionError
from main_logic.voice_input.interception import InterceptionDecision, InterceptionInstallationState
from tests.unit.voice_identity_service.test_interception_lifecycle_regressions import (
    BatchedTse, RegistryManager, RetiringTse, make_factory, process,
)
from tests.unit.voice_identity_service.test_interception_runtime import _SequenceClassifier, _Tse

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


def default_config():
    return InterceptionRuntimeConfig(
        session_id="settlement", ingress_generation=1, profile_generation="profile",
        model_generation="model", config_generation="config", scoring_parameters_digest="a" * 64,
    )


async def test_alternating_owner_input_does_not_exhaust_ledger_without_any_handoff():
    runtime = make_factory(
        lambda stream: _Tse(), config=default_config(),
        classifier=_SequenceClassifier(itertools.cycle([True, False])),
    ).create("g", ingress_token=None)
    try:
        for _ in range(2400):  # 60 seconds of default 25 ms capture frames.
            result = await process(runtime)
            assert result.decision is InterceptionDecision.PENDING
            assert not result.pcm16
        records = tuple(runtime._gate._ledger._records.values())
        assert len(records) <= 128
        assert any(record.commit_stage is PrewireCommitStage.LOCAL_CANCELLED for record in records)
        assert sum(record.commit_stage is PrewireCommitStage.ENQUEUED for record in records) <= 1
        cursor = runtime._gate._ledger.asr_cursor(runtime._stream)
        assert cursor > 128 * default_config().step_samples
    finally:
        await runtime.close()
    assert all(record.commit_stage is not PrewireCommitStage.ENQUEUED
               for record in runtime._gate._ledger._records.values())


@pytest.mark.parametrize("cancel_point", ["resolve", "finish_stream", "retire", "physical_close"])
async def test_cancelled_finish_settles_drained_but_unpublished_audio(cancel_point):
    runtime = make_factory(lambda stream: BatchedTse(10000), config=default_config()).create("g", ingress_token=None)
    for _ in range(60):
        assert not (await process(runtime)).pcm16
    entered, release = asyncio.Event(), asyncio.Event()
    target = runtime if cancel_point in {"retire", "physical_close"} else runtime._gate
    attribute = {"resolve": "resolve", "finish_stream": "finish_stream", "retire": "_retire_components",
                 "physical_close": "_close_components"}[cancel_point]
    original = getattr(target, attribute)

    async def barrier(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    setattr(target, attribute, barrier)
    task = asyncio.create_task(runtime.finish())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert runtime._outgoing_audio
        assert runtime._pending_audio_bytes == sum(map(len, runtime._outgoing_audio.values()))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not runtime._outgoing_audio
        assert runtime._pending_audio_bytes == 0
        records = tuple(runtime._gate._ledger._records.values())
        assert sum(record.commit_stage is PrewireCommitStage.LOCAL_CANCELLED for record in records) == 5
        assert not any(record.commit_stage is PrewireCommitStage.ENQUEUED for record in records)
    finally:
        release.set()
        setattr(target, attribute, original)
        await runtime.close()


async def test_finish_handoff_keeps_real_delivery_pending_after_close():
    runtime = make_factory(lambda stream: BatchedTse(10000), config=default_config()).create("g", ingress_token=None)
    for _ in range(60):
        assert not (await process(runtime)).pcm16
    result = await runtime.finish()
    assert result.decision is InterceptionDecision.KEEP
    assert len(result.pcm16) == 12000 * 2
    assert not runtime._outgoing_audio
    await runtime.close()
    records = tuple(runtime._gate._ledger._records.values())
    delivered = [record for record in records if record.commit_stage is PrewireCommitStage.ENQUEUED]
    assert len(delivered) == 5
    for record in delivered:
        runtime._gate.advance_delivery(record.spec.identity, expected=PrewireCommitStage.ENQUEUED,
                                       next_stage=PrewireCommitStage.WRITTEN)
        with pytest.raises(PrewireTransitionError):
            runtime._gate.cancel_local_delivery(record.spec.identity)
        runtime._gate.advance_delivery(record.spec.identity, expected=PrewireCommitStage.WRITTEN,
                                       next_stage=PrewireCommitStage.REMOTE_CONFIRMED)
    tails = [record for record in records if record.spec.event_ended]
    assert len(tails) == 5
    assert all(not record.spec.boundary_trusted and not record.spec.independent_event for record in tails)
    assert all(record.decision_reason == "scoring_window_unsupported" for record in tails)
    assert all(not record.used_ended_micro_event_rule for record in tails)


@pytest.mark.parametrize("transition", ["native", "independent", "blocked", "mute", "focus"])
async def test_temporary_invalidation_recovers_current_authority_without_register(transition):
    registry = OwnerVoiceRuntimeRegistry(enforce=False, restore_retry_interval_seconds=.001)
    manager = RegistryManager()
    await registry.register_manager(manager)
    factory = make_factory(lambda stream: _Tse())
    assert await registry.set_voice_interception_factory(factory)
    core = manager.core
    first_bridge = core._active_session_interception_bridge
    first_installation = registry._interception_installations[manager]
    await process(first_bridge)
    try:
        if transition in {"mute", "focus"}:
            await core._apply_voice_lease_state(
                owner="core", hard_muted=transition == "mute", focus_suppressed=transition == "focus",
                reason="hard_mute" if transition == "mute" else "focus_pause", force_abort=False,
            )
            await core._apply_voice_lease_state(
                owner="core", hard_muted=False, focus_suppressed=False,
                reason="hard_unmute" if transition == "mute" else "focus_resume", force_abort=False,
            )
        else:
            core._asr_route_mode = "independent" if transition == "native" else "native"
            core._set_microphone_route(transition)
            if transition == "blocked":
                core._set_microphone_route("native")
        assert first_installation.state is not InterceptionInstallationState.INSTALLED
        task = registry._interception_retry_task
        if core._active_session_interception_bridge is None:
            assert manager in registry._interception_pending
            assert task is not None
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 1)
        assert core._active_session_interception_bridge is not None
        assert core._active_session_interception_bridge is not first_bridge
        assert await registry.register_manager(manager) is VoiceIdentityActivationResult.READY
        assert (await process(core._active_session_interception_bridge)).decision is InterceptionDecision.PENDING
        # Late notifications from the old installation cannot revoke its successor.
        first_installation.invalidate(recoverable=False)
        assert registry._interception_installations[manager].state is InterceptionInstallationState.INSTALLED
    finally:
        await registry.close()


@pytest.mark.parametrize("revoke", ["interception", "activation"])
async def test_authority_revocation_cannot_auto_restore_cached_factory(revoke):
    registry = OwnerVoiceRuntimeRegistry(enforce=False, restore_retry_interval_seconds=.001)
    manager = RegistryManager()
    await registry.register_manager(manager)
    factory = make_factory(lambda stream: _Tse())
    assert await registry.set_voice_interception_factory(factory)
    try:
        if revoke == "activation":
            manager.core.require_voice_session_activation(activation_generation="new-profile")
        else:
            manager.core.require_active_session_interception()
        assert manager not in registry._interception_pending
        assert await registry.register_manager(manager) is VoiceIdentityActivationResult.RUNTIME_DEGRADED
        assert manager.core._active_session_interception_bridge is None
        assert registry._interception_retry_task is None
        assert await registry.set_voice_interception_factory(factory)
        assert manager.core._active_session_interception_bridge is not None
    finally:
        await registry.close()


async def test_unregister_during_recovery_does_not_reinstall_removed_manager():
    registry = OwnerVoiceRuntimeRegistry(enforce=False, restore_retry_interval_seconds=.001)
    manager = RegistryManager()
    await registry.register_manager(manager)
    assert await registry.set_voice_interception_factory(make_factory(lambda stream: _Tse()))
    receipt = registry._interception_installations[manager]
    manager.core._set_microphone_route("native")
    task = registry._interception_retry_task
    await registry.unregister_manager(manager)
    receipt.invalidate(recoverable=True)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 1)
    assert manager.core._active_session_interception_bridge is None
    assert not manager.core._active_session_interception_required
    assert manager not in registry._interception_pending
    await registry.close()


async def test_recovery_waits_for_physical_retirement_before_installation():
    registry = OwnerVoiceRuntimeRegistry(enforce=False, restore_retry_interval_seconds=.001)
    manager = RegistryManager()
    worker = RetiringTse(wait=True)
    await registry.register_manager(manager)
    assert await registry.set_voice_interception_factory(make_factory(lambda stream: worker))
    await process(manager.core._active_session_interception_bridge)
    manager.core._set_microphone_route("native")
    try:
        await asyncio.wait_for(worker.closing.wait(), 1)
        assert manager.core._active_session_interception_bridge is None
        assert manager in registry._interception_pending
        worker.release.set()
        await asyncio.wait_for(asyncio.shield(registry._interception_retry_task), 2)
        assert manager.core._active_session_interception_bridge is not None
        assert worker.close_calls >= 1
    finally:
        worker.release.set()
        await registry.close()


async def test_required_unavailable_factory_does_not_schedule_recovery():
    registry = OwnerVoiceRuntimeRegistry(enforce=False, restore_retry_interval_seconds=.001)
    manager = RegistryManager()
    await registry.register_manager(manager)
    assert not await registry.set_voice_interception_factory(None, interception_required=True)
    assert await registry.register_manager(manager) is VoiceIdentityActivationResult.RUNTIME_DEGRADED
    assert not registry._interception_pending
    assert registry._interception_retry_task is None
    await registry.close()


@pytest.mark.parametrize("failure", ["cancel", "reject"])
async def test_process_failure_settles_only_unpublished_batch(failure):
    runtime = make_factory(lambda stream: _Tse(), config=default_config()).create("g", ingress_token=None)
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime._gate.resolve
    count = 0

    async def third_resolution(submission):
        nonlocal count
        count += 1
        if count == 3:
            entered.set()
            await release.wait()
            if failure == "reject":
                raise RuntimeError("injected_resolution_failure")
        return await original(submission)

    runtime._gate.resolve = third_resolution
    task = asyncio.create_task(process(runtime, samples=24000))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert len(runtime._outgoing_audio) == 2
        if failure == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            result = await task
            assert result.decision is InterceptionDecision.UNAVAILABLE
            assert not result.pcm16
        assert not runtime._outgoing_audio
        assert runtime._pending_audio_bytes == 0
        assert sum(record.commit_stage is PrewireCommitStage.LOCAL_CANCELLED
                   for record in runtime._gate._ledger._records.values()) == 2
    finally:
        release.set()
        await runtime.close()


async def test_watchdog_rechecks_revocation_after_waiting_for_another_manager():
    registry = OwnerVoiceRuntimeRegistry(enforce=False, restore_retry_interval_seconds=.001)
    first, second = RegistryManager(), RegistryManager()
    await registry.register_manager(first)
    await registry.register_manager(second)
    assert await registry.set_voice_interception_factory(make_factory(lambda stream: _Tse()))
    entered, release = asyncio.Event(), asyncio.Event()
    original = first.set_active_session_interception_factory

    async def paused_setter(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    first.set_active_session_interception_factory = paused_setter
    first.core._set_microphone_route("native")
    second.core._set_microphone_route("native")
    try:
        await asyncio.wait_for(entered.wait(), 1)
        second.core.require_active_session_interception()
        release.set()
        await asyncio.wait_for(asyncio.shield(registry._interception_retry_task), 2)
        assert first.core._active_session_interception_bridge is not None
        assert second.core._active_session_interception_bridge is None
        assert await registry.register_manager(second) is VoiceIdentityActivationResult.RUNTIME_DEGRADED
    finally:
        release.set()
        first.set_active_session_interception_factory = original
        await registry.close()
