"""Real receipt owners cannot prevent independent ASR retirement."""

import asyncio
from contextlib import asynccontextmanager
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


@asynccontextmanager
async def _dispatch_case():
    runtime = IndependentAsrRuntime(_callbacks())
    detector = _RejectionDetector()
    detector.endpointing_ready = lambda token: True
    session, _, turn = _install_active_candidate(runtime, detector)
    dispatcher = runtime._asr_audio_dispatcher
    bridges = []
    worker = dispatcher._worker
    try:
        await dispatcher.wait_idle()

        async def output(*, long=False):
            payload, tags = b"", []
            while not tags or (long and len(payload) <= 32_000):
                bridge, result, group = await _output(turn.ingress)
                bridges.append(bridge)
                payload += result.pcm16
                tags.extend(group)
            return payload, tags, tuple(AudioDeliverySpan(tag, 0, tag.sample_count) for tag in tags)

        yield dispatcher, session, turn, output
    finally:
        # close() transfers its worker reference locally. Retain this case's
        # task so even a failing cancellation regression can reclaim it.
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        await dispatcher.close()
        await asyncio.gather(*tuple(dispatcher._failure_tasks), return_exceptions=True)
        runtime._asr_transcript_dispatcher.invalidate_all()
        await runtime._asr_detector_dispatcher.close()
        await detector.close()
        await session.close()
        for bridge in bridges:
            await bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["stale_queue", "after_send", "before_next"])
@pytest.mark.parametrize("unknown", [False, True])
async def test_handover_notifies_every_unsent_owner(point, unknown, caplog):
    async with _dispatch_case() as (dispatcher, session, turn, output):
        entered, release = asyncio.Event(), asyncio.Event()
        sent = []

        async def send(pcm, **kwargs):
            sent.append(len(pcm))
            if point != "before_next":
                entered.set()
                await release.wait()

        async def wire(*args):
            if point == "before_next":
                entered.set()
                await release.wait()

        session.stream_audio = send
        dispatcher._on_wire_audio = wire
        try:
            if point == "stale_queue":
                assert dispatcher.enqueue_audio(turn, session, b"\x01\x00",
                                                sample_rate_hz=16000, sequence_no=1)
                await asyncio.wait_for(entered.wait(), 1)
            payload, tags, spans = await output(long=point != "stale_queue")
            unsent = 0 if point == "stale_queue" else 32_000 // (tags[0].sample_count * 2)
            if unknown:
                tags[unsent].observe(0, tags[unsent].sample_count, Stage.UNKNOWN)
            assert dispatcher.enqueue_audio(
                turn, session, payload, sample_rate_hz=16000,
                sequence_no=2 if point == "stale_queue" else 1, delivery_spans=spans,
            )
            if point != "stale_queue":
                await asyncio.wait_for(entered.wait(), 1)
            assert dispatcher.activate(turn, session, b"")
            release.set()
            await asyncio.wait_for(dispatcher.wait_idle(), 1)
            assert sent == ([2] if point == "stale_queue" else [32_000])
            assert tags[unsent].settled is (not unknown)
            assert all(tag.settled for tag in tags[unsent + 1:])
            assert dispatcher.active_turn == turn  # old cleanup cannot abort the successor
            if unknown:
                assert "Delivery observer failed" in caplog.text
        finally:
            release.set()


@pytest.mark.asyncio
async def test_send_failure_preserves_original_error_and_notifies_all_ranges():
    async with _dispatch_case() as (dispatcher, session, turn, output):
        entered, release, failed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = OSError("controlled send failure")
        errors = []

        async def send(pcm, **kwargs):
            entered.set()
            await release.wait()
            raise original

        async def failure(token, error):
            errors.append(error)
            failed.set()

        session.stream_audio = send
        dispatcher._on_failure = failure
        try:
            payload, tags, spans = await output(long=True)
            assert dispatcher.enqueue_audio(turn, session, payload, sample_rate_hz=16000,
                                            sequence_no=1, delivery_spans=spans)
            await asyncio.wait_for(entered.wait(), 1)
            # The controlled sink has not admitted or written PCM. Its owner
            # may retire that interval before the suspended operation returns.
            tags[0].observe(0, tags[0].sample_count, Stage.NOT_SENT)
            release.set()
            await asyncio.wait_for(failed.wait(), 1)
            await asyncio.wait_for(dispatcher.wait_idle(), 1)
            assert errors == [original]
            assert Stage.UNKNOWN in tags[1]._reported
            assert not tags[1].settled
            assert tags[-1].settled
            assert dispatcher.active_turn is None
        finally:
            release.set()


@pytest.mark.asyncio
async def test_receipt_rejection_does_not_replace_worker_cancellation():
    async with _dispatch_case() as (dispatcher, session, turn, output):
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(pcm, **kwargs):
            entered.set()
            await release.wait()

        session.stream_audio = send
        try:
            payload, tags, spans = await output()
            assert dispatcher.enqueue_audio(turn, session, payload, sample_rate_hz=16000,
                                            sequence_no=1, delivery_spans=spans)
            await asyncio.wait_for(entered.wait(), 1)
            tags[0].observe(0, tags[0].sample_count, Stage.NOT_SENT)
            worker = dispatcher._worker
            await asyncio.wait_for(dispatcher.close(), 1)
            await asyncio.wait_for(dispatcher.close(), 1)
            assert worker.cancelled()
            assert Stage.UNKNOWN in tags[1]._reported
            assert not tags[1].settled
        finally:
            release.set()


@pytest.mark.asyncio
async def test_queued_receipt_rejection_still_fails_closed_after_notifying_other_owners():
    async with _dispatch_case() as (dispatcher, session, turn, output):
        stages = []

        def reject_queued(stage):
            stages.append(stage)
            return stage is not Stage.QUEUED

        payload, tags, spans = await output(long=True)
        tags[0] = AudioDeliveryTag(tags[0].sample_count, reject_queued)
        spans = tuple(AudioDeliverySpan(tag, 0, tag.sample_count) for tag in tags)
        sent, errors, failed = [], [], asyncio.Event()

        async def send(pcm, **kwargs):
            sent.append(len(pcm))

        async def failure(token, error):
            errors.append(error)
            failed.set()

        session.stream_audio = send
        dispatcher._on_failure = failure
        assert dispatcher.enqueue_audio(turn, session, payload, sample_rate_hz=16000,
                                        sequence_no=1, delivery_spans=spans)
        await asyncio.wait_for(failed.wait(), 1)
        await asyncio.wait_for(dispatcher.wait_idle(), 1)
        assert sent == [32_000]
        assert len(errors) == 1 and type(errors[0]) is RuntimeError
        assert stages == [Stage.QUEUED]
        assert Stage.QUEUED in tags[1]._reported
        assert tags[-1].settled
        assert dispatcher.active_turn is None


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
                assert "observer failed during interval retirement" in caplog.text
        finally:
            tasks = [getattr(session, name) for name in (
                "_worker_task", "_response_task", "_callback_task", "_close_task",
            ) if getattr(session, name) is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await bridge.close()
