"""Retired metadata is bounded without losing unknown transport evidence."""

import pytest

from main_logic.asr_client._infra import _AsrRequestQueue, _AsrWorkerRequest
from main_logic.asr_client.delivery import (
    begin_transport_write,
    complete_transport_write,
    delivery_evidence,
    retire_interval_deliveries,
)
from main_logic.voice_turn.audio_delivery import AudioDeliverySpan, AudioDeliveryTag
from main_logic.voice_turn.interception_events import InterceptionDeliveryStage as Stage

pytestmark = pytest.mark.unit_fast


def _request(tag, start=0, end=None):
    end = tag.sample_count if end is None else end
    return _AsrWorkerRequest(
        "audio", 7, 3, 1, bytes((end - start) * 2),
        delivery_spans=(AudioDeliverySpan(tag, start, end),),
    )


def _dequeue(queue, request):
    queue.put_nowait(request)
    assert queue.get_nowait() is request
    queue.task_done()


def test_long_lived_queue_reclaims_full_not_sent_tombstones_and_rejects_old_tag():
    queue = _AsrRequestQueue()
    stages = []
    old = _request(AudioDeliveryTag(1, stages.append))
    _dequeue(queue, old)
    retire_interval_deliveries(queue)
    assert stages == [Stage.QUEUED, Stage.NOT_SENT]
    for _ in range(4100):
        item = _request(AudioDeliveryTag(1, lambda stage: None))
        _dequeue(queue, item)
        retire_interval_deliveries(queue)
        assert item.delivery_spans[0].tag.settled
    evidence = delivery_evidence(queue)
    assert evidence.pending_intervals == {}
    assert evidence.retired_tags == set()
    assert queue.qsize() == 0
    for _ in range(2):
        retire_interval_deliveries(queue)
        with pytest.raises(RuntimeError, match="ASR_DELIVERY_RETIRED"):
            queue.put_nowait(old)
        with pytest.raises(RuntimeError, match="ASR_DELIVERY_RETIRED"):
            begin_transport_write(queue, delivery_spans=old.delivery_spans)
    complete_transport_write(
        evidence, 2, generation=7, buffer_epoch=3, provider="late",
        delivery_spans=old.delivery_spans, takes_ownership=True,
    )
    assert stages == [Stage.QUEUED, Stage.NOT_SENT]
    assert Stage.PROVIDER_CONFIRMED not in stages
    fresh = _request(AudioDeliveryTag(1, lambda stage: None))
    _dequeue(queue, fresh)
    retire_interval_deliveries(queue)


def test_unknown_tombstones_remain_protected_at_metadata_capacity():
    queue = _AsrRequestQueue()
    first = None
    first_stages = []
    for index in range(4096):
        item = _request(AudioDeliveryTag(1, first_stages.append if index == 0 else lambda stage: None))
        _dequeue(queue, item)
        begin_transport_write(queue, delivery_spans=item.delivery_spans)
        retire_interval_deliveries(queue)
        if first is None:
            first = item
    evidence = delivery_evidence(queue)
    assert evidence.pending_intervals == {}
    assert len(evidence.retired_tags) == 4096
    assert first_stages == [Stage.QUEUED, Stage.UNKNOWN]
    fresh = _request(AudioDeliveryTag(1, lambda stage: None))
    with pytest.raises(RuntimeError, match="ASR_DELIVERY_METADATA_CAPACITY"):
        queue.put_nowait(fresh)
    assert queue.qsize() == 0
    complete_transport_write(
        evidence, 2, generation=7, buffer_epoch=3, provider="late",
        delivery_spans=first.delivery_spans, takes_ownership=True,
    )
    assert first_stages == [Stage.QUEUED, Stage.UNKNOWN]
    assert len(evidence.retired_tags) == 4096
    with pytest.raises(RuntimeError, match="ASR_DELIVERY_RETIRED"):
        queue.put_nowait(first)


