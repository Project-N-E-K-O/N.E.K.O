"""Candidate picking for active engagement topics."""

from __future__ import annotations

from typing import Any

from . import active_topic_rules


def choose_candidate(
    selector: Any,
    candidates: list[dict[str, Any]],
    *,
    avoid_recent_fun_axis: bool,
    avoid_recent_family: bool,
    allow_similar_title: bool = False,
) -> dict[str, Any] | None:
    recent_spent_families = (
        selector._runtime._recent_spent_output_families()
        if avoid_recent_family
        else set()
    )
    for candidate in candidates:
        if not active_topic_rules._is_clean_live_material(candidate):
            if not selector._active_engagement_recent_topic_skip_reason:
                selector._active_engagement_recent_topic_skip_reason = (
                    "unclean_topic_material"
                )
            continue
        key = str(candidate.get("key") or candidate.get("title") or "").strip()
        if not key or key in selector._active_engagement_recent_topic_keys:
            continue
        title = str(candidate.get("title") or "").strip()
        if (
            not allow_similar_title
            and title
            and selector.is_similar_title(
                title, selector._active_engagement_recent_topic_titles
            )
        ):
            if not selector._active_engagement_recent_topic_skip_reason:
                selector._active_engagement_recent_topic_skip_reason = (
                    "similar_topic_title"
                )
            continue
        axis = str(candidate.get("fun_axis") or "").strip()
        if (
            avoid_recent_fun_axis
            and axis
            and axis in selector._active_engagement_recent_fun_axes
        ):
            continue
        reply_affordance = str(candidate.get("reply_affordance") or "").strip()
        if (
            avoid_recent_fun_axis
            and reply_affordance
            and reply_affordance in selector._active_engagement_recent_reply_affordances
        ):
            continue
        family = selector.host_material_family(candidate)
        if avoid_recent_family and family:
            if family in selector._recent_host_material_families:
                if not selector._active_engagement_recent_topic_skip_reason:
                    selector._active_engagement_recent_topic_skip_reason = (
                        "recent_host_family"
                    )
                continue
            if family in recent_spent_families:
                if not selector._active_engagement_recent_topic_skip_reason:
                    selector._active_engagement_recent_topic_skip_reason = (
                        "recent_spent_output_family"
                    )
                continue
        return candidate
    return None


def choose_fresh_candidate(
    selector: Any, candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    return (
        choose_candidate(
            selector,
            candidates,
            avoid_recent_fun_axis=True,
            avoid_recent_family=True,
        )
        or choose_candidate(
            selector,
            candidates,
            avoid_recent_fun_axis=False,
            avoid_recent_family=True,
        )
    )


def choose_fallback_candidate(
    selector: Any, candidates: list[dict[str, Any]], fallback: dict[str, Any]
) -> dict[str, Any]:
    chosen = (
        choose_fresh_candidate(selector, candidates)
        or choose_candidate(
            selector,
            candidates,
            avoid_recent_fun_axis=False,
            avoid_recent_family=True,
            allow_similar_title=True,
        )
        or choose_candidate(
            selector,
            candidates,
            avoid_recent_fun_axis=False,
            avoid_recent_family=False,
            allow_similar_title=True,
        )
        or fallback
    )
    if (
        getattr(selector, "_recent_host_material_families", None)
        and not selector._active_engagement_recent_topic_skip_reason
        and selector.host_material_family(chosen) not in selector._recent_host_material_families
    ):
        selector._active_engagement_recent_topic_skip_reason = "recent_host_family"
    return chosen


def clear_topic_cache(selector: Any) -> None:
    selector._active_engagement_topic_cache = []
    selector._active_engagement_topic_cache_at = 0.0
