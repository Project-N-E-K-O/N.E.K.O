"""Unit tests for an isolated join; production ASR does not consume it yet.

Events here stand in for a trusted, separately calibrated gate. The tests never
claim that model output or synthetic authorization establishes speaker identity.
"""

from __future__ import annotations

from dataclasses import replace
import struct

import pytest

from main_logic.asr_client.prewire_gate.contracts import (
    PrewireDecisionState, PrewireIntervalIdentity, PrewireStreamKey, SampleRange,
)
from main_logic.asr_client.prewire_gate.extraction import (
    ExtractedPrewireAudio, ExtractionAlignmentError, ExtractionCapacityError,
    ExtractionIdentityError, PrewireExtractionAdapter,
)
from main_logic.asr_client.prewire_gate.gate import PrewireAudioEvent, PrewireEndEvent, PrewireGapEvent


def pcm(values: list[int]) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


def opened(*, max_bytes: int = 64, max_events: int = 8, origin: int = 0, asr_origin: int = 0):
    adapter = PrewireExtractionAdapter(max_buffered_pcm_bytes=max_bytes, max_pending_events=max_events)
    handle = adapter.open_stream(PrewireStreamKey("microphone", 1), profile_generation="owner-1",
        model_generation="model-1", config_generation="config-1", original_cursor=origin, asr_cursor=asr_origin)
    return adapter, handle


def audio(handle, segment: int, start: int, end: int, *, asr_start: int | None = None):
    original = SampleRange(start, end)
    identity = PrewireIntervalIdentity(handle.stream, segment, original,
        handle.profile_generation, handle.model_generation, handle.config_generation)
    asr_start = start if asr_start is None else asr_start
    return PrewireAudioEvent(identity, original, SampleRange(asr_start, asr_start + end - start), pcm([-3000] * (end - start)))


def gap(handle, segment: int, start: int, end: int, *, decision=PrewireDecisionState.DROP):
    source = audio(handle, segment, start, end)
    return PrewireGapEvent(source.identity, source.original_range, decision, "gate_authorized_gap")


@pytest.mark.parametrize("gate_first", [True, False])
def test_only_authorized_extracted_samples_are_emitted_whichever_arrives_first(gate_first):
    adapter, handle = opened()
    event = audio(handle, 1, 0, 4)
    enhanced = pcm([10, 20, 30, 40])
    if gate_first:
        assert adapter.accept_event(handle, event) == ()
        result = adapter.append_extracted(handle, SampleRange(0, 4), enhanced)
    else:
        assert adapter.append_extracted(handle, SampleRange(0, 4), enhanced) == ()
        result = adapter.accept_event(handle, event)
    assert result == (ExtractedPrewireAudio(event.identity, event.original_range, event.asr_range, enhanced),)
    assert result[0].pcm16 != event.pcm16
    assert adapter.buffered_pcm_bytes == adapter.pending_event_count == 0


def test_tse_without_gate_permission_never_releases_audio():
    adapter, handle = opened()
    assert adapter.append_extracted(handle, SampleRange(0, 5), pcm([1, 2, 3, 4, 5])) == ()
    assert adapter.buffered_pcm_bytes == 10
    assert adapter.pending_event_count == 0


def test_gate_without_tse_never_falls_back_to_original_audio():
    adapter, handle = opened()
    assert adapter.accept_event(handle, audio(handle, 1, 0, 4)) == ()
    assert adapter.pending_event_count == 1
    assert adapter.buffered_pcm_bytes == 0
    assert adapter.retire(handle)
    assert adapter.pending_event_count == 0