@pytest.mark.parametrize("written", [False, True])
def test_partial_tag_retirement_preserves_exact_unsettled_evidence(written):
    queue = _AsrRequestQueue()
    stages = []
    tag = AudioDeliveryTag(2, stages.append)
    first, second = _request(tag, 0, 1), _request(tag, 1, 2)
    _dequeue(queue, first)
    _dequeue(queue, second)
    if written:
        evidence = begin_transport_write(queue, delivery_spans=first.delivery_spans)
        complete_transport_write(
            evidence, 2, generation=7, buffer_epoch=3, provider="fixture",
            delivery_spans=first.delivery_spans, takes_ownership=True,
        )
        retire_interval_deliveries(queue, only_spans=second.delivery_spans)
        assert stages == [Stage.QUEUED, Stage.PARTIALLY_DELIVERED]
        assert tag.settled
        assert tag not in evidence.retired_tags
        assert evidence.pending_intervals == {}
        with pytest.raises(RuntimeError, match="ASR_DELIVERY_RETIRED"):
            queue.put_nowait(second)
    else:
        retire_interval_deliveries(queue, only_spans=first.delivery_spans)
        assert not tag.settled
        assert tag in delivery_evidence(queue).retired_tags
        with pytest.raises(RuntimeError, match="ASR_DELIVERY_RETIRED"):
            begin_transport_write(queue, delivery_spans=second.delivery_spans)
        retire_interval_deliveries(queue, only_spans=second.delivery_spans)
        assert tag.settled
        assert stages == [Stage.QUEUED, Stage.NOT_SENT]
        assert tag not in delivery_evidence(queue).retired_tags


def test_rejected_not_sent_observation_does_not_free_replay_fence():
    queue = _AsrRequestQueue()
    def observer(stage):
        return stage is not Stage.NOT_SENT
    item = _request(AudioDeliveryTag(1, observer))
    _dequeue(queue, item)
    assert isinstance(retire_interval_deliveries(queue), RuntimeError)
    tag = item.delivery_spans[0].tag
    assert not tag.settled
    assert tag in delivery_evidence(queue).retired_tags
    with pytest.raises(RuntimeError, match="ASR_DELIVERY_RETIRED"):
        queue.put_nowait(item)


@pytest.mark.parametrize("attempted", [False, True])
def test_retirement_observer_failure_processes_all_spans_and_keeps_failed_debt(attempted):
    queue = _AsrRequestQueue()
    first_error = RuntimeError("first receipt observer failed")
    later_error = RuntimeError("later receipt observer failed")
    receipts = [[], [], []]
    terminal = Stage.UNKNOWN if attempted else Stage.NOT_SENT

    def observer(index, error=None):
        def observe(stage):
            receipts[index].append(stage)
            if stage is terminal and error is not None:
                raise error
        return observe

    items = [_request(AudioDeliveryTag(1, observer(index, error)))
             for index, error in enumerate((first_error, later_error, None))]
    for item in items:
        _dequeue(queue, item)
        if attempted:
            begin_transport_write(queue, delivery_spans=item.delivery_spans)
    assert retire_interval_deliveries(queue) is first_error
    assert receipts == [[Stage.QUEUED, terminal]] * 3
    evidence = delivery_evidence(queue)
    assert evidence.pending_intervals == {}
    assert evidence.attempted_intervals == set()
    for item in items[:2]:
        assert not item.delivery_spans[0].tag.settled
        assert item.delivery_spans[0].tag in evidence.retired_tags
        with pytest.raises(RuntimeError, match="ASR_DELIVERY_RETIRED"):
            queue.put_nowait(item)
    good_tag = items[2].delivery_spans[0].tag
    assert good_tag.settled is (not attempted)
    assert (good_tag in evidence.retired_tags) is attempted
    with pytest.raises(RuntimeError, match="ASR_DELIVERY_RETIRED"):
        queue.put_nowait(items[2])


@pytest.mark.asyncio
async def test_clear_observer_failure_preserves_control_queue_accounting(caplog):
    import asyncio

    queue = _AsrRequestQueue()
    failed_receipts, healthy_receipts = [], []
    def rejected(stage):
        failed_receipts.append(stage)
        if stage is Stage.NOT_SENT:
            raise RuntimeError("controlled clear observer failure")
    first = _request(AudioDeliveryTag(1, rejected))
    second = _request(AudioDeliveryTag(1, healthy_receipts.append))
    queue.put_nowait(first)
    queue.put_nowait(second)
    clear = _AsrWorkerRequest("clear", 7, 4)
    queue.put_nowait(clear)
    assert queue.qsize() == 3
    assert queue._unfinished_tasks == 3
    assert "observer failed during interval retirement: type=RuntimeError" in caplog.text
    assert "controlled clear observer failure" not in caplog.text
    assert healthy_receipts == [Stage.QUEUED, Stage.NOT_SENT]
    assert not first.delivery_spans[0].tag.settled
    assert first.delivery_spans[0].tag in delivery_evidence(queue).retired_tags
    assert second.delivery_spans[0].tag.settled
    for item in (first, second, clear):
        assert queue.get_nowait() is item
        queue.task_done()
    await asyncio.wait_for(queue.join(), timeout=1)
    assert queue.qsize() == 0
    assert queue._unfinished_tasks == 0
