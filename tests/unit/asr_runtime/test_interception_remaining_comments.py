"""Known mixed delivery and pre-start slot handover retain exact ownership."""

import asyncio
from dataclasses import replace
import time
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client.audio_ranges import RangedAudioBuffer
from main_logic.asr_client.input_delivery import InputAudioDeliveryLedger
from main_logic.voice_input.activation import OutputCommit
from main_logic.voice_input.interception import (
    ActiveSessionInterceptionBridge, InterceptionDecision, InterceptionResult, InterceptionRetirementPending,
)
from main_logic.voice_turn.audio_delivery import AudioDeliveryTag
from main_logic.voice_turn.interception_events import (
    InterceptionDeliveryReceipt, InterceptionDeliveryStage as Stage, InterceptionOutputKind,
)
from main_logic.voice_identity_service.prewire_gate.contracts import PrewireCommitStage
from tests.unit.asr_runtime.test_active_session_interception import _frame
from tests.unit.asr_runtime.test_interception_event_delivery import _core, _Factory, _EventRuntime
from tests.unit.asr_runtime.test_interception_receipt_cleanup import _output
from tests.unit.voice_identity_service.test_interception_bridge_recovery import factory, feed, _Tse
from tests.unit.voice_identity_service.test_interception_output_delivery import push


@pytest.mark.parametrize("discard_first", [True, False])
def test_known_mixed_tag_settles_only_after_every_retained_sample_is_owned(discard_first):
    stages = []
    tag = AudioDeliveryTag(12, stages.append)
    if discard_first:
        tag.observe(0, 3, Stage.NOT_SENT)
    tag.observe(3, 12, Stage.TRANSPORT_WRITTEN)
    assert not tag.settled and Stage.UNKNOWN not in stages
    tag.observe(3, 8, Stage.TRANSPORT_OWNED)
    assert not tag.settled
    tag.observe(8, 12, Stage.TRANSPORT_OWNED)
    if not discard_first:
        assert not tag.settled
        tag.observe(0, 3, Stage.NOT_SENT)
    assert tag.settled
    assert stages == [Stage.PARTIALLY_DELIVERED]
    tag.observe(0, 3, Stage.NOT_SENT)
    tag.observe(3, 12, Stage.UNKNOWN)  # Late cleanup cannot undo settled ownership.
    assert stages == [Stage.PARTIALLY_DELIVERED]


@pytest.mark.parametrize("unknown_after_ownership", [True, False])
def test_true_unknown_cannot_be_relabelled_as_known_mixed_delivery(unknown_after_ownership):
    stages = []
    tag = AudioDeliveryTag(12, stages.append)
    tag.observe(3, 12, Stage.TRANSPORT_WRITTEN)
    if unknown_after_ownership:
        tag.observe(3, 12, Stage.TRANSPORT_OWNED)
    tag.observe(0, 3, Stage.UNKNOWN)
    tag.observe(0, 3, Stage.NOT_SENT)
    tag.observe(3, 12, Stage.TRANSPORT_OWNED)
    assert not tag.settled
    assert stages == [Stage.UNKNOWN]


def test_mixed_tag_requires_complete_disjoint_evidence_and_owner_acceptance():
    stages = []
    tag = AudioDeliveryTag(12, stages.append)
    tag.observe(0, 3, Stage.NOT_SENT)
    tag.observe(4, 12, Stage.TRANSPORT_WRITTEN)
    tag.observe(4, 12, Stage.TRANSPORT_OWNED)
    assert not tag.settled and stages == []  # Sample 3 remains unresolved.
    with pytest.raises(ValueError, match="subrange evidence"):
        tag.observe(0, 12, Stage.PARTIALLY_DELIVERED)
    rejected = AudioDeliveryTag(12, lambda stage: stage is not Stage.PARTIALLY_DELIVERED)
    rejected.observe(0, 3, Stage.NOT_SENT)
    rejected.observe(3, 12, Stage.TRANSPORT_WRITTEN)
    with pytest.raises(RuntimeError, match="owner rejected"):
        rejected.observe(3, 12, Stage.TRANSPORT_OWNED)
    assert not rejected.settled
    tag.observe(5, 6, Stage.NOT_SENT)  # Contradictory discard of written audio.
    assert not tag.settled and stages == [Stage.UNKNOWN]


