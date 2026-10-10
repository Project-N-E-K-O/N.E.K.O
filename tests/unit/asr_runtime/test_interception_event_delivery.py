from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import time

import pytest

from main_logic.voice_input.interception import (
    ActiveSessionInterceptionBridge, InterceptionDecision, InterceptionResult,
    InterceptionOutputEvent, InterceptionOutputIdentity, InterceptionOutputKind,
    InterceptionDeliveryReceipt, InterceptionDeliveryStage,
)
from main_logic.voice_input.activation import ActivationGeneration, OutputCommit
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus
from tests.unit.asr_runtime.test_active_session_interception import _Runtime, _frame


IDENTITY = InterceptionOutputIdentity("session", 1, "profile", "model", "config")


def _event(sequence, start, end, kind=InterceptionOutputKind.AUDIO):
    return InterceptionOutputEvent(
        IDENTITY, str(sequence), sequence, kind, start, end,
        pcm16=b"\x01\x00" * (end - start) if kind is InterceptionOutputKind.AUDIO else b"",
    )


class _EventRuntime:
    def __init__(self, result, end=None):
        self.result = result
        self.end = end or InterceptionResult(InterceptionDecision.DROP, reason="capture_finished")
        self.receipts = []
        self.closed = []
        self.finish_calls = 0

    async def process(self, *_args, **_kwargs):
        return self.result

    async def finish(self):
        self.finish_calls += 1
        return self.end

    def record_delivery(self, receipt):
        self.receipts.append(receipt)

    async def close(self, reason="retired"):
        self.closed.append(reason)


class _Factory:
    def __init__(self, runtime):
        self.runtime = runtime

    def create(self, *_args, **_kwargs):
        return self.runtime

    def close(self):
        pass


def _core():
    core = _Runtime()
    core._asr_route_mode = "independent"
    core._voice_session_activation_degraded = False
    core._voice_activation_handoff = None
    core._voice_input_accepts_pcm = lambda: True
    generation = ActivationGeneration("session", 1, 1, 1, 1, "core")
    core._capture_voice_session_activation_generation = lambda: generation
    core._ingress_token_matches = lambda _token: True
    core._independent_asr_provider = "fake"
    return core, generation


@pytest.mark.asyncio
async def test_gap_is_explicit_boundary_and_only_filtered_intervals_reach_receiver():
    core, generation = _core()
    audio = _event(0, 0, 4)
    gap = _event(1, 4, 10, InterceptionOutputKind.GAP)
    successor = _event(2, 10, 14)
    model = _EventRuntime(InterceptionResult(
        InterceptionDecision.KEEP, audio.pcm16 + successor.pcm16,
        events=(audio, gap, successor),
    ))
    order = []

    async def receive(frame, **_kwargs):
        order.append(("audio", frame.pcm16))
        frame.delivery.observe(0, frame.delivery.sample_count, InterceptionDeliveryStage.TRANSPORT_WRITTEN)
        frame.delivery.observe(0, frame.delivery.sample_count, InterceptionDeliveryStage.TRANSPORT_OWNED)
        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

    async def boundary(**_kwargs):
        order.append(("gap",))

    core._asr_runtime.submit = receive
    core._asr_runtime.discontinue_input = boundary
    assert await core.set_active_session_interception_factory(_Factory(model))
    frame = _frame(core, generation, b"\xff\x7f" * 14)
    assert await core._route_voice_session_activation_output(frame, generation) is OutputCommit.LOCAL_ACCEPTED
    assert order == [("audio", audio.pcm16), ("gap",), ("audio", successor.pcm16)]
    written = [r.event for r in model.receipts if r.stage is InterceptionDeliveryStage.TRANSPORT_WRITTEN]
    assert written == [audio, successor]
    assert all(r.stage is not InterceptionDeliveryStage.PROVIDER_CONFIRMED for r in model.receipts)


