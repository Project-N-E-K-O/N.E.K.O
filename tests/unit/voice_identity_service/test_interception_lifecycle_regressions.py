"""Exercise actual interception composition and Core/app ownership boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import gc
import itertools
import time
from unittest.mock import AsyncMock
import weakref

import numpy as np
import pytest

from app.main_server.voice_identity_runtime import OwnerVoiceRuntimeRegistry
from main_logic.voice_identity_service.interception_runtime import (
    InterceptionRuntimeError,
    PrewireInterceptionFactory,
)
from main_logic.voice_identity_service.tse.contracts import TseAudioChunk
from main_logic.voice_identity_service.prewire_gate.contracts import PrewireCommitStage
from main_logic.voice_input.activation import ActivationGeneration, AudioFrame
from main_logic.voice_input.interception import ActiveSessionInterceptionBridge, InterceptionDecision
from main_logic.core.asr_runtime import VoiceSessionActivationRouteContext
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus
from tests.unit.asr_runtime.test_active_session_interception import _Runtime as CoreHarness
from tests.unit.voice_identity_service.test_interception_runtime import (
    _Classifier, _Scorer, _SequenceClassifier, _Tse, _config,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


class BatchedTse(_Tse):
    def __init__(self, batch: int = 6):
        super().__init__()
        self.batch = batch
        self.frames = []
        self.origin = 0

    async def push(self, pcm, *, start_sample):
        if not self.frames:
            self.origin = start_sample
        self.frames.append(pcm.copy())
        return await self.flush() if len(self.frames) >= self.batch else []

    async def flush(self):
        if not self.frames:
            return []
        samples = np.concatenate(self.frames)
        self.frames.clear()
        return [TseAudioChunk(self.origin, self.origin + samples.size, samples)]


class RetiringTse(_Tse):
    def __init__(self, *, can_stop: bool = False, wait: bool = False, fail_start: bool = False):
        super().__init__()
        self.can_stop = can_stop
        self.wait = wait
        self.fail_start = fail_start
        self.closing = asyncio.Event()
        self.release = asyncio.Event()
        self.close_calls = 0

    async def start(self, *, timeout=1.0):
        if self.fail_start:
            raise TimeoutError("startup_failed_with_live_resource")
        await super().start(timeout=timeout)

    async def close(self, *, timeout=1.0):
        self.close_calls += 1
        self.closing.set()
        if self.wait:
            await self.release.wait()
            self.can_stop = True
        return self.can_stop


def make_factory(tse_factory, *, classifier=None, config=None):
    return PrewireInterceptionFactory(
        config or _config(), score_backend=_Scorer(),
        classifier=classifier or _Classifier(), tse_factory=tse_factory,
    )


def pcm(value, samples=400):
    return np.full(samples, value, dtype="<i2").tobytes()


async def process(runtime, value=100, *, generation="g", samples=400):
    return await runtime.process(
        pcm(value, samples), sample_rate_hz=16000, generation=generation,
        ingress_token=None, captured_at=None,
    )


@pytest.mark.parametrize("batch", [1, 2, 3, 4, 6, 9, 40])
@pytest.mark.parametrize("decisions", [
    [True] * 33, [True, True, False] * 11,
    ([True, False, True, True, True] * 7)[:33], [False] * 33,
])
async def test_exact_authorized_pcm_survives_delayed_extraction_gaps_and_finish(batch, decisions):
    runtime = make_factory(
        lambda stream: BatchedTse(batch), classifier=_SequenceClassifier(decisions),
    ).create("g", ingress_token=None)
    actual = []
    try:
        for value in range(1, 37):
            result = await process(runtime, value * 100)
            assert result.decision in {InterceptionDecision.KEEP, InterceptionDecision.PENDING}
            actual.append(result.pcm16)
        final = await runtime.finish()
        assert final.decision in {InterceptionDecision.KEEP, InterceptionDecision.DROP}
        actual.append(final.pcm16)
        expected = []
        cursor = 0
        for owner, group in itertools.groupby(decisions):
            count = len(list(group))
            if owner and count >= 2:
                expected.extend(pcm(value * 100 - 1) for value in range(cursor + 1, cursor + count + 1))
            cursor += count
        assert b"".join(actual) == b"".join(expected)
    finally:
        await runtime.close()


@pytest.mark.parametrize("frames,expected_samples", [(4, 0), (5, 800)])
async def test_finish_keeps_confirmed_prefix_but_never_promotes_single_owner_window(frames, expected_samples):
    runtime = make_factory(lambda stream: BatchedTse(6)).create("g", ingress_token=None)
    for value in range(1, frames + 1):
        assert not (await process(runtime, value * 100)).pcm16
    final = await runtime.finish()
    assert final.pcm16 == b"".join(pcm(value * 100 - 1) for value in range(1, expected_samples // 400 + 1))


async def test_factory_reserves_unstarted_owner_and_releases_only_closed_owner():
    factory = make_factory(lambda stream: _Tse())
    old = factory.create("g1", ingress_token=None)
    try:
        with pytest.raises(InterceptionRuntimeError):
            factory.create("g2", ingress_token=None)
    finally:
        await old.close()
    new = factory.create("g2", ingress_token=None)
    await new.close()


async def test_factory_does_not_retain_completed_runtime_history():
    factory = make_factory(lambda stream: _Tse())
    references = []
    for generation in range(100):
        runtime = factory.create(generation, ingress_token=None)
        references.append(weakref.ref(runtime))
        await process(runtime, generation=generation, samples=1600)
        await runtime.close()
    del runtime
    await asyncio.sleep(0)  # Run completion callbacks before checking ownership.
    gc.collect()
    assert sum(reference() is not None for reference in references) <= 1


async def test_startup_failure_and_cancelled_waiter_keep_physical_retirement_owner():
    worker = RetiringTse(wait=True, fail_start=True)
    factory = make_factory(lambda stream: worker)
    runtime = factory.create("g", ingress_token=None)
    task = asyncio.create_task(process(runtime))
    try:
        await asyncio.wait_for(worker.closing.wait(), 1)
        assert not runtime.retirement_confirmed
        with pytest.raises(InterceptionRuntimeError):
            factory.create("new", ingress_token=None)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        worker.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()
    assert runtime.retirement_confirmed
    new = factory.create("new", ingress_token=None)
    await new.close()


async def test_bridge_retries_failed_close_and_recovers_after_physical_exit():
    old = RetiringTse()
    workers = iter([old, _Tse()])
    factory = make_factory(lambda stream: next(workers))
    bridge = ActiveSessionInterceptionBridge(factory)
    await process(bridge, generation="g1")
    try:
        assert (await process(bridge, generation="g2")).decision is InterceptionDecision.UNAVAILABLE
        old.can_stop = True
        assert (await process(bridge, generation="g2")).decision is InterceptionDecision.PENDING
        assert old.close_calls == 2
    finally:
        old.can_stop = True
        await bridge.close()


async def test_close_timeout_does_not_cancel_owned_cleanup_or_require_new_input():
    worker = RetiringTse(wait=True)
    runtime_factory = make_factory(lambda stream: worker)
    bridge = ActiveSessionInterceptionBridge(runtime_factory, close_timeout_s=0.02)
    await process(bridge)
    closing = asyncio.create_task(bridge.close())
    try:
        await asyncio.wait_for(worker.closing.wait(), 1)
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(closing, 0.5)
        cleanup = bridge._retirement_task
        worker.release.set()
        assert await asyncio.wait_for(asyncio.shield(cleanup), 1)
        assert worker.close_calls == 1
    finally:
        worker.release.set()
        await asyncio.gather(closing, return_exceptions=True)
        await bridge.close()


@pytest.mark.parametrize("revoke", ["setter", "authority", "route"])
async def test_core_never_replaces_unconfirmed_owner_even_after_failed_task(revoke):
    core = CoreHarness()
    old = RetiringTse()
    await core.set_active_session_interception_factory(make_factory(lambda stream: old))
    await process(core._active_session_interception_bridge)
    new = _Tse()
    replacement = make_factory(lambda stream: new)
    if revoke == "authority":
        core.require_active_session_interception()
    elif revoke == "route":
        core._invalidate_active_session_interception_now("route_changed")
    try:
        assert not await core.set_active_session_interception_factory(replacement)
        assert not await core.set_active_session_interception_factory(replacement)
        assert core._active_session_interception_bridge is None
        assert core._active_session_interception_required
        assert not new.started
        old.can_stop = True
        assert await core.set_active_session_interception_factory(replacement)
        await process(core._active_session_interception_bridge)
        assert new.started
    finally:
        old.can_stop = True
        await core.set_active_session_interception_factory(None, interception_required=False)


async def test_core_cancelled_setter_keeps_cleanup_and_latest_replacement_wins():
    core = CoreHarness()
    old = RetiringTse(wait=True)
    await core.set_active_session_interception_factory(make_factory(lambda stream: old))
    await process(core._active_session_interception_bridge)
    first = asyncio.create_task(core.set_active_session_interception_factory(make_factory(lambda stream: _Tse())))
    await asyncio.wait_for(old.closing.wait(), 1)
    cleanup = core._active_session_interception_retirement
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not cleanup.cancelled()
    assert core._active_session_interception_bridge is None
    latest = make_factory(lambda stream: _Tse())
    second = asyncio.create_task(core.set_active_session_interception_factory(latest))
    old.release.set()
    assert await second
    assert core._active_session_interception_bridge._factory is latest
    await core.set_active_session_interception_factory(None, interception_required=False)


async def test_disable_stays_fail_closed_until_retirement_completes():
    core = CoreHarness()
    old = RetiringTse(wait=True)
    await core.set_active_session_interception_factory(make_factory(lambda stream: old))
    await process(core._active_session_interception_bridge)
    disabling = asyncio.create_task(core.set_active_session_interception_factory(None, interception_required=False))
    try:
        await asyncio.wait_for(old.closing.wait(), 1)
        assert core._active_session_interception_required
    finally:
        old.release.set()
        await disabling
    assert not core._active_session_interception_required


class RegistryManager:
    def __init__(self):
        self.core = CoreHarness()

    def require_active_session_interception(self):
        return self.core.require_active_session_interception()

    async def set_active_session_interception_factory(self, factory, *, interception_required, installation=None):
        return await self.core.set_active_session_interception_factory(
            factory, interception_required=interception_required, installation=installation,
        )


async def test_same_factory_registration_reinstalls_revoked_core_bridge():
    registry = OwnerVoiceRuntimeRegistry(enforce=False)
    manager = RegistryManager()
    factory = make_factory(lambda stream: _Tse())
    await registry.register_manager(manager)
    try:
        assert await registry.set_voice_interception_factory(factory)
        await process(manager.core._active_session_interception_bridge)
        assert await registry.set_voice_interception_factory(factory)
        assert manager.core._active_session_interception_bridge is not None
        assert (await process(manager.core._active_session_interception_bridge)).decision is InterceptionDecision.PENDING
    finally:
        await registry.close()


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_both_core_receivers_get_exact_ordered_target_pcm(route):
    core = CoreHarness()
    core._asr_route_mode = route
    core._voice_session_activation_degraded = False
    core._voice_activation_handoff = None
    generation = ActivationGeneration("session", 1, 1, 1, 1, "core")
    core._capture_voice_session_activation_generation = lambda: generation
    core._voice_input_accepts_pcm = lambda: True
    if route == "native":
        receiver = core.session.stream_audio = AsyncMock()
    else:
        receiver = core._asr_runtime.submit = AsyncMock(return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED))
        core._independent_asr_provider = "fixture"
        core._ingress_token_matches = lambda token: True
    factory = make_factory(lambda stream: BatchedTse(6))
    assert await core.set_active_session_interception_factory(factory)
    try:
        for sequence in range(18):
            captured_at = time.time()
            frame = AudioFrame(
                sequence=sequence, sample_start=sequence * 400, sample_end=(sequence + 1) * 400,
                captured_at=captured_at, sample_rate=16000, pcm=pcm((sequence + 1) * 100), generation=generation,
                context=VoiceSessionActivationRouteContext(
                    speech_probability=1.0, rnnoise_available=True, rnnoise_evidence=None,
                    ingress_token=core._capture_ingress_token(), captured_at=captured_at,
                ),
            )
            await core._route_voice_session_activation_output(frame, generation)
        received = b"".join(call.args[0] if route == "native" else call.args[0].pcm16 for call in receiver.await_args_list)
        assert received == b"".join(pcm(value * 100 - 1) for value in range(1, 16))
    finally:
        await core.set_active_session_interception_factory(None, interception_required=False)


async def test_pending_audio_metadata_capacity_fails_closed():
    config = replace(_config(), extraction_max_pending_events=2)
    runtime = make_factory(lambda stream: BatchedTse(40), config=config).create("g", ingress_token=None)
    results = [await process(runtime) for _ in range(6)]
    assert results[-1].decision is InterceptionDecision.UNAVAILABLE
    assert all(not result.pcm16 for result in results)
    await runtime.close()


async def test_pending_authorized_pcm_capacity_fails_closed_and_releases_budget():
    # The legal confirmation cache holds 2,000 samples. Delayed extraction
    # releases several already-confirmed intervals together, exceeding the
    # runtime's 3,200-sample retained output budget before any public return.
    config = replace(_config(), max_held_pcm_bytes=6400)
    runtime = make_factory(lambda stream: BatchedTse(14), config=config).create("g", ingress_token=None)
    results = [await process(runtime) for _ in range(14)]
    assert all(not result.pcm16 for result in results)
    assert results[-1].decision is InterceptionDecision.UNAVAILABLE
    assert results[-1].reason == "authorized_audio_capacity"
    assert runtime._pending_audio_bytes == 0
    assert not runtime._pending_audio
    await runtime.close()


async def test_concurrent_setters_publish_only_latest_factory():
    core = CoreHarness()
    worker = RetiringTse(wait=True)
    await core.set_active_session_interception_factory(make_factory(lambda stream: worker))
    await process(core._active_session_interception_bridge)
    first = asyncio.create_task(core.set_active_session_interception_factory(None, interception_required=False))
    await asyncio.wait_for(worker.closing.wait(), 1)
    latest = make_factory(lambda stream: _Tse())
    second = asyncio.create_task(core.set_active_session_interception_factory(latest))
    try:
        # Let the second setter revoke the first operation before releasing
        # the real worker barrier; both setters wait for the same cleanup.
        await asyncio.sleep(0)
        worker.release.set()
        assert await asyncio.gather(first, second) == [False, True]
        assert core._active_session_interception_required
        assert core._active_session_interception_bridge._factory is latest
    finally:
        worker.release.set()
        await asyncio.gather(first, second, return_exceptions=True)
        await core.set_active_session_interception_factory(None, interception_required=False)


async def test_disabled_interception_route_invalidation_preserves_raw_outlet():
    core = CoreHarness()
    core._invalidate_active_session_interception_now("route_changed")
    assert not core._active_session_interception_required
    generation = ActivationGeneration("session", 1, 1, 1, 1, "core")
    original = pcm(100)
    frame = AudioFrame(
        sequence=0, sample_start=0, sample_end=400, captured_at=time.time(),
        sample_rate=16000, pcm=original, generation=generation,
        context=VoiceSessionActivationRouteContext(
            speech_probability=1.0, rnnoise_available=True, rnnoise_evidence=None,
            ingress_token=core._capture_ingress_token(), captured_at=time.time(),
        ),
    )
    assert await core._intercept_active_session_frame(frame, generation, frame.context) == original


async def test_factory_pruning_preserves_delivery_ledger_owned_by_caller():
    factory = make_factory(lambda stream: _Tse())
    old = factory.create("g", ingress_token=None)
    await process(old, samples=1600)
    identity = next(iter(old._pending_audio))
    assert (await process(old)).decision is InterceptionDecision.KEEP
    delivery_owner = old._gate
    await old.close()
    successor = factory.create("new", ingress_token=None)
    assert old not in factory._runtimes
    try:
        # Removing a factory's resource reference must not erase evidence a
        # separate delivery owner still needs to settle after retirement.
        delivery_owner.advance_delivery(
            identity, expected=PrewireCommitStage.ENQUEUED, next_stage=PrewireCommitStage.WRITTEN,
        )
        record = delivery_owner.advance_delivery(
            identity, expected=PrewireCommitStage.WRITTEN, next_stage=PrewireCommitStage.REMOTE_CONFIRMED,
        )
        assert record.commit_stage is PrewireCommitStage.REMOTE_CONFIRMED
    finally:
        await successor.close()


async def test_flush_failure_never_returns_confirmed_audio_or_raw_fallback():
    class FailingFlushTse(BatchedTse):
        async def flush(self):
            raise RuntimeError("flush_failed")

    runtime = make_factory(lambda stream: FailingFlushTse(40)).create("g", ingress_token=None)
    for _ in range(5):
        await process(runtime)
    result = await runtime.finish()
    assert result.decision is InterceptionDecision.UNAVAILABLE
    assert not result.pcm16
    assert runtime.retired


async def test_cancelled_finish_keeps_component_cleanup_owned_until_exit():
    worker = RetiringTse(wait=True)
    factory = make_factory(lambda stream: worker)
    runtime = factory.create("g", ingress_token=None)
    await process(runtime, samples=2000)
    finishing = asyncio.create_task(runtime.finish())
    try:
        await asyncio.wait_for(worker.closing.wait(), 1)
        finishing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await finishing
        with pytest.raises(InterceptionRuntimeError):
            factory.create("new", ingress_token=None)
        assert not runtime.retired
    finally:
        worker.release.set()
        await asyncio.gather(finishing, return_exceptions=True)
        await runtime.close()
    assert runtime.retired
    assert worker.close_calls == 1


async def test_worker_close_exception_preserves_retryable_owner():
    class FailingCloseTse(RetiringTse):
        async def close(self, *, timeout=1.0):
            if not self.can_stop:
                raise RuntimeError("physical_close_failed")
            return await super().close(timeout=timeout)

    worker = FailingCloseTse()
    factory = make_factory(lambda stream: worker)
    bridge = ActiveSessionInterceptionBridge(factory)
    await process(bridge)
    try:
        with pytest.raises(RuntimeError):
            await bridge.close()
        with pytest.raises(InterceptionRuntimeError):
            factory.create("new", ingress_token=None)
    finally:
        worker.can_stop = True
        await bridge.close()
    assert factory._runtimes[0].retired


@pytest.mark.parametrize("tse_factory", [None, lambda stream: None])
async def test_missing_tse_factory_or_worker_never_releases_pcm(tse_factory):
    runtime = make_factory(tse_factory).create("g", ingress_token=None)
    result = await process(runtime)
    assert result.decision is InterceptionDecision.UNAVAILABLE
    assert not result.pcm16
    await runtime.close()
    assert runtime.retired


@pytest.mark.parametrize("overrides", [
    {"required_consistent_observations": 2}, {"owner_streak_required": 0},
    {"prefix_deadline_seconds": float("nan")},
])
async def test_invalid_authorization_policy_cannot_reserve_a_runtime(overrides):
    allocated = []
    def allocate(stream):
        allocated.append(stream)
        return _Tse()
    with pytest.raises(ValueError):
        make_factory(allocate, config=replace(_config(), **overrides))
    assert not allocated


async def test_core_legacy_state_initializes_retirement_ownership_fields():
    core = CoreHarness()
    del core._active_session_interception_retiring_bridge
    del core._active_session_interception_retirement
    core._ensure_asr_runtime_state()
    assert core._active_session_interception_retiring_bridge is None
    assert core._active_session_interception_retirement is None
    assert not core._active_session_interception_required
