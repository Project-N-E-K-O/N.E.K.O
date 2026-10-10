"""Exact interval delivery crosses real request/send boundaries, not byte totals."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client._infra import AsrSessionConfig, _AsrRequestQueue, _AsrWorkerRequest, _RealtimeAsrSessionImpl
from main_logic.asr_client.delivery import begin_transport_write, complete_transport_write, delivery_evidence, interval_delivery_spans, retire_interval_deliveries
from main_logic.asr_client.workers.qwen import _QwenConnectionState, _qwen_sender
from main_logic.asr_client.workers.gemini import gemini_asr_worker
from main_logic.asr_client.workers.glm import glm_asr_worker
from main_logic.voice_input.interception_events import InterceptionDeliveryStage as Stage
from main_logic.voice_turn.audio_delivery import AudioDeliverySpan, AudioDeliveryTag
from tests.unit.test_asr_glm_worker import _FakeResponse


def request(tag, start, end, *, utterance=1):
    return _AsrWorkerRequest("audio", 7, 3, utterance, b"\x01\x00" * (end - start),
                             delivery_spans=(AudioDeliverySpan(tag, start, end),))


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_actual_socket_write_settles_exact_interval_without_provider_ack(outcome):
    entered, release = asyncio.Event(), asyncio.Event()
    stages = []
    tag = AudioDeliveryTag(160, stages.append)
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    requests.put_nowait(request(tag, 0, 160))
    state = _QwenConnectionState(7, 3, 1, False)
    state.configured.set()
    class Socket:
        async def send(self, payload):
            entered.set()
            await release.wait()
            if outcome == "error":
                raise RuntimeError("send interrupted")
    task = asyncio.create_task(_qwen_sender(Socket(), requests, responses, AsrSessionConfig(), state))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert stages == [Stage.QUEUED]
        assert not tag.settled
        if outcome == "cancel":
            task.cancel()
        else:
            release.set()
        if outcome == "success":
            await asyncio.wait_for(requests.join(), 1)
            assert stages == [Stage.QUEUED, Stage.TRANSPORT_WRITTEN, Stage.TRANSPORT_OWNED]
            assert tag.settled
            assert delivery_evidence(requests).pending_intervals == {}
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        retire_interval_deliveries(requests)
        assert Stage.PROVIDER_CONFIRMED not in stages
        if outcome != "success":
            assert stages == [Stage.QUEUED, Stage.UNKNOWN]
            assert delivery_evidence(requests).written_audio_bytes == 0
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_whole_tag_is_not_written_until_every_exact_subspan_has_written():
    stages = []
    tag = AudioDeliveryTag(320, stages.append)
    requests = _AsrRequestQueue()
    first, second = request(tag, 0, 160), request(tag, 160, 320)
    requests.put_nowait(first)
    requests.put_nowait(second)
    evidence = begin_transport_write(requests, delivery_spans=first.delivery_spans)
    complete_transport_write(evidence, 320, generation=7, buffer_epoch=3, provider="test",
                             delivery_spans=first.delivery_spans, takes_ownership=True)
    assert Stage.TRANSPORT_WRITTEN not in stages
    assert interval_delivery_spans(requests, (7, 3, 1)) == second.delivery_spans
    retire_interval_deliveries(requests)
    assert stages == [Stage.QUEUED, Stage.UNKNOWN]
    assert not tag.settled


def test_no_byte_count_guessing_and_successor_scope_retirement():
    a, b = [], []
    first, second = AudioDeliveryTag(10, a.append), AudioDeliveryTag(10, b.append)
    queue = _AsrRequestQueue()
    queue.put_nowait(request(first, 0, 10))
    queue.put_nowait(_AsrWorkerRequest("audio", 7, 4, 1, bytes(20),
                                     delivery_spans=(AudioDeliverySpan(second, 0, 10),)))
    evidence = begin_transport_write(queue)
    complete_transport_write(evidence, 20, generation=7, buffer_epoch=3, provider="legacy")
    assert a == [Stage.QUEUED]  # unrelated legacy byte count has no source authority
    queue.put_nowait(_AsrWorkerRequest("clear", 7, 4))
    assert a == [Stage.QUEUED, Stage.NOT_SENT]
    assert b == [Stage.QUEUED]
    retire_interval_deliveries(queue)
    assert b[-1] is Stage.NOT_SENT


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["gemini", "glm"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_segmented_request_snapshots_explicit_interval_identity(provider, outcome):
    entered, release = asyncio.Event(), asyncio.Event()
    stages = []
    tag = AudioDeliveryTag(160, stages.append)
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    async def dispatch(*args, **kwargs):
        entered.set()
        await release.wait()
        if outcome == "error":
            raise RuntimeError("request outcome unknown")
        if provider == "gemini":
            assert kwargs["config"]["http_options"]["retry_options"]["attempts"] == 1
        return SimpleNamespace(parsed={"transcript": "fixture"}) if provider == "gemini" else _FakeResponse({"text": "fixture"})
    if provider == "gemini":
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=dispatch)))
        worker = gemini_asr_worker(requests, responses, "key", AsrSessionConfig(), client=client)
    else:
        worker = glm_asr_worker(requests, responses, "key", AsrSessionConfig(), http_client=SimpleNamespace(post=dispatch))
    task = asyncio.create_task(worker)
    try:
        assert (await asyncio.wait_for(responses.get(), 1)).kind == "ready"
        requests.put_nowait(_AsrWorkerRequest("clear", 7, 3))
        requests.put_nowait(request(tag, 0, 160))
        await asyncio.wait_for(requests.join(), 1)
        requests.put_nowait(_AsrWorkerRequest("commit", 7, 3, 1))
        await asyncio.wait_for(entered.wait(), 1)
        assert stages == [Stage.QUEUED]
        if outcome == "cancel":
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            release.set()
            assert (await asyncio.wait_for(responses.get(), 1)).kind == ("final" if outcome == "success" else "error")
        assert stages == ([Stage.QUEUED, Stage.TRANSPORT_WRITTEN, Stage.TRANSPORT_OWNED]
                          if outcome == "success" else [Stage.QUEUED, Stage.UNKNOWN])
        assert delivery_evidence(requests).pending_intervals == {}
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        retire_interval_deliveries(requests)


@pytest.mark.asyncio
async def test_tagged_resampling_is_rejected_before_any_request_admission():
    session = _RealtimeAsrSessionImpl(worker_fn=AsyncMock(), api_key="key", config=AsrSessionConfig(),
                                    on_input_transcript=AsyncMock(), on_connection_error=AsyncMock())
    from main_logic.asr_client._infra import _SessionState
    session._state = _SessionState.READY
    stages = []
    tag = AudioDeliveryTag(160, stages.append)
    with pytest.raises(ValueError, match="ASR_DELIVERY_MAPPING_UNSUPPORTED"):
        await session.stream_audio(bytes(320), sample_rate_hz=48000, delivery_spans=(AudioDeliverySpan(tag, 0, 160),))
    assert session._request_queue is None
    assert stages == []


def test_source_mapping_and_metadata_capacity_are_enforced_at_queue_admission():
    queue = _AsrRequestQueue()
    tag = AudioDeliveryTag(160, lambda stage: None)
    with pytest.raises(ValueError, match="MAPPING_INVALID"):
        queue.put_nowait(_AsrWorkerRequest("audio", 1, 0, 1, bytes(2), delivery_spans=(AudioDeliverySpan(tag, 0, 160),)))
    assert queue.qsize() == 0
    evidence = delivery_evidence(queue)
    evidence.pending_intervals[(1, 0, 1)] = [AudioDeliverySpan(tag, 0, 1)] * 4096
    with pytest.raises(RuntimeError, match="CAPACITY"):
        queue.put_nowait(request(tag, 0, 160))
    assert queue.qsize() == 0


def test_late_native_begin_is_rejected_after_definite_non_delivery():
    stages = []
    tag = AudioDeliveryTag(10, stages.append)
    queue = _AsrRequestQueue()
    item = request(tag, 0, 10)
    queue.put_nowait(item)
    retire_interval_deliveries(queue, only_spans=item.delivery_spans)
    with pytest.raises(RuntimeError, match="ASR_DELIVERY_RETIRED"):
        begin_transport_write(queue, delivery_spans=item.delivery_spans)
    assert stages == [Stage.QUEUED, Stage.NOT_SENT]
    assert not delivery_evidence(queue).attempted


def test_unknown_cannot_become_late_ownership_or_be_replayed():
    stages = []
    tag = AudioDeliveryTag(10, stages.append)
    queue = _AsrRequestQueue()
    item = request(tag, 0, 10)
    queue.put_nowait(item)
    evidence = begin_transport_write(queue, delivery_spans=item.delivery_spans)
    retire_interval_deliveries(queue, only_spans=item.delivery_spans)
    assert stages == [Stage.QUEUED, Stage.UNKNOWN]
    complete_transport_write(evidence, 20, generation=7, buffer_epoch=3, provider="late",
                             delivery_spans=item.delivery_spans, takes_ownership=True)
    assert stages == [Stage.QUEUED, Stage.UNKNOWN]
    with pytest.raises(RuntimeError, match="ASR_DELIVERY_RETIRED"):
        queue.put_nowait(item)
    with pytest.raises(RuntimeError, match="ASR_DELIVERY_RETIRED"):
        begin_transport_write(queue, delivery_spans=item.delivery_spans)


@pytest.mark.asyncio
async def test_physical_segment_split_preserves_source_coordinates():
    from main_logic.asr_client._infra import _SessionState
    from main_logic.asr_client.provider_policy import AsrProviderPolicy
    policy = AsrProviderPolicy("segmented", "smart_turn", True, 20, 0, "none")
    session = _RealtimeAsrSessionImpl(worker_fn=AsyncMock(), api_key="key", config=AsrSessionConfig(),
                                    on_input_transcript=AsyncMock(), on_connection_error=AsyncMock(), provider_policy=policy)
    session._state = _SessionState.READY
    session._request_queue = _AsrRequestQueue()
    session._worker_task = asyncio.create_task(asyncio.Event().wait())
    tag = AudioDeliveryTag(400, lambda stage: None)
    try:
        await session.stream_audio(bytes(800), sample_rate_hz=16000, delivery_spans=(AudioDeliverySpan(tag, 0, 400),))
        audio = [item for item in session._request_queue._queue if item.kind == "audio"]
        assert [len(item.audio) for item in audio] == [640, 160]
        assert [(item.delivery_spans[0].start_sample, item.delivery_spans[0].end_sample) for item in audio] == [(0, 320), (320, 400)]
        assert audio[0].utterance_id != audio[1].utterance_id
    finally:
        session._worker_task.cancel()
        await asyncio.gather(session._worker_task, return_exceptions=True)
        retire_interval_deliveries(session._request_queue)


pytestmark = pytest.mark.unit_fast