@pytest.mark.asyncio
async def test_native_without_boundary_stops_before_successor_audio():
    core, generation = _core()
    core._asr_route_mode = "native"
    core.session.stream_audio = AsyncMock()
    audio, gap, successor = _event(0, 0, 4), _event(1, 4, 10, InterceptionOutputKind.GAP), _event(2, 10, 14)
    model = _EventRuntime(InterceptionResult(
        InterceptionDecision.KEEP, audio.pcm16 + successor.pcm16,
        events=(audio, gap, successor),
    ))
    await core.set_active_session_interception_factory(_Factory(model))
    result = await core._route_voice_session_activation_output(
        _frame(core, generation, b"\xff\x7f" * 14), generation,
    )
    assert result is OutputCommit.UNKNOWN
    assert core.session.stream_audio.await_count == 1
    assert core.session.stream_audio.await_args.args[0] == audio.pcm16
    assert model.closed == ["interception_native_boundary_unsupported"]
    assert [(r.event, r.stage) for r in model.receipts if r.event == successor] == [
        (successor, InterceptionDeliveryStage.NOT_SENT),
    ]


@pytest.mark.asyncio
async def test_consumed_original_is_not_replayed_when_downstream_rejects():
    core, generation = _core()
    audio = _event(0, 0, 4)
    model = _EventRuntime(InterceptionResult(InterceptionDecision.KEEP, audio.pcm16, events=(audio,)))
    await core.set_active_session_interception_factory(_Factory(model))
    core._route_microphone_audio_unfiltered = AsyncMock(return_value=OutputCommit.NOT_SENT)
    result = await core._route_voice_session_activation_output(_frame(core, generation, b"\x00\x40" * 4), generation)
    assert result is OutputCommit.UNKNOWN
    assert model.receipts[-1].stage is InterceptionDeliveryStage.NOT_SENT


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["reject", "unknown", "cancel", "raise"])
async def test_aborted_batch_settles_never_attempted_successor_on_original_owner(outcome):
    core, generation = _core()
    audio, successor = _event(0, 0, 4), _event(1, 4, 8)
    model = _EventRuntime(InterceptionResult(InterceptionDecision.KEEP,
        audio.pcm16 + successor.pcm16, events=(audio, successor)))
    await core.set_active_session_interception_factory(_Factory(model))

    async def route(*_args, **_kwargs):
        if outcome == "cancel":
            raise asyncio.CancelledError
        if outcome == "raise":
            raise RuntimeError("route failed")
        return OutputCommit.NOT_SENT if outcome == "reject" else OutputCommit.UNKNOWN

    core._route_microphone_audio_unfiltered = route
    call = core._route_voice_session_activation_output(_frame(core, generation, b"\x00\x40" * 8), generation)
    if outcome in {"cancel", "raise"}:
        with pytest.raises(asyncio.CancelledError if outcome == "cancel" else RuntimeError):
            await call
    else:
        assert await call is OutputCommit.UNKNOWN
    assert [(r.event, r.stage) for r in model.receipts if r.event == successor] == [
        (successor, InterceptionDeliveryStage.NOT_SENT),
    ]
    assert model.receipts[-2].stage is (
        InterceptionDeliveryStage.NOT_SENT if outcome == "reject" else InterceptionDeliveryStage.UNKNOWN
    )


@pytest.mark.asyncio
async def test_bridge_rejected_receipt_is_reported_as_failure_and_can_be_retried():
    audio = _event(0, 0, 4)
    model = _EventRuntime(InterceptionResult(InterceptionDecision.KEEP, audio.pcm16, events=(audio,)))
    model.record_delivery = lambda _receipt: False
    bridge = ActiveSessionInterceptionBridge(_Factory(model))
    result = await bridge.process(audio.pcm16, sample_rate_hz=16000, generation="g", ingress_token="i", captured_at=1)
    callback = bridge.delivery_callback(result, generation="g", ingress_token="i")
    receipt = InterceptionDeliveryReceipt(audio, InterceptionDeliveryStage.LOCAL_ACCEPTED, 1)
    with pytest.raises(RuntimeError, match="rejected"):
        callback(receipt)
    model.record_delivery = model.receipts.append
    callback(receipt)
    assert model.receipts == [receipt]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unavailable", "exception", "timeout"])
