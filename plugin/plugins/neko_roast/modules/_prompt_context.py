"""Prompt helpers shared by live interaction modules."""

from __future__ import annotations

from typing import Any


SHORT_REPLY_CONTRACT = "Hard length limit: one sentence, no paragraph, at most 14 Chinese characters or 8 English words."
RECENT_CONTEXT_DEFAULT_LIMIT = 12
RECENT_CONTEXT_LINE_LIMIT = 56
VIEWER_CONTEXT_LINE_LIMIT = 44
NEKO_ALREADY_SAID_MARKER = " / NEKO already said: "
REPLY_PATH_MARKER = " / reply: "
SPENT_OUTPUT_FAMILY_MARKER = " / spent_output_family="


def _compact_context_line(value: Any, *, limit: int) -> str:
    text = str(value or "").strip().replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())
    if NEKO_ALREADY_SAID_MARKER in text:
        context, output = text.split(NEKO_ALREADY_SAID_MARKER, 1)
        output = _compact_plain(output, limit=max(16, min(44, limit)))
        context = _compact_preserving_reply_path(context, limit=max(16, limit - len(output) - 22))
        text = f"NEKO already said: {output}"
        if context:
            text += f" / context: {context}"
    if len(text) <= limit:
        return text
    return _compact_preserving_reply_path(text, limit=limit)


def _compact_preserving_reply_path(text: str, *, limit: int) -> str:
    if REPLY_PATH_MARKER not in text:
        return _compact_preserving_spent_output_family(text, limit=limit)
    context, reply = text.split(REPLY_PATH_MARKER, 1)
    reply_limit = max(16, min(36, limit - 14))
    reply = _compact_plain(reply, limit=reply_limit)
    context_limit = max(8, limit - len(reply) - len(REPLY_PATH_MARKER))
    context = _compact_preserving_spent_output_family(context, limit=context_limit)
    if context:
        return f"{context}{REPLY_PATH_MARKER}{reply}"
    return f"reply: {reply}"


def _compact_preserving_spent_output_family(text: str, *, limit: int) -> str:
    if SPENT_OUTPUT_FAMILY_MARKER not in text:
        return _compact_plain(text, limit=limit)
    context, family = text.split(SPENT_OUTPUT_FAMILY_MARKER, 1)
    family = _compact_plain(family, limit=max(12, min(32, limit - 14)))
    context_limit = max(8, limit - len(family) - len(SPENT_OUTPUT_FAMILY_MARKER))
    context = _compact_plain(context, limit=context_limit)
    if context:
        return f"{context}{SPENT_OUTPUT_FAMILY_MARKER}{family}"
    return f"spent_output_family={family}"


def _compact_plain(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def short_reply_rules(*, kind: str = "reply") -> list[str]:
    shared = [
        SHORT_REPLY_CONTRACT,
        "One breath only: no more than 20 Chinese chars or 10 English words when the idea still works.",
        "Prefer a compact live punchline over explanation, setup, or follow-up commentary.",
        "Do not turn a reply into a host script, segment intro, plan, or audience survey.",
        "Do not chain multiple clauses with commas; if the draft has a comma, cut one side.",
        "Avoid phrases like special plan, everyone look, next let's, what should we talk about, or tell me what you want.",
        "Avoid repeated presence checks like anyone here, still here, 有人吗, 还在吗, or 在不在; use a concrete tiny beat instead.",
    ]
    if kind == "host":
        return [
            *shared,
            "If the room is quiet, keep the line even smaller.",
            "One small host beat only; if asking, ask one concrete low-pressure question.",
            "If recent context was longer than this host beat, shrink the line instead of matching it.",
            "No explanation, no setup, no second sentence, no extra follow-up after the concrete hook.",
        ]
    return [
        *shared,
        "If the viewer's danmaku is short, answer even shorter.",
        "For one-word or very short danmaku, answer with a tiny reaction.",
        "If recent context was longer than the current danmaku, shrink the reply instead of matching it.",
        "No explanation, no setup, no second sentence, no follow-up question unless the current danmaku asks one.",
    ]


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
    lines = [_compact_context_line(line, limit=RECENT_CONTEXT_LINE_LIMIT) for line in raw_lines]
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
    lines = [_compact_context_line(line, limit=VIEWER_CONTEXT_LINE_LIMIT) for line in raw_lines]
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


def anti_repeat_rules(*, kind: str = "reply") -> list[str]:
    rules = [
        "Before writing, compare against NEKO's recent live-output memory.",
        "Do not reuse the same wording, opening, rhythm, punchline, or topic framing as the previous NEKO reply.",
        "Do not paraphrase the previous NEKO reply with different words.",
        "Do not revive an old reward bit, plan, game, audience prompt, or host beat unless the current event explicitly asks for it.",
        "If the natural draft sounds like the previous reply, change the angle and make it shorter.",
    ]
    if kind == "host":
        return [
            *rules,
            "Do not repeat the same host beat shape twice in a row; switch between observation, tiny tease, and concrete easy hook.",
        ]
    return rules
