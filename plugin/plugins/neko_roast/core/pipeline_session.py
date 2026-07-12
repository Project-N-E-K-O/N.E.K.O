"""Session-local gates and pacing state for the interaction pipeline."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .contracts import ViewerEvent
from .pipeline_routing import entrance_pacing_interval_seconds


class PipelineSessionTracker:
    def __init__(self) -> None:
        self._uid_locks: dict[str, asyncio.Lock] = {}
        self._dry_run_roasted_uids: set[str] = set()
        self._session_roasted_uids: set[str] = set()
        self._last_avatar_roast_at: float | None = None

    def clear(self) -> None:
        self._dry_run_roasted_uids.clear()
        self._session_roasted_uids.clear()
        self._last_avatar_roast_at = None

    def clear_uid(self, uid: str) -> None:
        key = str(uid or "").strip()
        if not key:
            return
        self._dry_run_roasted_uids.discard(key)
        self._session_roasted_uids.discard(key)

    def entrance_pacing_active(self, config: Any, *, now: float | None = None) -> bool:
        if self._last_avatar_roast_at is None:
            return False
        level = str(getattr(config, "activity_level", "standard") or "standard")
        return (
            (self._now() if now is None else now) - self._last_avatar_roast_at
        ) < entrance_pacing_interval_seconds(level)

    def record_avatar_roast_sent(self, *, now: float | None = None) -> None:
        self._last_avatar_roast_at = self._now() if now is None else now

    def needs_uid_lock(
        self,
        config: Any,
        *,
        is_live_danmaku_with_text: bool,
        is_transient_event: bool,
    ) -> bool:
        if is_transient_event:
            return False
        return bool(getattr(config, "roast_once_per_uid", False)) or bool(
            is_live_danmaku_with_text
        )

    def lock_for(self, uid: str) -> asyncio.Lock:
        return self._uid_locks.setdefault(uid, asyncio.Lock())

    async def already_roasted(self, ctx: Any, event: ViewerEvent, uid: str) -> bool:
        if uid in self._session_roasted_uids:
            return True
        if ctx.config.roast_once_per_uid and await ctx.viewer_profile.has_roasted(uid):
            return True
        if ctx.config.dry_run and event.source == "live_danmaku":
            return uid in self._dry_run_roasted_uids
        return False

    def claim_roasted(self, uid: str) -> None:
        self._session_roasted_uids.add(uid)

    def mark_dry_run_roasted(self, uid: str) -> None:
        self._dry_run_roasted_uids.add(uid)

    @staticmethod
    def _now() -> float:
        return time.monotonic()
