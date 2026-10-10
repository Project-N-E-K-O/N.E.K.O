"""Sample-scoped delivery proofs; model fixtures are not acoustic evidence."""
from __future__ import annotations

from dataclasses import replace
import asyncio
import time

import pytest

from main_logic.voice_identity_service.interception_runtime import (
    InterceptionRuntimeConfig, InterceptionRuntimeError, PrewireInterceptionFactory,
)
from main_logic.voice_identity_service.prewire_gate.contracts import PrewireCommitStage, SampleRange
from main_logic.voice_identity_service.prewire_gate.scheduler import ScorerCapabilities
from main_logic.voice_input.interception_events import (
    InterceptionDeliveryReceipt, InterceptionDeliveryStage, InterceptionOutputKind,
)
from tests.unit.voice_identity_service.test_interception_runtime import (
    _Classifier, _Scorer, _SequenceClassifier, _Tse, _config,
)

pytestmark = pytest.mark.unit_fast


def factory(config=None, classifier=None):
    return PrewireInterceptionFactory(
        config or _config(), score_backend=_Scorer(),
        classifier=classifier or _Classifier(), tse_factory=lambda _: _Tse(),
    )


async def push(runtime, samples):
    return await runtime.process(
        b"\x00\x20" * samples, sample_rate_hz=16000,
        generation="owner", ingress_token=None, captured_at=None,
    )


def production_arguments():
    from tests.unit.voice_identity_service.test_continuous_calibration import classifier

    config = InterceptionRuntimeConfig(
        session_id="synthetic-control", ingress_generation=1,
        profile_generation="p", model_generation="m", config_generation="c",
        scoring_parameters_digest="b" * 64, window_samples=7200,
        scoring_window_samples=(4000, 7200), guard_samples=0,
        scorer_capabilities=ScorerCapabilities(16000, 4000, (4000, 7200), "m"),
    )
    scorer = _Scorer()
    scorer.capabilities = config.scorer_capabilities
    return config, dict(
        classifier=classifier(), score_backend=scorer,
        tse_factory=lambda _: _Tse(), quality_analyzer=object(),
        candidate_factory=lambda _: None,  # Preparation does not execute factories.
    )


def test_production_preparation_checks_registered_contract_without_enabling_or_loading():
    config, dependencies = production_arguments()
    allocations = []
    dependencies["candidate_factory"] = lambda key: allocations.append(key)
    dependencies["tse_factory"] = lambda key: allocations.append(key)
    with pytest.raises(InterceptionRuntimeError, match="candidate_calibration_release_unavailable"):
        PrewireInterceptionFactory.for_production(config, **dependencies)
    assert allocations == []


@pytest.mark.parametrize("changes", [{"guard_samples": 1600}, {"step_samples": 1600}])
def test_production_preparation_rejects_uncalibrated_decision_geometry_before_loading(changes):
    from main_logic.voice_identity_service.calibration import CalibrationError

    config, dependencies = production_arguments()
    allocations = []
    dependencies["tse_factory"] = lambda key: allocations.append(key)
    with pytest.raises(CalibrationError, match="continuous_package_decision_plan_mismatch"):
        PrewireInterceptionFactory.for_production(replace(config, **changes), **dependencies)
    assert allocations == []


@pytest.mark.parametrize("dependency", ["score_backend", "tse_factory", "quality_analyzer"])
def test_production_preparation_rejects_missing_model_dependencies(dependency):
    config, dependencies = production_arguments()
    dependencies[dependency] = None
    with pytest.raises(InterceptionRuntimeError, match="model_dependencies_unavailable"):
        PrewireInterceptionFactory.for_production(config, **dependencies)


def test_production_preparation_rejects_fixture_classifier_and_single_owner_override():
    config, dependencies = production_arguments()
    with pytest.raises(InterceptionRuntimeError, match="owner_confirmation_required"):
        PrewireInterceptionFactory.for_production(replace(config, owner_streak_required=1), **dependencies)
    dependencies["classifier"] = _Classifier()
    with pytest.raises(InterceptionRuntimeError, match="calibration_package_unavailable"):
        PrewireInterceptionFactory.for_production(config, **dependencies)


@pytest.mark.parametrize("changes", [
    {"profile_generation": "stale"}, {"model_generation": "stale"},
    {"config_generation": "stale"}, {"scoring_parameters_digest": "c" * 64},
])
def test_production_preparation_rejects_relabeling_registered_contract(changes):
    from main_logic.voice_identity_service.calibration import CalibrationError

    config, dependencies = production_arguments()
    with pytest.raises((CalibrationError, ValueError)):
        PrewireInterceptionFactory.for_production(replace(config, **changes), **dependencies)


