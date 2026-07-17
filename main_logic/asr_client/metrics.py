"""Low-cardinality metrics for independent-ASR lifecycle decisions."""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(slots=True)
class VoiceLifecycleMetrics:
    local_audio_ms: int = 0
    cloud_audio_ms: int = 0
    suppressed_silence_ms: int = 0
    shadow_suppressed_audio_ms: int = 0
    wake_candidate_count: int = 0
    wake_confirmed_count: int = 0
    false_wake_count: int = 0
    buffer_overflow_count: int = 0
    queue_backpressure_count: int = 0
    reconnect_count: int = 0
    stale_callback_count: int = 0
    omni_mic_audio_bytes: int = 0
    provider_wire_audio_ms: int = 0
    connect_latency_ms: int = 0
    first_partial_latency_ms: int = 0
    final_latency_ms: int = 0
    warm_hit_count: int = 0
    smart_turn_load_ms: int = 0
    smart_turn_inference_ms: int = 0

    @property
    def throttle_ratio(self) -> float:
        if self.local_audio_ms <= 0:
            return 0.0
        suppressed = max(0, self.local_audio_ms - self.cloud_audio_ms)
        return min(1.0, suppressed / self.local_audio_ms)

    def add_local_audio(self, duration_ms: int) -> None:
        self.local_audio_ms += max(0, int(duration_ms))

    def add_cloud_audio(self, duration_ms: int) -> None:
        self.cloud_audio_ms += max(0, int(duration_ms))

    def add_provider_wire_audio(self, duration_ms: int) -> None:
        value = max(0, int(duration_ms))
        self.provider_wire_audio_ms += value
        self.cloud_audio_ms += value

    def add_suppressed_audio(self, duration_ms: int, *, shadow: bool = False) -> None:
        value = max(0, int(duration_ms))
        if shadow:
            self.shadow_suppressed_audio_ms += value
        else:
            self.suppressed_silence_ms += value

    def add_omni_microphone_bytes(self, byte_count: int) -> None:
        value = max(0, int(byte_count))
        if value:
            raise RuntimeError(
                "OMNI_MICROPHONE_ROUTE_FORBIDDEN: microphone PCM belongs to independent ASR"
            )

    def snapshot(self) -> dict[str, int | float]:
        result: dict[str, int | float] = asdict(self)
        result["throttle_ratio"] = self.throttle_ratio
        return result
