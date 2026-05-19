"""Three-layer memory pyramid for galgame plugin context organization.

Layer 0 — line buffer: the most recent raw dialogue lines (~50 entries).
Layer 1 — scene summaries: per-scene summaries plus key lines (~32 entries).
Layer 2 — story so far: a single ~200-token global summary maintained
incrementally by the ``summary`` tier LLM.

Storage and push are deliberately separated. This module exposes structured
data and lets the caller decide what to push to the catgirl.

Concurrency
-----------

Python ``asyncio`` is cooperative single-threaded so there are no true data
races. The only realistic hazard is **coroutine interleaving** — a writer that
``await``\\s mid-update can leak partial state to a concurrent reader. Every
mutator in this module prepares the new list/string locally and then assigns
it in a single atomic step; readers always observe a complete snapshot.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


DEFAULT_MAX_LINES: int = 50
DEFAULT_MAX_SCENES: int = 32


class StorySummarizer(Protocol):
    """Pluggable summarizer used by :meth:`LayeredMemory.update_story_so_far`.

    Implementations must run on the ``summary`` LLM tier per the NekoGuide
    convention. They receive the existing global story summary together with
    the latest scene summaries and return the new global summary.
    """

    async def summarize_story(
        self,
        *,
        current_story: str,
        new_scenes: list[str],
    ) -> str: ...


@dataclass(slots=True)
class SceneSummaryEntry:
    """A single Layer 1 entry. Immutable in spirit — mutators always replace."""

    scene_id: str
    summary: str
    key_lines: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    ts: float = 0.0
    push_seq: int = 0
    route_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "summary": self.summary,
            "key_lines": [dict(line) for line in self.key_lines],
            "ts": self.ts,
            "push_seq": self.push_seq,
            "route_id": self.route_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SceneSummaryEntry":
        def _safe_float(value: Any, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def _safe_int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        key_lines_raw = data.get("key_lines")
        key_lines: tuple[dict[str, Any], ...]
        if isinstance(key_lines_raw, list):
            key_lines = tuple(
                dict(line) for line in key_lines_raw if isinstance(line, dict)
            )
        else:
            key_lines = ()
        return cls(
            scene_id=str(data.get("scene_id") or ""),
            summary=str(data.get("summary") or ""),
            key_lines=key_lines,
            ts=_safe_float(data.get("ts") or 0.0),
            push_seq=_safe_int(data.get("push_seq") or 0),
            route_id=str(data.get("route_id") or ""),
        )


class LayeredMemory:
    """Three-layer memory pyramid (storage only — push is a separate concern)."""

    def __init__(
        self,
        *,
        max_lines: int = DEFAULT_MAX_LINES,
        max_scenes: int = DEFAULT_MAX_SCENES,
        logger: logging.Logger | None = None,
    ) -> None:
        if max_lines <= 0:
            raise ValueError("max_lines must be positive")
        if max_scenes <= 0:
            raise ValueError("max_scenes must be positive")
        self._max_lines = max_lines
        self._max_scenes = max_scenes
        self._logger = logger or logging.getLogger(__name__)

        # Atomic replacements only — never mutate in place.
        self._line_buffer: tuple[dict[str, Any], ...] = ()
        self._scene_summaries: tuple[SceneSummaryEntry, ...] = ()
        self._story_so_far: str = ""
        self._story_last_updated_seq: int = 0

    # ------------------------------------------------------------------
    # Layer 0 — raw lines
    # ------------------------------------------------------------------

    def append_line(self, line: dict[str, Any]) -> None:
        if not isinstance(line, dict):
            return
        snapshot = dict(line)
        new_buffer = self._line_buffer + (snapshot,)
        if len(new_buffer) > self._max_lines:
            new_buffer = new_buffer[-self._max_lines :]
        self._line_buffer = new_buffer

    def extend_lines(self, lines: list[dict[str, Any]]) -> None:
        cleaned = tuple(dict(line) for line in lines if isinstance(line, dict))
        if not cleaned:
            return
        new_buffer = self._line_buffer + cleaned
        if len(new_buffer) > self._max_lines:
            new_buffer = new_buffer[-self._max_lines :]
        self._line_buffer = new_buffer

    def get_recent_lines(self, n: int = 10) -> list[dict[str, Any]]:
        if n <= 0:
            return []
        n = min(n, self._max_lines)
        slice_ = self._line_buffer[-n:]
        return [dict(line) for line in slice_]

    def line_count(self) -> int:
        return len(self._line_buffer)

    # ------------------------------------------------------------------
    # Layer 1 — scene summaries
    # ------------------------------------------------------------------

    def add_scene_summary(
        self,
        scene_id: str,
        summary: str,
        key_lines: list[dict[str, Any]] | None = None,
        *,
        push_seq: int = 0,
        route_id: str = "",
        ts: float | None = None,
    ) -> None:
        normalized = (scene_id or "").strip()
        if not normalized:
            return
        entry = SceneSummaryEntry(
            scene_id=normalized,
            summary=(summary or "").strip(),
            key_lines=tuple(
                dict(line)
                for line in (key_lines or [])
                if isinstance(line, dict)
            ),
            ts=float(ts if ts is not None else time.time()),
            push_seq=int(push_seq),
            route_id=(route_id or "").strip(),
        )
        new_summaries = self._scene_summaries + (entry,)
        if len(new_summaries) > self._max_scenes:
            new_summaries = new_summaries[-self._max_scenes :]
        self._scene_summaries = new_summaries

    def get_scene_context(self, scene_id: str | None = None) -> dict[str, Any] | None:
        if not self._scene_summaries:
            return None
        if scene_id is None or not str(scene_id).strip():
            entry = self._scene_summaries[-1]
        else:
            target = str(scene_id).strip()
            matched = [
                item for item in self._scene_summaries if item.scene_id == target
            ]
            if not matched:
                return None
            entry = matched[-1]
        result = entry.to_dict()
        result["recent_lines"] = self._lines_in_scene(entry.scene_id)
        return result

    def get_recent_scene_summaries(self, n: int = 3) -> list[dict[str, Any]]:
        if n <= 0:
            return []
        n = min(n, self._max_scenes)
        return [entry.to_dict() for entry in self._scene_summaries[-n:]]

    def scene_count(self) -> int:
        return len(self._scene_summaries)

    # ------------------------------------------------------------------
    # Layer 2 — story so far
    # ------------------------------------------------------------------

    @property
    def story_last_updated_seq(self) -> int:
        return self._story_last_updated_seq

    def get_story_so_far(self) -> str:
        return self._story_so_far or "故事刚开始。"

    def has_story_so_far(self) -> bool:
        return bool(self._story_so_far)

    async def update_story_so_far(
        self,
        summarizer: StorySummarizer,
        *,
        recent_count: int = 3,
    ) -> bool:
        """Regenerate ``story_so_far`` from the most recent scene summaries.

        Returns ``True`` when the global summary was updated, ``False`` when
        there were no recent scenes to feed the summarizer or the summarizer
        returned an empty string. Failures from the summarizer are propagated
        so callers can decide whether to retry.
        """
        recent = self.get_recent_scene_summaries(recent_count)
        if not recent:
            return False
        new_scenes = [str(entry.get("summary") or "").strip() for entry in recent]
        new_scenes = [text for text in new_scenes if text]
        if not new_scenes:
            return False
        summary = await summarizer.summarize_story(
            current_story=self._story_so_far,
            new_scenes=new_scenes,
        )
        if not summary or not summary.strip():
            return False
        # Atomic swap — no await between read and write.
        self._story_so_far = summary.strip()
        self._story_last_updated_seq = int(recent[-1].get("push_seq") or 0)
        return True

    def set_story_so_far(self, text: str, *, push_seq: int = 0) -> None:
        """Direct setter used by store restore and tests."""
        self._story_so_far = (text or "").strip()
        self._story_last_updated_seq = max(0, int(push_seq))

    # ------------------------------------------------------------------
    # Snapshot / restore (for the plugin store)
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "line_buffer": [dict(line) for line in self._line_buffer],
            "scene_summaries": [entry.to_dict() for entry in self._scene_summaries],
            "story_so_far": self._story_so_far,
            "story_last_updated_seq": self._story_last_updated_seq,
        }

    def restore(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        lines = payload.get("line_buffer")
        if isinstance(lines, list):
            cleaned = tuple(dict(line) for line in lines if isinstance(line, dict))
            if len(cleaned) > self._max_lines:
                cleaned = cleaned[-self._max_lines :]
            self._line_buffer = cleaned
        scenes = payload.get("scene_summaries")
        if isinstance(scenes, list):
            entries = tuple(
                SceneSummaryEntry.from_dict(item)
                for item in scenes
                if isinstance(item, dict)
            )
            if len(entries) > self._max_scenes:
                entries = entries[-self._max_scenes :]
            self._scene_summaries = entries
        story = payload.get("story_so_far")
        if isinstance(story, str):
            self._story_so_far = story.strip()
        seq = payload.get("story_last_updated_seq")
        if isinstance(seq, int) and seq >= 0:
            self._story_last_updated_seq = seq

    def clear(self) -> None:
        self._line_buffer = ()
        self._scene_summaries = ()
        self._story_so_far = ""
        self._story_last_updated_seq = 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _lines_in_scene(self, scene_id: str) -> list[dict[str, Any]]:
        target = scene_id.strip()
        if not target:
            return []
        return [
            dict(line)
            for line in self._line_buffer
            if str(line.get("scene_id") or "").strip() == target
        ]


__all__ = [
    "LayeredMemory",
    "SceneSummaryEntry",
    "StorySummarizer",
    "DEFAULT_MAX_LINES",
    "DEFAULT_MAX_SCENES",
]
