from __future__ import annotations

import pytest

from main_logic.asr_client.segment_aggregator import SegmentAggregator


def test_forced_physical_segments_are_not_published_early() -> None:
    aggregator = SegmentAggregator()
    turn_id = aggregator.begin_turn()

    assert aggregator.add_transcript(turn_id, 1, "我想一下", forced_split=True) is None
    assert aggregator.add_transcript(turn_id, 2, "明天去吧", forced_split=False) == "我想一下 明天去吧"


def test_stale_turn_and_duplicate_segment_are_ignored() -> None:
    aggregator = SegmentAggregator()
    old_turn = aggregator.begin_turn()
    new_turn = aggregator.begin_turn()

    assert aggregator.add_transcript(old_turn, 1, "旧文本", forced_split=False) is None
    assert aggregator.add_transcript(new_turn, 1, "第一段", forced_split=True) is None
    assert aggregator.add_transcript(new_turn, 1, "重复", forced_split=True) is None
    assert aggregator.add_transcript(new_turn, 2, "第二段", forced_split=False) == "第一段 第二段"


def test_empty_transcripts_do_not_create_blank_segments() -> None:
    aggregator = SegmentAggregator()
    turn_id = aggregator.begin_turn()

    assert aggregator.add_transcript(turn_id, 1, "   ", forced_split=True) is None
    assert aggregator.add_transcript(turn_id, 2, "完成", forced_split=False) == "完成"


def test_segment_ids_must_be_positive() -> None:
    aggregator = SegmentAggregator()
    turn_id = aggregator.begin_turn()

    with pytest.raises(ValueError, match="segment_id"):
        aggregator.add_transcript(turn_id, 0, "bad", forced_split=False)


def test_registered_segments_publish_only_after_logical_completion() -> None:
    aggregator = SegmentAggregator()
    turn_id = aggregator.begin_turn()

    assert aggregator.register_segment(turn_id, "physical-1")
    assert aggregator.register_segment(turn_id, "physical-2")
    assert aggregator.record_transcript("physical-2", "world")
    assert aggregator.collect_ready() == []
    assert aggregator.record_transcript("physical-1", "hello")
    assert aggregator.collect_ready() == []

    assert aggregator.complete_turn(turn_id)
    ready = aggregator.collect_ready()
    assert len(ready) == 1
    assert ready[0].segment_ids == ("physical-1", "physical-2")
    assert ready[0].text == "hello world"


def test_empty_physical_segment_still_completes_logical_turn() -> None:
    aggregator = SegmentAggregator()
    turn_id = aggregator.begin_turn()

    assert aggregator.register_segment(turn_id, "empty")
    assert aggregator.record_transcript("empty", "")
    assert aggregator.complete_turn(turn_id)

    ready = aggregator.collect_ready()
    assert len(ready) == 1
    assert ready[0].text == ""
