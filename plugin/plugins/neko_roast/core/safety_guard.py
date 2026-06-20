"""Runtime safety guard for live viewer interactions."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .contracts import RoastConfig, SafetyDecision, SafetyStatus, ViewerEvent

FailureKind = Literal["pipeline", "output"]


@dataclass
class SafetyGuard:
    """Keeps live failures from leaking into the stream or spamming output."""

    config: RoastConfig
    audit: Any
    manual_paused: bool = False
    auto_paused: bool = False
    degraded: bool = False
    connected: bool = True
    queue_size: int = 0
    queue_overflows: int = 0
    _pipeline_failures: list[float] = field(default_factory=list)
    _output_failures: list[float] = field(default_factory=list)
    _last_output_at: float = 0.0

    def update(self, config: RoastConfig) -> None:
        self.config = config
        self.queue_size = min(self.queue_size, self.config.queue_limit)

    def pause(self, reason: str = "manual pause") -> None:
        self.manual_paused = True
        self.audit.record("safety_manual_pause", reason, level="warning")

    def resume(self) -> None:
        self.manual_paused = False
        self.auto_paused = False
        self.degraded = False
        self.queue_overflows = 0
        self._pipeline_failures.clear()
        self._output_failures.clear()
        self._last_output_at = 0.0
        self.clear_queue()
        self.audit.record("safety_resumed", "safety guard reset", level="info")

    def clear_queue(self) -> None:
        self.queue_size = 0

    def set_connected(self, connected: bool) -> None:
        self.connected = bool(connected)

    def before_event(self, event: ViewerEvent) -> SafetyDecision:
        if event.source == "live_danmaku" and not self.connected:
            return SafetyDecision(False, "disconnected", "live event source is disconnected")
        if self.manual_paused:
            return SafetyDecision(False, "paused", "roast is manually paused")
        if self.auto_paused:
            return SafetyDecision(False, "tripped", "automatic safety stop is active")
        if self.queue_size >= self.config.queue_limit:
            self.queue_overflows += 1
            if self.queue_overflows >= self.config.safety_queue_overflow_limit:
                self.degraded = True
            self.audit.record(
                "safety_queue_overflow",
                "queue limit reached",
                level="warning",
                detail={"queue_size": self.queue_size, "queue_limit": self.config.queue_limit},
            )
            return SafetyDecision(False, self.status(), "queue limit reached")
        self.queue_size += 1
        return SafetyDecision(True, self.status(), "")

    def after_event(self) -> None:
        self.queue_size = max(0, self.queue_size - 1)

    def before_output(self, event: ViewerEvent | None = None) -> SafetyDecision:
        if self.manual_paused:
            return SafetyDecision(False, "paused", "output is manually paused")
        if self.auto_paused:
            return SafetyDecision(False, "tripped", "automatic safety stop is active")
        # 限流：直播态按 rate_limit_seconds 控制最小锐评间隔；开发者沙盒不限流（要即时调试反馈）。
        is_sandbox = event is not None and event.source == "developer_sandbox"
        if not is_sandbox and self.config.rate_limit_seconds > 0:
            now = time.monotonic()
            if (now - self._last_output_at) < self.config.rate_limit_seconds:
                return SafetyDecision(False, self.status(), "rate limited")
            self._last_output_at = now
        return SafetyDecision(True, self.status(), "")

    def output_cooldown_remaining(self, now: float | None = None) -> float:
        """到下一次允许投递还剩多少秒（按 rate_limit_seconds）。

        限流关闭（``rate_limit_seconds <= 0``）或冷却已过返回 ``0.0``。只描述限流时序，
        不含暂停/急停（那是 ``before_output`` 的硬闸门）。供 ``live_events`` 中枢把开窗
        择优对齐到这段冷却：窗口在冷却结束时 flush，胜者不会反被 ``before_output`` 判限流。
        """
        if self.config.rate_limit_seconds <= 0:
            return 0.0
        current = time.monotonic() if now is None else now
        remaining = self.config.rate_limit_seconds - (current - self._last_output_at)
        return remaining if remaining > 0 else 0.0

    def record_failure(self, kind: FailureKind, message: str) -> None:
        now = time.monotonic()
        bucket = self._pipeline_failures if kind == "pipeline" else self._output_failures
        bucket.append(now)
        self._trim(bucket, now)
        limit = (
            self.config.safety_pipeline_failure_limit
            if kind == "pipeline"
            else self.config.safety_output_failure_limit
        )
        self.audit.record(
            f"safety_{kind}_failure",
            message,
            level="error",
            detail={"count": len(bucket), "limit": limit},
        )
        if self.config.safety_auto_stop_enabled and len(bucket) >= limit:
            self.auto_paused = True
            self.audit.record(
                "safety_auto_stop",
                f"automatic stop after {len(bucket)} {kind} failures",
                level="error",
                detail={"kind": kind, "window_seconds": self.config.safety_window_seconds},
            )

    def _trim(self, bucket: list[float], now: float) -> None:
        window = self.config.safety_window_seconds
        bucket[:] = [item for item in bucket if now - item <= window]

    def status(self) -> SafetyStatus:
        if not self.connected:
            return "disconnected"
        if self.auto_paused:
            return "tripped"
        if self.manual_paused:
            return "paused"
        if self.degraded:
            return "degraded"
        return "running"

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status(),
            "manual_paused": self.manual_paused,
            "auto_paused": self.auto_paused,
            "auto_stop_enabled": self.config.safety_auto_stop_enabled,
            "degraded": self.degraded,
            "connected": self.connected,
            "queue_size": self.queue_size,
            "queue_limit": self.config.queue_limit,
            "queue_overflows": self.queue_overflows,
            "pipeline_failures": len(self._pipeline_failures),
            "output_failures": len(self._output_failures),
        }
