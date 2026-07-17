"""Resolve sanitized Douyin live viewer identity fields."""

from __future__ import annotations

from typing import Any

from ...core.contracts import ViewerEvent, ViewerIdentity
from .._base import BaseModule
from ..douyin_live_ingest.event_model import platform_uid, safe_avatar_url, safe_text
from ..douyin_live_ingest.public_projection import safe_public_bool


class DouyinIdentityModule(BaseModule):
    id = "douyin_identity"
    title = "抖音身份解析"

    async def resolve(self, event: ViewerEvent) -> ViewerIdentity:
        uid = self._platform_uid(event.uid)
        nickname = safe_text(event.nickname) or uid
        avatar_url = safe_avatar_url(event.avatar_url)
        return ViewerIdentity(
            uid=uid,
            nickname=nickname,
            name=nickname,
            avatar_url=avatar_url,
            source_url=self._source_url(uid),
            fetched=True,
        )

    @staticmethod
    def _platform_uid(value: object) -> str:
        return platform_uid(value)

    @staticmethod
    def _source_url(uid: str) -> str:
        stable_id = uid.removeprefix("douyin:")
        if not stable_id:
            return ""
        return f"https://www.douyin.com/user/{stable_id}"

    def status(self) -> dict[str, Any]:
        return {"enabled": safe_public_bool(self.enabled), "avatar_fetch": False, "profile_fetch": False}
