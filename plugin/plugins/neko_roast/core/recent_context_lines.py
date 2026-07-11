"""Line renderers for recent live-context memory."""

from __future__ import annotations

from typing import Any

from .recent_context_text import compact_context_text


def idle_hosting_context_line(route: str, event: dict[str, Any]) -> str:
    beat_shape = str(event.get("host_beat_shape") or "").strip()
    beat_family = str(event.get("host_beat_family") or "").strip()
    beat_axis = str(event.get("host_beat_fun_axis") or "").strip()
    beat_column = str(event.get("host_beat_live_column") or "").strip()
    beat_stage = str(event.get("host_beat_idle_stage") or "").strip()
    beat_title = str(event.get("host_beat_title") or "").strip()
    beat_reply = str(event.get("host_beat_reply_affordance") or "").strip()
    beat_bits = " ".join(
        bit
        for bit in (beat_stage, beat_column, beat_shape, beat_family, beat_axis)
        if bit
    )
    if beat_title:
        beat_bits = (
            f"{beat_bits} - {compact_context_text(beat_title, limit=50)}".strip()
        )
    if beat_reply:
        beat_bits = (
            f"{beat_bits} / reply: {compact_context_text(beat_reply, limit=60)}".strip()
        )
    return f"{route} / idle_hosting: {beat_bits or 'solo quiet-room host beat'}"


def active_engagement_context_line(route: str, event: dict[str, Any]) -> str:
    topic_source = str(event.get("topic_source") or "").strip()
    topic_shape = str(event.get("topic_shape") or "").strip()
    topic_intent = str(event.get("topic_intent") or "").strip()
    topic_family = str(event.get("topic_family") or "").strip()
    topic_axis = str(event.get("topic_fun_axis") or "").strip()
    topic_column = str(event.get("topic_live_column") or "").strip()
    topic_pack = str(event.get("topic_pack") or "").strip()
    topic_title = str(event.get("topic_title") or "").strip()
    topic_bits = " ".join(
        bit
        for bit in (
            topic_pack,
            topic_column,
            topic_source,
            topic_shape,
            topic_intent,
            topic_family,
            topic_axis,
        )
        if bit
    )
    if topic_title:
        topic_bits = (
            f"{topic_bits} - {compact_context_text(topic_title, limit=50)}".strip()
        )
    topic_reply = str(event.get("topic_reply_affordance") or "").strip()
    if topic_reply:
        topic_bits = f"{topic_bits} / reply: {compact_context_text(topic_reply, limit=60)}".strip()
    return f"{route} / active_engagement: {topic_bits or 'solo engagement beat'}"


def viewer_event_context_line(
    route: str, source: str, event: dict[str, Any], result: dict[str, Any]
) -> str:
    identity = (
        result.get("identity") if isinstance(result.get("identity"), dict) else {}
    )
    who = str(
        identity.get("nickname")
        or event.get("nickname")
        or event.get("uid")
        or "viewer"
    )
    text = str(event.get("danmaku_text") or "").strip()
    line = f"{route} / {source} from {who}"
    if text:
        line += f": {compact_context_text(text)}"
    return line
