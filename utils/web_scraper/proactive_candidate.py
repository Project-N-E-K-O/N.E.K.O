# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Platform adapter for preparing one selected proactive web candidate."""

from __future__ import annotations

from typing import Any, Callable

from .bilibili_content import (
    BilibiliEnrichmentPreempted,
    enrich_bilibili_video,
    format_bilibili_phase2_context,
)


class SelectedWebCandidatePreempted(Exception):
    """Raised when user activity supersedes selected-candidate preparation."""


async def prepare_selected_web_candidate(
    candidate: dict[str, Any],
    *,
    fallback_topic: str,
    language: str,
    is_preempted: Callable[[], bool] | None = None,
) -> tuple[dict[str, Any], str]:
    """Enrich and format a selected candidate through its platform adapter."""

    prepared = dict(candidate)
    if prepared.get("platform") != "bilibili":
        return prepared, fallback_topic

    if prepared.get("kind") == "video":
        try:
            prepared = await enrich_bilibili_video(
                prepared,
                language=language,
                is_preempted=is_preempted,
            )
        except BilibiliEnrichmentPreempted as exc:
            raise SelectedWebCandidatePreempted from exc
    return prepared, format_bilibili_phase2_context(prepared)
