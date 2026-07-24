"""Idle-hosting beat picker with freshness guards."""

from __future__ import annotations

from typing import Any

from . import live_hosting_beat_state


def next_idle_hosting_beat(runtime: Any) -> dict[str, Any]:
    candidates = runtime._idle_hosting_beat_candidates()
    if not candidates:
        return {}
    fallback = candidates[0]
    chosen = fallback
    chosen_offset: int | None = None
    preferred_stage = runtime._idle_hosting_preferred_stage()
    ordered_candidates = runtime._idle_hosting_stage_ordered_candidates(
        candidates,
        preferred_stage,
    )
    recent_spent_families = runtime._recent_spent_output_families()
    for offset, candidate in enumerate(ordered_candidates):
        if _candidate_passes_strict_guards(runtime, candidate, recent_spent_families):
            chosen = candidate
            chosen_offset = offset
            break
    else:
        chosen, chosen_offset = choose_idle_hosting_beat_with_relaxed_guards(
            runtime,
            ordered_candidates,
            fallback,
            recent_spent_families,
        )

    live_hosting_beat_state.advance_idle_hosting_beat_index(
        runtime, candidates, chosen_offset
    )
    return live_hosting_beat_state.record_chosen_idle_hosting_beat(
        runtime, chosen, fallback
    )


def _candidate_passes_strict_guards(
    runtime: Any,
    candidate: dict[str, Any],
    recent_spent_families: set[str],
) -> bool:
    key = str(candidate.get("key") or "").strip()
    axis = str(candidate.get("fun_axis") or "").strip()
    title = str(candidate.get("title") or "").strip()
    family = runtime._host_material_family(candidate)
    reply_affordance = str(candidate.get("reply_affordance") or "").strip()
    return (
        bool(key)
        and key not in runtime._idle_hosting_recent_beat_keys
        and bool(axis)
        and axis not in runtime._idle_hosting_recent_beat_axes
        and bool(family)
        and family not in runtime._recent_host_material_families
        and family not in recent_spent_families
        and (
            not reply_affordance
            or reply_affordance not in runtime._idle_hosting_recent_reply_affordances
        )
        and not runtime._is_similar_idle_hosting_beat_title(title)
    )


def choose_idle_hosting_beat_with_relaxed_guards(
    runtime: Any,
    ordered_candidates: list[dict[str, Any]],
    fallback: dict[str, Any],
    recent_spent_families: set[str],
) -> tuple[dict[str, Any], int | None]:
    for offset, candidate in enumerate(ordered_candidates):
        key = str(candidate.get("key") or "").strip()
        title = str(candidate.get("title") or "").strip()
        family = runtime._host_material_family(candidate)
        if (
            key
            and key not in runtime._idle_hosting_recent_beat_keys
            and family
            and family not in runtime._recent_host_material_families
            and family not in recent_spent_families
            and not runtime._is_similar_idle_hosting_beat_title(title)
        ):
            return candidate, offset
    for offset, candidate in enumerate(ordered_candidates):
        key = str(candidate.get("key") or "").strip()
        if key and key not in runtime._idle_hosting_recent_beat_keys:
            return candidate, offset
    return fallback, None
