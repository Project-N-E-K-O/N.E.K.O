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
_SPENT = "spent"  # once_per_battle：已报过且已退出，本局不再 re-arm（engine.reset() 复位）


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
        critical_heartbeat_seconds: float | None = None,
        once_per_battle: bool = False,
    ) -> None:
        self.id = event_id
        self.groups = groups
        self.confirm_enter = max(1, confirm_enter)
        self.confirm_exit = max(1, confirm_exit)
        self.payload_fn = payload_fn
        self.predicate = predicate
        self.wants_recovery = wants_recovery
        # 危急持续期心跳：>0 时，critical 在 ACTIVE 期间每隔这么久重发一条 enter。
        # 存在的原因：Arbiter 的 critical_preempt_cooldown 会把冷却期内的抢占候选整条丢弃，
        # 而本 FSM 进入 ACTIVE 后不再重发 —— 于是"冷却期内进入的危急"永远不会被播报
        # （例：over_g 刚播完 2s，失速进入并持续，玩家全程无提示）。
        # 心跳保证条件仍然为真时才重发，事实与 ts 都是新的；已播报过的同一条会被
        # Dispatcher 的 repeat-collapse 折叠掉，因此不会变成刷屏。
        self._critical_heartbeat_configurable = critical_heartbeat_seconds is not None
        self.critical_heartbeat_seconds = max(0.0, float(critical_heartbeat_seconds or 0.0))
        # EVENT_CATALOG 里 cooldown<0 声明"每局一次"，但 Arbiter 只对 cd>0 查冷却，
        # 于是该语义无人执行：电平 flag 在阈值附近抖动就能让检测器反复 re-arm 重报
        # （实测样本里 low_fuel 13 秒内报了三次）。置位后条件退出即进入 _SPENT，
        # 本局不再重新武装；warning→critical 升级仍在 ACTIVE 内正常发生。
        self.once_per_battle = bool(once_per_battle)
        self._phase = _ARMED
        self._count = 0
        self._level = "warning"
        self._last_emit_ts: float = 0.0

    @property
    def active(self) -> bool:
        return self._phase in (_ACTIVE, _CONFIRMING_EXIT)

    def reset(self) -> None:
        self._phase = _ARMED
        self._count = 0
        self._level = "warning"
        self._last_emit_ts = 0.0

    def reset_transient(self) -> None:
        """Clear life/mode-local state without forgetting a per-battle emission."""
        consumed = self.once_per_battle and self._phase in (
            _ACTIVE,
            _CONFIRMING_EXIT,
            _SPENT,
        )
        self._phase = _SPENT if consumed else _ARMED
        self._count = 0
        self._level = "warning"
        self._last_emit_ts = 0.0

    def configure_critical_heartbeat(self, seconds: float) -> None:
        """Update heartbeat cadence without resetting the detector FSM."""
        if self._critical_heartbeat_configurable:
            self.critical_heartbeat_seconds = max(0.0, float(seconds))

    def feed(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        if self.predicate is not None and not self.predicate(cur):
            # A domain/life boundary is not a new battle. In particular,
            # low_fuel must remain spent across a same-match respawn.
            self.reset_transient()
            return None

        active_now, level = _eval_flags(cur, self.groups)

        if self._phase == _SPENT:
            # 本局已报过；只等 engine.reset()（新 battle_id）重新武装。
            return None

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
            if self._should_heartbeat(cur, level):
                return self._make_event(cur, edge="enter")
            return None
        self._count = self._count + 1 if self._phase == _CONFIRMING_EXIT else 1
        if self._count >= self.confirm_exit:
            self._phase = _SPENT if self.once_per_battle else _ARMED
            self._count = 0
            if self.wants_recovery:
                return self._make_event(cur, edge="recovery")
            return None
        self._phase = _CONFIRMING_EXIT
        return None

    def _should_heartbeat(self, state: BattleState, level: str) -> bool:
        if self.critical_heartbeat_seconds <= 0 or level != "critical":
            return False
        ts = float(state.timestamp or 0.0)
        if ts <= 0 or self._last_emit_ts <= 0:
            return False
        return ts - self._last_emit_ts >= self.critical_heartbeat_seconds

    def _make_event(self, state: BattleState, *, edge: str) -> BattleEvent:
        payload = self.payload_fn(state) if self.payload_fn else {}
        self._last_emit_ts = float(state.timestamp or 0.0)
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
    # 阵亡期间的处理策略（见 DetectorEngine.feed）：
    #   "reset"   —— 清空检测器状态（电平型/快照型适用）。
    #   "consume" —— 照常喂帧以推进 id 游标，但丢弃产出。
    #   "allow"   —— 照常喂帧并保留候选，由 Arbiter 决定是否缓冲或丢弃。
    # 消费数据层整局持久 feed（combat/hud_notices/awards/proximity/chat）的检测器
    # 必须用 "consume"：这些 feed 只在换局或 HUD drain 时清空，同局重生不清空，
    # 阵亡期间 reset 游标会让整局旧条目在重生后被当成新事件重播。
    dead_state_policy = "reset"

    @property
    def active(self) -> bool:
        return False

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:  # pragma: no cover
        raise NotImplementedError

    def feed(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        return self.detect(prev, cur)

    def consume(self, prev: BattleState, cur: BattleState) -> None:
        """Advance a persistent feed while intentionally discarding its output."""
        self.detect(prev, cur)


class DetectorEngine:
    def __init__(self, detectors: list[Detector]) -> None:
        self.detectors = detectors

    def reset(self) -> None:
        """Clear per-battle detector state without rebuilding detector configuration."""
        for detector in self.detectors:
            reset = getattr(detector, "reset", None)
            if callable(reset):
                reset()

    def configure_critical_heartbeat(self, seconds: float) -> None:
        """Refresh heartbeat cadence in place so detector FSM/feed cursors survive config reloads."""
        for detector in self.detectors:
            configure = getattr(detector, "configure_critical_heartbeat", None)
            if callable(configure):
                configure(seconds)

    def feed(self, prev: BattleState, cur: BattleState) -> list[BattleEvent]:
        if cur.replay:
            self.reset()
            return []
        out: list[BattleEvent] = []
        for det in self.detectors:
            if cur.dead and getattr(det, "id", "") != "you_died":
                dead_state_policy = getattr(det, "dead_state_policy", "reset")
                if dead_state_policy == "consume":
                    # 只推进持久 feed 游标，不运行可能因 dead 状态重置游标的正常检测。
                    consume = getattr(det, "consume", None)
                    if callable(consume):
                        consume(prev, cur)
                    continue
                if dead_state_policy != "allow":
                    reset = getattr(det, "reset_transient", None)
                    if not callable(reset):
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