async def test_model_failure_terminates_capture_and_only_new_generation_recovers(failure):
    core, generation = _core()
    current = [generation]
    core._capture_voice_session_activation_generation = lambda: current[0]
    core._send_voice_session_activation_status = AsyncMock()
    audio = _event(0, 0, 4)

    class Model(_EventRuntime):
        calls = 0

        async def process(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return self.result
            if failure == "exception":
                raise RuntimeError("model failed")
            if failure == "timeout":
                await asyncio.Event().wait()
            return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="model_unavailable")

    old = Model(InterceptionResult(InterceptionDecision.KEEP, audio.pcm16, events=(audio,)))
    fresh_audio = replace(audio, identity=replace(audio.identity, ingress_generation=2))
    class FreshModel(_EventRuntime):
        calls = 0

        async def process(self, *_args, **_kwargs):
            self.calls += 1
            return self.result if self.calls == 1 else InterceptionResult(InterceptionDecision.PENDING)

    fresh = FreshModel(InterceptionResult(InterceptionDecision.KEEP, fresh_audio.pcm16, events=(fresh_audio,)))

    class Captures(_Factory):
        creates = 0

        def create(self, *_args, **_kwargs):
            self.creates += 1
            return old if self.creates == 1 else fresh

    factory = Captures(old)
    tags = []

    async def receive(frame, **_kwargs):
        tags.append(frame.delivery)
        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

    core._asr_runtime.submit = receive
    await core.set_active_session_interception_factory(factory)
    bridge = core._active_session_interception_bridge
    bridge._process_timeout_s = 0.01
    try:
        assert await core._route_voice_session_activation_output(_frame(core, generation, audio.pcm16), generation) is OutputCommit.LOCAL_ACCEPTED
        before = core._voice_activation_delivery_revision
        assert await core._route_voice_session_activation_output(_frame(core, generation, audio.pcm16), generation) is OutputCommit.UNKNOWN
        assert core._voice_session_activation_degraded
        assert core._voice_activation_delivery_revision == before + 1
        assert core._voice_session_activation_status[1].value == "unavailable"
        await asyncio.sleep(0)
        core._send_voice_session_activation_status.assert_awaited_once()
        assert await core._route_voice_session_activation_output(_frame(core, generation, audio.pcm16), generation) is OutputCommit.NOT_SENT
        token = core._capture_ingress_token()
        same = await bridge.process(audio.pcm16, sample_rate_hz=16000, generation=generation, ingress_token=token, captured_at=None)
        assert same.decision is InterceptionDecision.UNAVAILABLE
        assert factory.creates == 1 and old.calls == 2 and len(tags) == 1
        current[0] = replace(generation, microphone=2)
        from main_logic.voice_identity_service.activation_runtime import VoiceSessionActivationRuntime, VoiceSessionActivationRuntimeConfig
        from main_logic.voice_input.activation import VoiceActivationController
        from tests.unit.voice_identity_service.test_activation_runtime import _Scorer

        activations = []

        class ActivationFactory:
            enforce = True

            def create(self, gen, output, *, status_callback):
                runtime = VoiceSessionActivationRuntime(gen, _Scorer(), output,
                    controller=VoiceActivationController(clock=lambda: 1.5),
                    config=VoiceSessionActivationRuntimeConfig(first_checkpoint_seconds=0.001, second_checkpoint_seconds=0.002),
                    status_callback=status_callback)
                activations.append(runtime)
                return runtime

        core._voice_session_activation_factory = ActivationFactory()
        # The actual microphone entry owns recovery and activation creation;
        # calling the output helper directly would miss its early degraded gate.
        for index in range(15):
            await core._route_microphone_audio(b"\x01\x00" * 1600, sample_rate_hz=16000,
                speech_probability=1.0, rnnoise_available=True, ingress_token=token,
                received_at=index * 0.1, captured_at=time.time())
            await asyncio.sleep(0)
        for _ in range(30):
            if len(tags) == 2:
                break
            await asyncio.sleep(0)
        assert len(activations) == 1
        assert activations[0].generation == current[0]
        assert factory.creates == 2 and len(tags) == 2
        assert not core._voice_session_activation_degraded
        # Pre-failure audio's captured owner remains independently settleable.
        tags[0].observe(0, 4, InterceptionDeliveryStage.TRANSPORT_WRITTEN)
        tags[0].observe(0, 4, InterceptionDeliveryStage.TRANSPORT_OWNED)
        assert old.receipts[-1].stage is InterceptionDeliveryStage.TRANSPORT_OWNED
        assert all(r.stage is not InterceptionDeliveryStage.TRANSPORT_OWNED for r in fresh.receipts)
    finally:
        if core._voice_session_activation_runtime is not None:
            await core._voice_session_activation_runtime.close()
        await bridge.close()


