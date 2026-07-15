"""Idle-hosting beat rotation state updates."""

from __future__ import annotations

from typing import Any

from . import live_hosting_beat_rules


def advance_idle_hosting_beat_index(
    runtime: Any,
    candidates: list[dict[str, Any]],
    chosen_offset: int | None,
) -> None:
    if chosen_offset is None:
        runtime._idle_hosting_beat_index = (runtime._idle_hosting_beat_index + 1) % len(
            candidates
        )
        return
    runtime._idle_hosting_beat_index = (
        runtime._idle_hosting_beat_index + chosen_offset + 1
    ) % len(candidates)


def record_chosen_idle_hosting_beat(
    runtime: Any,
    chosen: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    key = str(chosen.get("key") or fallback.get("key") or "").strip()
    if not key:
        return {}
    axis = str(chosen.get("fun_axis") or "").strip()
    title = str(chosen.get("title") or "").strip()
    family = runtime._host_material_family(chosen)
    reply_affordance = str(chosen.get("reply_affordance") or "").strip()
    runtime._idle_hosting_recent_beat_keys.append(key)
    if axis:
        runtime._idle_hosting_recent_beat_axes.append(axis)
    if title:
        runtime._idle_hosting_recent_beat_titles.append(title)
    if family:
        runtime._recent_host_material_families.append(family)
    if reply_affordance:
        runtime._idle_hosting_recent_reply_affordances.append(reply_affordance)
    payload = dict(chosen)
    if family:
        payload["family"] = family
    payload["idle_stage"] = live_hosting_beat_rules.idle_hosting_material_stage(payload)
    return payload
