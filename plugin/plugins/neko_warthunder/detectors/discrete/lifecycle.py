"""离散/生命周期检测器：spawn / you_died / battle_end / you_killed。

按"跳变 / 新 id"去重（D-B3 已边沿型）：
- spawn：用 in_battle + vehicle_valid 跳变。
- you_died：消费数据层 combat.feed[].is_my_death，不再把 vehicle_valid 跳变作为主路径。
- battle_end：mission_status 进入结束态的跳变。
- you_killed：消费数据层 combat.feed[].is_my_kill，按 feed id 去重。
"""

from __future__ import annotations

from typing import Any

from ...core.contracts import BattleEvent, BattleState
from .._base import DiscreteDetector
from .free_text import FreeTextActivityDetector
from .notices import HudNoticeDetector
from .proximity import ProximityDetector
from .radio import RadioCommandDetector
from .situation import AirSituationDetector, GroundTargetDetector

_END_STATUSES = frozenset({"win", "won", "victory", "fail", "failed", "lost", "defeat", "left", "ended", "finished"})


def _alive(s: BattleState) -> bool:
    return s.is_alive()


class SpawnDetector(DiscreteDetector):
    id = "spawn"

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        # 要求 prev.connected：遥测瞬断（parse(None)→not alive）恢复后不误判为重生
        if _alive(cur) and not _alive(prev) and prev.connected:
            return BattleEvent(
                "spawn",
                payload={
                    "vehicle_type": cur.vehicle_type,
                    "domain": cur.domain,
                    "domain_label": cur.domain_label,
                },
                ts=cur.timestamp or 0.0,
                level="warning",
            )
        return None


def _feed_items(state: BattleState) -> list[dict[str, Any]]:
    feed = state.combat.get("feed") if isinstance(state.combat, dict) else None
    if not isinstance(feed, list):
        return []
    return [item for item in feed if isinstance(item, dict)]


def _feed_ids(feed: list[dict[str, Any]]) -> list[int]:
    ids: list[int] = []
    for item in feed:
        try:
            ids.append(int(item.get("id")))
        except (TypeError, ValueError):
            continue
    return ids


class DeathDetector(DiscreteDetector):
    """Death events come from data-layer combat.feed[].is_my_death."""

    id = "you_died"

    def __init__(self) -> None:
        self._last_seen_id: int = -1
        self._emitted_ids: set[int] = set()

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        feed = _feed_items(cur)
        ids = _feed_ids(feed)
        if not ids:
            return None
        max_id = max(ids)
        if max_id < self._last_seen_id:
            self._last_seen_id = -1
            self._emitted_ids.clear()
        self._last_seen_id = max(self._last_seen_id, max_id)

        newest: dict[str, Any] | None = None
        for item in feed:
            try:
                eid = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if eid in self._emitted_ids:
                continue
            if item.get("is_my_death") is True:
                if newest is None or eid > int(newest.get("id")):
                    newest = item
        if newest is None:
            return None
        for item in feed:
            try:
                eid = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if item.get("is_my_death") is True:
                self._emitted_ids.add(eid)

        return BattleEvent(
            "you_died",
            payload={
                "killer_name": newest.get("killer"),
                "killer_vehicle": newest.get("killer_vehicle"),
                "cause": newest.get("action") or "unknown",
                "domain": cur.domain,
            },
            ts=cur.timestamp or 0.0,
            level="critical",
        )


class BattleEndDetector(DiscreteDetector):
    id = "battle_end"

    def _ended(self, s: BattleState) -> bool:
        return (s.mission_status or "").lower() in _END_STATUSES

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        if self._ended(cur) and not self._ended(prev):
            payload: dict[str, Any] = {"result": cur.mission_status, "domain": cur.domain}
            my = cur.combat.get("my") if isinstance(cur.combat, dict) else None
            if isinstance(my, dict):
                payload["result"] = f"{cur.mission_status}, K{my.get('kills', 0)}/D{my.get('deaths', 0)}"
            return BattleEvent("battle_end", payload=payload, ts=cur.timestamp or 0.0, level="warning")
        return None


class KillDetector(DiscreteDetector):
    """Kill events come from data-layer combat.feed[].is_my_kill."""

    id = "you_killed"

    def __init__(self, player_name: str) -> None:
        self.player_name = (player_name or "").strip()
        self._last_seen_id: int = -1
        self._emitted_ids: set[int] = set()

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        feed = _feed_items(cur)
        if not feed:
            return None
        ids = _feed_ids(feed)
        if not ids:
            return None
        max_id = max(ids)
        if max_id < self._last_seen_id:  # 新对局 feed id 回退 → 重置
            self._last_seen_id = -1
            self._emitted_ids.clear()
        self._last_seen_id = max(self._last_seen_id, max_id)
        new_kills: list[tuple[int, dict[str, Any]]] = []
        for item in feed:
            try:
                eid = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if eid in self._emitted_ids:
                continue
            if item.get("is_my_kill") is True:
                new_kills.append((eid, item))
        if not new_kills:
            return None
        self._emitted_ids.update(eid for eid, _item in new_kills)
        _newest_id, newest = max(new_kills, key=lambda entry: entry[0])
        return BattleEvent(
            "you_killed",
            payload={
                "victim": newest.get("victim"),
                "victim_vehicle": newest.get("victim_vehicle"),
                "domain": cur.domain,
                "kill_count": len(new_kills),
            },
            ts=cur.timestamp or 0.0,
            level="warning",
        )


def build_discrete_detectors(player_name: str) -> list[DiscreteDetector]:
    return [
        SpawnDetector(),
        DeathDetector(),
        BattleEndDetector(),
        KillDetector(player_name),
        HudNoticeDetector(),
        RadioCommandDetector(),
        FreeTextActivityDetector(),
        ProximityDetector(),
        AirSituationDetector(),
        GroundTargetDetector(),
    ]
