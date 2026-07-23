from __future__ import annotations

import asyncio
import time as _time
from dataclasses import dataclass, field
from typing import Any

from .feedback_classifier import QQFeedbackClassifier

# ── 多维注意力权重（总和不必为 1，最终归一化）──
_DIMENSION_WEIGHTS = {
    "urgency": 0.30,    # 紧急度：@次数、点名频率、问题检测
    "interest": 0.30,   # 兴趣度：话题深度、追问链、与 AI 人物设定相关性
    "momentum": 0.25,   # 动量：消息频率、群活跃度
    "intimacy": 0.15,   # 亲密度：历史交互次数、信任用户匹配
}

_DIMENSION_LABEL = {
    "urgency": "紧急度",
    "interest": "兴趣度",
    "momentum": "动量",
    "intimacy": "亲密度",
}

_DIMENSION_ORDER = ("urgency", "interest", "momentum", "intimacy")

# ── 情绪 → 注意力倍率偏移 ──
_EMOTION_MULTIPLIER: dict[str, float] = {
    "arguing": 0.4,      # 上头，持续关注
    "proud": 0.3,        # 赢了想炫耀
    "annoyed": 0.2,      # 不爽但还在意
    "playful": 0.15,     # 玩得开心
    "curious": 0.1,      # 感兴趣
    "calm": 0.0,         # 正常
    "sad": -0.1,         # 难过不太想聊
    "embarrassed": -0.2, # 不好意思想溜
    "sulking": -0.3,     # 吵不过降温
}
_EMOTION_DECAY_ORDER = ["arguing", "annoyed", "playful", "curious", "calm", "sad", "embarrassed", "sulking"]
_EMOTION_DECAY_SECONDS = 30  # 30秒无新情绪则降温一级


