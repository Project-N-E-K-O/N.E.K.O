from __future__ import annotations

from main_logic.asr_client.metrics import VoiceLifecycleMetrics


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