@pytest.mark.parametrize("changes", [
    {"window_samples": 14400}, {"scoring_window_samples": (4000,)},
    {"guard_samples": 7200}, {"required_consistent_observations": 2},
    {"owner_streak_required": True}, {"max_outstanding_jobs": 0},
    {"prefix_deadline_seconds": float("inf")},
])
def test_production_preparation_rejects_invalid_runtime_config_before_ready(changes):
    config, dependencies = production_arguments()
    with pytest.raises((ValueError, InterceptionRuntimeError)):
        PrewireInterceptionFactory.for_production(replace(config, **changes), **dependencies)


@pytest.mark.asyncio
async def test_missing_tse_result_has_bounded_deadline_even_with_confirmed_owner():
    class NoOutputTse(_Tse):
        async def push(self, pcm, *, start_sample):
            return []

    prepared = PrewireInterceptionFactory(
        _config(), score_backend=_Scorer(), classifier=_Classifier(),
        tse_factory=lambda _: NoOutputTse(),
    )
    runtime = prepared.create("owner", ingress_token=None)
    try:
        await push(runtime, 1600)
        first_deadline = runtime._capture_deadline
        await push(runtime, 400)
        assert runtime._owner_streak == 2
        assert runtime._output_order
        assert runtime._capture_deadline <= first_deadline
        # Exercise the actual deadline coroutine without a timing-dependent
        # sleep: the owner is already confirmed and the clock is due.
        timer, runtime._deadline_task = runtime._deadline_task, None
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)
        runtime._capture_deadline = time.monotonic() - 1
        await runtime._expire_prefix()
        assert runtime._closed and runtime.retired
        assert not runtime._pending_audio
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_confirmed_stream_with_unsettled_raw_tail_expires_and_can_restart():
    prepared = factory()
    runtime = prepared.create("owner", ingress_token=None)
    try:
        await push(runtime, 1600)
        result = await push(runtime, 400)
        assert result.events and not runtime._output_order
        assert runtime._tse_sample == 2000 and runtime._settled_sample == 800
        assert runtime._gate.copy_pcm(runtime._stream, SampleRange(800, 2000)) == b"\x00\x20" * 1200
        assert runtime._ingress_anchors[0][0] == 1600
        runtime._arm_deadline(time.monotonic())
        timer = runtime._deadline_task
        await asyncio.wait_for(timer, 1)
        assert runtime._output_revoked and runtime.retired
        late = await push(runtime, 400)
        assert not late.pcm16 and not late.events
        successor = prepared.create("owner", ingress_token=None)
        try:
            await push(successor, 1600)
            assert (await push(successor, 400)).pcm16
        finally:
            await successor.close()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_late_tse_cannot_publish_after_deadline_while_timer_waits_for_lock():
    import numpy as np
    from main_logic.voice_identity_service.tse.contracts import TseAudioChunk

    class LateTse(_Tse):
        def __init__(self):
            super().__init__()
            self.frames = []

        async def push(self, pcm, *, start_sample):
            self.frames.append(pcm)
            if len(self.frames) < 3:
                return []
            combined = np.concatenate(self.frames)
            return [TseAudioChunk(0, combined.size, combined)]

    prepared = PrewireInterceptionFactory(
        _config(), score_backend=_Scorer(), classifier=_Classifier(),
        tse_factory=lambda _: LateTse(),
    )
    runtime = prepared.create("owner", ingress_token=None)
    try:
        await push(runtime, 1600)
        await push(runtime, 400)
        for interval in runtime._output_order.values():
            interval.deadline_monotonic = time.monotonic() - 1
        # No new scoring window: only the late extractor completes, while the
        # scheduled expiry task cannot acquire the process lock first.
        result = await push(runtime, 1)
        assert result.reason == "target_audio_deadline_expired"
        assert not result.pcm16 and not result.events
        assert runtime.retired
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_real_ack_after_transfer_works_until_original_ledger_record_is_evicted():
    runtime = factory().create("owner", ingress_token=None)
    try:
        await push(runtime, 1600)
        first = (await push(runtime, 400)).events[0]
        runtime.record_delivery(receipt(first, InterceptionDeliveryStage.TRANSPORT_WRITTEN))
        runtime.record_delivery(receipt(first, InterceptionDeliveryStage.TRANSPORT_OWNED))
        assert runtime.record_delivery(receipt(first, InterceptionDeliveryStage.PROVIDER_CONFIRMED, confirmation_id="genuine-ack"))
        # A separate retained interval is permitted to age out normally.
        second = (await push(runtime, 400)).events[0]
        runtime.record_delivery(receipt(second, InterceptionDeliveryStage.TRANSPORT_WRITTEN))
        runtime.record_delivery(receipt(second, InterceptionDeliveryStage.TRANSPORT_OWNED))
        for _ in range(150):
            result = await push(runtime, 400)
            for event in result.events:
                if event.kind is InterceptionOutputKind.AUDIO:
                    runtime.record_delivery(receipt(event, InterceptionDeliveryStage.TRANSPORT_WRITTEN))
                    runtime.record_delivery(receipt(event, InterceptionDeliveryStage.TRANSPORT_OWNED))
        assert len(runtime._owned_delivery_events) <= 128
        assert not runtime.record_delivery(receipt(second, InterceptionDeliveryStage.PROVIDER_CONFIRMED, confirmation_id="late-ack"))
    finally:
        await runtime.close()