@pytest.mark.asyncio
async def test_new_capture_does_not_clear_a_separate_published_dsp_failure():
    from main_logic.voice_input.activation import ActivationState
    core, generation = _core()
    core._interception_failure_generation = generation
    core._voice_session_activation_degraded = True
    newer = replace(generation, microphone=2)
    core._capture_voice_session_activation_generation = lambda: newer
    core._voice_session_activation_status = (newer, ActivationState.UNAVAILABLE, "audio_processing_unavailable")
    core._voice_session_activation_factory = SimpleNamespace(enforce=True, create=AsyncMock())
    assert await core._route_microphone_audio(b"\x01\x00" * 4, sample_rate_hz=16000)
    assert core._voice_session_activation_degraded
    core._voice_session_activation_factory.create.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [InterceptionDecision.DROP, InterceptionDecision.PENDING])
async def test_expected_drop_or_pending_does_not_make_capture_terminal(decision):
    core, generation = _core()
    model = _EventRuntime(InterceptionResult(decision))
    factory = _Factory(model)
    await core.set_active_session_interception_factory(factory)
    try:
        frame = _frame(core, generation, b"\x01\x00" * 4)
        assert await core._route_voice_session_activation_output(frame, generation) is OutputCommit.LOCAL_ACCEPTED
        model.result = InterceptionResult(InterceptionDecision.KEEP, pcm16=frame.pcm)
        core._asr_runtime.submit = AsyncMock(return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED))
        assert await core._route_voice_session_activation_output(frame, generation) is OutputCommit.LOCAL_ACCEPTED
        assert not core._voice_session_activation_degraded
        assert core._interception_failure_generation is None
    finally:
        await core._active_session_interception_bridge.close()


@pytest.mark.asyncio
async def test_model_failure_fences_event_writer_already_waiting_downstream():
    core, generation = _core()
    core._send_voice_session_activation_status = AsyncMock()
    first, successor = _event(0, 0, 4), _event(1, 4, 8)
    model = _EventRuntime(InterceptionResult(InterceptionDecision.KEEP,
        first.pcm16 + successor.pcm16, events=(first, successor)))
    await core.set_active_session_interception_factory(_Factory(model))
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def receive(pcm, **_kwargs):
        calls.append(pcm)
        entered.set()
        await release.wait()
        return OutputCommit.LOCAL_ACCEPTED

    core._route_microphone_audio_unfiltered = receive
    pending = asyncio.create_task(core._route_voice_session_activation_output(
        _frame(core, generation, first.pcm16 + successor.pcm16), generation))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        model.result = InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="model_failed")
        assert await core._route_voice_session_activation_output(_frame(core, generation, first.pcm16), generation) is OutputCommit.UNKNOWN
        release.set()
        assert await asyncio.wait_for(pending, 1) is OutputCommit.UNKNOWN
        assert calls == [first.pcm16]
        assert model.receipts[-2].event == first
        assert model.receipts[-2].stage is InterceptionDeliveryStage.UNKNOWN
        assert model.receipts[-1].event == successor
        assert model.receipts[-1].stage is InterceptionDeliveryStage.NOT_SENT
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await core._active_session_interception_bridge.close()


@pytest.mark.asyncio
async def test_capacity_failure_fences_model_result_that_arrives_after_failure():
    core, generation = _core()
    core._send_voice_session_activation_status = AsyncMock()
    entered, release = asyncio.Event(), asyncio.Event()
    audio = _event(0, 0, 4)

    class DelayedModel(_EventRuntime):
        async def process(self, *_args, **_kwargs):
            entered.set()
            await release.wait()
            return self.result

    model = DelayedModel(InterceptionResult(InterceptionDecision.KEEP, audio.pcm16, events=(audio,)))
    await core.set_active_session_interception_factory(_Factory(model))
    core._route_microphone_audio_unfiltered = AsyncMock(return_value=OutputCommit.LOCAL_ACCEPTED)
    task = asyncio.create_task(core._route_voice_session_activation_output(_frame(core, generation, audio.pcm16), generation))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert await core._route_voice_session_activation_output(_frame(core, generation, audio.pcm16), generation) is OutputCommit.UNKNOWN
        release.set()
        assert await asyncio.wait_for(task, 1) is OutputCommit.UNKNOWN
        core._route_microphone_audio_unfiltered.assert_not_awaited()
        assert [r.stage for r in model.receipts] == [InterceptionDeliveryStage.NOT_SENT]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await core._active_session_interception_bridge.close()


