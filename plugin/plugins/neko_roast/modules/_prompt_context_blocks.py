"""Prompt context blocks shared by live interaction modules."""

from __future__ import annotations

from typing import Any

from ..core.meme_knowledge import render_meme_knowledge_block, retrieve_meme_knowledge
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
        "Used live material, for anti-repeat only:\n"
        + "\n".join(f"- {line}" for line in lines[:limit])
        + "\n\n"
        + "Anti-repeat rule: Treat every line above as already spent material.\n"
        + "Lines starting with 'NEKO already said' are previous broadcast outputs; never reuse, continue, or paraphrase those lines.\n"
        + "Do not continue, summarize, paraphrase, or remix those old lines.\n"
        + "Do not inherit their topic, rhythm, sentence length, reward bit, plan, or audience prompt.\n"
        + "If a recent line lists topic_family, host_beat_family, spent_output_family, fun_axis, shape, intent, or reply path, treat that material as spent and avoid using the same family or reply path again.\n"
        + "This block is a forbidden-material list, not context to continue and not a script prefix.\n"
        + "If a recent line and the current draft share the same subject, opening, or joke shape, choose a different angle or answer only the current danmaku.\n"
        + "Do not reuse the same opening, punchline shape, reward/present bit, plan, audience-suggestion beat, or host beat.\n"
        + "Current danmaku wins over recent context.\n"
        + "The current danmaku is always the primary target. Short danmaku should receive a short reply.\n"
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
        "Same viewer used material, for anti-repeat only:\n"
        + "\n".join(f"- {line}" for line in lines[:limit])
        + "\n\n"
        + "Viewer anti-repeat rule: This viewer has already heard the material above.\n"
        + "Lines starting with 'NEKO already said' are previous outputs to this viewer; never repeat or paraphrase them.\n"
        + "Use it only to avoid repeating old replies; do not summarize their history or expose internal memory.\n"
        + "Treat same-viewer history as spent material, not as a topic to resume by default.\n"
        + "If a line lists spent_output_family, treat that family as already used for this viewer.\n"
        + "Do not repeat this viewer's previous danmaku, old joke, or NEKO's previous answer to them.\n"
        + "Only continue an old thread if the current danmaku explicitly asks to continue that exact thread.\n"
        + "Do not repeat avatar, ID, or first-appearance comments for this viewer.\n"
        + "If the current danmaku changes topic, follow the current danmaku instead of forcing continuity.\n"
    )


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
        "Recent room danmaku context, for topic grouping:\n"
        + "\n".join(f"- {line}" for line in lines[:limit])
        + "\n\n"
        + "Room context rule: Use this only to understand the current room mood and avoid one-by-one tunnel vision.\n"
        + "The current danmaku remains the trigger and must be answered first.\n"
        + "If recent danmaku share a theme, bridge that theme in one compact reply instead of asking the same prompt again.\n"
        + "Filter low-value repeats silently; do not read or answer every tiny repeat.\n"
        + "Do not announce this summary, counts, labels, or stored context.\n"
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
