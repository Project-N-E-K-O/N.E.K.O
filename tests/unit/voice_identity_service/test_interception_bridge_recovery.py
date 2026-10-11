"""Terminal captures recover only after owned physical retirement."""
from __future__ import annotations

import asyncio
import pytest

from main_logic.voice_identity_service.interception_runtime import PrewireInterceptionFactory
from main_logic.voice_input.interception import ActiveSessionInterceptionBridge, InterceptionDecision, InterceptionResult
from tests.unit.voice_identity_service.test_interception_runtime import _config, _Scorer, _Classifier, _Tse

pytestmark = [pytest.mark.unit_fast, pytest.mark.asyncio]


def factory(*, scorer=None, worker_factory=None):
    return PrewireInterceptionFactory(_config(), score_backend=scorer or _Scorer(), classifier=_Classifier(),
                                      tse_factory=worker_factory or (lambda _: _Tse()))


async def feed(bridge, count=1600, value=2):
    return await bridge.process(bytes((value, 0)) * count, sample_rate_hz=16000,
        generation="authority", ingress_token="route", captured_at=None)


async def test_real_score_failure_restarts_on_new_frame_without_replaying_failed_pcm():
    class FailOnce(_Scorer):
        calls = 0
        def score(self, pcm16, sample_rate_hz):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("score_failed_once")
            return super().score(pcm16, sample_rate_hz)
    scorer = FailOnce()
    prepared = factory(scorer=scorer)
    bridge = ActiveSessionInterceptionBridge(prepared)
    try:
        assert (await feed(bridge, 2000, 100)).decision is InterceptionDecision.UNAVAILABLE
        old = bridge._runtime
        assert old.is_closed and old.retired
        assert (await feed(bridge, 1600, 20)).decision is InterceptionDecision.PENDING
        new = bridge._runtime
        assert new is not old and new._stream.ingress_generation == old._stream.ingress_generation + 1
        result = await feed(bridge, 400, 30)
        assert result.decision is InterceptionDecision.KEEP
        assert result.pcm16 == b"\x13\x00" * 800
        assert new._tse_sample == 2000
        assert scorer.calls == 3
    finally:
        await bridge.close()


async def test_real_prefix_expiry_does_not_leave_bridge_permanently_stale():
    prepared = factory()
    bridge = ActiveSessionInterceptionBridge(prepared)
    try:
        await feed(bridge, 400)
        old = bridge._runtime
        old._arm_deadline(asyncio.get_running_loop().time())
        timer = old._deadline_task
        await asyncio.wait_for(timer, 1)
        assert old.is_closed and old.retired
        assert (await feed(bridge)).decision is InterceptionDecision.PENDING
        assert bridge._runtime is not old
        assert (await feed(bridge, 400)).decision is InterceptionDecision.KEEP
    finally:
        await bridge.close()


async def test_terminal_capture_does_not_revive_revoked_factory():
    prepared = factory()
    bridge = ActiveSessionInterceptionBridge(prepared)
    try:
        await feed(bridge, 400)
        old = bridge._runtime
        await old.close("capture_failed")
        prepared.close()
        for _ in range(3):
            result = await feed(bridge)
            assert result.decision is InterceptionDecision.UNAVAILABLE and not result.pcm16
            assert bridge._runtime is None
        assert prepared._runtimes == [old]
    finally:
        await bridge.close()


async def test_unavailable_without_terminal_evidence_does_not_recreate_runtime():
    class Runtime:
        is_closed = False
        calls = 0
        async def process(self, pcm16, **kwargs):
            self.calls += 1
            return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="temporary_unavailable")
        async def close(self, reason):
            self.is_closed = True
    class Factory:
        calls = 0
        def create(self, generation, *, ingress_token):
            self.calls += 1
            return Runtime()
    prepared = Factory()
    bridge = ActiveSessionInterceptionBridge(prepared)
    try:
        for _ in range(3):
            assert (await feed(bridge)).decision is InterceptionDecision.UNAVAILABLE
        assert prepared.calls == 1 and bridge._runtime.calls == 3
    finally:
        await bridge.close()


async def test_bridge_rechecks_declared_factory_authority_before_recovery_allocation():
    class Runtime:
        is_closed = False
        async def process(self, pcm16, **kwargs):
            return InterceptionResult(InterceptionDecision.PENDING)
        async def close(self, reason):
            self.is_closed = True
    class Factory:
        is_available = True
        calls = 0
        def create(self, generation, *, ingress_token):
            self.calls += 1
            return Runtime()
    prepared = Factory()
    bridge = ActiveSessionInterceptionBridge(prepared)
    try:
        await feed(bridge)
        bridge._runtime.is_closed = True
        prepared.is_available = False
        assert (await feed(bridge)).decision is InterceptionDecision.UNAVAILABLE
        assert prepared.calls == 1 and bridge._runtime is None
    finally:
        await bridge.close()