def test_different_chunk_sizes_keep_ranges_complete_and_emit_each_gate_event_once():
    adapter, handle = opened(origin=100, asr_origin=500)
    first = audio(handle, 1, 100, 104, asr_start=500)
    second = audio(handle, 2, 104, 109, asr_start=504)
    assert adapter.accept_event(handle, first) == ()
    assert adapter.accept_event(handle, second) == ()
    assert adapter.append_extracted(handle, SampleRange(100, 102), pcm([1, 2])) == ()
    out = adapter.append_extracted(handle, SampleRange(102, 106), pcm([3, 4, 5, 6]))
    assert len(out) == 1
    assert out[0].original_range == SampleRange(100, 104)
    assert out[0].asr_range == SampleRange(500, 504)
    assert out[0].pcm16 == pcm([1, 2, 3, 4])
    out = adapter.append_extracted(handle, SampleRange(106, 109), pcm([7, 8, 9]))
    assert len(out) == 1
    assert out[0].original_range == SampleRange(104, 109)
    assert out[0].pcm16 == pcm([5, 6, 7, 8, 9])
    assert adapter.buffered_pcm_bytes == 0


def test_gap_is_an_explicit_boundary_and_never_concatenates_discontinuous_audio():
    adapter, handle = opened()
    adapter.append_extracted(handle, SampleRange(0, 10), pcm(list(range(10))))
    result = list(adapter.accept_event(handle, audio(handle, 1, 0, 3)))
    dropped = gap(handle, 2, 3, 7)
    result.extend(adapter.accept_event(handle, dropped))
    result.extend(adapter.accept_event(handle, audio(handle, 3, 7, 10, asr_start=3)))
    assert [type(event) for event in result] == [ExtractedPrewireAudio, PrewireGapEvent, ExtractedPrewireAudio]
    assert result[0].pcm16 == pcm([0, 1, 2])
    assert result[1] == dropped
    assert result[2].pcm16 == pcm([7, 8, 9])
    assert result[2].original_range == SampleRange(7, 10)
    assert result[2].asr_range == SampleRange(3, 6)
    assert adapter.buffered_pcm_bytes == 0


def test_gap_decided_before_inference_discards_future_samples_inside_gap():
    adapter, handle = opened()
    dropped = gap(handle, 1, 0, 5)
    assert adapter.accept_event(handle, dropped) == (dropped,)
    assert adapter.accept_event(handle, audio(handle, 2, 5, 8, asr_start=0)) == ()
    assert adapter.append_extracted(handle, SampleRange(0, 3), pcm([99] * 3)) == ()
    assert adapter.buffered_pcm_bytes == 0
    out = adapter.append_extracted(handle, SampleRange(3, 8), pcm([99, 99, 11, 12, 13]))
    assert out[0].pcm16 == pcm([11, 12, 13])
    assert out[0].asr_range == SampleRange(0, 3)
    assert adapter.buffered_pcm_bytes == 0


def test_future_gap_cannot_overtake_earlier_authorized_audio_waiting_for_tse():
    adapter, handle = opened()
    assert adapter.accept_event(handle, audio(handle, 1, 0, 3)) == ()
    dropped = gap(handle, 2, 3, 6)
    assert adapter.accept_event(handle, dropped) == ()
    out = adapter.append_extracted(handle, SampleRange(0, 3), pcm([1, 2, 3]))
    assert [type(event) for event in out] == [ExtractedPrewireAudio, PrewireGapEvent]
    assert out[1] == dropped


@pytest.mark.parametrize("start,end", [(0, 4), (2, 6), (5, 8)])
def test_duplicate_overlapping_and_reordered_tse_output_retire_current_stream(start, end):
    adapter, handle = opened()
    adapter.append_extracted(handle, SampleRange(0, 4), pcm([1] * 4))
    with pytest.raises(ExtractionAlignmentError, match="gap_overlap_or_reorder"):
        adapter.append_extracted(handle, SampleRange(start, end), pcm([2] * (end - start)))
    assert adapter.buffered_pcm_bytes == 0
    with pytest.raises(ExtractionIdentityError):
        adapter.accept_event(handle, audio(handle, 1, 0, 4))


def test_duplicate_gate_event_cannot_resubmit_an_already_returned_range():
    adapter, handle = opened()
    event = audio(handle, 1, 0, 2)
    adapter.append_extracted(handle, event.original_range, pcm([1, 2]))
    assert len(adapter.accept_event(handle, event)) == 1
    with pytest.raises(ExtractionAlignmentError, match="replay"):
        adapter.accept_event(handle, event)


