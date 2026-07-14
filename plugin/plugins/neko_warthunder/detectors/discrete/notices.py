"""Discrete detectors for data-layer HUD technical notices.

Only notice codes are promoted into BattleEvent facts. Raw notice text stays in
BattleState.raw / audit paths and must not enter event payloads or prompts.
"""

from __future__ import annotations

from typing import Any

from ...core.contracts import BattleEvent, BattleState
from .._base import DiscreteDetector

_OVERHEAT_CODES = frozenset({"engine_overheat", "oil_overheat"})


def _notice_items(state: BattleState) -> list[dict[str, Any]]:
    return [item for item in state.hud_notices if isinstance(item, dict)]


def _notice_id(item: dict[str, Any]) -> int | None:
    try:
        return int(item.get("id"))
    except (TypeError, ValueError):
        return None


class HudNoticeDetector(DiscreteDetector):
    """Promote safe technical notice codes into existing BattleEvents."""

    id = "hud_notice"

    def __init__(self) -> None:
        self._last_id: int = -1

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        if not cur.is_alive():
            return None

        notices = _notice_items(cur)
        ids = [eid for item in notices if (eid := _notice_id(item)) is not None]
        if not ids:
            return None
        max_id = max(ids)
        if max_id < self._last_id:
            self._last_id = -1

        newest: dict[str, Any] | None = None
        newest_id = -1
        for item in notices:
            eid = _notice_id(item)
            if eid is None or eid <= self._last_id:
                continue
            code = str(item.get("code") or "")
            if code in _OVERHEAT_CODES and eid > newest_id:
                newest = item
                newest_id = eid

        self._last_id = max(self._last_id, max_id)
        if newest is None:
            return None

        code = str(newest.get("code") or "")
        severity = str(newest.get("level") or newest.get("severity") or "warning").lower()
        level = "critical" if severity == "critical" else "warning"
        payload = {"source": "hud_notice", "notice_code": code}
        if cur.domain and cur.domain != "unknown":
            payload["domain"] = cur.domain
        return BattleEvent(
            "overheat",
            payload=payload,
            ts=cur.timestamp or 0.0,
            level=level,
        )
