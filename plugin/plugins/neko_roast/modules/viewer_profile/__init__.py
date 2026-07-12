"""Viewer profile module."""

from __future__ import annotations

from ...core.contracts import ViewerIdentity, ViewerProfile
from .._base import BaseModule


class ViewerProfileModule(BaseModule):
    id = "viewer_profile"
    title = "观众档案"

    async def upsert(self, identity: ViewerIdentity) -> ViewerProfile:
        return await self.ctx.viewer_store.upsert_identity(identity)

    async def record_live_danmaku(
        self,
        identity: ViewerIdentity,
        danmaku_text: str,
    ) -> ViewerProfile:
        return await self.ctx.viewer_store.record_live_danmaku(identity, danmaku_text)

    async def has_roasted(self, uid: str) -> bool:
        return await self.ctx.viewer_store.has_roasted(uid)

    async def mark_roasted(self, uid: str, output: str) -> bool:
        return await self.ctx.viewer_store.mark_roasted(uid, output)