@pytest.mark.asyncio
async def test_mixed_receipt_reaches_real_bridge_runtime_and_gate_without_full_write_or_ack():
    bridge, result, tags = await _output("mixed-settlement")
    try:
        event, tag = result.events[0], tags[0]
        runtime = bridge._runtime
        identity = runtime._delivery_events[event.interval_id][1]
        tag.observe(0, 100, Stage.NOT_SENT)
        tag.observe(100, tag.sample_count, Stage.TRANSPORT_WRITTEN)
        assert runtime._gate.get_interval_record(identity).commit_stage is PrewireCommitStage.ENQUEUED
        tag.observe(100, tag.sample_count, Stage.TRANSPORT_OWNED)
        assert tag.settled and tag._reported == {Stage.PARTIALLY_DELIVERED}
        assert event.interval_id not in runtime._delivery_events
        assert runtime._gate.get_interval_record(identity).commit_stage is PrewireCommitStage.PARTIALLY_DELIVERED
        assert not runtime.record_delivery(InterceptionDeliveryReceipt(event, Stage.NOT_SENT, time.monotonic()))
        assert not runtime.record_delivery(InterceptionDeliveryReceipt(event, Stage.TRANSPORT_WRITTEN, time.monotonic()))
        assert not runtime.record_delivery(InterceptionDeliveryReceipt(
            event, Stage.PROVIDER_CONFIRMED, time.monotonic(), confirmation_id="not-a-whole-interval-ack",
        ))
        uncertain = result.events[1]
        tags[1].observe(0, tags[1].sample_count, Stage.UNKNOWN)
        assert not runtime.record_delivery(InterceptionDeliveryReceipt(uncertain, Stage.PARTIALLY_DELIVERED, time.monotonic()))
        assert uncertain.interval_id in runtime._delivery_events
    finally:
        await bridge.close()


@pytest.mark.asyncio
async def test_repeated_real_trim_settles_all_three_ledgers_without_capacity_recovery():
    runtime = factory().create("owner", ingress_token=None)
    ledger = InputAudioDeliveryLedger()
    delivered = 0
    try:
        for index in range(400):
            result = await push(runtime, 1600 if index == 0 else 400)
            for event in result.events:
                if event.kind is not InterceptionOutputKind.AUDIO:
                    continue
                def report(stage, event=event):
                    return runtime.record_delivery(InterceptionDeliveryReceipt(event, stage, time.monotonic()))
                tag = AudioDeliveryTag(len(event.pcm16) // 2, report)
                ledger.register(event.asr_start_sample, event.asr_end_sample, tag)
                buffer = RangedAudioBuffer(capacity_ms=1000)
                buffer.on_discard = ledger.discard
                buffer.append(event.pcm16, start_sample=event.asr_start_sample)
                buffer.trim_to_bytes(len(event.pcm16) - 200)
                spans = ledger.spans(buffer.spans, len(buffer.peek()) // 2)
                buffer.drain()
                for span in spans:
                    span.observe(Stage.TRANSPORT_WRITTEN)
                    span.observe(Stage.TRANSPORT_OWNED)
                assert tag.settled and tag._reported == {Stage.PARTIALLY_DELIVERED}
                delivered += 1
            assert len(ledger._tags) <= 1
            assert not runtime._delivery_events
            assert len(runtime._gate._ledger._records) <= 128
            assert len(runtime._owned_delivery_events) <= 128
        assert delivered > 300 and not runtime.is_closed
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["release", "cancel", "revoke", "close", "timeout", "generation_change", "bridge_swap", "capacity", "slot_taken"])
async def test_core_first_frame_waits_for_real_shared_slot_without_reviving_failed_capture(outcome):
    entered, release, attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    class Worker(_Tse):
        async def close(self, *, timeout=1):
            entered.set()
            await release.wait()
            return True
    workers = iter((Worker(), _Tse()))
    underlying = factory(worker_factory=lambda _: next(workers))
    class CountingFactory:
        calls = 0
        @property
        def is_available(self):
            return underlying.is_available
        def create(self, *args, **kwargs):
            self.calls += 1
            attempted.set()
            return underlying.create(*args, **kwargs)
        def close(self):
            underlying.close()
    counted = CountingFactory()
    old_bridge = ActiveSessionInterceptionBridge(underlying)
    core, generation = _core()
    core._send_voice_session_activation_status = AsyncMock()
    core._route_microphone_audio_unfiltered = AsyncMock(return_value=OutputCommit.LOCAL_ACCEPTED)
    task = close_task = old_close = swap_task = stolen = None
    try:
        await feed(old_bridge, 400)
        old = old_bridge._runtime
        if outcome == "slot_taken":
            original_wait = old.wait_retired
            async def take_slot_before_retry(deadline):
                nonlocal stolen
                retired = await original_wait(deadline)
                if retired:
                    stolen = underlying.create("another-owner", ingress_token="another-route")
                return retired
            old.wait_retired = take_slot_before_retry
        old_close = asyncio.create_task(old_bridge.retire())
        await asyncio.wait_for(entered.wait(), 1)
        assert await core.set_active_session_interception_factory(counted)
        bridge = core._active_session_interception_bridge
        bridge._close_timeout_s = 0.05 if outcome == "timeout" else 1
        def frame():
            value = _frame(core, generation, b"\x01\x00" * 400)
            return replace(value, captured_at=time.monotonic(), context=replace(value.context, captured_at=time.time()))
        task = asyncio.create_task(core._route_voice_session_activation_output(frame(), generation))
        await asyncio.wait_for(attempted.wait(), 1)
        assert not task.done() and bridge._runtime is None and counted.calls == 1
        assert not core._voice_session_activation_degraded
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not old._tse_close_task.cancelled()
        elif outcome == "timeout":
            assert await asyncio.wait_for(task, 1) is OutputCommit.UNKNOWN
        else:
            if outcome == "capacity":
                assert await core._route_voice_session_activation_output(frame(), generation) is OutputCommit.UNKNOWN
            elif outcome == "revoke":
                counted.close()
            elif outcome == "close":
                close_task = asyncio.create_task(bridge.close())
                # Enter close deterministically while its admission lock is held.
                await _assert_close_waiting(close_task)
                assert bridge._closed
            elif outcome == "generation_change":
                core._capture_voice_session_activation_generation = lambda: replace(generation, microphone=2)
            elif outcome == "bridge_swap":
                successor = _EventRuntime(InterceptionResult(InterceptionDecision.PENDING))
                swap_task = asyncio.create_task(core.set_active_session_interception_factory(_Factory(successor)))
                await _assert_close_waiting(swap_task)
                assert bridge._closed
            release.set()
            result = await asyncio.wait_for(task, 1)
            if outcome == "release":
                assert result is OutputCommit.LOCAL_ACCEPTED
                assert counted.calls == 2 and bridge._runtime is not old
                assert not core._voice_session_activation_degraded
            elif outcome == "generation_change":
                assert result is OutputCommit.NOT_SENT
                core._route_microphone_audio_unfiltered.assert_not_awaited()
                assert not core._voice_session_activation_degraded
            elif outcome == "bridge_swap":
                assert result is OutputCommit.NOT_SENT and counted.calls == 1
                assert await swap_task
                assert core._active_session_interception_bridge is not bridge
                assert await core._route_voice_session_activation_output(frame(), generation) is OutputCommit.LOCAL_ACCEPTED
                assert not core._voice_session_activation_degraded and successor.closed == []
            elif outcome == "capacity":
                assert result is OutputCommit.NOT_SENT and counted.calls == 1
            elif outcome == "slot_taken":
                assert result is OutputCommit.UNKNOWN and counted.calls == 2
                assert stolen is not None and not stolen.is_closed
            else:
                assert result is OutputCommit.UNKNOWN and counted.calls == 1
        release.set()
        await asyncio.wait_for(old_close, 1)
        assert old.retired
        if outcome in {"timeout", "revoke", "close", "capacity", "slot_taken"}:
            assert await core._route_voice_session_activation_output(frame(), generation) is OutputCommit.NOT_SENT
            assert counted.calls == (2 if outcome == "slot_taken" else 1)
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
        await asyncio.gather(*(item for item in (task, old_close, close_task, swap_task) if item is not None), return_exceptions=True)
        await old_bridge.close()
        if stolen is not None:
            await stolen.close()
        if core._active_session_interception_bridge is not None:
            await core._active_session_interception_bridge.close()
        await asyncio.gather(*core._core_asr_cleanup_tasks, return_exceptions=True)


async def _assert_close_waiting(task):
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), 0.01)