def receipt(event, stage, **kwargs):
    return InterceptionDeliveryReceipt(event, stage, time.monotonic(), **kwargs)


@pytest.mark.asyncio
async def test_capture_restart_has_distinct_event_identity_and_rejects_old_receipts():
    config = _config()
    prepared = factory(config)
    old = prepared.create("owner", ingress_token=None)
    await push(old, 1600)
    old_event = (await push(old, 400)).events[0]
    await old.close()
    new = prepared.create("owner", ingress_token=None)
    try:
        await push(new, 1600)
        new_event = (await push(new, 400)).events[0]
        assert old_event.sequence == new_event.sequence == 0
        assert (old_event.start_sample, old_event.end_sample) == (new_event.start_sample, new_event.end_sample)
        assert old_event.identity.ingress_generation == config.ingress_generation
        assert new_event.identity.ingress_generation == config.ingress_generation + 1
        assert prepared._config is config
        assert not new.record_delivery(receipt(old_event, InterceptionDeliveryStage.TRANSPORT_WRITTEN))
        assert new.record_delivery(receipt(new_event, InterceptionDeliveryStage.TRANSPORT_WRITTEN))
        assert new.record_delivery(receipt(new_event, InterceptionDeliveryStage.TRANSPORT_OWNED))
    finally:
        await new.close()


@pytest.mark.asyncio
async def test_failed_runtime_construction_does_not_reuse_capture_identity(monkeypatch):
    import main_logic.voice_identity_service.interception_runtime as module

    prepared = factory()
    constructor = module.PrewireInterceptionRuntime
    attempted = []

    def fail(config, **kwargs):
        attempted.append(config.ingress_generation)
        raise RuntimeError("injected construction failure")

    with monkeypatch.context() as patch:
        patch.setattr(module, "PrewireInterceptionRuntime", fail)
        with pytest.raises(RuntimeError, match="injected construction failure"):
            prepared.create("owner", ingress_token=None)
    runtime = prepared.create("owner", ingress_token=None)
    try:
        assert isinstance(runtime, constructor)
        await push(runtime, 1600)
        event = (await push(runtime, 400)).events[0]
        assert event.identity.ingress_generation == attempted[0] + 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_cancelled_owner_prefix_remains_gap_with_compact_output_axis():
    runtime = factory(classifier=_SequenceClassifier([True, False, True, True])).create("owner", ingress_token=None)
    outputs = []
    try:
        for samples in (1600, 400, 400, 400):
            outputs.extend((await push(runtime, samples)).events)
        assert [event.kind for event in outputs] == [
            InterceptionOutputKind.GAP, InterceptionOutputKind.GAP,
            InterceptionOutputKind.AUDIO, InterceptionOutputKind.AUDIO,
        ]
        assert [(event.start_sample, event.end_sample) for event in outputs] == [
            (0, 400), (400, 800), (800, 1200), (1200, 1600),
        ]
        assert [event.sequence for event in outputs] == list(range(4))
        assert outputs[0].reason == "owner_run_unconfirmed"
        assert [(event.asr_start_sample, event.asr_end_sample) for event in outputs[2:]] == [(0, 400), (400, 800)]
        # The ledger's reservation axis must not be rewritten by cancellation.
        records = sorted(runtime._gate._ledger._records.values(), key=lambda record: record.spec.commit_range.start)
        assert records[0].commit_stage is PrewireCommitStage.LOCAL_CANCELLED
        assert records[0].original_to_asr.asr_range.start == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("window_ms", [900, 450, 350, 250])
