"""Idle-hosting beat material rules and stage helpers."""

from __future__ import annotations

from typing import Any

from .live_material_rules import (
    is_clean_live_material,
    is_similar_live_material_title,
)


def idle_hosting_beat_candidates() -> list[dict[str, Any]]:
    raw_candidates = _raw_idle_hosting_beat_candidates()
    candidates = [
        candidate
        for candidate in raw_candidates
        if is_clean_live_material(candidate)
    ]
    return candidates


def _raw_idle_hosting_beat_candidates() -> list[dict[str, Any]]:
    try:
        from .live_content import idle_hosting_beat_candidates
    except ModuleNotFoundError as exc:
        if exc.name != "plugin.plugins.neko_roast.core.live_content":
            raise
        return []
    return idle_hosting_beat_candidates()


def idle_hosting_preferred_stage(recent_idle_hosting_streak: int) -> str:
    if recent_idle_hosting_streak <= 0:
        return "settle"
    if recent_idle_hosting_streak == 1:
        return "column"
    return "callback"


def idle_hosting_stage_ordered_candidates(
    candidates: list[dict[str, Any]],
    *,
    preferred_stage: str,
    start_index: int,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    rotated = [
        candidates[(start_index + offset) % len(candidates)]
        for offset in range(len(candidates))
    ]
    preferred = [
        candidate
        for candidate in rotated
        if idle_hosting_material_stage(candidate) == preferred_stage
    ]
    rest = [candidate for candidate in rotated if candidate not in preferred]
    return [*preferred, *rest]


def idle_hosting_material_stage(material: dict[str, Any] | None) -> str:
    if not isinstance(material, dict):
        return "settle"
    explicit = str(material.get("idle_stage") or "").strip()
    if explicit:
        return explicit
    shape = str(material.get("shape") or "").strip()
    axis = str(material.get("fun_axis") or "").strip()
    if shape in {"one_word_call", "micro_challenge"} or axis in {
        "viewer_callback",
        "micro_challenge",
    }:
        return "callback"
    if shape in {"tiny_choice", "light_tease"} or axis in {"choice", "tease"}:
        return "column"
    return "settle"


def is_similar_idle_hosting_beat_title(title: str, recent_titles: Any) -> bool:
    return bool(title) and is_similar_live_material_title(
        title,
        recent_titles,
    )
