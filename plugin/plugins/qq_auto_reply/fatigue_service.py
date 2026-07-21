"""
疲劳 + 苏醒/睡眠状态机（KiraAI-style）

三维疲劳计算：
  1. 昼夜节律 — 24小时余弦曲线，凌晨疲劳高、下午精力好
  2. 会话累积 — 每条回复增加疲劳，随时间衰减
  3. 全局负载 — 近期消息总量产生的疲劳

苏醒/睡眠：
  - SLEEPING: 群消息默认忽略，只有 @/回复/关键词触发才处理
  - AWAKE: 所有消息正常处理，空闲超时后回 SLEEPING
"""

from __future__ import annotations

import math
import time
from typing import Any, Optional


class QQFatigueService:
    """疲劳计算 + 苏醒/睡眠状态机"""

    # ── 昼夜节律参数 ──
    CIRCADIAN_PEAK_HOUR = 15       # 精力峰值时间（下午3点）
    CIRCADIAN_LOW_HOUR = 3         # 精力低谷时间（凌晨3点）
    CIRCADIAN_PEAK_FATIGUE = 0     # 峰值时的疲劳值
    CIRCADIAN_LOW_FATIGUE = 40     # 低谷时的疲劳值

    # ── 会话疲劳参数 ──
    SESSION_FATIGUE_PER_REPLY = 5.0    # 每条回复增加的疲劳
    SESSION_RECOVERY_PER_SECOND = 3.0 / 60  # 每秒恢复量（3/分钟）
    SESSION_FATIGUE_CAP = 50          # 会话疲劳上限

    # ── 全局负载参数 ──
    GLOBAL_FATIGUE_WINDOW = 600       # 统计窗口（秒）
    GLOBAL_FATIGUE_PER_MSG = 0.8      # 每条消息的疲劳增量
    GLOBAL_FATIGUE_CAP = 40           # 全局疲劳上限

    # ── 动态睡眠参数 ──
    _bedtime_hour: float = 23.0       # 动态就寝时间（会根据疲劳调整）
    _wake_hour: float = 7.0           # 动态起床时间
    _sleep_duration: float = 8.0      # 动态睡眠时长（6~10小时）
    _last_schedule_update: float = 0.0
    _sleep_start_at: float = 0.0      # 实际入睡时间戳
    _woken_early: bool = False        # 是否被强制唤醒（起床气标记）
    _woken_early_at: float = 0.0      # 被强制唤醒的时间戳
    _bedtime_grumpiness_seconds: float = 600.0  # 起床气持续 10 分钟

    # ── 苏醒/睡眠参数 ──
    AWAKE_IDLE_TIMEOUT = 10.0         # 苏醒状态下空闲多久后进入睡眠（秒）

    # ── 疲劳 → 提示词注入映射 ──
    _FATIGUE_TIERS = [
        (15, (
            "【强制】你精力充沛，心情很好。回复可以活泼一些。"
            "每条消息严格控制在50字以内。"
        )),
        (30, (
            "【强制】你感觉状态不错。正常聊天即可。"
            "每条消息严格控制在40字以内。"
        )),
        (45, (
            "【强制】你开始有点累了。每条消息不超过30字，最多发1个<msg>块。"
            "语气可以随意一点，不要太正式。"
        )),
        (60, (
            "【强制】你挺累了不想说话。每条消息不超过20字，严格只发1个<msg>块。"
            "用\"嗯\"\"哦\"\"好的呀..\"这种懒散语气。能用颜文字代替就不打字。"
        )),
        (75, (
            "【强制】你已经非常疲惫。每条消息不超过10字，严格只发1个<msg>块。"
            "尽量用单个颜文字或表情包回应。大部分情况直接<msg></msg>不回复。"
        )),
        (100, (
            "【强制】你已经累到快睡着了。每条消息不超过5字或一个颜文字。"
            "除了点名非回不可的情况，其他全部<msg></msg>。回复也只用最简单的方式。"
        )),
    ]

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._sleeping: dict[str, bool] = {}       # 群/私聊 → 是否睡眠中
        self._last_active: dict[str, float] = {}    # 群/私聊 → 最后活跃时间戳
        self._session_fatigue_values: dict[str, float] = {}  # 会话疲劳分值
        self._global_msg_timestamps: list[float] = []  # 全局消息时间戳窗口

    # ── 昼夜节律 ──

    def _circadian_fatigue(self) -> float:
        """计算当前时刻的昼夜节律疲劳值（0-40）。"""
        now = time.localtime()
        hour = now.tm_hour + now.tm_min / 60.0 + now.tm_sec / 3600.0
        # 余弦变换：精力峰值在 15:00=1，低谷在 3:00=-1
        phase = (hour - self.CIRCADIAN_PEAK_HOUR) / 24.0 * 2 * math.pi
        cosine = math.cos(phase)
        # 映射到 [CIRCADIAN_PEAK_FATIGUE, CIRCADIAN_LOW_FATIGUE]
        # cosine=1 → 0, cosine=-1 → 40
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

    # ── 苏醒/睡眠状态机 ──

    def is_sleeping(self, session_key: str) -> bool:
        """检查指定会话是否在睡眠中。"""
        # 私聊默认不睡眠
        if session_key.startswith("private:"):
            return False
        default = self._sleeping.get(session_key, True)  # 新会话默认睡眠
        if not default:
            # 已苏醒：检查是否空闲超时
            last = self._last_active.get(session_key, time.time())
            idle = time.time() - last
            fatigue = self.calculate_fatigue(session_key)
            timeout = self.AWAKE_IDLE_TIMEOUT
            if fatigue > 70:
                timeout = self.AWAKE_IDLE_TIMEOUT * 0.3
            elif fatigue > 50:
                timeout = self.AWAKE_IDLE_TIMEOUT * 0.6
            if idle > timeout:
                self._sleeping[session_key] = True
                self._record_sleep_start()  # 进入睡眠时记录
                return True
        else:
            # 正在睡眠中，确保持续记录
            if self._sleep_start_at == 0:
                self._record_sleep_start()
        return default

    def should_wake(self, session_key: str, *, is_mentioned: bool, has_keyword: bool) -> bool:
        """判断是否应该被唤醒并处理此消息。"""
        is_asleep = self.is_sleeping(session_key)
        if not is_asleep:
            # 已苏醒 → 正常处理
            return True
        # 睡眠中 → 只对 @/回复/关键词 唤醒
        fatigue = self.calculate_fatigue(session_key)
        if fatigue > 70:
            # 极度疲劳 → 连关键词都可能忽略
            if not is_mentioned:
                return False
        if is_mentioned or has_keyword:
            self._sleeping[session_key] = False
            self._last_active[session_key] = time.time()
            return True
        return False

    def mark_active(self, session_key: str) -> None:
        """标记会话活跃（消息已处理）。"""
        self._last_active[session_key] = time.time()
        # 每次处理回复后增加会话疲劳
        self._add_session_fatigue(session_key)

    def force_awake(self, session_key: str) -> None:
        """强制唤醒（@/关键词触发）。如果在睡眠中被叫醒，产生起床气。"""
        now = time.time()
        was_asleep = self.is_sleeping(session_key)
        self._sleeping[session_key] = False
        self._last_active[session_key] = now

        # 检测是否在睡眠时间被强制唤醒（有起床气）
        if was_asleep and self._sleep_start_at > 0:
            # 计算睡了多久
            slept = now - self._sleep_start_at
            planned = self._sleep_duration * 3600
            if slept < planned * 0.5:
                # 睡了不到一半就被叫醒 → 起床气
                self._woken_early = True
                self._woken_early_at = now
                self._sleep_duration = max(4.0, slept / 3600)  # 实际睡眠时长缩水
                self._wake_hour = (time.localtime(now).tm_hour + time.localtime(now).tm_min / 60.0) % 24
            self._sleep_start_at = 0

    def _record_sleep_start(self) -> None:
        """记录入睡时间（睡前调用）。"""
        if self._sleep_start_at == 0:
            self._sleep_start_at = time.time()

    # ── 提示词注入 ──

    # ── 动态睡眠时间表 ──

    def _update_sleep_schedule(self) -> None:
        """每小时更新一次动态睡眠时间表：疲劳高→早睡晚起，疲劳低→晚睡早起。"""
        now = time.time()
        if now - self._last_schedule_update < 3600:
            # 检查是否自然醒（过了起床时间且还在睡眠记录中）
            if self._sleep_start_at > 0:
                import datetime as _dt
                hour = _dt.datetime.now().hour + _dt.datetime.now().minute / 60.0
                if hour >= self._wake_hour:
                    self._sleep_start_at = 0
                    self._woken_early = False  # 自然醒，清除起床气
            return
        self._last_schedule_update = now
        # 取最近疲劳趋势（昼夜节律均值 ≈ 当前疲劳水平）
        circadian = self._circadian_fatigue()
        # 疲劳高 → 睡眠长（8→10h），疲劳低 → 睡眠短（8→6h）
        if circadian > 30:
            self._sleep_duration = 8.0 + (circadian - 30) / 10.0 * 2.0  # 8~10h
            self._bedtime_hour = max(21.0, 23.0 - (circadian - 30) / 10.0 * 2.0)
        elif circadian < 10:
            self._sleep_duration = max(6.0, 8.0 - (10 - circadian) / 10.0 * 2.0)  # 6~8h
            self._bedtime_hour = min(24.0, 23.0 + (10 - circadian) / 10.0 * 2.0)
        else:
            self._sleep_duration = 8.0
            self._bedtime_hour = 23.0
        self._sleep_duration = max(6.0, min(10.0, self._sleep_duration))
        self._bedtime_hour = max(21.0, min(24.0, self._bedtime_hour))
        self._wake_hour = (self._bedtime_hour + self._sleep_duration) % 24

    def get_dynamic_time_context(self) -> str:
        """生成动态时间上下文。时间信息为主体，睡眠状态为附加。"""
        self._update_sleep_schedule()
        import datetime
        now = datetime.datetime.now()
        now_ts = time.time()
        hour = now.hour + now.minute / 60.0

        # ── 主体：时间信息 ──
        ctx = f"当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{'一二三四五六日'[now.weekday()]}。\n"

        # 时段 + 行为指引
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

        # 相对时间提醒
        ctx += '注意结合当前时间理解对话中的时间表达（如"刚刚""昨天""下周"等）。\n'

        # ── 附加：睡眠状态（一句话）──
        sleep_note = ""
        if self._sleep_start_at > 0:
            slept_sec = now_ts - self._sleep_start_at
            slept_h = int(slept_sec / 3600)
            slept_m = int((slept_sec % 3600) / 60)
            sleep_note = f"（你正在睡觉，已经睡了{slept_h}h{slept_m}m，如果被叫醒按性格自然反应）"
        elif self._woken_early and now_ts - self._woken_early_at < self._bedtime_grumpiness_seconds:
            sleep_note = f"（你刚被强制叫醒，睡了不到{int(self._sleep_duration)}h，按性格自然反应）"
        elif self._woken_early and now_ts - self._woken_early_at < 1800:
            sleep_note = "（被叫醒有一会了，差不多清醒了）"
            self._woken_early = False
        else:
            bh = int(self._bedtime_hour)
            bm = int((self._bedtime_hour - bh) * 60)
            wh = int(self._wake_hour)
            wm = int((self._wake_hour - wh) * 60)
            dur_h = int(self._sleep_duration)
            sleep_note = f"（昨晚{bh:02d}:{bm:02d}睡，今早{wh:02d}:{wm:02d}起，约睡{dur_h}h）"

        if sleep_note:
            ctx += sleep_note + "\n"

        return ctx

    # ── 疲劳提示词 ──

    def get_fatigue_prompt(self, session_key: str) -> str:
        """获取疲劳状态提示词（精力层面，时段信息见 TIME 层）。"""
        fatigue = self.calculate_fatigue(session_key)
        sleeping = self.is_sleeping(session_key)
        hour = __import__("time").localtime().tm_hour

        # 层级行为约束
        for threshold, text in self._FATIGUE_TIERS:
            if fatigue <= threshold:
                status = "睡眠中" if sleeping else "活跃中"
                parts = [f"## 当前精力状态（疲劳 {fatigue:.0f}/100，{status}）"]
                parts.append(text)
                if fatigue > 60:
                    parts.append("如果没什么必要回的，可以直接不回。")
                # 疲劳高 + 深夜 → 劝休息
                if fatigue > 45 and (hour < 6 or hour >= 23):
                    parts.append("现在已经很晚了，你也很累。如果对方还在聊，可以劝他们早点休息。")
                # 疲劳低 + 深夜 → 有人在陪你熬夜，精神点
                elif fatigue <= 30 and (hour < 6 or hour >= 23):
                    parts.append("虽然现在是深夜，但有人还在和你聊天，你的精神意外地还不错。不用刻意催睡觉，保持自然的聊天状态就好。")
                return "\n".join(parts)

        return ""