async def test_supported_real_tails_end_once_and_preserve_every_fixture_sample(window_ms):
    window = window_ms * 16
    counts = tuple(sorted({800, 2400, 4000, window}))
    config = InterceptionRuntimeConfig(
        session_id="fixture", ingress_generation=1, profile_generation="p",
        model_generation="m", config_generation=f"fixture-{window_ms}",
        scoring_parameters_digest="a" * 64, window_samples=window,
        scoring_window_samples=counts,
    )
    runtime = factory(config).create("owner", ingress_token=None)
    events = []
    try:
        for _ in range(125):
            result = await push(runtime, 640)
            events.extend(result.events)
            for event in result.events:
                if event.kind is InterceptionOutputKind.AUDIO:
                    assert runtime.record_delivery(receipt(event, InterceptionDeliveryStage.TRANSPORT_WRITTEN))
                    assert runtime.record_delivery(receipt(event, InterceptionDeliveryStage.TRANSPORT_OWNED))
        events.extend((await runtime.finish()).events)
        assert events[-1].kind is InterceptionOutputKind.END
        assert (events[-1].start_sample, events[-1].end_sample) == (80000, 80000)
        assert sum(len(event.pcm16) // 2 for event in events) == 80000
        assert all(event.kind is not InterceptionOutputKind.GAP for event in events)
        assert [event.sequence for event in events] == list(range(len(events)))
        assert not (await runtime.finish()).events
        for previous, current in zip(events, events[1:]):
            assert previous.end_sample == current.start_sample
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_delivery_settles_exact_output_after_close_without_inventing_ack():
    runtime = factory().create("owner", ingress_token=None)
    await push(runtime, 1600)
    result = await push(runtime, 400)
    event = result.events[0]
    assert runtime.record_delivery(receipt(event, InterceptionDeliveryStage.QUEUED))
    await runtime.close()
    assert not runtime.record_delivery(receipt(replace(event, pcm16=b"\x01\x00" * 400), InterceptionDeliveryStage.TRANSPORT_WRITTEN))
    assert not runtime.record_delivery(receipt(replace(event, identity=replace(event.identity, config_generation="other")), InterceptionDeliveryStage.TRANSPORT_WRITTEN))
    assert runtime.record_delivery(receipt(event, InterceptionDeliveryStage.TRANSPORT_WRITTEN))
    record = next(record for record in runtime._gate._ledger._records.values() if record.spec.commit_range.start == event.start_sample)
    assert record.commit_stage is PrewireCommitStage.WRITTEN
    assert runtime.record_delivery(receipt(event, InterceptionDeliveryStage.PROVIDER_CONFIRMED, confirmation_id="actual-provider-proof"))
    assert runtime._gate.get_interval_record(record.spec.identity).commit_stage is PrewireCommitStage.REMOTE_CONFIRMED


@pytest.mark.asyncio
async def test_unwritten_transfer_is_rejected_and_unknown_cannot_be_transferred():
    runtime = factory().create("owner", ingress_token=None)
    try:
        await push(runtime, 1600)
        event = (await push(runtime, 400)).events[0]
        assert not runtime.record_delivery(receipt(event, InterceptionDeliveryStage.TRANSPORT_OWNED))
        assert runtime.record_delivery(receipt(event, InterceptionDeliveryStage.UNKNOWN))
        assert not runtime.record_delivery(receipt(event, InterceptionDeliveryStage.TRANSPORT_OWNED))
        assert not runtime.record_delivery(receipt(event, InterceptionDeliveryStage.NOT_SENT))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_written_and_explicitly_owned_stream_stays_bounded_without_provider_ack():
    runtime = factory().create("owner", ingress_token=None)
    delivered = 0
    try:
        for index in range(600):
            result = await push(runtime, 1600 if index == 0 else 400)
            for event in result.events:
                if event.kind is InterceptionOutputKind.AUDIO:
                    delivered += 1
                    assert runtime.record_delivery(receipt(event, InterceptionDeliveryStage.TRANSPORT_WRITTEN))
                    assert runtime.record_delivery(receipt(event, InterceptionDeliveryStage.TRANSPORT_OWNED))
            assert len(runtime._delivery_events) == 0
            assert len(runtime._gate._ledger._records) <= 128
        assert delivered > 500
        assert not runtime._closed
    finally:
        await runtime.close()


@pytest.mark.parametrize("invalid", [False, [], "", (), (1600, 400), (400,)])
def test_explicit_invalid_scoring_plans_are_not_silently_defaulted(invalid):
    with pytest.raises((TypeError, ValueError)):
        factory(replace(_config(), scoring_window_samples=invalid)).create("owner", ingress_token=None)
