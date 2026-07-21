from __future__ import annotations

from main_logic.asr_client.lifecycle import VoiceLifecycleMetrics


def test_metrics_report_throttle_ratio_without_division_by_zero() -> None:
    metrics = VoiceLifecycleMetrics()
    assert metrics.throttle_ratio == 0.0

    metrics.add_local_audio(1_000)
    metrics.add_cloud_audio(250)
    metrics.add_suppressed_audio(750)

    assert metrics.throttle_ratio == 0.75
    assert metrics.snapshot()["omni_mic_audio_bytes"] == 0


def test_omni_microphone_bytes_are_rejected_as_an_invariant() -> None:
    metrics = VoiceLifecycleMetrics()

    try:
        metrics.add_omni_microphone_bytes(2)
    except RuntimeError as exc:
        assert "OMNI_MICROPHONE_ROUTE_FORBIDDEN" in str(exc)
    else:
        raise AssertionError("Omni microphone bytes must never be accepted")


def test_async_detector_and_audio_ordering_metrics_are_low_cardinality() -> None:
    metrics = VoiceLifecycleMetrics()
    snapshot = metrics.snapshot()

    assert snapshot["detector_submit_latency_ms"] == 0
    assert snapshot["detector_queue_audio_ms"] == 0
    assert snapshot["detector_queue_high_water_ms"] == 0
    assert snapshot["detector_overflow_count"] == 0
    assert snapshot["smart_turn_stale_result_count"] == 0
    assert snapshot["smart_turn_coalesced_evaluation_count"] == 0
    assert snapshot["detector_stale_event_count"] == 0
    assert snapshot["asr_audio_command_queue_ms"] == 0
    assert snapshot["asr_abort_discarded_command_count"] == 0
    assert snapshot["provider_wire_sequence"] == 0
    assert snapshot["omni_mic_audio_bytes"] == 0
