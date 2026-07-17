"""Stateful active-engagement topic selection for solo stream hosting."""

from __future__ import annotations

from typing import Any

from . import active_topic_builder, active_topic_candidate_picker
from .active_topic_compat import ActiveTopicCompatibilityMixin


class ActiveTopicSelector(ActiveTopicCompatibilityMixin):
    """Selects and rotates active-engagement material for the runtime.

    The selector owns selection behavior while the runtime still owns the
    mutable deques/caches for backward-compatible tests and dashboard state.
    """

    def __init__(self, runtime: Any) -> None:
        object.__setattr__(self, "_runtime", runtime)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runtime, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_runtime":
            object.__setattr__(self, name, value)
            return
        setattr(self._runtime, name, value)

    async def select_topic(self) -> dict[str, Any]:
        candidates = await self.topic_candidates()
        fallback_candidates = self.runtime_fallback_topic_candidates()
        if not fallback_candidates:
            fallback_candidates = self.fallback_topic_candidates()
        fallback = fallback_candidates[0]
        shape = self.next_shape()
        chosen = fallback
        exhausted_cached_topics = not bool(candidates)
        candidate = active_topic_candidate_picker.choose_fresh_candidate(
            self, candidates
        )
        if candidate is not None:
            chosen = candidate
            exhausted_cached_topics = False
        elif candidates:
            exhausted_cached_topics = True
        if exhausted_cached_topics:
            active_topic_candidate_picker.clear_topic_cache(self)
            refreshed_candidates = await self.topic_candidates()
            candidate = active_topic_candidate_picker.choose_fresh_candidate(
                self, refreshed_candidates
            )
            if candidate is not None:
                chosen = candidate
                exhausted_cached_topics = False
        if exhausted_cached_topics or chosen is fallback:
            chosen = active_topic_candidate_picker.choose_fallback_candidate(
                self, fallback_candidates, fallback
            )
        return active_topic_builder.build_topic(self, chosen, fallback, shape)
