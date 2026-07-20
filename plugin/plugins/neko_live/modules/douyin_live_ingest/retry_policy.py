"""Bounded retry policy for the pending Douyin live transport."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .public_projection import safe_public_bool, safe_public_float, safe_public_int, safe_public_text


@dataclass(frozen=True, slots=True)
class DouyinReconnectPolicy:
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    backoff_multiplier: float = 2.0

    def next_delay(self, retry_count: int) -> float:
        if retry_count <= 0:
            return 0.0
        base_delay = _non_negative_finite_float(self.base_delay_seconds)
        max_delay = _non_negative_finite_float(self.max_delay_seconds)
        multiplier = _non_negative_finite_float(self.backoff_multiplier)
        if base_delay == 0.0 or max_delay == 0.0:
            return 0.0
        raw = base_delay * (multiplier ** (retry_count - 1))
        if not math.isfinite(raw) or raw < 0:
            return 0.0
        return min(max_delay, raw)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "max_retries": safe_public_int(self.max_retries),
            "base_delay_seconds": safe_public_float(self.base_delay_seconds),
            "max_delay_seconds": safe_public_float(self.max_delay_seconds),
            "backoff_multiplier": safe_public_float(self.backoff_multiplier),
        }


@dataclass(slots=True)
class DouyinReconnectState:
    policy: DouyinReconnectPolicy
    retry_count: int = 0
    next_delay_seconds: float = 0.0
    exhausted: bool = False
    last_reason: str = ""

    def reset(self) -> None:
        self.retry_count = 0
        self.next_delay_seconds = 0.0
        self.exhausted = False
        self.last_reason = ""

    def record_failure(self, reason: Any) -> None:
        self.last_reason = _safe_reason(reason)
        self.exhausted = safe_public_bool(self.exhausted)
        if self.exhausted:
            self.next_delay_seconds = 0.0
            return
        self.retry_count += 1
        if self.retry_count > _non_negative_finite_int(self.policy.max_retries):
            self.exhausted = True
            self.next_delay_seconds = 0.0
            return
        self.next_delay_seconds = self.policy.next_delay(self.retry_count)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy.to_public_dict(),
            "retry_count": safe_public_int(self.retry_count),
            "next_delay_seconds": safe_public_float(self.next_delay_seconds),
            "exhausted": safe_public_bool(self.exhausted),
            "last_reason": self.last_reason,
        }


def _safe_reason(value: Any) -> str:
    return safe_public_text(value, limit=120)


def _non_negative_finite_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else 0.0
    if not isinstance(value, str):
        return 0.0
    try:
        number = float(value.strip())
    except ValueError:
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _non_negative_finite_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value >= 0 else 0
    if not isinstance(value, str):
        return 0
    text = value.strip()
    if not text.isdigit():
        return 0
    return int(text)
