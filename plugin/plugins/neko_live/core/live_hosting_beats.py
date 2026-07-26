"""Backward-compatible idle-hosting beat facade."""

from __future__ import annotations

from .live_hosting_beat_picker import (
    choose_idle_hosting_beat_with_relaxed_guards,
    next_idle_hosting_beat,
)
from .live_hosting_beat_rules import (
    idle_hosting_beat_candidates,
    idle_hosting_material_stage,
    idle_hosting_preferred_stage,
    idle_hosting_stage_ordered_candidates,
    is_similar_idle_hosting_beat_title,
)
from .live_hosting_beat_state import (
    advance_idle_hosting_beat_index,
    record_chosen_idle_hosting_beat,
)

__all__ = [
    "advance_idle_hosting_beat_index",
    "choose_idle_hosting_beat_with_relaxed_guards",
    "idle_hosting_beat_candidates",
    "idle_hosting_material_stage",
    "idle_hosting_preferred_stage",
    "idle_hosting_stage_ordered_candidates",
    "is_similar_idle_hosting_beat_title",
    "next_idle_hosting_beat",
    "record_chosen_idle_hosting_beat",
]