@pytest.mark.asyncio
async def test_arbitrary_creation_failure_is_not_retried_as_retirement():
    class FailedFactory:
        calls = 0
        def create(self, *_args, **_kwargs):
            self.calls += 1
            raise RuntimeError("previous_runtime_retirement_pending")  # String alone grants no retry.
    factory = FailedFactory()
    bridge = ActiveSessionInterceptionBridge(factory, terminal_on_unavailable=True)
    try:
        for _ in range(3):
            result = await bridge.process(b"\x01\x00", sample_rate_hz=16000,
                                          generation="g", ingress_token="i", captured_at=None)
            assert result.decision is InterceptionDecision.UNAVAILABLE
        assert factory.calls == 1 and bridge._runtime is None
    finally:
        await bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", [False, 1, object()])
async def test_startup_retry_requires_affirmative_boolean_retirement_evidence(proof):
    async def wait_retired(_deadline):
        return proof
    class Factory:
        calls = 0
        def create(self, *_args, **_kwargs):
            self.calls += 1
            raise InterceptionRetirementPending(wait_retired)
    factory = Factory()
    bridge = ActiveSessionInterceptionBridge(factory, terminal_on_unavailable=True)
    try:
        result = await bridge.process(b"\x01\x00", sample_rate_hz=16000,
                                      generation="g", ingress_token="i", captured_at=None)
        assert result.decision is InterceptionDecision.UNAVAILABLE
        assert factory.calls == 1 and bridge._runtime is None
    finally:
        await bridge.close()