@dataclass(slots=True)
class QQGroupAttentionState:
    group_id: str
    # ── 综合分数（兼容旧接口，由四维度加权计算）──
    attention_score: float = 0.0
    # ── 四维度 ──
    urgency: float = 0.0       # 0~1
    interest: float = 0.0      # 0~1
    momentum: float = 0.0      # 0~1
    intimacy: float = 0.0      # 0~1
    # ── 元数据 ──
    at_count: int = 0          # 近期 @ 次数
    question_count: int = 0    # 近期问题次数（?、？结尾或疑问词）
    message_count_window: int = 0  # 近期消息数（滑动窗口）
    total_interactions: int = 0    # 历史总交互次数
    matching_user_count: int = 0   # 匹配信任用户/管理员的发言人数
    # ── 兼容旧字段 ──
    last_boost_at: int = 0
    last_decay_at: int = 0
    last_message_at: int = 0
    last_reply_at: int = 0
    recent_message_count: int = 0
    keyword_boost_score: float = 0.0
    # ── 情绪 ──
    emotion: str = "calm"              # calm/playful/curious/annoyed/arguing/proud/embarrassed/sad/sulking
    emotion_updated_at: int = 0
    focus_lock_until: int = 0
    focus_cooldown_until: int = 0
    last_focus_reason: str = ""
    last_message_id: str = ""
    last_sender_id: str = ""
    last_focus_at: int = 0
    focus_acquired_at: int = 0
    # ignored_messages removed — now uses unified backlog_store

    def _compute_weighted_score(self) -> float:
        """四维度加权计算综合注意力分数（0~10）。"""
        raw = (
            _DIMENSION_WEIGHTS["urgency"] * float(self.urgency)
            + _DIMENSION_WEIGHTS["interest"] * float(self.interest)
            + _DIMENSION_WEIGHTS["momentum"] * float(self.momentum)
            + _DIMENSION_WEIGHTS["intimacy"] * float(self.intimacy)
        )
        return max(0.0, min(10.0, raw * 10.0))

    def recompute_score(self) -> float:
        """重新计算综合分数并写回 attention_score。"""
        self.attention_score = self._compute_weighted_score()
        return self.attention_score

    def dimension_dict(self) -> dict[str, float]:
        """返回四维度明细（用于 prompt 注入和 UI 展示）。"""
        return {
            "urgency": float(self.urgency),
            "interest": float(self.interest),
            "momentum": float(self.momentum),
            "intimacy": float(self.intimacy),
        }

    def dimension_label(self, key: str) -> str:
        return _DIMENSION_LABEL.get(key, key)

    def dominant_dimension(self) -> str:
        """返回最高的维度名（用于解释焦点原因）。"""
        d = self.dimension_dict()
        return max(d, key=lambda k: d[k]) if d else "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "attention_score": float(self.attention_score),
            "urgency": float(self.urgency),
            "interest": float(self.interest),
            "momentum": float(self.momentum),
            "intimacy": float(self.intimacy),
            "at_count": int(self.at_count),
            "question_count": int(self.question_count),
            "message_count_window": int(self.message_count_window),
            "total_interactions": int(self.total_interactions),
            "matching_user_count": int(self.matching_user_count),
            "last_boost_at": int(self.last_boost_at),
            "last_decay_at": int(self.last_decay_at),
            "last_message_at": int(self.last_message_at),
            "last_reply_at": int(self.last_reply_at),
            "recent_message_count": int(self.recent_message_count),
            "keyword_boost_score": float(self.keyword_boost_score),
            "focus_lock_until": int(self.focus_lock_until),
            "focus_cooldown_until": int(self.focus_cooldown_until),
            "last_focus_reason": str(self.last_focus_reason or ""),
            "last_message_id": str(self.last_message_id or ""),
            "last_sender_id": str(self.last_sender_id or ""),
            "last_focus_at": int(self.last_focus_at),
            "focus_acquired_at": int(self.focus_acquired_at),
            "emotion": str(self.emotion or "calm"),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None, *, group_id: str) -> "QQGroupAttentionState":
        data = dict(payload or {})
        st = cls(
            group_id=group_id,
            attention_score=float(data.get("attention_score") or 0.0),
            urgency=float(data.get("urgency") or 0.0),
            interest=float(data.get("interest") or 0.0),
            momentum=float(data.get("momentum") or 0.0),
            intimacy=float(data.get("intimacy") or 0.0),
            at_count=int(data.get("at_count") or 0),
            question_count=int(data.get("question_count") or 0),
            message_count_window=int(data.get("message_count_window") or 0),
            total_interactions=int(data.get("total_interactions") or 0),
            matching_user_count=int(data.get("matching_user_count") or 0),
            last_boost_at=int(data.get("last_boost_at") or 0),
            last_decay_at=int(data.get("last_decay_at") or 0),
            last_message_at=int(data.get("last_message_at") or 0),
            last_reply_at=int(data.get("last_reply_at") or 0),
            recent_message_count=int(data.get("recent_message_count") or 0),
            keyword_boost_score=float(data.get("keyword_boost_score") or 0.0),
            focus_lock_until=int(data.get("focus_lock_until") or 0),
            focus_cooldown_until=int(data.get("focus_cooldown_until") or 0),
            last_focus_reason=str(data.get("last_focus_reason") or ""),
            last_message_id=str(data.get("last_message_id") or ""),
            last_sender_id=str(data.get("last_sender_id") or ""),
            last_focus_at=int(data.get("last_focus_at") or 0),
            focus_acquired_at=int(data.get("focus_acquired_at") or 0),
            emotion=str(data.get("emotion") or "calm"),
            emotion_updated_at=int(data.get("emotion_updated_at") or 0),
        )
        # 从旧数据迁移：如果没有维度数据但有关键词加分，给 urgency 初值
        if float(st.urgency) <= 0 and float(st.interest) <= 0 and float(st.momentum) <= 0 and float(st.intimacy) <= 0:
            if float(st.attention_score) > 0:
                st.urgency = min(1.0, float(st.attention_score) / 20.0)
                st.momentum = min(1.0, float(st.attention_score) / 30.0)
                st.intimacy = 0.2
                st.recompute_score()
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

    def _current_time(self) -> int:
        return int(_time.time())

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
        state = QQGroupAttentionState.from_dict(attention_state if isinstance(attention_state, dict) else None, group_id=group_id)
        if float(state.attention_score) < 1.0 and not isinstance(attention_state, dict):
            state.attention_score = 1.0
            state.momentum = 0.1
            state.intimacy = 0.1
        return state

    def get_state(self, group_id: str) -> QQGroupAttentionState:
        return self._load_state(str(group_id or "").strip())

    def _write_state(self, state: QQGroupAttentionState) -> None:
        self._cache[state.group_id] = state.to_dict()

    def _enabled(self) -> bool:
        return bool((self.plugin._qq_settings or {}).get("enable_group_attention", False))

    def _decay_per_second(self) -> float:
        return float((self.plugin._qq_settings or {}).get("group_attention_decay_per_second", 0.02) or 0.02)

    def _message_recovery(self) -> float:
        return float((self.plugin._qq_settings or {}).get("group_attention_message_recovery", 0.6) or 0.6)

    def _reply_penalty(self) -> float:
        return float((self.plugin._qq_settings or {}).get("group_attention_reply_penalty", 1.3) or 1.3)

    def _keyword_boost_scale(self) -> float:
        return float((self.plugin._qq_settings or {}).get("group_attention_keyword_boost_scale", 2.5) or 2.5)

    def _focus_lock_seconds(self) -> int:
        return max(0, int((self.plugin._qq_settings or {}).get("group_attention_focus_lock_seconds", 120) or 120))

    def _max_attention(self) -> float:
        return float((self.plugin._qq_settings or {}).get("group_attention_max_score", 10.0) or 10.0)

    def _focus_threshold(self) -> float:
        return float((self.plugin._qq_settings or {}).get("group_attention_focus_threshold", 4.0) or 4.0)

    def _minimum_threshold(self) -> float:
        return float((self.plugin._qq_settings or {}).get("group_attention_min_threshold", 1.0) or 1.0)

    def _message_gain(self) -> float:
        return float((self.plugin._qq_settings or {}).get("group_attention_message_gain", 0.25) or 0.25)

    def _focus_cooldown_seconds(self) -> int:
        return max(10, int((self.plugin._qq_settings or {}).get("group_attention_focus_cooldown_seconds", 60) or 60))

    def _focus_rise_seconds(self) -> int:
        return max(0, int((self.plugin._qq_settings or {}).get("group_attention_focus_rise_seconds", 120) or 120))

    def _normalize_state(self, state: QQGroupAttentionState) -> QQGroupAttentionState:
        max_attention = self._max_attention()
        state.attention_score = max(0.0, min(max_attention, float(state.attention_score)))
        state.keyword_boost_score = max(0.0, float(state.keyword_boost_score))
        state.recent_message_count = max(0, int(state.recent_message_count))
        # 维度裁剪到 0~1
        for dim in _DIMENSION_ORDER:
            setattr(state, dim, max(0.0, min(1.0, float(getattr(state, dim, 0.0)))))
        return state

    # ── 维度更新辅助 ──

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

    def _estimate_intimacy(self, group_id: str, sender_id: str) -> float:
        """根据发送者是否在信任用户列表中估计亲密度增益。"""
        if not self.plugin.permission_mgr:
            return 0.0
        level = self.plugin.permission_mgr.get_permission_level(sender_id)
        if level == "admin":
            return 0.3
        if level == "trusted":
            return 0.2
        return 0.0

    # ── 核心：消息更新四维度 ──

    async def update_on_message(self, message: dict[str, Any]) -> dict[str, Any]:
        if not self._enabled():
            return self.get_snapshot()
        group_id = str(message.get("group_id") or "").strip()
        if not group_id:
            return self.get_snapshot()

        # 该群正在睡眠中 → 不累计注意力（逐群判定，不影响其他群）
        fatigue = getattr(self.plugin, "fatigue_service", None)
        if fatigue and fatigue.is_sleeping(f"group:{group_id}"):
            return self.get_snapshot()

        focus_group_id = self.get_focus_group_id()
        now = int(message.get("timestamp") or self._current_time())
        text = str(message.get("content") or message.get("text") or "").strip()
        is_at_bot = bool(message.get("is_at_bot"))
        sender_id = str(message.get("user_id") or "")

        state = self._apply_decay(self._load_state(group_id), now, is_focus=(group_id == focus_group_id))
        state.last_message_at = now
        state.last_message_id = str(message.get("message_id") or "")
        state.last_sender_id = sender_id
        state.recent_message_count = min(9999, int(state.recent_message_count or 0) + 1)
        state.message_count_window = min(99, int(state.message_count_window or 0) + 1)
        state.total_interactions = min(99999, int(state.total_interactions or 0) + 1)

        # ── urgency 更新 ──
        if is_at_bot:
            state.at_count = min(99, int(state.at_count or 0) + 1)
            state.urgency = min(1.0, float(state.urgency) + 0.35)
        if self._detect_question(text):
            state.question_count = min(99, int(state.question_count or 0) + 1)
            state.urgency = min(1.0, float(state.urgency) + 0.15)

        # ── interest 更新 ──
        her_name = getattr(getattr(self.plugin, "reply_context_node", None), "_her_name", "") or ""
        if her_name and her_name.lower() in str(text or "").lower():
            state.interest = min(1.0, float(state.interest) + 0.20)

        # ── momentum 更新 ──
        state.momentum = min(1.0, state.message_count_window / 20.0)

        # ── intimacy 更新 ──
        intimacy_gain = self._estimate_intimacy(group_id, sender_id)
        if intimacy_gain > 0:
            state.intimacy = min(1.0, float(state.intimacy) + intimacy_gain)
            state.matching_user_count = min(99, int(state.matching_user_count or 0) + 1)

        # ── 分类/关键词处理（注入 urgency 和 interest）──
        category = str(message.get("category") or "").strip()
        if not category and text:
            category = QQFeedbackClassifier.classify(text, list((self.plugin._qq_settings or {}).get("backlog_labels") or []))
        if category == "mention" and not is_at_bot:
            category = "chat"
        if category == "blacklist":
            category = "chat"
        if category and category != "chat":
            label_priority = 0.0
            for item in list((self.plugin._qq_settings or {}).get("backlog_labels") or []):
                if not isinstance(item, dict):
                    continue
                if str(item.get("id") or "").strip() != category:
                    continue
                try:
                    label_priority = max(0.0, float(item.get("priority") or 0)) / 25.0
                except Exception:
                    label_priority = 0.0
                break
            if label_priority > 0:
                boost = label_priority * self._keyword_boost_scale()
                if category == "mention" and group_id == focus_group_id:
                    boost *= 0
                state.keyword_boost_score += label_priority
                state.urgency = min(1.0, float(state.urgency) + boost * 0.15)
                state.interest = min(1.0, float(state.interest) + boost * 0.12)
                state.last_boost_at = now
                state.last_focus_reason = category

        # 重新计算综合分
        state.recompute_score()
        self._write_state(self._normalize_state(state))
        await self._persist()
        return self.get_snapshot()

    # ── 回复消耗（重置维度）──

    async def update_on_reply(self, group_id: str, *, reply_message_id: str = "", at_user_id: str = "") -> dict[str, Any]:
        if not self._enabled():
            return self.get_snapshot()
        # 该群正在睡眠中 → 不更新注意力（逐群判定）
        fatigue = getattr(self.plugin, "fatigue_service", None)
        if fatigue and fatigue.is_sleeping(f"group:{group_id}"):
            return self.get_snapshot()
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return self.get_snapshot()
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        state.last_reply_at = now
        state.last_message_id = str(reply_message_id or state.last_message_id or "")
        state.last_sender_id = str(at_user_id or state.last_sender_id or "")
        # 回复消耗：urgency 优先衰减（已处理），再降各个维度
        state.urgency = max(0.0, float(state.urgency) - 0.25)
        state.interest = max(0.0, float(state.interest) - 0.10)
        state.momentum = max(0.0, float(state.momentum) * 0.85)
        state.keyword_boost_score = max(0.0, state.keyword_boost_score * 0.9)
        state.at_count = max(0, int(state.at_count) - 1)
        state.last_focus_reason = "reply_penalty"
        state.focus_lock_until = now + self._focus_lock_seconds() if self._focus_lock_seconds() > 0 else 0
        state.recompute_score()
        self._write_state(self._normalize_state(state))
        await self._persist()
        return self.get_snapshot()

    async def update_on_message_count(self, group_id: str, *, message_count: int = 1) -> dict[str, Any]:
        if not self._enabled():
            return self.get_snapshot()
        fatigue = getattr(self.plugin, "fatigue_service", None)
        if fatigue and fatigue.is_sleeping(f"group:{group_id}"):
            return self.get_snapshot()
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return self.get_snapshot()
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        gain = max(0, int(message_count or 0)) * self._message_gain()
        state.attention_score += gain
        state.recent_message_count = min(9999, int(state.recent_message_count or 0) + max(0, int(message_count or 0)))
        state.momentum = min(1.0, float(state.momentum) + gain * 0.05)
        state.last_focus_reason = "message_recovery"
        state.recompute_score()
        self._write_state(self._normalize_state(state))
        await self._persist()
        return self.get_snapshot()

    # ── 衰减 ──

    def _apply_decay(self, state: QQGroupAttentionState, now: int, *, is_focus: bool = False, fatigue: float = 0.0) -> QQGroupAttentionState:
        if now <= 0:
            now = self._current_time()
        last_decay_at = int(state.last_decay_at or state.last_message_at or state.last_boost_at or now)
        elapsed = max(0, now - last_decay_at)
        if elapsed <= 0:
            return state
        in_cooldown = bool(state.focus_cooldown_until and state.focus_cooldown_until > now)
        # fatigue multiplier
        if fatigue > 80:
            fatigue_mult = 2.5
        elif fatigue > 60:
            fatigue_mult = 1.8
        elif fatigue > 40:
            fatigue_mult = 1.3
        elif fatigue < 15:
            fatigue_mult = 0.7
        else:
            fatigue_mult = 1.0
        decay_rate = self._decay_per_second()
        rise_seconds = self._focus_rise_seconds()
        if is_focus and not in_cooldown and rise_seconds > 0 and state.focus_acquired_at > 0 and (now - state.focus_acquired_at) < rise_seconds and fatigue <= 75:
            # 焦点群上升期：维度向 1.0 上升
            rate = (1.0 / rise_seconds) / fatigue_mult
            for dim in _DIMENSION_ORDER:
                val = float(getattr(state, dim, 0.0))
                setattr(state, dim, min(1.0, val + elapsed * rate))
        elif is_focus or in_cooldown:
            # 焦点群 / 冷却群：各维度衰减（burnout）
            for dim in _DIMENSION_ORDER:
                val = float(getattr(state, dim, 0.0))
                setattr(state, dim, max(0.0, val - elapsed * decay_rate * 0.5 * fatigue_mult))
            # 消息窗口滑动衰减
            state.message_count_window = max(0, int(state.message_count_window) - max(0, elapsed // 10))
        else:
            # 非焦点群：恢复（不变）
            recovery = elapsed * 0.01
            for dim in _DIMENSION_ORDER:
                val = float(getattr(state, dim, 0.0))
                target = 0.3
                setattr(state, dim, min(target, val + recovery * 0.3))
        state.last_decay_at = now
        state.recompute_score()
        return self._normalize_state(state)

    # ── 排序 ──

    def _sort_states(self, states: list[QQGroupAttentionState]) -> list[QQGroupAttentionState]:
        return sorted(states, key=lambda item: (item.attention_score, item.last_message_at, item.keyword_boost_score), reverse=True)

    def _get_top_group_id(self) -> str:
        states = [self._load_state(gid) for gid in self._normalized_groups()]
        states = self._sort_states(states)
        return states[0].group_id if states else ""

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
        states: list[QQGroupAttentionState] = [self._load_state(gid) for gid in self._normalized_groups()]
        states = self._sort_states(states)
        if not states:
            return self._default_snapshot()
        focus = states[0]
        return {
            "enabled": self._enabled(),
            "focus_group_id": focus.group_id,
            "focus_score": float(focus.attention_score),
            "focus_reason": focus.last_focus_reason,
            "dominant_dimension": focus.dominant_dimension(),
            "dimensions": focus.dimension_dict(),
            "groups": [state.to_dict() for state in states],
        }

    # ── 多维度上下文注入（供 LLM prompt 使用）──

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
            dominant = snapshot.get("dominant_dimension", "")
            dims = snapshot.get("dimensions") or {}
            dim_str = ", ".join(
                f"{_DIMENSION_LABEL.get(d, d)} {dims.get(d, 0):.1f}"
                for d in _DIMENSION_ORDER
            )
            parts.append(f"这是你当前关注的焦点群（综合分 {snapshot.get('focus_score', 0):.1f}）")
            parts.append(f"维度: {dim_str}")
            parts.append(f"主要原因: {snapshot.get('focus_reason', '') or '活跃度'}")
        elif this_state:
            dims = {d: float(this_state.get(d, 0)) for d in _DIMENSION_ORDER}
            dim_str = ", ".join(
                f"{_DIMENSION_LABEL.get(d, d)} {dimns[d]:.1f}" if (dimns := dims) else ""
                for d in _DIMENSION_ORDER
            )
            parts.append(f"这不是你当前关注的群（综合分 {float(this_state.get('attention_score', 0)):.1f}）")
            parts.append(f"维度: {dim_str}")
        else:
            parts.append("此群暂无注意力数据。")

        # 情绪状态（从该群的状态中读取）
        emo = (this_state or {}).get("emotion", "calm") if this_state else "calm"
        if emo and emo != "calm":
            parts.append(f"当前情绪: {emo}")

        return "\n".join(parts)

    # ── 兼容旧接口（内部计算不变）──

    def get_focus_group_id(self) -> str:
        return str(self.get_snapshot().get("focus_group_id") or "")

    def get_focus_score(self) -> float:
        try:
            return float(self.get_snapshot().get("focus_score") or 0.0)
        except Exception:
            return 0.0

    def get_group_multiplier(self, group_id: str) -> float:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id or not self._enabled():
            return 1.0
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        focus_score = self.get_focus_score()
        group_score = float(state.attention_score)
        if focus_group_id and focus_group_id != normalized_group_id:
            gap = max(0.0, focus_score - group_score)
            if gap >= self._focus_threshold():
                return 0.05
            return max(0.35, 1.0 - min(0.6, gap / max(self._focus_threshold(), 1.0)))
        if state.focus_lock_until and state.focus_lock_until > now:
            return 1.35
        if group_score >= self._focus_threshold():
            return min(1.65, 1.0 + min(0.65, group_score / max(self._focus_threshold(), 1.0) * 0.25))
        if group_score <= self._minimum_threshold():
            return 0.8
        # 情绪偏移
        emo = state.emotion or "calm"
        return max(0.05, 1.0 + _EMOTION_MULTIPLIER.get(emo, 0.0))

    def should_focus_group(self, group_id: str) -> bool:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id or not self._enabled():
            return True
        focus_group_id = self.get_focus_group_id()
        if not focus_group_id or focus_group_id == normalized_group_id:
            return True
        state = self._apply_decay(self._load_state(normalized_group_id), self._current_time(), is_focus=False)
        focus_state = self._apply_decay(self._load_state(focus_group_id), self._current_time(), is_focus=True)
        return float(state.attention_score) + self._minimum_threshold() >= float(focus_state.attention_score)

    # ── 回溯补回（ignored message tracking）──

        # ── 回溯补回：已统一使用 backlog_store ──

    def mark_focus(self, group_id: str) -> None:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        now = self._current_time()
        state = self._load_state(normalized_group_id)
        state.last_focus_at = now
        state.focus_acquired_at = now
        state.focus_cooldown_until = 0
        self._write_state(state)

    def set_focus_cooldown(self, group_id: str) -> None:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        state = self._load_state(normalized_group_id)
        state.focus_cooldown_until = self._current_time() + self._focus_cooldown_seconds()
        self._write_state(state)

    def wake_boost(self, group_id: str) -> None:
        """叫醒时给一个注意力启动值，确保突破全局休眠阈值（1.0）。"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        state = self._load_state(normalized_group_id)
        if state.attention_score < 2.0:
            state.urgency = max(state.urgency, 0.3)
            state.interest = max(state.interest, 0.2)
            state.recompute_score()
            self._write_state(state)
            self.plugin._emit_log("INFO", f"[Attention] 唤醒 boost: 群{normalized_group_id} score={state.attention_score:.1f}")

    def set_emotion(self, group_id: str, emotion: str) -> None:
        """LLM 回复中的 <feeling> 标签更新情绪状态。"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        if emotion not in _EMOTION_MULTIPLIER:
            return
        state = self._load_state(normalized_group_id)
        state.emotion = emotion
        state.emotion_updated_at = self._current_time()
        self._write_state(state)
        self.plugin._emit_log("INFO", f"[Emotion] 群{normalized_group_id} 情绪: {emotion}")

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
            # 正向情绪 → calm
            state.emotion = "calm" if idx + 1 >= order.index("calm") else order[idx + 1]
        else:
            # 负向情绪 → calm
            state.emotion = "calm"
        state.emotion_updated_at = now

    def get_last_focus_at(self, group_id: str) -> int:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return 0
        return int(self._load_state(normalized_group_id).last_focus_at)

    # ── 全局休眠判定 ──

    def is_global_sleep(self) -> bool:
        # 全局休眠 = 所有群都睡眠中。单个群被叫醒不触发全局休眠。
        fatigue = getattr(self.plugin, "fatigue_service", None)
        if fatigue:
            for group_id in self._normalized_groups():
                if not fatigue.check_sleeping(f"group:{group_id}"):
                    return False  # 至少有一个群醒着
            if not self._normalized_groups():
                return False
            return True  # 所有群都睡了
        # 无疲劳服务 → 降级到注意力阈值判定
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

    # ── 手动增减注意力 ──

    async def boost_attention(self, group_id: str, amount: float, reason: str = "") -> None:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id or amount <= 0:
            return
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        state.attention_score += amount
        state.last_boost_at = now
        state.last_focus_reason = reason or "manual_boost"
        state.recompute_score()
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
        state.recompute_score()
        self._write_state(self._normalize_state(state))
        await self._persist()

    # ── 后台衰减循环 ──

    async def start_decay_loop(self, interval_seconds: float = 5.0) -> None:
        import asyncio
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
        import asyncio
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
            # 睡眠中的群不参与注意力竞争
            if fatigue_svc and fatigue_svc.check_sleeping(f"group:{group_id}"):
                state = self._load_state(group_id)
                state.attention_score = 0.0
                self._write_state(state)
                continue
            fatigue = float(fatigue_svc.calculate_fatigue(f"group:{group_id}") or 0.0) if fatigue_svc else 0.0
            state = self._load_state(group_id)
            state = self._apply_decay(state, now, is_focus=(group_id == old_focus_id), fatigue=fatigue)
            self._decay_emotion(state, now)
            self._write_state(state)
        # 检查焦点是否变化，自动设置 focus_acquired_at
        new_focus_id = self._get_top_group_id()
        if new_focus_id and new_focus_id != old_focus_id:
            new_state = self._load_state(new_focus_id)
            new_state.focus_acquired_at = now
            self._write_state(new_state)
        await self._persist()

    async def _persist(self) -> None:
        if not getattr(self.plugin, "backlog_store", None):
            return
        self.cleanup_stale_cache()
        state = await self.plugin.backlog_store.load()
        state["group_attention_state"] = dict(self._cache)
        await self.plugin.backlog_store.save(state)
