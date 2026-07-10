from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .feedback_classifier import QQFeedbackClassifier


@dataclass(slots=True)
class QQGroupAttentionState:
    group_id: str
    attention_score: float = 0.0
    last_boost_at: int = 0
    last_decay_at: int = 0
    last_message_at: int = 0
    last_reply_at: int = 0
    recent_message_count: int = 0
    keyword_boost_score: float = 0.0
    focus_lock_until: int = 0
    focus_cooldown_until: int = 0
    last_focus_reason: str = ""
    last_message_id: str = ""
    last_sender_id: str = ""
    last_focus_at: int = 0
    # 被忽略的消息（供回溯补回），保留最近 N 条
    ignored_messages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "attention_score": float(self.attention_score),
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
            "ignored_messages": list(self.ignored_messages),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None, *, group_id: str) -> "QQGroupAttentionState":
        data = dict(payload or {})
        return cls(
            group_id=group_id,
            attention_score=float(data.get("attention_score") or 0.0),
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
            ignored_messages=list(data.get("ignored_messages") or []),
        )


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

    def _current_time(self) -> int:
        return int(__import__("time").time())

    def _normalized_groups(self) -> list[str]:
        groups: set[str] = set()
        if self.plugin.group_permission_mgr:
            for item in self.plugin.group_permission_mgr.list_groups():
                gid = item.get("group_id", "") if isinstance(item, dict) else str(item or "")
                normalized = str(gid or "").strip()
                if normalized:
                    groups.add(normalized)
        for group_id in list(self._cache.keys()):
            # 清除被旧 bug 污染的 dict-string 键
            if isinstance(group_id, str) and group_id.startswith("{"):
                del self._cache[group_id]
                continue
            normalized = str(group_id or "").strip()
            if normalized and not normalized.startswith("{"):
                groups.add(normalized)
        return sorted(groups)

    def _load_state(self, group_id: str) -> QQGroupAttentionState:
        attention_state = self._cache.get(group_id)
        state = QQGroupAttentionState.from_dict(attention_state if isinstance(attention_state, dict) else None, group_id=group_id)
        # 新群给基础分，不低于 1.0
        if float(state.attention_score) < 1.0 and not isinstance(attention_state, dict):
            state.attention_score = 1.0
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

    def _normalize_state(self, state: QQGroupAttentionState) -> QQGroupAttentionState:
        max_attention = self._max_attention()
        state.attention_score = max(0.0, min(max_attention, float(state.attention_score)))
        state.keyword_boost_score = max(0.0, float(state.keyword_boost_score))
        state.recent_message_count = max(0, int(state.recent_message_count))
        return state

    def _apply_decay(self, state: QQGroupAttentionState, now: int, *, is_focus: bool = False) -> QQGroupAttentionState:
        if now <= 0:
            now = self._current_time()
        last_decay_at = int(state.last_decay_at or state.last_message_at or state.last_boost_at or now)
        elapsed = max(0, now - last_decay_at)
        if elapsed <= 0:
            return state
        in_cooldown = bool(state.focus_cooldown_until and state.focus_cooldown_until > now)
        if is_focus or in_cooldown:
            # 焦点群 / 冷却期内的原焦点群：衰减（burnout）
            decay = elapsed * self._decay_per_second()
            if decay > 0:
                state.attention_score = max(0.0, state.attention_score - decay)
        else:
            # 非焦点群：随时间恢复，上限为焦点阈值
            recovery = elapsed * 0.01  # 每5秒+0.05
            if recovery > 0:
                state.attention_score = min(self._focus_threshold(), state.attention_score + recovery)
        state.last_decay_at = now
        return self._normalize_state(state)

    def _sort_states(self, states: list[QQGroupAttentionState]) -> list[QQGroupAttentionState]:
        return sorted(states, key=lambda item: (item.attention_score, item.last_message_at, item.keyword_boost_score), reverse=True)

    def _default_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled(),
            "focus_group_id": "",
            "focus_score": 0.0,
            "focus_reason": "",
            "groups": [],
        }

    def get_snapshot(self) -> dict[str, Any]:
        now = self._current_time()
        # 先找当前焦点群（基于缓存中的最高分）
        old_states: list[QQGroupAttentionState] = [self._load_state(gid) for gid in self._normalized_groups()]
        old_states = self._sort_states(old_states)
        focus_group_id = old_states[0].group_id if old_states else ""
        states: list[QQGroupAttentionState] = []
        for group_id in self._normalized_groups():
            state = self._apply_decay(self._load_state(group_id), now, is_focus=(group_id == focus_group_id))
            self._write_state(state)
            states.append(state)
        states = self._sort_states(states)
        if not states:
            return self._default_snapshot()
        focus = states[0]
        return {
            "enabled": self._enabled(),
            "focus_group_id": focus.group_id,
            "focus_score": float(focus.attention_score),
            "focus_reason": focus.last_focus_reason,
            "groups": [state.to_dict() for state in states],
        }

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
                return 0.0
            return max(0.35, 1.0 - min(0.6, gap / max(self._focus_threshold(), 1.0)))
        if state.focus_lock_until and state.focus_lock_until > now:
            return 1.35
        if group_score >= self._focus_threshold():
            return min(1.65, 1.0 + min(0.65, group_score / max(self._focus_threshold(), 1.0) * 0.25))
        if group_score <= self._minimum_threshold():
            return 0.8
        return 1.0

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

    async def update_on_message(self, message: dict[str, Any]) -> dict[str, Any]:
        if not self._enabled():
            return self.get_snapshot()
        group_id = str(message.get("group_id") or "").strip()
        if not group_id:
            return self.get_snapshot()
        focus_group_id = self.get_focus_group_id()
        now = int(message.get("timestamp") or self._current_time())
        state = self._apply_decay(self._load_state(group_id), now, is_focus=(group_id == focus_group_id))
        state.last_message_at = now
        state.last_message_id = str(message.get("message_id") or "")
        state.last_sender_id = str(message.get("user_id") or "")
        state.recent_message_count = min(9999, int(state.recent_message_count or 0) + 1)
        # 不再有消息增益——注意力通过时间恢复，非焦点群自然回升
        text = str(message.get("content") or message.get("text") or "").strip()
        category = str(message.get("category") or "").strip()
        if not category and text:
            category = QQFeedbackClassifier.classify(text, list((self.plugin._qq_settings or {}).get("backlog_labels") or []))
        # 点名分类只用 is_at_bot 判定，避免 @任何人都会触发
        if category == "mention" and not bool(message.get("is_at_bot")):
            category = "chat"
        # 黑名单：不触发关键词爆发
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
                # 焦点群被@ → 注意力加分减半，避免已关注的群因@而过度霸占焦点
                if category == "mention" and group_id == focus_group_id:
                    boost *= 0
                state.keyword_boost_score += label_priority
                state.attention_score += boost
                state.last_boost_at = now
                state.last_focus_reason = category
        self._write_state(self._normalize_state(state))
        await self._persist()
        return self.get_snapshot()

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
        state.last_message_id = str(reply_message_id or state.last_message_id or "")
        state.last_sender_id = str(at_user_id or state.last_sender_id or "")
        state.attention_score = max(0.0, state.attention_score - self._reply_penalty())
        state.keyword_boost_score = max(0.0, state.keyword_boost_score * 0.9)
        state.last_focus_reason = "reply_penalty"
        state.focus_lock_until = now + self._focus_lock_seconds() if self._focus_lock_seconds() > 0 else 0
        self._write_state(self._normalize_state(state))
        await self._persist()
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
        state.attention_score += gain
        state.recent_message_count = min(9999, int(state.recent_message_count or 0) + max(0, int(message_count or 0)))
        state.last_focus_reason = "message_recovery"
        self._write_state(self._normalize_state(state))
        await self._persist()
        return self.get_snapshot()

    async def decay_all(self) -> None:
        if not self._enabled():
            return
        now = self._current_time()
        focus_id = self.get_focus_group_id()
        for group_id in self._normalized_groups():
            state = self._load_state(group_id)
            state = self._apply_decay(state, now, is_focus=(group_id == focus_id))
            self._write_state(state)
        await self._persist()

    async def _persist(self) -> None:
        if not getattr(self.plugin, "backlog_store", None):
            return
        state = await self.plugin.backlog_store.load()
        state["group_attention_state"] = dict(self._cache)
        await self.plugin.backlog_store.save(state)

    # ==========================================
    # 回溯补回（ignored message tracking）
    # ==========================================

    _MAX_IGNORED_MESSAGES = 50

    def record_ignored_message(
        self,
        group_id: str,
        *,
        message_id: str = "",
        message_text: str = "",
        sender_id: str = "",
        sender_nickname: str = "",
        timestamp: int = 0,
    ) -> None:
        """记录一条被忽略的消息（供焦点切换后回溯补回）"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        state = self._load_state(normalized_group_id)
        if not isinstance(state.ignored_messages, list):
            state.ignored_messages = []
        state.ignored_messages.append({
            "message_id": str(message_id or ""),
            "message_text": str(message_text or ""),
            "sender_id": str(sender_id or ""),
            "sender_nickname": str(sender_nickname or ""),
            "timestamp": int(timestamp or self._current_time()),
            "recorded_at": self._current_time(),
        })
        # 保留最近 N 条
        if len(state.ignored_messages) > self._MAX_IGNORED_MESSAGES:
            state.ignored_messages = state.ignored_messages[-self._MAX_IGNORED_MESSAGES:]
        self._write_state(state)

    def get_ignored_messages_since(self, group_id: str, since_timestamp: int = 0) -> list[dict[str, Any]]:
        """取出指定时间之后被忽略的消息"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return []
        state = self._load_state(normalized_group_id)
        messages = list(state.ignored_messages or [])
        if since_timestamp > 0:
            messages = [m for m in messages if int(m.get("timestamp", 0)) >= since_timestamp]
        return messages

    def clear_ignored_messages(self, group_id: str) -> None:
        """清空某群的 ignored 消息列表（回溯完成后调用）"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        state = self._load_state(normalized_group_id)
        state.ignored_messages = []
        self._write_state(state)

    def mark_focus(self, group_id: str) -> None:
        """标记该群成为焦点（记录时间戳，供回溯时计算 since）"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        state = self._load_state(normalized_group_id)
        state.last_focus_at = self._current_time()
        state.focus_cooldown_until = 0  # 成为焦点时清除冷却
        self._write_state(state)

    def set_focus_cooldown(self, group_id: str) -> None:
        """焦点切换后给原焦点群设置冷却期，期间继续衰减不恢复"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        state = self._load_state(normalized_group_id)
        state.focus_cooldown_until = self._current_time() + self._focus_cooldown_seconds()
        self._write_state(state)

    def get_last_focus_at(self, group_id: str) -> int:
        """获取该群上次成为焦点的时刻"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return 0
        return int(self._load_state(normalized_group_id).last_focus_at)

    # ==========================================
    # 全局休眠判定
    # ==========================================

    def is_global_sleep(self) -> bool:
        """所有群 attention < min_threshold → 全局休眠"""
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        for group_id in self._normalized_groups():
            state = self._apply_decay(self._load_state(group_id), now, is_focus=(group_id == focus_group_id))
            if float(state.attention_score) >= self._minimum_threshold():
                return False
        # 没有任何托管群：不算休眠
        if not self._normalized_groups():
            return False
        return True

    def get_focus_group(self) -> str | None:
        """返回当前焦点群 ID，全局休眠或无群时返回 None"""
        if self.is_global_sleep():
            return None
        snapshot = self.get_snapshot()
        focus_id = str(snapshot.get("focus_group_id") or "")
        return focus_id if focus_id else None

    # ==========================================
    # 手动增减注意力（用于回溯补回等场景）
    # ==========================================

    async def boost_attention(self, group_id: str, amount: float, reason: str = "") -> None:
        """手动增加注意力"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id or amount <= 0:
            return
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        state.attention_score += amount
        state.last_boost_at = now
        state.last_focus_reason = reason or "manual_boost"
        self._write_state(self._normalize_state(state))
        await self._persist()

    async def consume_attention(self, group_id: str, amount: float, reason: str = "") -> None:
        """手动消耗注意力（回复等）"""
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

    # ==========================================
    # 后台衰减循环
    # ==========================================

    async def start_decay_loop(self, interval_seconds: float = 5.0) -> None:
        """启动后台衰减循环（在插件 startup 中调用）"""
        import asyncio
        self._decay_task = asyncio.create_task(self._decay_loop(interval_seconds))

    async def stop_decay_loop(self) -> None:
        """停止后台衰减循环"""
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
        """后台定期对所有群做衰减"""
        import asyncio
        while True:
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
            await self.decay_all()