@pytest.mark.asyncio
async def test_core_bridge_real_prewire_factory_applies_gap_and_end_without_audio_debt():
    from main_logic.voice_identity_service.interception_runtime import PrewireInterceptionFactory
    from tests.unit.voice_identity_service.test_interception_runtime import _config, _Scorer, _SequenceClassifier, _Tse

    core, generation = _core()
    factory = PrewireInterceptionFactory(_config(), score_backend=_Scorer(),
        classifier=_SequenceClassifier([True, True, False, True, True] + [True] * 100),
        tse_factory=lambda _stream: _Tse())
    received = []

    async def receive(frame, **_kwargs):
        received.append(frame.pcm16)
        frame.delivery.observe(0, frame.delivery.sample_count, InterceptionDeliveryStage.TRANSPORT_WRITTEN)
        frame.delivery.observe(0, frame.delivery.sample_count, InterceptionDeliveryStage.TRANSPORT_OWNED)
        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

    core._asr_runtime.submit = receive
    core._asr_runtime.discontinue_input = AsyncMock()
    await core.set_active_session_interception_factory(factory)
    try:
        for samples in (1600, 400, 400, 400, 400):
            frame = _frame(core, generation, b"\x01\x00" * samples)
            frame = replace(frame, captured_at=time.monotonic(),
                            context=replace(frame.context, captured_at=time.time()))
            assert await core._route_voice_session_activation_output(frame, generation) is OutputCommit.LOCAL_ACCEPTED
        assert received
        assert core._asr_runtime.discontinue_input.await_count >= 1
        assert await core.finish_active_session_interception(generation=generation, context=frame.context) is OutputCommit.LOCAL_ACCEPTED
        assert core._interception_output_cursor[-1] is True
        runtime = factory._runtimes[0]
        assert not runtime._delivery_events
        # This real model runtime rejects control receipts by design. The
        # bridge must apply GAP/END locally instead of treating them as PCM.
        assert runtime.retired
    finally:
        await core._active_session_interception_bridge.close()


@pytest.mark.asyncio
async def test_cancelled_core_writer_late_socket_completion_cannot_resurrect_real_interval():
    from main_logic.voice_identity_service.interception_runtime import PrewireInterceptionFactory
    from main_logic.voice_identity_service.prewire_gate.contracts import PrewireCommitStage
    from main_logic.asr_client._infra import _AsrRequestQueue, AsrSessionConfig
    from main_logic.asr_client.workers.qwen import _QwenConnectionState, _qwen_sender
    from tests.unit.asr.test_asr_interval_transport_delivery import request
    from tests.unit.voice_identity_service.test_interception_runtime import _config, _Scorer, _Classifier, _Tse

    core, generation = _core()
    factory = PrewireInterceptionFactory(_config(), score_backend=_Scorer(), classifier=_Classifier(), tse_factory=lambda _stream: _Tse())
    await core.set_active_session_interception_factory(factory)
    entered, release = asyncio.Event(), asyncio.Event()
    queue, responses = _AsrRequestQueue(), asyncio.Queue()
    state = _QwenConnectionState(7, 3, 1, False)
    state.configured.set()
    sender = None
    captured_tag = None

    class Socket:
        async def send(self, _payload):
            entered.set()
            await release.wait()

    async def route(_pcm, **kwargs):
        nonlocal sender, captured_tag
        captured_tag = kwargs["delivery"]
        queue.put_nowait(request(captured_tag, 0, captured_tag.sample_count))
        sender = asyncio.create_task(_qwen_sender(Socket(), queue, responses, AsrSessionConfig(), state))
        await asyncio.wait_for(entered.wait(), 1)
        raise asyncio.CancelledError

    core._route_microphone_audio_unfiltered = route
    try:
        first = _frame(core, generation, b"\x01\x00" * 1600)
        first = replace(first, context=replace(first.context, captured_at=time.time()))
        assert await core._route_voice_session_activation_output(first, generation) is OutputCommit.LOCAL_ACCEPTED
        second = _frame(core, generation, b"\x01\x00" * 400)
        second = replace(second, context=replace(second.context, captured_at=time.time()))
        with pytest.raises(asyncio.CancelledError):
            await core._route_voice_session_activation_output(second, generation)
        runtime = factory._runtimes[0]
        unsettled = len(runtime._delivery_events)
        assert unsettled > 0 and not captured_tag.settled
        release.set()
        await asyncio.wait_for(queue.join(), 1)
        assert len(runtime._delivery_events) == unsettled
        assert InterceptionDeliveryStage.TRANSPORT_WRITTEN not in captured_tag._reported
        assert InterceptionDeliveryStage.TRANSPORT_OWNED not in captured_tag._reported
        assert any(record.commit_stage is PrewireCommitStage.UNKNOWN for record in runtime._gate._ledger._records.values())
    finally:
        if sender is not None:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        await core._active_session_interception_bridge.close()