@pytest.mark.parametrize("event_factory", [
    lambda handle: audio(handle, 1, 1, 3),
    lambda handle: replace(audio(handle, 1, 0, 3), asr_range=SampleRange(1, 4)),
    lambda handle: replace(audio(handle, 1, 0, 3), pcm16=b"x"),
    lambda handle: gap(handle, 1, 0, 3, decision=PrewireDecisionState.KEEP),
    lambda handle: gap(handle, 1, 0, 3, decision=PrewireDecisionState.PENDING),
])
def test_invalid_gate_contract_never_grants_permission(event_factory):
    adapter, handle = opened()
    with pytest.raises(ExtractionAlignmentError):
        adapter.accept_event(handle, event_factory(handle))
    assert adapter.buffered_pcm_bytes == adapter.pending_event_count == 0


def test_wrong_pcm_length_fails_closed_before_any_part_of_the_range_is_emitted():
    adapter, handle = opened()
    adapter.accept_event(handle, audio(handle, 1, 0, 3))
    with pytest.raises(ExtractionAlignmentError, match="pcm_range_mismatch"):
        adapter.append_extracted(handle, SampleRange(0, 3), pcm([1, 2]))
    assert adapter.pending_event_count == 0


def test_buffers_and_pending_events_have_independent_hard_caps():
    adapter, handle = opened(max_bytes=4)
    with pytest.raises(ExtractionCapacityError, match="pcm_capacity"):
        adapter.append_extracted(handle, SampleRange(0, 3), pcm([1, 2, 3]))
    assert adapter.buffered_pcm_bytes == 0
    adapter, handle = opened(max_events=1)
    adapter.accept_event(handle, audio(handle, 1, 0, 2))
    with pytest.raises(ExtractionCapacityError, match="event_capacity"):
        adapter.accept_event(handle, audio(handle, 2, 2, 4))
    assert adapter.pending_event_count == 0


def test_impossible_atomic_gate_range_is_rejected_before_waiting_forever():
    adapter, handle = opened(max_bytes=4)
    with pytest.raises(ExtractionCapacityError, match="authorized_range_exceeds"):
        adapter.accept_event(handle, audio(handle, 1, 0, 3))


def test_end_waits_for_exact_tse_tail_even_when_gate_audio_was_authorized_earlier():
    adapter, handle = opened()
    adapter.accept_event(handle, audio(handle, 1, 0, 5))
    end = PrewireEndEvent(handle.stream, 5, 5)
    assert adapter.accept_event(handle, end) == ()
    assert adapter.append_extracted(handle, SampleRange(0, 3), pcm([1, 2, 3])) == ()
    out = adapter.append_extracted(handle, SampleRange(3, 5), pcm([4, 5]))
    assert len(out) == 2
    assert out[0].pcm16 == pcm([1, 2, 3, 4, 5])
    assert out[1] == end
    assert adapter.pending_event_count == adapter.buffered_pcm_bytes == 0
    with pytest.raises(ExtractionIdentityError):
        adapter.accept_event(handle, end)


def test_end_with_only_a_gap_still_waits_for_capture_tail_without_releasing_audio():
    adapter, handle = opened()
    adapter.accept_event(handle, gap(handle, 1, 0, 5))
    end = PrewireEndEvent(handle.stream, 5, 0)
    assert adapter.accept_event(handle, end) == ()
    assert adapter.append_extracted(handle, SampleRange(0, 5), pcm([99] * 5)) == (end,)


@pytest.mark.parametrize("end", [(4, 5), (5, 4)])
def test_wrong_capture_or_asr_end_cannot_hide_an_unresolved_range(end):
    adapter, handle = opened()
    adapter.accept_event(handle, audio(handle, 1, 0, 5))
    with pytest.raises(ExtractionAlignmentError, match="capture_end_cursor"):
        adapter.accept_event(handle, PrewireEndEvent(handle.stream, *end))


def test_tse_tail_cannot_exceed_gate_capture_end():
    adapter, handle = opened()
    adapter.accept_event(handle, audio(handle, 1, 0, 3))
    adapter.accept_event(handle, PrewireEndEvent(handle.stream, 3, 3))
    with pytest.raises(ExtractionAlignmentError, match="exceeds_capture_end"):
        adapter.append_extracted(handle, SampleRange(0, 4), pcm([1] * 4))