@pytest.mark.parametrize("stopped", [True, False])
async def test_two_bridges_share_authority_but_not_concurrent_capture_slot(stopped):
    class Worker(_Tse):
        can_stop = stopped
        async def close(self, *, timeout=1):
            return self.can_stop
    old_worker = Worker()
    workers = iter((old_worker, _Tse()))
    prepared = factory(worker_factory=lambda _: next(workers))
    first = ActiveSessionInterceptionBridge(prepared)
    second = ActiveSessionInterceptionBridge(prepared)
    try:
        assert (await feed(first, 400)).decision is InterceptionDecision.PENDING
        old = first._runtime
        for _ in range(2):
            assert (await feed(second)).decision is InterceptionDecision.UNAVAILABLE
        if stopped:
            await first.retire()
        else:
            with pytest.raises(RuntimeError, match="retirement_timeout"):
                await first.retire()
            for _ in range(2):
                assert (await feed(second)).decision is InterceptionDecision.UNAVAILABLE
                assert second._runtime is None and not old.retired
            old_worker.can_stop = True
            await first.retire()
        assert prepared.is_available
        assert (await feed(second)).decision is InterceptionDecision.PENDING
        assert second._runtime._stream != old._stream
        assert (await feed(second, 400)).decision is InterceptionDecision.KEEP
    finally:
        old_worker.can_stop = True
        await first.close()
        await second.close()


async def test_failed_capture_waits_for_physical_exit_before_new_frame_can_recover():
    class FailedWorker(_Tse):
        can_stop = False
        async def push(self, pcm, *, start_sample):
            raise RuntimeError("extractor_fault")
        async def close(self, *, timeout=1):
            return self.can_stop
    worker = FailedWorker()
    workers = iter((worker, _Tse()))
    prepared = factory(worker_factory=lambda _: next(workers))
    bridge = ActiveSessionInterceptionBridge(prepared)
    try:
        assert (await feed(bridge)).decision is InterceptionDecision.UNAVAILABLE
        old = bridge._runtime
        for _ in range(3):
            assert (await feed(bridge)).decision is InterceptionDecision.UNAVAILABLE
            assert not old.retired
        worker.can_stop = True
        assert (await feed(bridge)).decision is InterceptionDecision.PENDING
        assert bridge._runtime is not old
        assert (await feed(bridge, 400)).decision is InterceptionDecision.KEEP
    finally:
        worker.can_stop = True
        await bridge.close()


@pytest.mark.parametrize("late", ["keep", "exception", "invalid", "timeout", "cancel"])
async def test_late_old_process_cannot_retire_or_publish_over_same_route_successor(late):
    entered, release = asyncio.Event(), asyncio.Event()
    class Runtime:
        def __init__(self, old):
            self.old, self.is_closed = old, False
            self.close_calls = 0
        async def process(self, pcm16, **kwargs):
            if self.old:
                entered.set()
                await release.wait()
                if late == "exception":
                    raise RuntimeError("late_fault")
                if late == "invalid":
                    return None
            return InterceptionResult(InterceptionDecision.KEEP, b"\x01\x00", "filtered")
        async def close(self, reason):
            self.is_closed = True
            self.close_calls += 1
    old, successor = Runtime(True), Runtime(False)
    class Factory:
        def __init__(self):
            self.runtimes = iter((old, successor))
        def create(self, generation, *, ingress_token):
            return next(self.runtimes)
    bridge = ActiveSessionInterceptionBridge(Factory(), max_inflight=2, process_timeout_s=.1)
    pending = asyncio.create_task(feed(bridge))
    try:
        await entered.wait()
        old.is_closed = True
        assert (await feed(bridge)).decision is InterceptionDecision.KEEP
        assert bridge._runtime is successor
        if late == "cancel":
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        elif late == "timeout":
            assert (await pending).decision is InterceptionDecision.UNAVAILABLE
        else:
            release.set()
            result = await pending
            assert not result.pcm16
            assert result.decision is (InterceptionDecision.STALE if late == "keep" else InterceptionDecision.UNAVAILABLE)
        assert bridge._runtime is successor and not successor.is_closed
        assert successor.close_calls == 0
        assert (await feed(bridge)).decision is InterceptionDecision.KEEP
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await bridge.close()
