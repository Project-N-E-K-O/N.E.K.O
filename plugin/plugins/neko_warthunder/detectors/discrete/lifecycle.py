"""离散/生命周期检测器：spawn / you_died / battle_end / you_killed。

按"跳变 / 新 id"去重（D-B3 已边沿型）：
- spawn：用 in_battle + vehicle_valid 跳变。
- you_died：消费数据层 combat.feed[].is_my_death，不再把 vehicle_valid 跳变作为主路径。
- battle_end：mission_status 进入结束态的跳变。
- you_killed：消费数据层 combat.feed[].is_my_kill，按 feed id 去重。
"""

from __future__ import annotations

from typing import Any

from ...core.contracts import (
    BattleEvent,
    BattleState,
    classify_battle_result,
    is_battle_end_status,
)
from .._base import DiscreteDetector
from .free_text import FreeTextActivityDetector
from .notices import HudNoticeDetector
from .proximity import ProximityDetector
from .radio import RadioCommandDetector
from .situation import AirSituationDetector, GroundTargetDetector

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
                    "respawn": bool(prev.dead),
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
        self._dead_edge_emitted = False

    def reset(self) -> None:
        self._last_seen_id = -1
        self._emitted_ids.clear()
        self._dead_edge_emitted = False

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        if not cur.dead:
            self._dead_edge_emitted = False
        feed = _feed_items(cur)
        ids = _feed_ids(feed)
        if ids:
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
        if newest is not None:
            for item in feed:
                try:
                    eid = int(item.get("id"))
                except (TypeError, ValueError):
                    continue
                if item.get("is_my_death") is True:
                    self._emitted_ids.add(eid)
            if self._dead_edge_emitted and cur.dead:
                return None
            self._dead_edge_emitted = True
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

        # HUD 在部分陆战模式不提供本人死亡事件；数据层的 ground_crew 仅在
        # crew_total>=2 且 crew_current<=1 时产生，可以安全作为一次通用阵亡边沿。
        if (
            cur.dead
            and not prev.dead
            and cur.dead_source == "ground_crew"
            and not self._dead_edge_emitted
        ):
            self._dead_edge_emitted = True
            return BattleEvent(
                "you_died",
                payload={"cause": "ground_crew", "domain": cur.domain},
                ts=cur.timestamp or 0.0,
                level="critical",
            )
        return None


class BattleEndDetector(DiscreteDetector):
    id = "battle_end"

    def _ended(self, s: BattleState) -> bool:
        return is_battle_end_status(s.mission_status)

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        if self._ended(cur) and not self._ended(prev):
            payload: dict[str, Any] = {
                "result": cur.mission_status,
                "result_kind": classify_battle_result(cur.mission_status),
                "domain": cur.domain,
            }
            my = cur.combat.get("my") if isinstance(cur.combat, dict) else None
            if isinstance(my, dict):
                payload["result"] = f"{cur.mission_status}, K{my.get('kills', 0)}/D{my.get('deaths', 0)}"
            return BattleEvent("battle_end", payload=payload, ts=cur.timestamp or 0.0, level="warning")
        return None


class KillDetector(DiscreteDetector):
    """Kill events come from data-layer combat.feed[].is_my_kill."""

    id = "you_killed"
    # 延迟到账的同归于尽/航弹战果必须进入 Arbiter 的 DEAD 场景交易击杀路径；
    # detector 自身仍推进并记录 feed id，因此重生后不会补播同一条。
    dead_state_policy = "allow"

    def __init__(self) -> None:
        # 刻意不接收 player_name：归属完全由数据层的 is_my_kill 判定，插件侧不做
        # 本地名字比对。曾经保存过该字段但从未读取，容易让人误以为还有第二条归属路径。
        self._last_seen_id: int = -1
        self._emitted_ids: set[int] = set()

    def reset(self) -> None:
        self._last_seen_id = -1
        self._emitted_ids.clear()

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


def build_discrete_detectors(player_name: str = "") -> list[DiscreteDetector]:
    """构造离散检测器。

    ``player_name`` 不再传给任何检测器——击杀/阵亡归属由数据层的 is_my_kill /
    is_my_death 给出。保留该形参是因为调用方用"昵称变化"作为重建引擎的信号
    （见 NekoWarthunderPlugin._apply_config：身份变了就要清空 id 游标）。
    """
    return [
        SpawnDetector(),
        DeathDetector(),
        BattleEndDetector(),
        KillDetector(),
        HudNoticeDetector(),
        RadioCommandDetector(),
        FreeTextActivityDetector(),
        ProximityDetector(),
        AirSituationDetector(),
        GroundTargetDetector(),
    ]
