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


def _format_neko_community_phase2_context(candidate: dict[str, Any]) -> str:
    """Render the selected community card without discarding its prompt evidence."""

    lines = [
        "【喵宇宙社区候选资料】",
        f"标题：{str(candidate.get('title') or '').strip()}",
    ]
    if candidate.get("author"):
        lines.append(f"作者：{str(candidate['author']).strip()}")
    tags = candidate.get("tags")
    if isinstance(tags, list):
        tag_text = "、".join(str(tag).strip() for tag in tags if str(tag).strip())
    else:
        tag_text = str(tags or "").strip()
    if tag_text:
        lines.append(f"标签：{tag_text}")
    summary = str(candidate.get("description_hint") or "").strip()
    if summary:
        lines.append(f"正文摘要：{summary[:500]}")
    else:
        lines.append("正文摘要：无；不得根据标题臆造具体内容。")
    if candidate.get("published_at"):
        lines.append(f"发布时间：{str(candidate['published_at']).strip()}")
    lines.extend(
        [
            "表达约束：只基于上述资料自然搭话，不补充资料中不存在的情节。",
            "【喵宇宙社区候选资料结束】",
        ]
    )
    return "\n".join(lines)


async def prepare_selected_web_candidate(
    candidate: dict[str, Any],
    *,
    fallback_topic: str,
    language: str,
    is_preempted: Callable[[], bool] | None = None,
) -> tuple[dict[str, Any], str]:
    """Enrich and format a selected candidate through its platform adapter."""

    prepared = dict(candidate)
    if prepared.get("mode") == "community":
        return prepared, _format_neko_community_phase2_context(prepared)
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