@pytest.mark.asyncio
async def test_batch_delivery_receipt_settles_original_owner_after_close():
    audio = _event(0, 0, 4)
    model = _EventRuntime(InterceptionResult(InterceptionDecision.KEEP, audio.pcm16, events=(audio,)))
    bridge = ActiveSessionInterceptionBridge(_Factory(model))
    result = await bridge.process(audio.pcm16, sample_rate_hz=16000, generation="g", ingress_token="i", captured_at=1)
    callback = bridge.delivery_callback(result, generation="g", ingress_token="i")
    await bridge.close()
    receipt = InterceptionDeliveryReceipt(audio, InterceptionDeliveryStage.TRANSPORT_WRITTEN, 1)
    callback(receipt)
    callback(receipt)
    assert model.receipts == [receipt]
    with pytest.raises(ValueError):
        callback(replace(receipt, event=replace(audio, pcm16=b"\x02\x00" * 4)))
    with pytest.raises(ValueError):
        callback(replace(receipt, stage=InterceptionDeliveryStage.NOT_SENT))


@pytest.mark.asyncio
async def test_finish_is_once_and_returns_no_second_end_event():
    end = _event(0, 0, 0, InterceptionOutputKind.END)
    model = _EventRuntime(InterceptionResult(InterceptionDecision.PENDING), InterceptionResult(InterceptionDecision.DROP, events=(end,)))
    bridge = ActiveSessionInterceptionBridge(_Factory(model))
    await bridge.process(b"\x01\x00", sample_rate_hz=16000, generation="g", ingress_token="i", captured_at=1)
    assert (await bridge.finish(generation="g", ingress_token="i")).events == (end,)
    assert (await bridge.finish(generation="g", ingress_token="i")).events == ()
    assert model.finish_calls == 1


@pytest.mark.asyncio
async def test_real_capture_end_control_finishes_before_lease_revocation():
    core, generation = _core()
    end = _event(0, 0, 0, InterceptionOutputKind.END)
    model = _EventRuntime(InterceptionResult(InterceptionDecision.PENDING), InterceptionResult(InterceptionDecision.DROP, events=(end,)))
    await core.set_active_session_interception_factory(_Factory(model))
    token = core._capture_ingress_token()
    await core._active_session_interception_bridge.process(
        b"\x01\x00", sample_rate_hz=16000, generation=generation, ingress_token=token, captured_at=1,
    )
    pipeline = SimpleNamespace(finalize_stream=AsyncMock(return_value=b""))
    core._voice_input_audio_pipeline = pipeline
    core._last_microphone_dsp_context = (pipeline, token, 1.0, True, None, 1.0)
    core._voice_session_activation_runtime = SimpleNamespace(
        output_inflight=False, pending_output_bytes=0, verification_inflight=False,
        pause_output=AsyncMock(return_value=True), fail_output=AsyncMock(),
    )
    core._asr_runtime.wait_input_settled = AsyncMock()
    lease = core._voice_lease_generation
    assert await core._handle_voice_input_control("capture_end", lease + 1)
    assert core._voice_lease_generation == lease
    assert model.finish_calls == 1
    pipeline.finalize_stream.assert_awaited_once()
    # END is locally applied and has no model audio-delivery debt.
    assert not model.receipts
    core._asr_runtime.wait_input_settled.assert_awaited_once()
