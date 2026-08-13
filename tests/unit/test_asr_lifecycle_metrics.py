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


def test_wall_clock_metrics_are_excluded_from_snapshot_comparisons():
    """Guard the exclusion list in ``test_core_independent_asr`` against typos.

    That test compares whole metric snapshots for equality, so any metric read
    off ``time.monotonic()`` has to be excluded or the assertion turns into a
    timing race. It cannot be derived (nothing marks a field as wall-clock), so
    the least this can do is catch a name that no longer exists — a rename or
    typo would silently stop excluding a real wall-clock metric.
    """
    import re
    from pathlib import Path

    from main_logic.asr_client.lifecycle import VoiceLifecycleMetrics

    source = (
        Path(__file__).with_name("test_core_independent_asr.py").read_text(encoding="utf-8")
    )
    block = re.search(
        r"volatile_metric_names = frozenset\(\s*\{(.*?)\}\s*\)", source, re.DOTALL
    )
    assert block, "找不到 volatile_metric_names，测试形状变了"
    excluded = set(re.findall(r'"([^"]+)"', block.group(1)))
    assert excluded, "排除集是空的"

    known = set(VoiceLifecycleMetrics().snapshot())
    unknown = sorted(excluded - known)
    assert not unknown, f"排除集里有不存在的指标名（改名或拼错）：{unknown}"

    # The one that actually bit us: it is read off time.monotonic().
    assert "asr_audio_command_queue_ms" in excluded
