"""Resolve Twitch chat identities from already-public EventSub fields."""

from __future__ import annotations

from typing import Any

from ...core.contracts import ViewerEvent, ViewerIdentity
from .._base import BaseModule


class TwitchIdentityModule(BaseModule):
    id = "twitch_identity"
    title = "Twitch identity"

    async def resolve(self, event: ViewerEvent) -> ViewerIdentity:
        raw_uid = event.uid.strip() if isinstance(event.uid, str) else ""
        uid = raw_uid if raw_uid.startswith("twitch:") else f"twitch:{raw_uid}"
        nickname = event.nickname.strip() if isinstance(event.nickname, str) else ""
        nickname = nickname[:80] or raw_uid[:80]
        login = ""
        if isinstance(event.raw, dict) and isinstance(event.raw.get("chatter_login"), str):
            candidate = event.raw["chatter_login"].strip().lower()
            if candidate.isascii() and candidate.replace("_", "").isalnum():
                login = candidate[:25]
        return ViewerIdentity(
            uid=uid,
            nickname=nickname,
            name=nickname,
            source_url=f"https://www.twitch.tv/{login}" if login else "",
            fetched=True,
        )

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled is True, "avatar_fetch": False, "profile_fetch": False}
