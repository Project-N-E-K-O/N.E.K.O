"""Compatibility hook-answer detector for the pipeline split.

Later hosting-flow slices replace this stub with the full active-engagement
answer detector. The split slice only needs the import boundary to exist.
"""

from __future__ import annotations

from typing import Any

from .contracts import ViewerEvent


def is_active_hook_answer_event(recent_results: Any, event: ViewerEvent) -> bool:
    _ = recent_results, event
    return False
