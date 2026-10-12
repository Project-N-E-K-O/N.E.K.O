"""Real receipt owners cannot prevent independent ASR retirement."""

import asyncio
from functools import partial
from unittest.mock import AsyncMock

import httpx
import pytest

from main_logic.asr_client._infra import AsrSessionConfig, _RealtimeAsrSessionImpl
from main_logic.asr_client.delivery import delivery_evidence
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.asr_client.runtime import IndependentAsrRuntime
from main_logic.asr_client.workers.glm import glm_asr_worker
from main_logic.voice_identity_service.interception_runtime import PrewireInterceptionFactory
from main_logic.voice_input.interception import ActiveSessionInterceptionBridge
from main_logic.voice_turn.audio_delivery import AudioDeliverySpan, AudioDeliveryTag
from main_logic.voice_turn.interception_events import InterceptionDeliveryReceipt, InterceptionDeliveryStage as Stage
from tests.unit.asr_client.test_candidate_rejection_runtime import (
    _callbacks, _install_active_candidate, _RejectionDetector,
)
from tests.unit.voice_identity_service.test_candidate_runtime import (
    CandidateScorer, RawScorerMustNotRun, make_selector, WindowSource,
)
from tests.unit.voice_identity_service.test_interception_runtime import _config, _Classifier, _Tse

pytestmark = pytest.mark.runtime


async def _output(ingress):
    scorer = CandidateScorer()
    selector = make_selector(scorer)
    factory = PrewireInterceptionFactory(
        _config(), score_backend=RawScorerMustNotRun(), classifier=_Classifier(),
        tse_factory=lambda _: _Tse(), candidate_factory=lambda _: selector,
        candidate_source_factory=lambda _: WindowSource(lambda start: (100,)),
    )
    bridge = ActiveSessionInterceptionBridge(factory, terminal_on_unavailable=True)
    try:
        generation = object()
        await bridge.process(bytes([1, 0]) * 1600, sample_rate_hz=16000,
                             generation=generation, ingress_token=ingress, captured_at=None)
        result = await bridge.process(bytes([1, 0]) * 400, sample_rate_hz=16000,
                                      generation=generation, ingress_token=ingress, captured_at=None)
        assert len(result.events) == 2 and len(scorer.calls) == 2
        callback = bridge.delivery_callback(result, generation=generation, ingress_token=ingress)
        tags = []
        for event in result.events:
            def report(stage, event=event):
                callback(InterceptionDeliveryReceipt(event, stage, asyncio.get_running_loop().time()))
            report(Stage.LOCAL_ACCEPTED)
            tags.append(AudioDeliveryTag(len(event.pcm16) // 2, report))
        return bridge, result, tags
    except BaseException:
        await bridge.close()
        raise


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["abort", "close"])
async def test_runtime_cleanup_survives_unknown_owner_rejecting_not_sent(action, caplog):
    runtime = IndependentAsrRuntime(_callbacks())
    detector = _RejectionDetector()
    detector.endpointing_ready = lambda token: True
    session, _, turn = _install_active_candidate(runtime, detector)
    dispatcher = runtime._asr_audio_dispatcher
    started, release = asyncio.Event(), asyncio.Event()
    sent = []

    async def send(pcm, **kwargs):
        sent.append(pcm)
        started.set()
        await release.wait()

    session.stream_audio = send
    bridge = None
    try:
        await dispatcher.wait_idle()
        bridge, result, tags = await _output(turn.ingress)
        assert dispatcher.enqueue_audio(turn, session, bytes([4, 0]) * 4,
                                        sample_rate_hz=16000, sequence_no=1)
        await asyncio.wait_for(started.wait(), 1)
        # Two original owners share one command: a rejected first receipt must
        # not suppress the second span or the queue's completion accounting.
        assert dispatcher.enqueue_audio(
            turn, session, result.pcm16, sample_rate_hz=16000, sequence_no=2,
            delivery_spans=tuple(AudioDeliverySpan(tag, 0, tag.sample_count) for tag in tags),
        )
        tags[0].observe(0, tags[0].sample_count, Stage.UNKNOWN)
        if action == "abort":
            await runtime.abort("explicit_cancel")
        else:
            await runtime.close()
        release.set()
        await asyncio.wait_for(dispatcher.wait_idle(), 1)
        assert len(sent) == 1
        assert dispatcher.active_turn is None
        assert dispatcher.asr_abort_discarded_command_count == 1
        assert runtime._asr_session is None
        session.close.assert_awaited_once()
        assert not tags[0].settled
        assert tags[1].settled
        assert "observer failed during audio abort" in caplog.text
    finally:
        release.set()
        if dispatcher._worker is not None:
            dispatcher._worker.cancel()
            await asyncio.gather(dispatcher._worker, return_exceptions=True)
        runtime._asr_transcript_dispatcher.invalidate_all()
        await runtime._asr_detector_dispatcher.close()
        await detector.close()
        if session.close.await_count == 0:
            await session.close()
        if bridge is not None:
            await bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", [False, True])
async def test_glm_close_retires_tasks_without_forgiving_unknown_delivery(unknown, caplog):
    async def no_http(request):
        raise AssertionError("uncommitted audio must not call HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_http)) as client:
        bridge, result, tags = await _output("glm-ingress")
        session = _RealtimeAsrSessionImpl(
            worker_fn=partial(glm_asr_worker, http_client=client), api_key="fixture",
            config=AsrSessionConfig(endpointing_mode="manual"),
            provider_policy=resolve_provider_policy("glm", "manual"),
            on_input_transcript=AsyncMock(), on_connection_error=AsyncMock(),
        )
        try:
            await session.connect()
            tag = tags[0]
            await session.stream_audio(
                result.events[0].pcm16, sample_rate_hz=16000,
                delivery_spans=(AudioDeliverySpan(tag, 0, tag.sample_count),),
            )
            await asyncio.wait_for(session._request_queue.join(), 1)
            if unknown:
                tag.observe(0, tag.sample_count, Stage.UNKNOWN)
            await asyncio.wait_for(session.close(), 2)
            await asyncio.wait_for(session.close(), 2)
            assert session._state.value == "closed"
            assert all(task.done() for task in (session._worker_task, session._response_task, session._callback_task))
            evidence = delivery_evidence(session._request_queue)
            assert evidence.pending_intervals == {}
            assert tag.settled is (not unknown)
            assert (tag in evidence.retired_tags) is unknown
            if unknown:
                assert "observer failed during shutdown" in caplog.text
        finally:
            tasks = [getattr(session, name) for name in (
                "_worker_task", "_response_task", "_callback_task", "_close_task",
            ) if getattr(session, name) is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await bridge.close()
