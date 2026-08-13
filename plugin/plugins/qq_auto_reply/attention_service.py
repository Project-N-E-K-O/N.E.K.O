"""注意力服务（周期模型）

每个群一份独立注意力标量（0~10），按相位机推进：

- rise: 随时间增长（基础速率 + 消息/@/提问加成），情绪/疲劳调制速率
- fall: 随时间回落（夺冠蜜月结束、发言消耗、让位回落）

焦点 = 所有 attention >= 焦点线的群中最高者；发言消耗注意力；情绪可抢/让焦点。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .feedback_classifier import QQFeedbackClassifier

# ── 情绪 → 注意力速率偏移（rise 加速 / fall 减速）──
_EMOTION_MULTIPLIER: dict[str, float] = {
    "arguing": 1.2,      # 上头死磕，涨得快跌得慢
    "proud": 0.8,        # 赢了要炫耀，猛拉注意力
    "annoyed": 0.5,      # 不爽，比正常更专注
    "playful": 0.3,      # 玩闹中，微微挂住
    "curious": 0.2,      # 被勾起兴趣
    "calm": 0.0,         # 正常
    "sad": -0.4,         # 难过，不太想说话
    "embarrassed": -0.6, # 尴尬想溜
    "sulking": -0.9,     # 赌气——基本清零，主动让出焦点
}
# 强情绪直接触发焦点切换
_EMOTION_FORCE_FOCUS = {"arguing", "proud"}     # 立刻抢焦点
_EMOTION_DROP_FOCUS = {"sulking", "embarrassed"} # 立刻让出焦点
_EMOTION_DECAY_ORDER = ["arguing", "annoyed", "playful", "curious", "calm", "sad", "embarrassed", "sulking"]
_EMOTION_DECAY_SECONDS = 30


@dataclass(slots=True)
class QQGroupAttentionState:
    """单群注意力状态（周期模型）。

    ``attention_score`` 是唯一标量（0~10），按相位机推进：
      - rise: 随时间增长（消息/@ 加速；情绪/疲劳调制速率）
      - fall: 随时间回落（发言消耗、夺冠蜜月结束后自然回落）
    焦点 = 所有 attention_score >= 焦点线的群中最高者。
    """

    group_id: str
    attention_score: float = 0.0      # 唯一标量 0~10
    phase: str = "rise"               # rise | fall
    phase_started_at: int = 0         # 当前相位起始时间（fall 回落计时 / 蜜月判断用）
    # ── 时间戳 ──
    last_decay_at: int = 0            # 上次相位推进时刻（幂等推进用）
    last_message_at: int = 0
    last_reply_at: int = 0
    last_boost_at: int = 0
    last_focus_at: int = 0
    focus_acquired_at: int = 0        # 最近夺冠时刻（蜜月窗口计时）
    last_focus_reason: str = ""
    total_interactions: int = 0
    # ── 情绪 ──
    emotion: str = "calm"             # calm/playful/curious/annoyed/arguing/proud/embarrassed/sad/sulking
    emotion_updated_at: int = 0
    emotion_display: str = "calm"     # 前端展示用标签，衰减比 logic emotion 慢
    emotion_display_until: int = 0

    def recompute_score(self) -> float:
        """兼容接口：注意力即标量，无需加权计算。"""
        return self.attention_score

    def dimension_dict(self) -> dict[str, float]:
        """展示用：标量 + 相位 + 情绪是否活跃。"""
        return {
            "attention": float(self.attention_score),
            "phase": 1.0 if self.phase == "rise" else 0.0,
            "emotion": 1.0 if self.emotion != "calm" else 0.0,
        }

    def dimension_label(self, key: str) -> str:
        return {"attention": "注意力", "phase": "相位", "emotion": "情绪"}.get(key, key)

    def dominant_dimension(self) -> str:
        """用于解释焦点原因：当前相位。"""
        return self.phase

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "attention_score": float(self.attention_score),
            "phase": str(self.phase),
            "phase_started_at": int(self.phase_started_at),
            "last_decay_at": int(self.last_decay_at),
            "last_message_at": int(self.last_message_at),
            "last_reply_at": int(self.last_reply_at),
            "last_boost_at": int(self.last_boost_at),
            "last_focus_at": int(self.last_focus_at),
            "focus_acquired_at": int(self.focus_acquired_at),
            "last_focus_reason": str(self.last_focus_reason or ""),
            "total_interactions": int(self.total_interactions),
            "emotion": str(self.emotion or "calm"),
            "emotion_updated_at": int(self.emotion_updated_at),
            "emotion_display": str(self.emotion_display or "calm"),
            "emotion_display_until": int(self.emotion_display_until),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None, *, group_id: str) -> "QQGroupAttentionState":
        data = dict(payload or {})
        st = cls(
            group_id=group_id,
            attention_score=float(data.get("attention_score") or 0.0),
            phase=str(data.get("phase") or "rise"),
            phase_started_at=int(data.get("phase_started_at") or 0),
            last_decay_at=int(data.get("last_decay_at") or 0),
            last_message_at=int(data.get("last_message_at") or 0),
            last_reply_at=int(data.get("last_reply_at") or 0),
            last_boost_at=int(data.get("last_boost_at") or 0),
            last_focus_at=int(data.get("last_focus_at") or 0),
            focus_acquired_at=int(data.get("focus_acquired_at") or data.get("last_focus_at") or 0),
            last_focus_reason=str(data.get("last_focus_reason") or ""),
            total_interactions=int(data.get("total_interactions") or 0),
            emotion=str(data.get("emotion") or "calm"),
            emotion_updated_at=int(data.get("emotion_updated_at") or 0),
            emotion_display=str(data.get("emotion_display") or "calm"),
            emotion_display_until=int(data.get("emotion_display_until") or 0),
        )
        # 旧维度模型迁移：旧 attention_score 是四维加权分，与周期模型标量语义不同——
        # 直接按新模型从当前值开始重新积累，相位默认 rise。
        if not data.get("phase"):
            st.phase = "rise"
        return st


class QQAttentionService:
    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._cache: dict[str, dict[str, Any]] = {}

    async def load_cached_state(self) -> None:
        if not getattr(self.plugin, "backlog_store", None):
            self._cache = {}
            return
        state = await self.plugin.backlog_store.load()
        attention_state = state.get("group_attention_state")
        self._cache = dict(attention_state) if isinstance(attention_state, dict) else {}
        self.cleanup_stale_cache()
        # 重启后重置相位时钟：last_decay_at/phase_started_at 是停机前的旧值，
        # 直接用停机时长推进会把注意力一步推到极端（rise 顶满 / fall 归零）。
        # 重置为当前时刻，让周期从干净状态重新开始。
        now = self._current_time()
        for payload in self._cache.values():
            if isinstance(payload, dict):
                payload["last_decay_at"] = now
                payload["phase_started_at"] = now

    def _current_time(self) -> int:
        return int(__import__("time").time())

    def _normalized_groups(self) -> list[str]:
        """只读：返回信任列表 + 缓存中已有的群（不含清理逻辑）。"""
        groups: set[str] = set()
        if self.plugin.group_permission_mgr:
            for item in self.plugin.group_permission_mgr.list_groups():
                gid = item.get("group_id", "") if isinstance(item, dict) else str(item or "")
                normalized = str(gid or "").strip()
                if normalized:
                    groups.add(normalized)
        for group_id in list(self._cache.keys()):
            if isinstance(group_id, str) and group_id.startswith("{"):
                continue
            normalized = str(group_id or "").strip()
            if normalized and not normalized.startswith("{"):
                groups.add(normalized)
        return sorted(groups)

    def cleanup_stale_cache(self) -> int:
        """显式清理不在信任列表中的缓存群，返回清理数。调用时机：load_cached_state / persist。"""
        trust_groups: set[str] = set()
        if self.plugin.group_permission_mgr:
            for item in self.plugin.group_permission_mgr.list_groups():
                gid = item.get("group_id", "") if isinstance(item, dict) else str(item or "")
                normalized = str(gid or "").strip()
                if normalized:
                    trust_groups.add(normalized)
        removed = 0
        for group_id in list(self._cache.keys()):
            if isinstance(group_id, str) and group_id.startswith("{"):
                del self._cache[group_id]
                removed += 1
                continue
            normalized = str(group_id or "").strip()
            if normalized and not normalized.startswith("{") and normalized not in trust_groups:
                del self._cache[group_id]
                removed += 1
        return removed

    def _load_state(self, group_id: str) -> QQGroupAttentionState:
        attention_state = self._cache.get(group_id)
        state = QQGroupAttentionState.from_dict(
            attention_state if isinstance(attention_state, dict) else None, group_id=group_id
        )
        # 新群：启动相位时钟，从 0 开始随时间爬升
        if not isinstance(attention_state, dict):
            now = self._current_time()
            state.last_decay_at = now
            state.phase_started_at = now
        return state

    def get_state(self, group_id: str) -> QQGroupAttentionState:
        return self._load_state(str(group_id or "").strip())

    def _write_state(self, state: QQGroupAttentionState) -> None:
        self._cache[state.group_id] = state.to_dict()

    def _enabled(self) -> bool:
        return bool((self.plugin._qq_settings or {}).get("enable_group_attention", False))

    # ── 周期模型参数（可配）──

    def _setting(self, key: str, default: Any) -> Any:
        """读配置，尊重 0 值：缺失（未设置/None）才回退 default。

        旧写法 ``get(key, default) or default`` 会把保存的 0 当成 falsy 回退
        默认——例如 attention_consume_ratio=0（禁用回复消耗）被读回 0.10，
        attention_fall_rate=0 被读回 0.015，dashboard 报保存成功但运行时
        行为不变。
        """
        value = (self.plugin._qq_settings or {}).get(key)
        return default if value is None else value

    def _rise_rate(self) -> float:
        """rise 相位基础增速（/秒）。"""
        return max(0.0, float(self._setting("attention_base_rise_rate", 0.02)))

    def _message_boost(self) -> float:
        """单条消息对注意力的加成。"""
        return max(0.0, float(self._setting("attention_message_boost", 0.15)))

    def _keyword_boost_ratio(self) -> float:
        """分类命中（mention/关键词）时的额外加成倍率。"""
        return max(0.0, float(self._setting("attention_keyword_boost_ratio", 1.8)))

    def _message_gain(self) -> float:
        """批量消息计数时每条消息的注意力增益。"""
        return max(0.0, float(self._setting("group_attention_message_gain", 0.25)))

    def _honeymoon_seconds(self) -> int:
        """夺冠后继续上升的蜜月窗口（秒）。"""
        return max(0, int(self._setting("attention_honeymoon_seconds", 60)))

    def _fall_seconds(self) -> int:
        """进入 fall 相位至少持续多久才允许回升（秒）。"""
        return max(0, int(self._setting("attention_fall_seconds", 30)))

    def _fall_rate(self) -> float:
        """fall 相位回落速率（/秒）。"""
        return max(0.0, float(self._setting("attention_fall_rate", 0.015)))

    def _consume_ratio(self) -> float:
        """猫娘每次发言消耗注意力的比例（0~1）。"""
        return min(1.0, max(0.0, float(self._setting("attention_consume_ratio", 0.10))))

    def _max_attention(self) -> float:
        return float(self._setting("group_attention_max_score", 10.0))

    def _focus_threshold(self) -> float:
        return float(self._setting("group_attention_focus_threshold", 4.0))

    def _focus_send_threshold(self) -> float:
        """焦点群的发送门控线（默认 2.0）：低于焦点线、高于最低线。

        焦点线（_focus_threshold，默认 4.0）是「赢得焦点」的资格线。一旦成为
        焦点群，注意力会随回复消耗（_consume_ratio）和时间衰减；若发送门控
        也用焦点线，焦点群回一条就跌破线、立刻被门控——焦点形同虚设。这里用
        更低的「焦点保持线」作为发送门控，让焦点群在合理注意力水平上继续回应。
        """
        return float(self._setting("group_attention_focus_send_threshold", 2.0))

    def _minimum_threshold(self) -> float:
        return float(self._setting("group_attention_min_threshold", 1.0))

    # ── 相位推进 ──

    def _fatigue_rate_scale(self, fatigue: float) -> tuple[float, float]:
        """疲劳 → (rise 减速系数, fall 加速系数)。疲劳 0→(1.0,1.0)，100→(0.0,2.0)。"""
        fatigue = max(0.0, float(fatigue or 0.0))
        rise_scale = max(0.0, 1.0 - fatigue / 100.0)
        fall_scale = 1.0 + fatigue / 100.0
        return rise_scale, fall_scale

    def _advance_phase(self, state: QQGroupAttentionState, now: int, *, fatigue: float = 0.0) -> None:
        """按当前相位推进注意力，处理相位切换。幂等：基于 last_decay_at 差分。"""
        last = int(state.last_decay_at or state.last_message_at or state.last_boost_at or now)
        dt = max(0, now - last)
        if dt <= 0:
            return
        state.last_decay_at = now
        emo = _EMOTION_MULTIPLIER.get(state.emotion or "calm", 0.0)
        rise_scale, fall_scale = self._fatigue_rate_scale(fatigue)

        if state.phase == "fall":
            # 回落：正向情绪跌得慢，疲劳跌得快
            rate = self._fall_rate() * max(0.05, 1.0 - emo) * fall_scale
            state.attention_score = max(0.0, state.attention_score - rate * dt)
            # 回落满 T2 → 回升
            if now - state.phase_started_at >= self._fall_seconds():
                state.phase = "rise"
                state.phase_started_at = now
        else:
            # 上升：正向情绪涨得快，疲劳涨得慢。
            # 自然上升只作用于低于焦点线的群：未到线的群随时间涨到焦点线就停，
            # 不会一路打满到 max_attention。高于焦点线的分数来自消息/@/关键词
            # boost（update_on_message）或情绪抢焦点（set_emotion），rise 相位
            # 不叠加时间增长、也绝不砍掉——min(焦点线, 高分) 会把 8.0 直接砍回
            # 4.0，让 @bot 抢来的高注意力在 decay_all（每 5s）里瞬间蒸发。
            rate = self._rise_rate() * (1.0 + emo) * rise_scale
            if state.attention_score < self._focus_threshold():
                state.attention_score = min(self._focus_threshold(), state.attention_score + rate * dt)
            # 分数不低于焦点线且夺冠计时未记录 → 记录夺冠时刻（蜜月窗口从此刻起算）。
            # 覆盖「从低涨到线」和「本来就高于线」两种情况——旧条件 before < th
            # 在分数本来就高于 th 时永远不成立，导致 focus_acquired_at 记不上。
            if state.attention_score >= self._focus_threshold() and int(state.focus_acquired_at or 0) <= 0:
                state.focus_acquired_at = now
            # 到线夺冠后蜜月结束 → 回落；未到线的群继续上升不回落
            if (
                state.attention_score >= self._focus_threshold()
                and state.focus_acquired_at
                and now - state.focus_acquired_at >= self._honeymoon_seconds()
            ):
                state.phase = "fall"
                state.phase_started_at = now

    # ── 焦点选择 ──

    def _current_focus_group_id(self, states: list[QQGroupAttentionState]) -> str:
        # 焦点候选资格用焦点线 _focus_threshold()（默认 4.0）而非最低线 1.0：
        # 低于焦点线的群不参与焦点竞争，避免 1.1 分的群被当成焦点绕过门控。
        focused = [
            state for state in states
            if int(state.focus_acquired_at or 0) > 0 and float(state.attention_score) >= self._focus_threshold()
        ]
        if not focused:
            return ""
        return max(
            focused,
            key=lambda item: (
                int(item.focus_acquired_at or 0),
                int(item.last_focus_at or 0),
                float(item.attention_score),
            ),
        ).group_id

    def _top_candidate_group_id(self, states: list[QQGroupAttentionState], now: int) -> str:
        candidate = self._top_candidate_state(states, now)
        return candidate.group_id if candidate else ""

    def _top_candidate_state(self, states: list[QQGroupAttentionState], now: int) -> QQGroupAttentionState | None:
        # 焦点候选资格用焦点线 _focus_threshold()（默认 4.0）而非最低线 1.0，
        # 与「焦点 = 所有 attention ≥ 焦点线的群中最高者」的语义一致。
        eligible = [state for state in states if float(state.attention_score) >= self._focus_threshold()]
        if not eligible:
            return None
        return max(
            eligible,
            key=lambda item: (
                float(item.attention_score),
                int(item.last_message_at or 0),
                int(item.focus_acquired_at or 0),
            ),
        )

    def _held_focus_state(self, states: list[QQGroupAttentionState]) -> QQGroupAttentionState | None:
        """当前被**保持**的焦点：曾夺得焦点（focus_acquired_at>0）且分数仍 >= 发送保持线
        （``_focus_send_threshold``，默认 2.0）的群。

        焦点线（``_focus_threshold``，默认 4.0）是「赢得焦点」的资格线；一旦赢得，
        只要分数不掉破发送保持线（2.0）就继续持有——否则焦点群回复一次（分数被
        消耗到 2.x）就跌出焦点线、下一条消息被当非焦点拦下，发送门控形同虚设。
        多个曾夺得焦点的群取最近夺得的那个。无则返回 None。
        """
        held = [
            state for state in states
            if int(state.focus_acquired_at or 0) > 0
            and float(state.attention_score) >= self._focus_send_threshold()
        ]
        if not held:
            return None
        return max(
            held,
            key=lambda item: (
                int(item.last_focus_at or 0),
                int(item.focus_acquired_at or 0),
                float(item.attention_score),
            ),
        )

    def _choose_focus_state(
        self,
        states: list[QQGroupAttentionState],
        now: int,
        *,
        stamp_transition: bool = False,
    ) -> QQGroupAttentionState | None:
        if not states:
            return None
        # 新焦点候选：必须达到焦点线（_focus_threshold，默认 4.0）
        candidate = self._top_candidate_state(states, now)
        # 焦点保持：曾夺得焦点的群只要分数 >= 发送保持线（2.0）就继续持有，
        # 即使低于焦点线；只有更高分的挑战者（>= 焦点线）才能抢走。
        held = self._held_focus_state(states)
        if held is not None:
            if candidate is None or held.attention_score >= candidate.attention_score:
                return held
        # 无持有焦点：恢复「最高分群」回退（与 get_snapshot 的 states[0] 一致）。
        # 否则低于焦点线且无持有群时 _choose_focus_state 返回 None，而 get_snapshot
        # 却把最高分群报为焦点——分裂导致 _get_top_group_id 等其它调用者拿不到焦点，
        # 门控把最高分群的后续消息判成 non_focus。
        if candidate is None:
            return max(states, key=lambda s: float(s.attention_score))
        if stamp_transition:
            candidate.focus_acquired_at = now
            candidate.last_focus_reason = "highest_attention"
        return candidate

    def _normalize_state(self, state: QQGroupAttentionState) -> QQGroupAttentionState:
        max_attention = self._max_attention()
        state.attention_score = max(0.0, min(max_attention, float(state.attention_score)))
        return state

    # ── 核心：消息更新 ──

    @staticmethod
    def _detect_question(text: str) -> bool:
        """检测消息是否为问题（问号结尾或疑问词开头）。"""
        t = str(text or "").strip()
        if not t:
            return False
        if t.endswith(("?", "？")):
            return True
        question_prefixes = ("为什么", "怎么", "什么", "如何", "能不能", "可以", "有没有", "谁知道", "请问")
        return t.startswith(question_prefixes) or any(p in t for p in ("吗？", "吗?", "么？", "么?"))

    async def update_on_message(self, message: dict[str, Any]) -> dict[str, Any]:
        if not self._enabled():
            return self.get_snapshot()
        group_id = str(message.get("group_id") or "").strip()
        if not group_id:
            return self.get_snapshot()
        focus_group_id = self.get_focus_group_id()
        now = int(message.get("timestamp") or self._current_time())
        text = str(message.get("content") or message.get("text") or "").strip()
        is_at_bot = bool(message.get("is_at_bot"))

        state = self._apply_decay(self._load_state(group_id), now, is_focus=(group_id == focus_group_id))
        state.last_message_at = now
        state.total_interactions = min(99999, int(state.total_interactions or 0) + 1)

        # 消息加速增长：@ 最强，问题次之，普通消息基础加成
        boost = self._message_boost()
        if is_at_bot:
            boost *= 3.0
        elif self._detect_question(text):
            boost *= 1.5
        # 分类命中（mention/关键词）额外加成
        category = str(message.get("category") or "").strip()
        if not category and text:
            try:
                category = QQFeedbackClassifier.classify(
                    text, list((self.plugin._qq_settings or {}).get("backlog_labels") or [])
                )
            except Exception:
                category = ""
        if category and category != "chat":
            boost *= self._keyword_boost_ratio()
            state.last_focus_reason = category
        # fall 相位消息加成减弱：正在让位的群不会因继续刷屏而赖着不走
        if state.phase == "fall":
            boost *= 0.3
        # 疲劳减慢回升：高疲劳时消息增益被压缩
        fatigue_svc = getattr(self.plugin, "fatigue_service", None)
        if fatigue_svc:
            rise_scale, _ = self._fatigue_rate_scale(fatigue_svc.calculate_fatigue(f"group:{group_id}"))
            boost *= rise_scale
        state.attention_score = min(self._max_attention(), state.attention_score + boost)
        state.last_boost_at = now

        self._write_state(self._normalize_state(state))
        await self._persist()
        getattr(self.plugin, "_maybe_push_status_event", lambda: None)()  # 注意力变更 → SSE 通知前端
        return self.get_snapshot()

    async def update_on_message_count(self, group_id: str, *, message_count: int = 1) -> dict[str, Any]:
        if not self._enabled():
            return self.get_snapshot()
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return self.get_snapshot()
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        gain = max(0, int(message_count or 0)) * self._message_gain()
        fatigue_svc = getattr(self.plugin, "fatigue_service", None)
        if fatigue_svc:
            rise_scale, _ = self._fatigue_rate_scale(fatigue_svc.calculate_fatigue(f"group:{normalized_group_id}"))
            gain *= rise_scale
        state.attention_score = min(self._max_attention(), state.attention_score + gain)
        state.last_focus_reason = "message_recovery"
        state.last_boost_at = now
        self._write_state(self._normalize_state(state))
        await self._persist()
        return self.get_snapshot()

    # ── 回复消耗 ──

    async def update_on_reply(self, group_id: str, *, reply_message_id: str = "", at_user_id: str = "") -> dict[str, Any]:
        if not self._enabled():
            return self.get_snapshot()
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return self.get_snapshot()
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        state.last_reply_at = now
        # 猫娘发言消耗注意力：回复一次按比例扣减，并进入回落相位（耗光让位）
        consume = self._consume_ratio()
        state.attention_score = max(0.0, state.attention_score * (1.0 - consume))
        state.phase = "fall"
        state.phase_started_at = now
        state.last_focus_reason = "reply_consume"
        self._write_state(self._normalize_state(state))
        await self._persist()
        return self.get_snapshot()

    # ── 相位推进（幂等）──

    def _apply_decay(self, state: QQGroupAttentionState, now: int, *, is_focus: bool = False, fatigue: float = 0.0) -> QQGroupAttentionState:
        if now <= 0:
            now = self._current_time()
        self._advance_phase(state, now, fatigue=fatigue)
        return self._normalize_state(state)

    # ── 排序 ──

    def _sort_states(self, states: list[QQGroupAttentionState], now: int, *, focus_group_id: str = "") -> list[QQGroupAttentionState]:
        return sorted(
            states,
            key=lambda item: (
                1 if focus_group_id and item.group_id == focus_group_id else 0,
                float(item.attention_score),
                int(item.last_message_at or 0),
            ),
            reverse=True,
        )

    # ── Snapshot ──

    def _default_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled(),
            "focus_group_id": "",
            "focus_score": 0.0,
            "focus_reason": "",
            "dominant_dimension": "",
            "dimensions": {},
            "groups": [],
        }

    def get_snapshot(self) -> dict[str, Any]:
        now = self._current_time()
        # 第一步：对所有群做相位推进（此时还不知道谁是焦点，is_focus 统一用 False）
        states: list[QQGroupAttentionState] = []
        for group_id in self._normalized_groups():
            state = self._apply_decay(self._load_state(group_id), now, is_focus=False)
            self._write_state(state)
            states.append(state)
        # 第二步：选焦点（比注意力分）
        focus_state = self._choose_focus_state(states, now, stamp_transition=True)
        focus_group_id = focus_state.group_id if focus_state else ""
        if focus_state:
            self._write_state(focus_state)
        # 排序
        states = self._sort_states(states, now, focus_group_id=focus_group_id)
        if not states:
            return self._default_snapshot()
        focus = next((state for state in states if state.group_id == focus_group_id), states[0])
        return {
            "enabled": self._enabled(),
            "focus_group_id": focus.group_id,
            "focus_score": float(focus.attention_score),
            "focus_reason": focus.last_focus_reason,
            "dominant_dimension": focus.dominant_dimension(),
            "dimensions": focus.dimension_dict(),
            "groups": [state.to_dict() for state in states],
        }

    # ── 注意力上下文注入（供 LLM prompt 使用）──

    def get_attention_context(self, group_id: str) -> str:
        """生成注意力上下文文本，注入到系统提示中。"""
        snapshot = self.get_snapshot()
        is_focus = (snapshot.get("focus_group_id") == str(group_id))
        states = snapshot.get("groups") or []
        this_state = None
        for s in states:
            if str(s.get("group_id") or "") == str(group_id):
                this_state = s
                break

        parts: list[str] = []
        parts.append("## 当前群聊注意力状态")

        if is_focus:
            parts.append(f"这是你当前关注的焦点群（注意力 {snapshot.get('focus_score', 0):.1f}，相位 {snapshot.get('dominant_dimension', 'rise')}）")
            parts.append(f"主要原因: {snapshot.get('focus_reason', '') or '注意力最高'}")
        elif this_state:
            parts.append(f"这不是你当前关注的群（注意力 {float(this_state.get('attention_score', 0)):.1f}，相位 {this_state.get('phase', 'rise')}）")
        else:
            parts.append("此群暂无注意力数据。")

        emo = (this_state or {}).get("emotion", "calm") if this_state else "calm"
        if emo and emo != "calm":
            parts.append(f"当前情绪: {emo}")

        return "\n".join(parts)

    # ── 兼容旧接口 ──

    def get_focus_group_id(self) -> str:
        return str(self.get_snapshot().get("focus_group_id") or "")

    def get_focus_score(self) -> float:
        try:
            snapshot = self.get_snapshot()
            focus_id = str(snapshot.get("focus_group_id") or "")
            if not focus_id:
                return 0.0
            now = self._current_time()
            state = self._apply_decay(self._load_state(focus_id), now, is_focus=True)
            return float(state.attention_score)
        except Exception:
            return 0.0

    def _effective_focus_score(self, state: QQGroupAttentionState | None, now: int) -> float:
        if state is None:
            return 0.0
        return float(state.attention_score)

    def get_group_multiplier(self, group_id: str) -> float:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id or not self._enabled():
            return 1.0
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        focus_state = self._apply_decay(self._load_state(focus_group_id), now, is_focus=True) if focus_group_id else None
        focus_score = self._effective_focus_score(focus_state, now)
        group_score = self._effective_focus_score(state, now) if normalized_group_id == focus_group_id else float(state.attention_score)
        if focus_group_id and focus_group_id != normalized_group_id:
            gap = max(0.0, focus_score - group_score)
            if gap >= self._focus_threshold():
                return 0.0
            return max(0.35, 1.0 - min(0.6, gap / max(self._focus_threshold(), 1.0)))
        if group_score >= self._focus_threshold():
            return min(1.65, 1.0 + min(0.65, group_score / max(self._focus_threshold(), 1.0) * 0.25))
        if group_score <= self._minimum_threshold():
            return 0.8
        emo = state.emotion or "calm"
        return max(0.05, 1.0 + _EMOTION_MULTIPLIER.get(emo, 0.0))

    def should_focus_group(self, group_id: str) -> bool:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id or not self._enabled():
            return True
        focus_group_id = self.get_focus_group_id()
        if not focus_group_id or focus_group_id == normalized_group_id:
            return True
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=False)
        focus_state = self._apply_decay(self._load_state(focus_group_id), now, is_focus=True)
        focus_score = self._effective_focus_score(focus_state, now)
        return float(state.attention_score) + self._minimum_threshold() >= focus_score

    def mark_focus(self, group_id: str) -> None:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        state = self._load_state(normalized_group_id)
        now = self._current_time()
        current_id = self._current_focus_group_id([self._load_state(gid) for gid in self._normalized_groups()])
        state.last_focus_at = now
        if current_id != normalized_group_id or int(state.focus_acquired_at or 0) <= 0:
            state.focus_acquired_at = now
            state.phase = "rise"
            state.phase_started_at = now
        self._write_state(state)
        getattr(self.plugin, "_maybe_push_status_event", lambda: None)()  # 焦点变更 → SSE 通知前端

    def wake_boost(self, group_id: str) -> None:
        """叫醒时给一个注意力启动值，确保能突破焦点阈值。"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        state = self._load_state(normalized_group_id)
        if state.attention_score < self._focus_threshold():
            state.attention_score = max(state.attention_score, self._focus_threshold() * 0.75)
            state.phase = "rise"
            state.phase_started_at = self._current_time()
            self._write_state(state)
            self.plugin._emit_log("INFO", f"[Attention] 唤醒 boost: 群{normalized_group_id} score={state.attention_score:.1f}")
            getattr(self.plugin, "_maybe_push_status_event", lambda: None)()  # 注意力唤醒 → SSE 通知前端

    def get_last_focus_at(self, group_id: str) -> int:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return 0
        return int(self._load_state(normalized_group_id).last_focus_at)

    def _get_top_group_id(self) -> str:
        now = self._current_time()
        states = [self._load_state(gid) for gid in self._normalized_groups()]
        focus = self._choose_focus_state(states, now, stamp_transition=False)
        return focus.group_id if focus else ""

    # ── 全局休眠判定 ──

    def is_global_sleep(self) -> bool:
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        for group_id in self._normalized_groups():
            state = self._apply_decay(self._load_state(group_id), now, is_focus=(group_id == focus_group_id))
            if float(state.attention_score) >= self._minimum_threshold():
                return False
        if not self._normalized_groups():
            return False
        return True

    def get_focus_group(self) -> str | None:
        if self.is_global_sleep():
            return None
        snapshot = self.get_snapshot()
        focus_id = str(snapshot.get("focus_group_id") or "")
        return focus_id if focus_id else None

    # ── 情绪 ──

    async def set_emotion(self, group_id: str, emotion: str) -> None:
        """LLM 回复中的 <feeling> 标签更新情绪状态。

        强情绪直接触发焦点切换：arguing/proud 抢焦点，sulking/embarrassed 让焦点。
        """
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        if emotion not in _EMOTION_MULTIPLIER:
            return
        state = self._load_state(normalized_group_id)
        now = self._current_time()
        state.emotion = emotion
        state.emotion_updated_at = now
        # emotion_display 供前端展示，停留 120s 比逻辑衰减更久
        state.emotion_display = emotion
        state.emotion_display_until = now + 120

        if emotion in _EMOTION_FORCE_FOCUS:
            # 抢焦点：把注意力抬到焦点线之上并进入蜜月上升
            state.last_focus_at = now
            state.focus_acquired_at = now
            state.last_focus_reason = f"emotion:{emotion}"
            state.attention_score = max(state.attention_score, self._focus_threshold())
            state.phase = "rise"
            state.phase_started_at = now
            self.plugin._emit_log("INFO", f"[Emotion] 群{normalized_group_id} 抢焦点: {emotion} score={state.attention_score:.1f}")
        elif emotion in _EMOTION_DROP_FOCUS:
            # 让焦点：把注意力压到目标线下并进入回落
            floor = self._minimum_threshold() if emotion == "sulking" else self._focus_threshold()
            state.attention_score = min(state.attention_score, floor)
            state.phase = "fall"
            state.phase_started_at = now
            self.plugin._emit_log("INFO", f"[Emotion] 群{normalized_group_id} 让焦点: {emotion} score={state.attention_score:.1f}")

        self._write_state(state)
        await self._persist()
        self.plugin._emit_log("INFO", f"[Emotion] 群{normalized_group_id} 情绪: {emotion}")
        getattr(self.plugin, "_maybe_push_status_event", lambda: None)()  # 情绪变更 → SSE 通知前端

    def _decay_emotion(self, state: QQGroupAttentionState, now: int) -> None:
        """情绪自然衰减：30秒无新情绪则向 calm 方向降温一级。"""
        if state.emotion == "calm":
            return
        elapsed = now - state.emotion_updated_at
        if elapsed < _EMOTION_DECAY_SECONDS:
            return
        order = _EMOTION_DECAY_ORDER
        idx = order.index(state.emotion) if state.emotion in order else -1
        if idx < 0:
            state.emotion = "calm"
        elif state.emotion in ("arguing", "annoyed", "playful", "curious"):
            if idx + 1 >= order.index("calm"):
                state.emotion = "calm"
            else:
                state.emotion = order[idx + 1]
        else:
            calm_idx = order.index("calm")
            if idx - 1 <= calm_idx:
                state.emotion = "calm"
            else:
                state.emotion = order[idx - 1]
        state.emotion_updated_at = now
        state.emotion_display = state.emotion

    # ── 手动增减注意力 ──

    async def boost_attention(self, group_id: str, amount: float, reason: str = "") -> None:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id or amount <= 0:
            return
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        state.attention_score = min(self._max_attention(), state.attention_score + amount)
        state.last_boost_at = now
        state.last_focus_reason = reason or "manual_boost"
        self._write_state(self._normalize_state(state))
        await self._persist()

    async def consume_attention(self, group_id: str, amount: float, reason: str = "") -> None:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id or amount <= 0:
            return
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        state.attention_score = max(0.0, state.attention_score - amount)
        state.last_reply_at = now
        state.last_focus_reason = reason or "manual_consume"
        self._write_state(self._normalize_state(state))
        await self._persist()

    # ── 后台衰减循环 ──

    async def start_decay_loop(self, interval_seconds: float = 5.0) -> None:
        self._decay_task = asyncio.create_task(self._decay_loop(interval_seconds))

    async def stop_decay_loop(self) -> None:
        task = getattr(self, "_decay_task", None)
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _decay_loop(self, interval_seconds: float) -> None:
        while True:
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
            await self.decay_all()

    async def decay_all(self) -> None:
        if not self._enabled():
            return
        now = self._current_time()
        old_focus_id = self._get_top_group_id()
        fatigue_svc = getattr(self.plugin, "fatigue_service", None)
        for group_id in self._normalized_groups():
            state = self._load_state(group_id)
            # emotion_display 到期 → 重置为 calm
            if now > state.emotion_display_until and state.emotion_display != "calm":
                state.emotion_display = "calm"
            fatigue = float(fatigue_svc.calculate_fatigue(f"group:{group_id}") or 0.0) if fatigue_svc else 0.0
            state = self._apply_decay(state, now, fatigue=fatigue)
            self._decay_emotion(state, now)
            self._write_state(state)
        # 检查焦点是否变化，自动设置 focus_acquired_at（蜜月计时起点）
        new_focus_id = self._get_top_group_id()
        if new_focus_id and new_focus_id != old_focus_id:
            new_state = self._load_state(new_focus_id)
            new_state.focus_acquired_at = now
            new_state.phase = "rise"
            new_state.phase_started_at = now
            self._write_state(new_state)
        await self._persist()

    async def _persist(self) -> None:
        if not getattr(self.plugin, "backlog_store", None):
            return
        self.cleanup_stale_cache()
        state = await self.plugin.backlog_store.load()
        state["group_attention_state"] = dict(self._cache)
        await self.plugin.backlog_store.save(state)
