"""安全门 / 限流时钟（D-B4 + neko_roast safety_guard 同款，去掉队列细节，加 critical 抢占冷却）。

职责：持有"全局限流时钟 / 抢占冷却 / 手动急停 / 连续失败自动急停 / dry_run"等状态；
仲裁(arbiter)调用它判定与记录。它不决定"说哪条"（那是 arbiter）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .contracts import WtConfig


@dataclass
class SafetyGuard:
    config: WtConfig
    manual_paused: bool = False
    auto_paused: bool = False
    _last_output_at: float = 0.0
    _last_critical_at: float = 0.0
    _failures: list[float] = field(default_factory=list)

    # --- 状态控制 ---
    def update(self, config: WtConfig) -> None:
        self.config = config

    def pause(self) -> None:
        self.manual_paused = True

    def resume(self) -> None:
        self.manual_paused = False
        self.auto_paused = False
        self._failures.clear()
        self._last_output_at = 0.0
        self._last_critical_at = 0.0

    @property
    def stopped(self) -> bool:
        return self.manual_paused or self.auto_paused

    def status(self) -> str:
        if self.auto_paused:
            return "tripped"
        if self.manual_paused:
            return "paused"
        return "running"

    # --- 限流 / 抢占时钟 ---
    def rate_limit_remaining(self, now: float | None = None) -> float:
        """非抢占输出：到下次允许还剩多少秒。limit<=0 关闭限流。"""
        if self.config.global_rate_limit_seconds <= 0:
            return 0.0
        cur = time.time() if now is None else now
        remaining = self.config.global_rate_limit_seconds - (cur - self._last_output_at)
        return remaining if remaining > 0 else 0.0

    def critical_cooldown_remaining(self, now: float | None = None) -> float:
        """抢占输出之间的最小间隔（防抢占风暴）。"""
        if self.config.critical_preempt_cooldown_seconds <= 0:
            return 0.0
        cur = time.time() if now is None else now
        remaining = self.config.critical_preempt_cooldown_seconds - (cur - self._last_critical_at)
        return remaining if remaining > 0 else 0.0

    def mark_output(self, *, critical: bool, now: float | None = None) -> None:
        cur = time.time() if now is None else now
        self._last_output_at = cur
        if critical:
            self._last_critical_at = cur

    # --- 失败 / 自动急停 ---
    def record_failure(self, now: float | None = None) -> None:
        cur = time.time() if now is None else now
        self._failures.append(cur)
        window = self.config.safety_window_seconds
        self._failures = [t for t in self._failures if cur - t <= window]
        if self.config.safety_auto_stop_enabled and len(self._failures) >= self.config.safety_failure_limit:
            self.auto_paused = True

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status(),
            "manual_paused": self.manual_paused,
            "auto_paused": self.auto_paused,
            "failures": len(self._failures),
            "rate_limit_remaining": round(self.rate_limit_remaining(), 1),
        }
