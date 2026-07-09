"""Failure-window and auto-stop helpers for the live safety guard."""

from __future__ import annotations

import time
from typing import Any

from .safety_guard_types import FailureKind


def record_failure(guard: Any, kind: FailureKind, message: str) -> None:
    now = time.monotonic()
    bucket = guard._pipeline_failures if kind == "pipeline" else guard._output_failures
    bucket.append(now)
    trim_failure_bucket(guard, bucket, now)
    limit = (
        guard.config.safety_pipeline_failure_limit
        if kind == "pipeline"
        else guard.config.safety_output_failure_limit
    )
    guard.audit.record(
        f"safety_{kind}_failure",
        message,
        level="error",
        detail={"count": len(bucket), "limit": limit},
    )
    if guard.config.safety_auto_stop_enabled and len(bucket) >= limit:
        guard.auto_paused = True
        guard.audit.record(
            "safety_auto_stop",
            f"automatic stop after {len(bucket)} {kind} failures",
            level="error",
            detail={
                "kind": kind,
                "window_seconds": guard.config.safety_window_seconds,
            },
        )


def trim_failure_bucket(guard: Any, bucket: list[float], now: float) -> None:
    window = guard.config.safety_window_seconds
    bucket[:] = [item for item in bucket if now - item <= window]
