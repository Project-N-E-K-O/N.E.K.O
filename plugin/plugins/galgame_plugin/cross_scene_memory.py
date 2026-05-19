"""Cross-scene memory: character arcs + plot threads.

``cross_scene_memory`` is the **compressed semantic index** sitting on top of
``scene_memory`` (Layer 1). Where ``scene_memory`` stores *what happened*,
``cross_scene_memory`` stores *what it means across scenes* — the catgirl uses
it for push context, and ``GameLLMAgent`` uses it as strategy context.

Shape::

    {
        "characters": {
            "<name>": {
                "arc": str,                  # 弧光阶段
                "last_key_event": str,       # 最近关键事件
                "current_emotion": str,      # 当前情绪
                "confidence": float          # 0..1
            },
            ...
        },
        "plot_threads": [
            {
                "thread": str,               # 线程名（叢雨的感情萌芽）
                "status": str,               # 当前状态
                "key_scenes": list[str],     # 触发线程的场景 id
                "updated_at_seq": int,
                "confidence": float
            },
            ...
        ],
        "last_updated_seq": int,
        "low_confidence_streak": int,        # 连续 confidence<0.5 计数
    }

LLM updates are routed through the ``summary`` tier (per NekoGuide and the
host-play-mode plan). The summarizer must return JSON in the
:class:`MemoryUpdateResponse` shape; malformed responses are dropped and the
``low_confidence_streak`` counter is bumped so callers can decide when to do
a full rebuild from ``scene_memory[-5:]``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol


LOW_CONFIDENCE_THRESHOLD: float = 0.5
"""Confidence below which the update is considered noisy."""

REBUILD_AFTER_STREAK: int = 3
"""Consecutive low-confidence updates that trigger a full rebuild."""

DEFAULT_REBUILD_SCENE_COUNT: int = 5
"""Number of recent scenes to feed into a full rebuild prompt."""

DEFAULT_INCREMENTAL_SCENE_COUNT: int = 3
"""Number of recent scenes to feed into an incremental update."""


class MemoryUpdater(Protocol):
    """LLM-backed update callable (``summary`` tier).

    Implementations receive the current memory blob (or empty dict for a full
    rebuild) plus the recent scene summaries and must return a JSON-parseable
    string in the :class:`MemoryUpdateResponse` shape.
    """

    async def update_memory(
        self,
        *,
        current_memory: dict[str, Any],
        recent_scene_summaries: list[dict[str, Any]],
        full_rebuild: bool,
    ) -> str: ...


@dataclass(slots=True)
class MemoryUpdateResult:
    """Outcome of :func:`update_cross_scene_memory`."""

    memory: dict[str, Any]
    updated: bool
    confidence: float = 0.0
    triggered_rebuild: bool = False
    skipped_reason: str = ""
    parse_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory": self.memory,
            "updated": self.updated,
            "confidence": self.confidence,
            "triggered_rebuild": self.triggered_rebuild,
            "skipped_reason": self.skipped_reason,
            "parse_error": self.parse_error,
        }


# ---------------------------------------------------------------------------
# Default empty memory + sanitisation
# ---------------------------------------------------------------------------


def empty_memory() -> dict[str, Any]:
    return {
        "characters": {},
        "plot_threads": [],
        "last_updated_seq": 0,
        "low_confidence_streak": 0,
    }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def sanitize_memory(memory: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce ``memory`` into the canonical shape; missing fields default."""
    base = empty_memory()
    if not isinstance(memory, dict):
        return base
    characters = memory.get("characters")
    if isinstance(characters, dict):
        base["characters"] = {
            str(name): _sanitize_character_entry(entry)
            for name, entry in characters.items()
            if isinstance(entry, dict)
        }
    plot_threads = memory.get("plot_threads")
    if isinstance(plot_threads, list):
        base["plot_threads"] = [
            _sanitize_thread_entry(entry)
            for entry in plot_threads
            if isinstance(entry, dict)
        ]
    base["last_updated_seq"] = _safe_int(memory.get("last_updated_seq") or 0)
    base["low_confidence_streak"] = max(
        0, _safe_int(memory.get("low_confidence_streak") or 0)
    )
    return base


def _sanitize_character_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "arc": str(entry.get("arc") or "").strip(),
        "last_key_event": str(entry.get("last_key_event") or "").strip(),
        "current_emotion": str(entry.get("current_emotion") or "").strip(),
        "confidence": _clamp_confidence(entry.get("confidence")),
    }


def _sanitize_thread_entry(entry: dict[str, Any]) -> dict[str, Any]:
    key_scenes_raw = entry.get("key_scenes")
    if isinstance(key_scenes_raw, list):
        key_scenes = [
            str(scene_id).strip()
            for scene_id in key_scenes_raw
            if str(scene_id or "").strip()
        ]
    else:
        key_scenes = []
    return {
        "thread": str(entry.get("thread") or "").strip(),
        "status": str(entry.get("status") or "").strip(),
        "key_scenes": key_scenes,
        "updated_at_seq": _safe_int(entry.get("updated_at_seq") or 0),
        "confidence": _clamp_confidence(entry.get("confidence")),
    }


def _clamp_confidence(value: Any) -> float:
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value_f))


# ---------------------------------------------------------------------------
# Update pipeline
# ---------------------------------------------------------------------------


