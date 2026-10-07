import struct

import pytest

from main_logic.asr_client.audio_ranges import AudioSampleSpan, RangedAudioBuffer
from main_logic.asr_client.lifecycle import (
    VoiceInputLifecycleController, VoiceLifecycleEvent as Event, VoiceRouteMode,
)
from main_logic.asr_client.provider_policy import resolve_provider_policy


def pcm(start, count):
    return struct.pack(f"<{count}h", *(i % 32768 for i in range(start, start + count)))


def controller():
    result = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"), shadow_mode=False,
    )
    result.open(route_mode=VoiceRouteMode.INDEPENDENT)
    return result


def buffer(control, start, count):
    return control.buffer_admission_audio(
        pcm(start, count), start_sample=start, through_sample=start + count, confirmed=False,
    )


@pytest.mark.parametrize("second_start", [111, 109])
def test_hole_and_overlap_are_not_hidden_by_sufficient_byte_count(second_start):
    control = controller()
    buffer(control, 100, 10)
    buffer(control, second_start, 20)
    assert not control.retain_admitted_candidate(start_sample=105)
    assert control.admission_failure_reason == "candidate_audio_range_discontinuous"
    assert control.pre_roll_bytes == 60


def test_missing_prefix_is_not_clamped_to_retained_audio():
    control = controller()
    buffer(control, 10766, 4096)
    assert not control.retain_admitted_candidate(start_sample=10752)
    assert control.admission_failure_reason == "candidate_audio_range_missing"
    assert control.retain_admitted_candidate(start_sample=10766)


def test_gap_before_requested_candidate_does_not_reject_complete_successor():
    control = controller()
    buffer(control, 100, 10)
    buffer(control, 120, 40)
    assert control.retain_admitted_candidate(start_sample=125)
    control.transition(Event.SOFT_WAKE)
    control.transition(Event.SPEECH_CONFIRMED)
    assert control.drain_active_start_audio() == pcm(125, 35)
    assert control.last_drained_spans == (AudioSampleSpan(125, 35),)


def test_candidate_trim_and_activation_preserve_exact_pcm_and_span():
    control = controller()
    buffer(control, 100, 489)
    control.transition(Event.SOFT_WAKE)
    buffer(control, 589, 490)
    assert control.retain_admitted_candidate(start_sample=107)
    control.transition(Event.SPEECH_CONFIRMED)
    assert control.peek_active_start_spans() == (AudioSampleSpan(107, 972),)
    assert control.drain_active_start_audio() == pcm(107, 972)
    assert control.last_drained_spans == (AudioSampleSpan(107, 972),)
    assert control.drain_active_start_audio() == b""


@pytest.mark.parametrize("confirmed", [False, True])
def test_pending_successor_survives_old_final_with_its_range(confirmed):
    control = controller()
    control.transition(Event.SOFT_WAKE)
    control.transition(Event.SPEECH_CONFIRMED)
    control.transition(Event.TURN_SEALED)
    buffer(control, 10766, 489)
    buffer(control, 11255, 490)
    assert control.retain_admitted_candidate(start_sample=10766)
    if confirmed:
        control.mark_pending_turn_speech()
    control.transition(Event.PROVIDER_FINAL)
    if confirmed:
        assert control.begin_pending_turn() == pcm(10766, 979)
    else:
        assert control.preserve_unconfirmed_pending_audio()
        assert control.retain_admitted_candidate(start_sample=10766)
        control.transition(Event.SPEECH_CONFIRMED)
        assert control.drain_active_start_audio() == pcm(10766, 979)
    assert control.last_drained_spans == (AudioSampleSpan(10766, 979),)


def test_move_and_eviction_preserve_actual_sample_positions():
    source = RangedAudioBuffer(capacity_ms=2)
    target = RangedAudioBuffer(capacity_ms=1)
    source.append(pcm(100, 32), start_sample=100)
    assert source.move_to(target) == 32
    assert source.byte_count == 0 and source.spans == ()
    assert target.peek() == pcm(116, 16)
    assert target.spans == (AudioSampleSpan(116, 16),)


def test_confirmed_pending_capacity_failure_preserves_first_samples():
    control = controller()
    control.transition(Event.SOFT_WAKE)
    control.transition(Event.SPEECH_CONFIRMED)
    control.transition(Event.TURN_SEALED)
    control.mark_pending_turn_speech()
    limit = control.config.pending_audio_ms * 16
    control.buffer_admission_audio(pcm(0, limit), through_sample=limit, confirmed=True, pending_only=True)
    result = control.buffer_admission_audio(pcm(limit, 1), through_sample=limit + 1, confirmed=True, pending_only=True)
    assert result.backpressure
    assert control.admission_failure_reason == "candidate_audio_capacity_exceeded"
    control.transition(Event.PROVIDER_FINAL)
    assert control.begin_pending_turn() == pcm(0, limit)


def active_pending_prefix(count=979):
    control = controller()
    control.transition(Event.SOFT_WAKE)
    control.transition(Event.SPEECH_CONFIRMED)
    control.transition(Event.TURN_SEALED)
    buffer(control, 10766, count)
    control.mark_pending_turn_speech()
    control.transition(Event.PROVIDER_FINAL)
    assert control.begin_pending_turn() == pcm(10766, count)
    return control


def test_pending_prefix_keeps_new_audio_until_atomic_dispatcher_handoff():
    control = active_pending_prefix()
    assert control.peek_active_start_audio() == pcm(10766, 979)
    decision = control.buffer_active_start_audio(pcm(11745, 490), start_sample=11745)
    assert decision.disposition.value == "buffer"
    assert control.peek_active_start_audio() == pcm(10766, 1469)
    assert control.peek_active_start_spans() == (AudioSampleSpan(10766, 1469),)
    assert control.drain_active_start_audio() == pcm(10766, 1469)
    assert control.drain_active_start_audio() == b""


@pytest.mark.parametrize("offset", [-1, 1])
def test_pending_prefix_rejects_overlap_or_gap_without_mutating_pcm(offset):
    control = active_pending_prefix()
    decision = control.buffer_active_start_audio(pcm(11745 + offset, 10), start_sample=11745 + offset)
    assert decision.disposition.value == "block"
    assert control.admission_failure_reason == "candidate_audio_range_discontinuous"
    assert control.drain_active_start_audio() == pcm(10766, 979)


def test_pending_prefix_capacity_never_evicts_admitted_first_word():
    control = active_pending_prefix()
    remaining = control.prefix_capacity_bytes // 2 - 979
    control.buffer_active_start_audio(pcm(11745, remaining), start_sample=11745)
    end = 11745 + remaining
    result = control.buffer_active_start_audio(pcm(end, 1), start_sample=end)
    assert result.backpressure
    assert control.admission_failure_reason == "candidate_audio_capacity_exceeded"
    assert control.drain_active_start_audio() == pcm(10766, 979 + remaining)
