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