async def update_cross_scene_memory(
    *,
    current_memory: dict[str, Any] | None,
    scene_memory: list[dict[str, Any]],
    updater: MemoryUpdater,
    push_seq: int = 0,
    logger: logging.Logger | None = None,
    rebuild_scene_count: int = DEFAULT_REBUILD_SCENE_COUNT,
    incremental_scene_count: int = DEFAULT_INCREMENTAL_SCENE_COUNT,
) -> MemoryUpdateResult:
    """Run an incremental or full update against ``current_memory``.

    The function picks the recent scene summary window (``incremental_scene_count``
    by default; ``rebuild_scene_count`` when the low-confidence streak has
    reached :data:`REBUILD_AFTER_STREAK`) and dispatches it to ``updater``.
    Malformed responses leave the memory untouched but bump the streak so the
    next call can escalate to a full rebuild.
    """

    log = logger or logging.getLogger(__name__)
    base_memory = sanitize_memory(current_memory)

    if not scene_memory:
        return MemoryUpdateResult(
            memory=base_memory,
            updated=False,
            skipped_reason="no_scene_memory",
        )

    streak = int(base_memory.get("low_confidence_streak") or 0)
    full_rebuild = streak >= REBUILD_AFTER_STREAK
    window_size = (
        max(1, int(rebuild_scene_count))
        if full_rebuild
        else max(1, int(incremental_scene_count))
    )
    window = [dict(scene) for scene in scene_memory[-window_size:]]
    if not window:
        return MemoryUpdateResult(
            memory=base_memory,
            updated=False,
            skipped_reason="empty_window",
        )

    try:
        raw_response = await updater.update_memory(
            current_memory={} if full_rebuild else dict(base_memory),
            recent_scene_summaries=window,
            full_rebuild=full_rebuild,
        )
    except Exception as exc:  # noqa: BLE001 — LLM call failures are non-fatal
        log.warning(
            "cross_scene_memory updater raised; keeping previous memory: %s", exc
        )
        return MemoryUpdateResult(
            memory=_bump_streak(base_memory),
            updated=False,
            skipped_reason="updater_exception",
            parse_error=str(exc),
        )

    parsed = parse_memory_update_response(raw_response)
    if parsed is None:
        log.info(
            "cross_scene_memory update returned unparseable JSON; bumping streak"
        )
        return MemoryUpdateResult(
            memory=_bump_streak(base_memory),
            updated=False,
            skipped_reason="parse_error",
            parse_error=(raw_response or "")[:200],
        )

    confidence = _aggregate_confidence(parsed)
    new_memory = sanitize_memory(parsed)
    new_memory["last_updated_seq"] = max(int(push_seq or 0), int(base_memory.get("last_updated_seq") or 0))
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        new_memory["low_confidence_streak"] = streak + 1
    else:
        new_memory["low_confidence_streak"] = 0
    return MemoryUpdateResult(
        memory=new_memory,
        updated=True,
        confidence=confidence,
        triggered_rebuild=full_rebuild,
    )


def parse_memory_update_response(raw: str) -> dict[str, Any] | None:
    """Parse the LLM's JSON output; tolerate fenced code blocks."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        # Strip a single fenced block (```json ... ``` or ``` ... ```)
        first_newline = text.find("\n")
        if first_newline > 0:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _aggregate_confidence(parsed: dict[str, Any]) -> float:
    confidences: list[float] = []
    characters = parsed.get("characters")
    if isinstance(characters, dict):
        for entry in characters.values():
            if isinstance(entry, dict):
                confidences.append(_clamp_confidence(entry.get("confidence")))
    threads = parsed.get("plot_threads")
    if isinstance(threads, list):
        for entry in threads:
            if isinstance(entry, dict):
                confidences.append(_clamp_confidence(entry.get("confidence")))
    top_level = parsed.get("confidence")
    if isinstance(top_level, (int, float)):
        confidences.append(_clamp_confidence(top_level))
    if not confidences:
        return 0.0
    return sum(confidences) / len(confidences)


def _bump_streak(memory: dict[str, Any]) -> dict[str, Any]:
    bumped = dict(memory)
    bumped["low_confidence_streak"] = int(memory.get("low_confidence_streak") or 0) + 1
    return bumped


# ---------------------------------------------------------------------------
# Push-context rendering
# ---------------------------------------------------------------------------


def render_for_push(
    memory: dict[str, Any] | None,
    *,
    max_chars: int = 240,
) -> str:
    """Render a compact textual block of the cross-scene memory for pushes.

    Returns an empty string when the memory has no meaningful content.
    """
    base = sanitize_memory(memory)
    parts: list[str] = []
    for name, entry in base["characters"].items():
        arc = entry.get("arc") or ""
        emotion = entry.get("current_emotion") or ""
        if arc or emotion:
            label_bits: list[str] = []
            if arc:
                label_bits.append(f"弧光：{arc}")
            if emotion:
                label_bits.append(f"情绪：{emotion}")
            parts.append(f"{name}（{'，'.join(label_bits)}）")
    for thread in base["plot_threads"][:3]:
        text = thread.get("thread") or ""
        status = thread.get("status") or ""
        if text and status:
            parts.append(f"{text} → {status}")
        elif text:
            parts.append(text)
    if not parts:
        return ""
    if max_chars <= 0:
        return ""
    rendered = "；".join(parts)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max_chars - 1] + "…"


__all__ = [
    "MemoryUpdater",
    "MemoryUpdateResult",
    "empty_memory",
    "sanitize_memory",
    "update_cross_scene_memory",
    "parse_memory_update_response",
    "render_for_push",
    "LOW_CONFIDENCE_THRESHOLD",
    "REBUILD_AFTER_STREAK",
    "DEFAULT_REBUILD_SCENE_COUNT",
    "DEFAULT_INCREMENTAL_SCENE_COUNT",
]
