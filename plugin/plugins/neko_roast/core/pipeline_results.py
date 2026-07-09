"""Backward-compatible pipeline result helper facade."""

from __future__ import annotations

from .pipeline_dispatch_results import (
    dry_run_result,
    pushed_result,
    skip_dispatcher,
)
from .pipeline_failure_results import fail_dispatcher, fail_pipeline
from .pipeline_skip_results import (
    reject_missing_uid,
    skip_already_roasted,
    skip_before_event,
    skip_before_output,
    skip_permission,
)

__all__ = [
    "dry_run_result",
    "fail_dispatcher",
    "fail_pipeline",
    "pushed_result",
    "reject_missing_uid",
    "skip_already_roasted",
    "skip_before_event",
    "skip_before_output",
    "skip_dispatcher",
    "skip_permission",
]
