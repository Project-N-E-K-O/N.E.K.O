"""
疲劳计算（三维模型）

三维疲劳：
  1. 昼夜节律 — 24小时余弦曲线，凌晨疲劳高、下午精力好
  2. 会话累积 — 每条回复增加疲劳，随时间衰减
  3. 全局负载 — 近期消息总量产生的疲劳

疲劳影响（只服务注意力系统）：
  - 注意力衰减速度：decay_all 传入 fatigue 乘数，高疲劳衰减更快
  - 注意力回升速度：update_on_message_count 的恢复增益乘 recovery_scale，高疲劳回升更慢
"""

from __future__ import annotations

import math
import time
from typing import Any, Optional


class QQFatigueService:
    """疲劳计算（无睡眠状态机）"""

    # ── 配置读取辅助 ──
    def _cfg(self, key: str, default):
        return (self.plugin._qq_settings or {}).get(key, default)

    # ── 昼夜节律参数 ──
    @property
    def CIRCADIAN_PEAK_HOUR(self): return self._cfg("fatigue_circadian_peak_hour", 15)
    @property
    def CIRCADIAN_LOW_HOUR(self): return self._cfg("fatigue_circadian_low_hour", 3)
    CIRCADIAN_PEAK_FATIGUE = 0
    CIRCADIAN_LOW_FATIGUE = 40

    # ── 会话疲劳参数 ──
    @property
    def SESSION_FATIGUE_PER_REPLY(self): return self._cfg("fatigue_session_per_reply", 5.0)
    SESSION_RECOVERY_PER_SECOND = 3.0 / 60
    SESSION_FATIGUE_CAP = 50

    # ── 全局负载参数 ──
    GLOBAL_FATIGUE_WINDOW = 600
    GLOBAL_FATIGUE_PER_MSG = 0.8
    GLOBAL_FATIGUE_CAP = 40

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._last_active: dict[str, float] = {}              # 群/私聊 → 最后回复时间戳（用于会话疲劳衰减）
        self._session_fatigue_values: dict[str, float] = {}   # 会话疲劳分值
        self._global_msg_timestamps: list[float] = []         # 全局消息时间戳窗口

    # ── 昼夜节律 ──

    def _circadian_fatigue(self) -> float:
        """计算当前时刻的昼夜节律疲劳值（0-40）。"""
        now = time.localtime()
        hour = now.tm_hour + now.tm_min / 60.0 + now.tm_sec / 3600.0
        phase = (hour - self.CIRCADIAN_PEAK_HOUR) / 24.0 * 2 * math.pi
        cosine = math.cos(phase)
        mid = (self.CIRCADIAN_PEAK_FATIGUE + self.CIRCADIAN_LOW_FATIGUE) / 2
        half_range = (self.CIRCADIAN_LOW_FATIGUE - self.CIRCADIAN_PEAK_FATIGUE) / 2
        return mid - half_range * cosine

    # ── 会话疲劳 ──

    def _session_fatigue(self, session_key: str) -> float:
        """计算指定会话的疲劳值，含自动衰减。"""
        now = time.time()
        raw = self._session_fatigue_values.get(session_key, 0.0)
        last = self._last_active.get(session_key, now)
        elapsed = max(0.0, now - last)
        decayed = max(0.0, raw - elapsed * self.SESSION_RECOVERY_PER_SECOND)
        self._session_fatigue_values[session_key] = decayed
        return decayed

    def _add_session_fatigue(self, session_key: str) -> None:
        """记录一次回复，增加会话疲劳。"""
        current = self._session_fatigue_values.get(session_key, 0.0)
        self._session_fatigue_values[session_key] = min(
            self.SESSION_FATIGUE_CAP,
            current + self.SESSION_FATIGUE_PER_REPLY,
        )

    # ── 全局负载疲劳 ──

    def _global_load_fatigue(self) -> float:
        """计算全局负载疲劳（近期消息量）。"""
        now = time.time()
        cutoff = now - self.GLOBAL_FATIGUE_WINDOW
        self._global_msg_timestamps[:] = [
            t for t in self._global_msg_timestamps if t > cutoff
        ]
        return min(self.GLOBAL_FATIGUE_CAP,
                   len(self._global_msg_timestamps) * self.GLOBAL_FATIGUE_PER_MSG)

    def record_incoming_message(self) -> None:
        """记录一条收到的消息（用于全局负载计算）。"""
        self._global_msg_timestamps.append(time.time())

    # ── 综合疲劳计算 ──

    def calculate_fatigue(self, session_key: str) -> float:
        """综合三维疲劳值（0-100）。"""
        circadian = self._circadian_fatigue()
        session = self._session_fatigue(session_key)
        global_load = self._global_load_fatigue()
        return min(100.0, circadian + session + global_load)

    # ── 活跃标记（回复后调用，累积会话疲劳）──

    def mark_active(self, session_key: str) -> None:
        """标记会话活跃（消息已处理），累积会话疲劳。"""
        self._last_active[session_key] = time.time()
        self._add_session_fatigue(session_key)

    # ── 时间上下文 ──

    def get_dynamic_time_context(self) -> str:
        """生成当前时间上下文（供 LLM 系统提示注入）。"""
        import datetime
        now = datetime.datetime.now()
        hour = now.hour + now.minute / 60.0

        ctx = f"当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{'一二三四五六日'[now.weekday()]}。\n"

        if hour < 6:
            ctx += "现在是深夜凌晨。\n"
        elif hour < 9:
            ctx += "现在是早晨。\n"
        elif hour < 12:
            ctx += "现在是上午。\n"
        elif hour < 14:
            ctx += "现在是中午/午后。\n"
        elif hour < 18:
            ctx += "现在是下午。\n"
        elif hour < 22:
            ctx += "现在是傍晚/晚间。\n"
        else:
            ctx += "现在是深夜。\n"

        ctx += '注意结合当前时间理解对话中的时间表达（如"刚刚""昨天""下周"等）。\n'
        return ctx

    # ── 注意力耦合 ──

    def recovery_scale(self, session_key: str) -> float:
        """疲劳 → 注意力回升倍率（0.0-1.0）。

        高疲劳时注意力回升更慢：疲劳 0 → 1.0（完全恢复），
        疲劳 50 → 0.5，疲劳 100 → 0（不再回升）。
        """
        fatigue = self.calculate_fatigue(session_key)
        return max(0.0, 1.0 - fatigue / 100.0)
