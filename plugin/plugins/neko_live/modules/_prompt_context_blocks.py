"""Prompt context blocks shared by live interaction modules."""

from __future__ import annotations

from typing import Any

from ..core.meme_knowledge import render_meme_knowledge_block, retrieve_meme_knowledge
from ..core.viewer_preferences import viewer_preference_prompt_block
from ._prompt_context_compaction import compact_context_line


RECENT_CONTEXT_DEFAULT_LIMIT = 12
RECENT_CONTEXT_LINE_LIMIT = 56
VIEWER_CONTEXT_LINE_LIMIT = 44
ROOM_CONTEXT_DEFAULT_LIMIT = 6
ROOM_CONTEXT_LINE_LIMIT = 96


def recent_context_block(ctx: Any, *, limit: int = RECENT_CONTEXT_DEFAULT_LIMIT) -> str:
    provider = getattr(ctx, "recent_interaction_context", None)
    if not callable(provider):
        return ""
    try:
        raw_lines = provider(limit=limit)
    except TypeError:
        raw_lines = provider()
    except Exception:
        return ""
    if not isinstance(raw_lines, list):
        return ""
    lines = [compact_context_line(line, limit=RECENT_CONTEXT_LINE_LIMIT) for line in raw_lines]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    return (
        "Recent spent live material:\n"
        + "\n".join(f"- {line}" for line in lines[:limit])
        + "\n\n"
        + "Rule: this is a spent-material block, not dialogue to continue. Never reuse or paraphrase prior NEKO output, wording, rhythm, joke, topic family, reply path, plan, or host beat.\n"
        + "The current input always wins. Continue a pending thread only when it explicitly connects; otherwise choose a fresh angle, and keep short input short.\n"
    )


def viewer_session_context_block(ctx: Any, uid: str, *, limit: int = 2) -> str:
    provider = getattr(ctx, "viewer_session_context", None)
    if not callable(provider):
        return ""
    try:
        raw_lines = provider(uid, limit=limit)
    except TypeError:
        raw_lines = provider(uid)
    except Exception:
        return ""
    if not isinstance(raw_lines, list):
        return ""
    lines = [compact_context_line(line, limit=VIEWER_CONTEXT_LINE_LIMIT) for line in raw_lines]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    return (
        "Same-viewer spent material:\n"
        + "\n".join(f"- {line}" for line in lines[:limit])
        + "\n\n"
        + "Rule: use only to avoid repeating this viewer's prior danmaku, NEKO reply, joke, spent family, avatar/ID, or first-appearance material.\n"
        + "Resume only an explicitly continued thread; otherwise follow the current input without exposing memory.\n"
    )


def viewer_preference_context_block(ctx: Any, profile: Any) -> str:
    """Render durable personalization only when the streamer enabled it."""

    config = getattr(ctx, "config", None)
    if getattr(config, "viewer_memory_enabled", True) is False:
        return ""
    return viewer_preference_prompt_block(profile)


def room_danmaku_context_block(
    ctx: Any,
    event: Any,
    *,
    limit: int = ROOM_CONTEXT_DEFAULT_LIMIT,
) -> str:
    provider = getattr(ctx, "recent_room_danmaku_context", None)
    if not callable(provider):
        return ""
    try:
        raw_lines = provider(event, limit=limit)
    except TypeError:
        raw_lines = provider(event)
    except Exception:
        return ""
    if not isinstance(raw_lines, list):
        return ""
    lines = [compact_context_line(line, limit=ROOM_CONTEXT_LINE_LIMIT) for line in raw_lines]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    return (
        "Recent room theme context:\n"
        + "\n".join(f"- {line}" for line in lines[:limit])
        + "\n\n"
        + "Rule: answer the current danmaku first; use a shared theme only as one compact bridge. Silently ignore low-value repeats and never announce stored context or counts.\n"
    )


def live_events_context_block(ctx: Any, event: Any) -> str:
    live_events = getattr(ctx, "live_events", None) if ctx is not None else None
    provider = getattr(live_events, "prompt_block_for_event", None)
    if not callable(provider):
        return ""
    try:
        return str(provider(event) or "")
    except Exception:
        return ""


def meme_knowledge_context_block(*parts: str, limit: int = 2) -> str:
    try:
        entries = retrieve_meme_knowledge(*parts, limit=limit)
    except Exception:
        return ""
    return render_meme_knowledge_block(entries)
