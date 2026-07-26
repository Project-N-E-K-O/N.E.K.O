"""Small TwitchIO Helix projection for channel/live status."""

from __future__ import annotations

from typing import Any

from ...core.contracts import LiveRoomStatus
from .room_ref import parse_twitch_room_ref


async def lookup_channel_status(client: Any, room_ref: Any, *, token_for: str) -> LiveRoomStatus:
    parsed = parse_twitch_room_ref(room_ref)
    if not parsed.ok:
        return LiveRoomStatus(room_id=0, ok=False, message=parsed.message)
    if not isinstance(token_for, str) or not token_for.strip():
        return LiveRoomStatus(room_id=0, ok=False, message="twitch authorization is required")
    try:
        users = await client.fetch_users(logins=[parsed.room_ref], token_for=token_for.strip())
        user = users[0] if isinstance(users, list) and users else None
        user_id = _numeric_id(getattr(user, "id", None))
        if user is None or not user_id:
            return LiveRoomStatus(
                room_id=0,
                ok=False,
                live_status="unknown",
                message="twitch channel was not found",
            )
        streams = client.fetch_streams(
            user_ids=[user_id],
            type="live",
            token_for=token_for.strip(),
            max_results=1,
        )
        stream = None
        async for candidate in streams:
            stream = candidate
            break
    except Exception as exc:
        return LiveRoomStatus(
            room_id=0,
            ok=False,
            live_status="unknown",
            message=f"twitch channel lookup failed: {type(exc).__name__}",
        )
    display_name = _text(getattr(user, "display_name", None), 80) or _text(getattr(user, "name", None), 25)
    if stream is None:
        return LiveRoomStatus(
            room_id=int(user_id),
            ok=True,
            anchor_name=display_name,
            live_status="offline",
        )
    return LiveRoomStatus(
        room_id=int(user_id),
        ok=True,
        title=_text(getattr(stream, "title", None), 160),
        anchor_name=_text(getattr(stream, "user_name", None), 80) or display_name,
        live_status="live" if getattr(stream, "type", None) == "live" else "offline",
    )


def _numeric_id(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text if text.isascii() and text.isdigit() and len(text) <= 32 else ""


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split()).strip()[:limit] if isinstance(value, str) else ""
