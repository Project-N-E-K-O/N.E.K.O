"""Privacy-safe, in-memory audience summary for the current live session."""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

from .._base import BaseModule
from ..live_events.provider_event import event_nickname, event_type, event_uid, public_text


_PUBLIC_VIEWER_LIMIT = 30
_DETAIL_VIEWER_LIMIT = 100
_UNIQUE_VIEWER_LIMIT = 5000
_SUPPORT_EVENT_TYPES = {"gift", "super_chat", "guard"}


class LiveAudienceSessionModule(BaseModule):
    """Aggregate the common, provider-neutral facts needed by the audience page."""

    id = "live_audience_session"
    title = "Live audience session"
    domain = "viewers"

    def __init__(self) -> None:
        super().__init__()
        self._unsubscribes: list[Any] = []
        self._now = time.time
        self._active = False
        self._started_at = 0.0
        self._ended_at = 0.0
        self._danmaku_count = 0
        self._support_event_count = 0
        self._neko_output_count = 0
        self._unique_viewer_keys: set[str] = set()
        self._viewers: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._session_salt = secrets.token_bytes(32)

    async def setup(self, ctx: Any) -> None:
        await super().setup(ctx)
        bus = getattr(ctx, "event_bus", None)
        if bus is None:
            return
        for live_event_type in ("danmaku", "gift", "super_chat", "guard"):
            self._unsubscribes.append(
                bus.subscribe(live_event_type, self._on_live_event, owner=self.id)
            )
        self._unsubscribes.append(bus.subscribe("result", self._on_result, owner=self.id))

    async def teardown(self) -> None:
        for unsubscribe in self._unsubscribes:
            if callable(unsubscribe):
                unsubscribe()
        self._unsubscribes = []
        self._active = False
        await super().teardown()

    def start_session(self) -> None:
        """Start a new explicit listening session and discard the previous summary."""
        self._session_salt = secrets.token_bytes(32)
        self._active = True
        self._started_at = self._safe_now()
        self._ended_at = 0.0
        self._danmaku_count = 0
        self._support_event_count = 0
        self._neko_output_count = 0
        self._unique_viewer_keys = set()
        self._viewers = OrderedDict()

    def finish_session(self) -> None:
        """End explicit listening while retaining the last summary until the next start."""
        if self._active:
            self._ended_at = self._safe_now()
        self._active = False

    def snapshot(self) -> dict[str, Any]:
        viewers = sorted(
            self._viewers.values(),
            key=lambda item: float(item.get("last_interaction_ts") or 0.0),
            reverse=True,
        )[:_PUBLIC_VIEWER_LIMIT]
        return {
            "active": self._active,
            "has_session": self._started_at > 0.0,
            "started_at": _public_timestamp(self._started_at),
            "ended_at": _public_timestamp(self._ended_at),
            "interaction_viewer_count": len(self._unique_viewer_keys),
            "interaction_viewer_count_capped": len(self._unique_viewer_keys) >= _UNIQUE_VIEWER_LIMIT,
            "danmaku_count": self._danmaku_count,
            "support_event_count": self._support_event_count,
            "neko_output_count": self._neko_output_count,
            "viewers": [self._public_viewer(item) for item in viewers],
        }

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "active": self._active,
            "interaction_viewer_count": len(self._unique_viewer_keys),
            "tracked_viewer_count": len(self._viewers),
        }

    def _on_live_event(self, event: Any) -> None:
        if not self._active:
            return
        kind = event_type(event)
        if kind == "danmaku":
            self._danmaku_count += 1
        elif kind in _SUPPORT_EVENT_TYPES:
            self._support_event_count += 1
        else:
            return

        uid = event_uid(event)
        if not uid:
            return
        viewer_key = self._viewer_key(uid)
        if (
            viewer_key not in self._unique_viewer_keys
            and len(self._unique_viewer_keys) < _UNIQUE_VIEWER_LIMIT
        ):
            self._unique_viewer_keys.add(viewer_key)

        item = self._viewers.get(viewer_key)
        if item is None:
            item = {
                "viewer_key": f"viewer_{viewer_key[:12]}",
                "nickname": "",
                "interaction_count": 0,
                "danmaku_count": 0,
                "support_event_count": 0,
                "neko_reply_count": 0,
                "last_event_type": "",
                "last_interaction_ts": 0.0,
            }
        nickname = event_nickname(event)
        if nickname:
            item["nickname"] = nickname
        item["interaction_count"] += 1
        if kind == "danmaku":
            item["danmaku_count"] += 1
        else:
            item["support_event_count"] += 1
        item["last_event_type"] = kind
        item["last_interaction_ts"] = self._event_timestamp(event)
        self._viewers[viewer_key] = item
        self._viewers.move_to_end(viewer_key)
        while len(self._viewers) > _DETAIL_VIEWER_LIMIT:
            self._viewers.popitem(last=False)

    def _on_result(self, result: Any) -> None:
        if not self._active or not isinstance(result, dict):
            return
        if str(result.get("status") or "").strip().lower() != "pushed":
            return
        self._neko_output_count += 1
        uid = _result_uid(result)
        if not uid:
            return
        item = self._viewers.get(self._viewer_key(uid))
        if item is not None:
            item["neko_reply_count"] += 1

    def _viewer_key(self, uid: str) -> str:
        return hashlib.blake2b(
            uid.encode("utf-8", errors="ignore"),
            key=self._session_salt,
            digest_size=8,
        ).hexdigest()

    def _event_timestamp(self, event: Any) -> float:
        value = getattr(event, "ts", 0.0)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value)
        return self._safe_now()

    def _safe_now(self) -> float:
        value = self._now()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value)
        return time.time()

    @staticmethod
    def _public_viewer(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "viewer_key": public_text(item.get("viewer_key"), max_length=32),
            "nickname": public_text(item.get("nickname"), max_length=64),
            "interaction_count": _public_count(item.get("interaction_count")),
            "danmaku_count": _public_count(item.get("danmaku_count")),
            "support_event_count": _public_count(item.get("support_event_count")),
            "neko_reply_count": _public_count(item.get("neko_reply_count")),
            "last_event_type": public_text(item.get("last_event_type"), max_length=32),
            "last_interaction_at": _public_timestamp(item.get("last_interaction_ts")),
        }


def _result_uid(result: dict[str, Any]) -> str:
    for container_name in ("identity", "event"):
        container = result.get(container_name)
        if isinstance(container, dict):
            uid = container.get("uid")
            if isinstance(uid, str) and uid.strip():
                return uid.strip()
            if isinstance(uid, int) and not isinstance(uid, bool) and uid > 0:
                return str(uid)
    return ""


def _public_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _public_timestamp(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return ""
    try:
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


__all__ = ["LiveAudienceSessionModule"]
