"""Detector 协议 + 边沿 FSM + 引擎（D-B3）。

Detector 只产"候选 BattleEvent"，不做门控/限流/拼台词（那些归 Arbiter / Dispatcher）。
- ConditionDetector：消费数据层电平 flag，做 confirm/迟滞/re-arm 的边沿 FSM。
- DiscreteDetector：消费已边沿/跳变来源（hud_events/combat/state），按 id/跳变去重。
- DetectorEngine：每 tick 喂 (prev, cur)，收集候选。
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from ..core.contracts import BattleEvent, BattleState

# ConditionDetector FSM 相位
_ARMED = "armed"
_CONFIRMING_ENTER = "confirming_enter"
_ACTIVE = "active"
_CONFIRMING_EXIT = "confirming_exit"


class Detector(Protocol):
    id: str

    def feed(self, prev: BattleState, cur: BattleState) -> BattleEvent | None: ...

    @property
    def active(self) -> bool: ...


def _eval_flags(state: BattleState, groups: list[tuple[str, str]]) -> tuple[bool, str]:
    """任一组 warn/crit 命中即 active；critical 优先。返回 (active, level)。"""
    active = False
    level = "warning"
    for warn_code, crit_code in groups:
        if state.flag(crit_code):
            return True, "critical"
        if state.flag(warn_code):
            active = True
    return active, level


class ConditionDetector:
    """电平 flag → 边沿事件。enter 谓词=任一组 flag 真；迟滞由 confirm_exit 提供。"""

    def __init__(
        self,
        event_id: str,
        groups: list[tuple[str, str]],
        *,
        confirm_enter: int = 2,
        confirm_exit: int = 2,
        payload_fn: Callable[[BattleState], dict[str, Any]] | None = None,
        predicate: Callable[[BattleState], bool] | None = None,
        wants_recovery: bool = False,
    ) -> None:
        self.id = event_id
        self.groups = groups
        self.confirm_enter = max(1, confirm_enter)
        self.confirm_exit = max(1, confirm_exit)
        self.payload_fn = payload_fn
        self.predicate = predicate
        self.wants_recovery = wants_recovery
        self._phase = _ARMED
        self._count = 0
        self._level = "warning"

    @property
    def active(self) -> bool:
        return self._phase in (_ACTIVE, _CONFIRMING_EXIT)

    def reset(self) -> None:
        self._phase = _ARMED
        self._count = 0
        self._level = "warning"

    def feed(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        if self.predicate is not None and not self.predicate(cur):
            self.reset()
            return None

        active_now, level = _eval_flags(cur, self.groups)

        # 进入侧：ARMED / CONFIRMING_ENTER
        if self._phase in (_ARMED, _CONFIRMING_ENTER):
            if not active_now:
                self._phase = _ARMED
                self._count = 0
                return None
            self._level = level
            self._count = self._count + 1 if self._phase == _CONFIRMING_ENTER else 1
            if self._count >= self.confirm_enter:
                self._phase = _ACTIVE
                self._count = 0
                return self._make_event(cur, edge="enter")
            self._phase = _CONFIRMING_ENTER
            return None

        # 持续侧：ACTIVE / CONFIRMING_EXIT
        if active_now:
            self._phase = _ACTIVE
            self._count = 0
            if level == "critical" and self._level != "critical":
                self._level = "critical"  # warning→critical 升级：重报一条 critical（可被 Arbiter 抢占）
                return self._make_event(cur, edge="enter")
            return None
        self._count = self._count + 1 if self._phase == _CONFIRMING_EXIT else 1
        if self._count >= self.confirm_exit:
            self._phase = _ARMED
            self._count = 0
            if self.wants_recovery:
                return self._make_event(cur, edge="recovery")
            return None
        self._phase = _CONFIRMING_EXIT
        return None

    def _make_event(self, state: BattleState, *, edge: str) -> BattleEvent:
        payload = self.payload_fn(state) if self.payload_fn else {}
        return BattleEvent(
            event_id=self.id,
            edge=edge,
            payload=payload,
            ts=state.timestamp or 0.0,
            level=self._level if edge == "enter" else "warning",
        )


class DiscreteDetector:
    """已边沿/跳变来源 → 候选。子类实现 detect(prev, cur)；自行按 id/跳变去重。"""

    id = "discrete"

    @property
    def active(self) -> bool:
        return False

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:  # pragma: no cover
        raise NotImplementedError

    def feed(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        return self.detect(prev, cur)


class DetectorEngine:
    def __init__(self, detectors: list[Detector]) -> None:
        self.detectors = detectors

    def feed(self, prev: BattleState, cur: BattleState) -> list[BattleEvent]:
        if cur.replay:
            for det in self.detectors:
                reset = getattr(det, "reset", None)
                if callable(reset):
                    reset()
            return []
        out: list[BattleEvent] = []
        for det in self.detectors:
            if cur.dead:
                reset = getattr(det, "reset", None)
                if callable(reset):
                    reset()
                    continue
            ev = det.feed(prev, cur)
            if ev is not None:
                out.append(ev)
        return out

    def critical_active(self) -> bool:
        """危急集合中是否有 detector 处于 active（供场景解析旁路用，当前场景直接读 flag）。"""
        from ..core.contracts import CRITICAL_EVENT_IDS

        return any(d.active for d in self.detectors if getattr(d, "id", "") in CRITICAL_EVENT_IDS)
