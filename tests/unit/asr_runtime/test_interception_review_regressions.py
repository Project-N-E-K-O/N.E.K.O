"""Owner refusal must preserve operation, queue and source-identity contracts."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client._infra import (
    AsrSessionConfig, _AsrRequestQueue, _AsrWorkerRequest, _RealtimeAsrSessionImpl, _SessionState,
)
from main_logic.asr_client.delivery import begin_transport_write, complete_transport_write, delivery_evidence
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.asr_client.runtime import IndependentAsrRuntime
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.workers.qwen import _QwenConnectionState, _qwen_sender
from main_logic.voice_input.activation import OutputCommit
from main_logic.voice_input.interception import InterceptionDecision, InterceptionResult
from main_logic.voice_turn.audio_delivery import AudioDeliverySpan, AudioDeliveryTag
from main_logic.voice_turn.interception_events import InterceptionDeliveryStage as Stage
from tests.unit.asr_runtime.test_active_session_interception import _frame
from tests.unit.asr_runtime.test_interception_event_delivery import _core, _event, _EventRuntime, _Factory
from tests.unit.asr_runtime.test_interception_receipt_cleanup import _output
from tests.unit.asr_client.test_candidate_rejection_runtime import _callbacks, _install_active_candidate
from tests.unit.asr_runtime.test_candidate_receiver_integration import Detector


pytestmark = pytest.mark.runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["qwen", "openai", "step", "grok", "soniox"])
async def test_unsupported_route_is_rejected_before_model_and_dispatcher_admission(provider):
    core, generation = _core()
    callbacks = _callbacks()
    independent = IndependentAsrRuntime(callbacks)
    _old_session, lifecycle, token = _install_active_candidate(independent, Detector())
    dispatcher = independent._asr_audio_dispatcher
    worker = dispatcher._worker
    await dispatcher.wait_idle()
    dispatcher.abort()
    policy = resolve_provider_policy(provider, "provider")
    lifecycle.provider_policy = policy
    session = _RealtimeAsrSessionImpl(
        worker_fn=AsyncMock(), api_key="fixture", config=AsrSessionConfig(endpointing_mode="provider"),
        provider_policy=policy, on_input_transcript=AsyncMock(), on_connection_error=AsyncMock(),
    )
    session._state = _SessionState.READY
    independent._asr_session = session
    independent._asr_provider = provider
    assert dispatcher.activate(token, session, b"")
    await dispatcher.wait_idle()
    core._asr_runtime = independent
    core._independent_asr_provider = provider
    core._voice_lease_connection_id = token.ingress.connection_id
    core._voice_lease_generation = token.ingress.lease_generation
    core._microphone_route_generation = token.ingress.route_generation
    audio = _event(0, 0, 4)
    model = _EventRuntime(InterceptionResult(InterceptionDecision.KEEP, audio.pcm16, events=(audio,)))
    model.process = AsyncMock(wraps=model.process)
    await core.set_active_session_interception_factory(_Factory(model))
    try:
        result = await core._route_voice_session_activation_output(_frame(core, generation, bytes(8)), generation)
        await asyncio.wait_for(dispatcher.wait_idle(), 1)
        assert result is OutputCommit.UNKNOWN
        assert core._voice_session_activation_degraded
        assert core._voice_session_activation_status[2] == "interception_unavailable:interception_delivery_unsupported"
        model.process.assert_not_awaited()
        assert model.receipts == []  # Neither local acceptance nor UNKNOWN debt.
        assert independent._asr_input_sample_end == 0
        assert session._request_queue is None
        assert session._input_sample_rate_hz is None
        callbacks.on_failure.assert_not_awaited()
    finally:
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        await independent.close()
        await asyncio.gather(*tuple(dispatcher._failure_tasks), return_exceptions=True)
        await asyncio.gather(*tuple(independent._asr_close_tasks), return_exceptions=True)
        await session.close()
        await core._active_session_interception_bridge.close()
        await asyncio.gather(*tuple(core._core_asr_cleanup_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_interception_capability_follows_published_runtime_lifecycle():
    runtime = IndependentAsrRuntime(_callbacks())
    assert not runtime.supports_interception_delivery
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"), shadow_mode=False,
    )
    assert runtime.supports_interception_delivery
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "provider"), shadow_mode=False,
    )
    assert not runtime.supports_interception_delivery
    await runtime.close()
    assert not runtime.supports_interception_delivery


def _request(spans):
    return _AsrWorkerRequest("audio", 1, 0, 1, bytes(2 * sum(s.samples for s in spans)),
                             delivery_spans=tuple(spans))


@pytest.mark.parametrize("reject_rollback", [False, True])
def test_failed_queue_admission_rolls_back_only_new_spans(reject_rollback):
    queue = _AsrRequestQueue()
    old = AudioDeliveryTag(2, lambda _: None)
    old_request = _request((AudioDeliverySpan(old, 0, 1),))
    queue.put_nowait(old_request)
    calls = []
    error = RuntimeError("private receipt text")

    def refuse(stage):
        calls.append(stage)
        if stage is Stage.QUEUED or (reject_rollback and stage is Stage.NOT_SENT):
            raise error

    bad, good = AudioDeliveryTag(1, refuse), AudioDeliveryTag(1, lambda _: None)
    spans = (AudioDeliverySpan(old, 1, 2), AudioDeliverySpan(bad, 0, 1), AudioDeliverySpan(good, 0, 1))
    with pytest.raises(RuntimeError) as caught:
        queue.put_nowait(_request(spans))
    assert caught.value is error
    evidence = delivery_evidence(queue)
    assert evidence.pending_intervals == {(1, 0, 1): list(old_request.delivery_spans)}
    assert queue.qsize() == queue._unfinished_tasks == 1
    assert calls == [Stage.QUEUED, Stage.NOT_SENT]
    assert good.settled
    assert bad.settled is (not reject_rollback)
    assert (bad in evidence.retired_tags) is reject_rollback
    assert old not in evidence.retired_tags
    begin_transport_write(queue, delivery_spans=old_request.delivery_spans)
    assert queue.get_nowait() is old_request
    queue.task_done()


@pytest.mark.parametrize("refused_stage", [Stage.TRANSPORT_WRITTEN, Stage.TRANSPORT_OWNED])
def test_written_receipt_refusal_does_not_hide_independent_transport_success(refused_stage, caplog):
    stages = [[], []]

    def first(stage):
        stages[0].append(stage)
        return stage is not refused_stage

    tags = (AudioDeliveryTag(1, first), AudioDeliveryTag(1, stages[1].append))
    request = _request(tuple(AudioDeliverySpan(tag, 0, 1) for tag in tags))
    queue = _AsrRequestQueue()
    queue.put_nowait(request)
    evidence = begin_transport_write(queue, delivery_spans=request.delivery_spans)
    complete_transport_write(evidence, 4, generation=1, buffer_epoch=0, provider="fixture",
                             delivery_spans=request.delivery_spans, takes_ownership=True)
    assert stages[1] == [Stage.QUEUED, Stage.TRANSPORT_WRITTEN, Stage.TRANSPORT_OWNED]
    assert tags[1].settled
    assert not tags[0].settled
    assert evidence.pending_intervals == {}
    assert tags[0] in evidence.retired_tags
    with pytest.raises(RuntimeError, match="ASR_DELIVERY_RETIRED"):
        queue.put_nowait(_request((request.delivery_spans[0],)))
    assert "observer failed" in caplog.text
    queue.get_nowait()
    queue.task_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("reject", [False, True])
async def test_qwen_cancellation_always_finishes_request_and_hold_accounting(reject):
    queue, responses = _AsrRequestQueue(), asyncio.Queue()
    entered, release = asyncio.Event(), asyncio.Event()
    stages = []

    def observe(stage):
        stages.append(stage)
        return not (reject and stage is Stage.UNKNOWN)

    tag = AudioDeliveryTag(4, observe)
    queue.put_nowait(_request((AudioDeliverySpan(tag, 0, 4),)))

    async def send(_payload):
        entered.set()
        await release.wait()

    state = _QwenConnectionState(1, 0, 1, True)
    state.configured.set()
    holds = {}
    task = asyncio.create_task(_qwen_sender(SimpleNamespace(send=send), queue, responses,
                                          AsrSessionConfig(endpointing_mode="manual"), state,
                                          audio_holds=holds))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(queue.join(), 1)
        assert queue.held_audio_items == queue.held_audio_bytes == 0
        assert holds == {}
        assert stages == [Stage.QUEUED, Stage.UNKNOWN]
        assert not tag.settled
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for hold in tuple(holds.values()):
            hold.release()
        while queue._unfinished_tasks:
            queue.task_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["unknown", "error", "cancel", "admission"])
async def test_core_rejected_receipt_reaches_remaining_owners_and_preserves_original_error(outcome):
    core, generation = _core()
    events = tuple(_event(i, i * 4, i * 4 + 4) for i in range(3))
    model = _EventRuntime(InterceptionResult(InterceptionDecision.KEEP,
                                          b"".join(event.pcm16 for event in events), events=events))

    def observe(receipt):
        model.receipts.append(receipt)
        return not ((receipt.event == events[0] and receipt.stage is Stage.UNKNOWN)
                    or (receipt.event == events[1] and receipt.stage is Stage.NOT_SENT)
                    or (outcome == "admission" and receipt.stage is Stage.LOCAL_ACCEPTED))

    model.record_delivery = observe
    await core.set_active_session_interception_factory(_Factory(model))
    original = asyncio.CancelledError() if outcome == "cancel" else OSError("private operation text")

    async def sink(*_args, **_kwargs):
        if outcome in {"error", "cancel"}:
            raise original
        return OutputCommit.UNKNOWN

    core._route_microphone_audio_unfiltered = AsyncMock(side_effect=sink)
    try:
        call = core._route_voice_session_activation_output(_frame(core, generation, bytes(24)), generation)
        if outcome in {"error", "cancel"}:
            with pytest.raises(type(original)) as caught:
                await call
            assert caught.value is original
        else:
            assert await call is OutputCommit.UNKNOWN
        assert any(receipt.event == events[2] and receipt.stage is Stage.NOT_SENT
                   for receipt in model.receipts)
        if outcome == "admission":
            core._route_microphone_audio_unfiltered.assert_not_awaited()
    finally:
        await core._active_session_interception_bridge.close()


@pytest.mark.asyncio
async def test_native_interception_is_rejected_before_model_or_delivery_admission():
    core, generation = _core()
    core._asr_route_mode = "native"
    core.session.stream_audio = AsyncMock()
    audio = _event(0, 0, 4)
    model = _EventRuntime(InterceptionResult(InterceptionDecision.KEEP, audio.pcm16, events=(audio,)))
    model.process = AsyncMock(wraps=model.process)
    await core.set_active_session_interception_factory(_Factory(model))
    try:
        result = await core._route_voice_session_activation_output(_frame(core, generation, bytes(8)), generation)
        assert result is OutputCommit.UNKNOWN
        assert core._voice_session_activation_degraded
        assert "interception_delivery_unsupported" in core._voice_session_activation_status[2]
        model.process.assert_not_awaited()
        core.session.stream_audio.assert_not_awaited()
        assert model.receipts == []
    finally:
        await core._active_session_interception_bridge.close()
        await asyncio.gather(*tuple(core._core_asr_cleanup_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_native_without_interception_keeps_ordinary_audio_outlet():
    core, generation = _core()
    core._asr_route_mode = "native"
    core.session.stream_audio = AsyncMock()
    await core.set_active_session_interception_factory(None, interception_required=False)
    frame = _frame(core, generation, bytes(8))
    assert await core._route_voice_session_activation_output(frame, generation) is OutputCommit.LOCAL_ACCEPTED
    core.session.stream_audio.assert_awaited_once()
    assert core.session.stream_audio.await_args.args[0] == frame.pcm


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [resolve_provider_policy("qwen", "manual"), None])
async def test_tagged_session_requires_explicit_manual_delivery_capability(policy):
    session = _RealtimeAsrSessionImpl(worker_fn=AsyncMock(), api_key="fixture",
                                     config=AsrSessionConfig(endpointing_mode="manual"), provider_policy=policy,
                                     on_input_transcript=AsyncMock(), on_connection_error=AsyncMock())
    session._state = _SessionState.READY
    session._request_queue = _AsrRequestQueue()
    session._worker_task = asyncio.create_task(asyncio.Event().wait())
    stages = []
    span = AudioDeliverySpan(AudioDeliveryTag(4, stages.append), 0, 4)
    try:
        if policy is None:
            with pytest.raises(ValueError, match="ASR_DELIVERY_MAPPING_UNSUPPORTED"):
                await session.stream_audio(bytes(8), sample_rate_hz=16000, delivery_spans=(span,))
            assert session._request_queue.empty()
            assert stages == []
        else:
            await session.stream_audio(bytes(8), sample_rate_hz=16000, delivery_spans=(span,))
            assert stages == [Stage.QUEUED]
            item = session._request_queue.get_nowait()
            assert item.delivery_spans == (span,)
            session._request_queue.task_done()
    finally:
        session._worker_task.cancel()
        await asyncio.gather(session._worker_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,mode", [("openai", "provider"), ("qwen", "provider"),
                                         ("grok", "provider"), ("step", "provider"),
                                         ("soniox", "provider")])
async def test_unsupported_session_rejects_tag_before_queue_or_resampler(provider, mode):
    session = _RealtimeAsrSessionImpl(worker_fn=AsyncMock(), api_key="fixture",
                                     config=AsrSessionConfig(endpointing_mode=mode),
                                     provider_policy=resolve_provider_policy(provider, mode),
                                     on_input_transcript=AsyncMock(), on_connection_error=AsyncMock())
    session._state = _SessionState.READY
    stages = []
    span = AudioDeliverySpan(AudioDeliveryTag(4, stages.append), 0, 4)
    with pytest.raises(ValueError, match="ASR_DELIVERY_MAPPING_UNSUPPORTED"):
        await session.stream_audio(bytes(8), sample_rate_hz=16000, delivery_spans=(span,))
    assert stages == []
    assert session._request_queue is None
    assert session._input_sample_rate_hz is None
    # The capability gate applies to interception only, preserving ordinary ASR.
    session._request_queue = _AsrRequestQueue()
    session._worker_task = asyncio.create_task(asyncio.Event().wait())
    try:
        await session.stream_audio(bytes(8), sample_rate_hz=16000)
        assert session._request_queue.qsize() == 1
        session._request_queue.get_nowait()
        session._request_queue.task_done()
    finally:
        session._worker_task.cancel()
        await asyncio.gather(session._worker_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_background_deadline_cannot_silently_rebuild_same_capture():
    bridge, _result, _tags = await _output("ingress")
    runtime = bridge._runtime
    generation = bridge.generation
    identity = runtime._output_identity
    try:
        runtime._arm_deadline(time.monotonic() - 1)
        await asyncio.wait_for(runtime._deadline_task, 2)
        assert runtime.is_closed and runtime.retired
        for _ in range(2):
            result = await bridge.process(bytes(800), sample_rate_hz=16000, generation=generation,
                                          ingress_token="ingress", captured_at=None)
            assert result.decision is InterceptionDecision.UNAVAILABLE
            assert result.reason == "interception_runtime_closed"
            assert bridge._runtime is runtime
            assert bridge._runtime._output_identity == identity
    finally:
        await bridge.close()
