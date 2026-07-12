"""Free-text source activity detector.

This detector intentionally emits only safe metadata. Raw HUD, combat feed,
award, or player text must stay out of BattleEvent payloads that can reach
prompt construction.
"""

from __future__ import annotations

from typing import Any

from ...core.contracts import BattleEvent, BattleState
from .._base import DiscreteDetector

_SOURCE_ORDER = ("awards", "combat_feed", "hud_notices", "hud_events", "hudmsg")
_TECHNICAL_HUD_NOTICE_CODES = frozenset({"engine_overheat", "oil_overheat", "powertrain_failure"})


def _alive(state: BattleState) -> bool:
    return state.is_alive()


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _feed_from_mapping(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    return _items(value.get("feed"))


def _item_id(item: dict[str, Any], fallback: int) -> int:
    try:
        return int(item.get("id"))
    except (TypeError, ValueError):
        return fallback


class FreeTextActivityDetector(DiscreteDetector):
    """Surface free-text source activity as dry-run-only safe metadata."""

    id = "free_text_activity"

    def __init__(self) -> None:
        self._last_ids: dict[str, int] = {}
        self._hudmsg_seen = False

    def reset(self) -> None:
        self._last_ids.clear()
        self._hudmsg_seen = False

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        if not _alive(cur):
            self.reset()
            return None

        for source in _SOURCE_ORDER:
            payload = self._candidate_payload(cur, source)
            if payload:
                return BattleEvent(
                    "free_text_activity",
                    payload=payload,
                    ts=cur.timestamp or 0.0,
                    level="warning",
                )
        return None

    def _candidate_payload(self, cur: BattleState, source: str) -> dict[str, Any] | None:
        if source == "hudmsg":
            raw_hudmsg = cur.raw.get("hudmsg") if isinstance(cur.raw, dict) else None
            if isinstance(raw_hudmsg, str) and raw_hudmsg.strip() and not self._hudmsg_seen:
                self._hudmsg_seen = True
                return {"source": source, "count": 1}
            return None

        items = self._source_items(cur, source)
        if not items:
            return None

        ids = [_item_id(item, index + 1) for index, item in enumerate(items)]
        max_id = max(ids, default=0)
        last_id = self._last_ids.get(source, 0)
        if max_id < last_id:
            last_id = 0

        new_items = [item for item, item_id in zip(items, ids) if item_id > last_id]
        if not new_items:
            self._last_ids[source] = max(self._last_ids.get(source, 0), max_id)
            return None

        self._last_ids[source] = max_id
        payload: dict[str, Any] = {"source": source, "count": len(new_items)}
        return payload

    def _source_items(self, cur: BattleState, source: str) -> list[dict[str, Any]]:
        raw = cur.raw if isinstance(cur.raw, dict) else {}
        if source == "awards":
            return _feed_from_mapping(raw.get("awards"))
        if source == "combat_feed":
            feed = cur.combat.get("feed") if isinstance(cur.combat, dict) else None
            return [
                item
                for item in _items(feed)
                if item.get("is_my_kill") is not True and item.get("is_my_death") is not True
            ]
        if source == "hud_notices":
            return [
                item
                for item in _items(cur.hud_notices)
                if str(item.get("code") or "").strip().lower() not in _TECHNICAL_HUD_NOTICE_CODES
            ]
        if source == "hud_events":
            return _items(cur.hud_events)
        return []