def test_retired_inference_and_cleanup_cannot_poison_successor_owner():
    adapter, old = opened()
    adapter.accept_event(old, audio(old, 1, 0, 3))
    adapter.append_extracted(old, SampleRange(0, 1), pcm([1]))
    assert adapter.retire(old)
    assert adapter.pending_event_count == adapter.buffered_pcm_bytes == 0
    current = adapter.open_stream(PrewireStreamKey("microphone", 2), profile_generation="owner-2",
        model_generation="model-1", config_generation="config-1")
    with pytest.raises(ExtractionIdentityError):
        adapter.append_extracted(old, SampleRange(1, 3), pcm([2, 3]))
    with pytest.raises(ExtractionIdentityError):
        adapter.accept_event(current, audio(old, 2, 0, 3))
    assert adapter.retire(old) is False
    adapter.accept_event(current, audio(current, 1, 0, 3))
    out = adapter.append_extracted(current, SampleRange(0, 3), pcm([7, 8, 9]))
    assert out[0].pcm16 == pcm([7, 8, 9])
    assert out[0].identity.profile_generation == "owner-2"


def test_handle_value_clone_does_not_acquire_a_stream():
    adapter, handle = opened()
    forged = replace(handle)
    assert forged == handle and forged is not handle
    with pytest.raises(ExtractionIdentityError):
        adapter.append_extracted(forged, SampleRange(0, 2), pcm([1, 2]))
    assert adapter.append_extracted(handle, SampleRange(0, 2), pcm([1, 2])) == ()


def test_close_is_permanent_idempotent_and_releases_unsubmitted_audio():
    adapter, handle = opened()
    adapter.append_extracted(handle, SampleRange(0, 2), pcm([1, 2]))
    adapter.accept_event(handle, audio(handle, 1, 0, 4))
    adapter.close()
    adapter.close()
    assert adapter.buffered_pcm_bytes == adapter.pending_event_count == 0
    with pytest.raises(ExtractionIdentityError):
        adapter.append_extracted(handle, SampleRange(2, 4), pcm([3, 4]))
    with pytest.raises(ExtractionAlignmentError, match="adapter_closed"):
        adapter.open_stream(handle.stream, profile_generation="owner-1", model_generation="model-1", config_generation="config-1")


def test_empty_capture_end_and_successor_stream_have_independent_origins():
    adapter, handle = opened(origin=50, asr_origin=7)
    end = PrewireEndEvent(handle.stream, 50, 7)
    assert adapter.accept_event(handle, end) == (end,)
    current = adapter.open_stream(handle.stream, profile_generation="owner-1", model_generation="model-1", config_generation="config-1")
    assert current.generation > handle.generation
    adapter.accept_event(current, audio(current, 1, 0, 2))
    assert adapter.append_extracted(current, SampleRange(0, 2), pcm([10, 20]))[0].pcm16 == pcm([10, 20])


def test_returned_audio_has_a_delivery_fence_through_capture_end_and_retirement():
    adapter, handle = opened()
    adapter.accept_event(handle, audio(handle, 1, 0, 2))
    adapter.accept_event(handle, PrewireEndEvent(handle.stream, 2, 2))
    output = adapter.append_extracted(handle, SampleRange(0, 2), pcm([1, 2]))
    assert len(output) == 2
    assert adapter.delivery_is_current(handle)
    adapter.retire(handle)
    assert not adapter.delivery_is_current(handle)
    assert not adapter.delivery_is_current(None)
    with pytest.raises(ExtractionIdentityError):
        adapter.append_extracted(None, SampleRange(2, 4), pcm([3, 4]))
    current = adapter.open_stream(handle.stream, profile_generation=handle.profile_generation,
        model_generation=handle.model_generation, config_generation=handle.config_generation)
    assert not adapter.delivery_is_current(handle)
    assert adapter.delivery_is_current(current)
    adapter.close()
    assert not adapter.delivery_is_current(current)
