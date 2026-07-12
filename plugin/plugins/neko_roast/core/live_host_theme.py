"""Stable stream-theme prompt anchor for NEKO Live."""

from __future__ import annotations

from typing import Any


from .contracts_public import public_text


def live_host_theme_block(config: Any | None = None, *, kind: str = "reply") -> str:
    """Render a private style anchor that keeps live beats from feeling random."""

    source = config
    config = getattr(source, "config", source)
    room_context = getattr(source, "live_room_context", {})
    if not isinstance(room_context, dict):
        room_context = {}
    stream_theme = _optional_text(getattr(config, "stream_theme", ""), max_len=120)
    live_mode = _optional_text(getattr(config, "live_mode", ""), max_len=40)
    room_title = _optional_text(room_context.get("title"), max_len=120)
    anchor_name = _optional_text(room_context.get("anchor_name"), max_len=80)
    live_status = _prompt_live_status(room_context.get("live_status"))
    stream_goal = _optional_text(getattr(config, "stream_goal", ""), max_len=160)
    stream_columns = _optional_text(getattr(config, "stream_columns", ""), max_len=160)
    stream_avoid_topics = _optional_text(getattr(config, "stream_avoid_topics", ""), max_len=160)

    lines = [
        "Current stream theme (private style anchor):",
        "- continuity_rule: use this only as light flavor; never announce the theme name or explain the format.",
    ]
    if stream_theme:
        lines.extend(
            [
                f"- human_theme: {stream_theme}",
                "- premise: this is today's configured stream anchor; keep replies and idle beats connected to it when relevant.",
                "- variety_rule: use the theme as continuity, not a slogan; do not force every line to repeat it.",
            ]
        )
    elif room_title:
        lines.extend(
            [
                f"- live_room_title_theme: {room_title}",
                "- premise: use the current live-room title as today's stream anchor when it is relevant to the danmaku or hosting beat.",
                "- variety_rule: treat the title as context, not a slogan; do not force every line to repeat it.",
            ]
        )
    else:
        lines.extend(
            [
                "- theme_name: NEKO tiny radio patrol",
                "- premise: NEKO is a small cat host keeping a tiny room-radio desk alive while watching the room.",
                "- recurring_motifs: tiny radio, desk patrol, paw stamp, room weather, snack inventory, password card.",
                "- variety_rule: rotate motifs; do not force every line to mention radio, desk, paw, weather, or snacks.",
            ]
        )
    if anchor_name:
        lines.append(f"- live_room_anchor_name: {anchor_name}")
    if live_status:
        lines.append(f"- live_room_status: {live_status}")
    if stream_goal:
        lines.append(f"- stream_goal: {stream_goal}")
    if stream_columns:
        lines.append(f"- preferred_columns_or_style: {stream_columns}")
    if stream_avoid_topics:
        lines.append(f"- avoid_topics_or_bits: {stream_avoid_topics}")
    if kind == "host":
        lines.extend(
            [
                "- host_rule: make idle/warmup/active beats feel like beads from the same tiny show, not unrelated random prompts.",
                "- host_hook_rule: if asking, use one natural non-numeric danmaku cue; one concrete reply handle is enough, then leave space for viewers.",
            ]
        )
        if live_mode == "solo_stream":
            lines.append(
                "- solo_stream_rule: NEKO is the on-stage host; do not address an unseen human host/operator or ask them to provide content."
            )
    else:
        lines.extend(
            [
                "- reply_rule: answer the current viewer first; theme flavor may only be a small callback after the answer is clear.",
                "- no_drift_rule: do not ignore the danmaku just to continue the theme.",
            ]
        )
    return "\n".join(lines) + "\n\n"


def _optional_text(value: Any, *, max_len: int) -> str:
    if not isinstance(value, str):
        return ""
    return public_text(value.strip(), max_len=max_len)


def _prompt_live_status(value: Any) -> str:
    text = _optional_text(value, max_len=40).casefold()
    if text in {"offline", "ended", "finish", "finished", "unknown"}:
        return ""
    return text
