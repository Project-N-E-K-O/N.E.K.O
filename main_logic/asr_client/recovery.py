"""Conservative failure policy and finite replacement budget for independent ASR.

This module never decides to replay audio. Recovery replaces a failed transport
for future input; a failed or uncertain old write must be settled separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class FailureSource(str, Enum):
    RUNTIME = "runtime"
    PROVIDER = "provider"
    CONNECT = "connect"


class RecoveryDisposition(str, Enum):
    RECOVER = "recover"
    RETRY_CONNECT = "retry_connect"
    STOP = "stop"


def classify_failure(code: str, *, source: FailureSource) -> RecoveryDisposition:
    """Match trusted codes and their origin, never human-readable error text.

    In particular a generic worker error or connection-closed code can describe
    an uncertain write. Neither is evidence of a recoverable read disconnect.
    """
    if (source, code) in {
        (FailureSource.RUNTIME, "ASR_PROVIDER_FINAL_TIMEOUT"),
        (FailureSource.PROVIDER, "ASR_PROVIDER_FINAL_TIMEOUT"),
        (FailureSource.PROVIDER, "ASR_QWEN_READ_DISCONNECTED"),
    }:
        return RecoveryDisposition.RECOVER
    if (source, code) == (FailureSource.CONNECT, "ASR_QWEN_CONNECTION_FAILED"):
        return RecoveryDisposition.RETRY_CONNECT
    return RecoveryDisposition.STOP


@dataclass(frozen=True, slots=True)
class FailureDecision:
    """Keep fault ownership separate from old-input delivery diagnostics."""

    cause_code: str
    failure_source: FailureSource
    delivery_risk: str | None
    recovery_disposition: RecoveryDisposition
    notification_code: str


def decide_failure(
    cause_code: str, *, source: FailureSource, delivery_risk: str | None = None,
) -> FailureDecision:
    """Classify the original cause; a protected prefix is not a failure cause.

    Legacy generic delivery paths still need their existing failure notice.
    Explicit provider, detector, ordering, delivery and recovery-terminal codes
    retain their meaning. This decision grants no permission to replay audio.
    """
    disposition = classify_failure(cause_code, source=source)
    notification_code = cause_code
    if delivery_risk is not None and cause_code in {
        "ASR_INDEPENDENT_FAILED",
        "ASR_INDEPENDENT_STREAM_FAILED",
        "ASR_STREAM_BACKPRESSURE",
        "ASR_QWEN_CONNECTION_CLOSED",
        "ASR_QWEN_WORKER_FAILED",
        "ASR_STEP_CONNECTION_CLOSED",
        "ASR_STEP_WORKER_FAILED",
        "ASR_OPENAI_WORKER_FAILED",
        "ASR_SONIOX_PROTECTED_REPLAY_DISABLED",
        "ASR_SONIOX_REPLAY_INCOMPLETE",
    }:
        notification_code = delivery_risk
    return FailureDecision(
        cause_code, source, delivery_risk, disposition, notification_code,
    )


@dataclass(slots=True)
class RecoveryBudget:
    """Replacement attempts survive handshakes and successive failed sessions.

    Call ``begin`` once at the owned recovery boundary, before old cleanup, and
    claim an attempt before creating each replacement connection. A successful
    handshake deliberately has no reset API. Only a useful finalized turn which
    the caller has actually accepted, or a new user session (a fresh instance),
    may reset the consecutive failure allowance.

    Times are monotonic seconds supplied by the caller, allowing the same
    absolute deadline to cover old cleanup, backoff, and connection startup.
    """

    max_attempts: int = 2
    total_seconds: float = 12.0
    attempts_used: int = 0
    deadline: float | None = None

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if not math.isfinite(self.total_seconds) or self.total_seconds <= 0:
            raise ValueError("total_seconds must be finite and positive")
        if self.attempts_used < 0 or self.attempts_used > self.max_attempts:
            raise ValueError("attempts_used is outside the replacement budget")
        if self.deadline is not None and not math.isfinite(self.deadline):
            raise ValueError("deadline must be finite")

    def begin(self, now: float) -> float:
        """Start one owned fault operation without replenishing its attempts."""
        self._validate_time(now)
        self.deadline = now + self.total_seconds
        return self.deadline

    def remaining_seconds(self, now: float) -> float:
        self._validate_time(now)
        if self.deadline is None:
            return 0.0
        return max(0.0, self.deadline - now)

    def claim_attempt(self, now: float) -> bool:
        if self.remaining_seconds(now) <= 0 or self.attempts_used >= self.max_attempts:
            return False
        self.attempts_used += 1
        return True

    def mark_completed_turn(self) -> None:
        """Caller must fence and accept a useful final before calling this."""
        self.attempts_used = 0
        self.deadline = None

    @staticmethod
    def _validate_time(now: float) -> None:
        if not math.isfinite(now):
            raise ValueError("now must be finite monotonic seconds")
