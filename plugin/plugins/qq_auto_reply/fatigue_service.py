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

    # ── 动态睡眠参数 ──
    _bedtime_hour: float = 23.0       # 动态就寝时间（会根据疲劳调整）
    _wake_hour: float = 7.0           # 动态起床时间
    _sleep_duration: float = 8.0      # 动态睡眠时长（6~10小时）
    _last_schedule_update: float = 0.0
    _sleep_start_at: float = 0.0      # 实际入睡时间戳
    _woken_early: bool = False        # 是否被强制唤醒（起床气标记）
    _woken_early_at: float = 0.0      # 被强制唤醒的时间戳
    _bedtime_grumpiness_seconds: float = 120.0  # 起床气持续 2 分钟

    # ── 苏醒/睡眠参数 ──
    AWAKE_IDLE_TIMEOUT = 300.0        # 苏醒状态下空闲多久后进入睡眠（秒）

    # ── 疲劳 → 提示词注入映射 ──
    _FATIGUE_TIERS = [
        (15, (
            "【强制】精力充沛，心情很好。回复可以活泼主动，每条不超过50字。"
        )),
        (30, (
            "【强制】状态不错，正常聊天。每条不超过40字。"
        )),
        (45, (
            "【强制】开始有点累了。回复随意一点，每条不超过30字，最多1个<msg>。"
        )),
        (60, (
            "【强制】挺累了不太想说话。每条不超过20字，用懒散语气。"
            "能用表情包(`<sticker>`)或颜文字代替就不打字。"
        )),
        (75, (
            "【强制】你已经非常疲惫，不太想说话。每条消息不超过10字。"
            "优先用表情包(`<sticker>`)或颜文字敷衍过去。不太重要的消息直接`<msg></msg>`不回。"
        )),
        (100, (
            "【强制】你已经累到快睡着了。非必要不回复。"
            "实在非回不可时只发一个表情包(`<sticker>`)或一个颜文字。"
        )),
    ]

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._sleeping: dict[str, bool] = {}       # 群/私聊 → 是否睡眠中
        self._last_active: dict[str, float] = {}    # 群/私聊 → 最后活跃时间戳
        self._wake_penalty: dict[str, float] = {}   # 强制唤醒惩罚时间（秒）
        self._session_fatigue_values: dict[str, float] = {}  # 会话疲劳分值
        self._global_msg_timestamps: list[float] = []  # 全局消息时间戳窗口
        self._sleep_segments: list[tuple[float, float]] = []  # 24h 睡眠记录: [(end_ts, duration_s)]
        self._last_sleep_start: float = 0
        self._last_sleep_end: float = 0

    # ── 持久化 ──

    async def load_state(self) -> None:
        if not getattr(self.plugin, "backlog_store", None): return
        try:
            data = await self.plugin.backlog_store.load()
            fs = data.get("fatigue_state")
            if not isinstance(fs, dict): return
            self._sleep_segments = [(float(ts), float(d)) for ts, d in fs.get("sleep_segments") or [] if isinstance(d, (int, float))]
            self._last_sleep_start = float(fs.get("last_sleep_start") or 0)
            self._last_sleep_end = float(fs.get("last_sleep_end") or 0)
            self._bedtime_hour = float(fs.get("bedtime_hour") or 23.0)
            self._wake_hour = float(fs.get("wake_hour") or 7.0)
            self._sleep_duration = float(fs.get("sleep_duration") or 8.0)
            self._last_schedule_update = float(fs.get("last_schedule_update") or 0)
            self._sleep_start_at = float(fs.get("sleep_start_at") or 0)
            self._woken_early = bool(fs.get("woken_early"))
            self._woken_early_at = float(fs.get("woken_early_at") or 0)
        except Exception: pass

    async def save_state(self) -> None:
        if not getattr(self.plugin, "backlog_store", None): return
        try:
            data = await self.plugin.backlog_store.load()
            data["fatigue_state"] = {
                "sleep_segments": [(ts, d) for ts, d in self._sleep_segments[-20:]],
                "last_sleep_start": self._last_sleep_start, "last_sleep_end": self._last_sleep_end,
                "bedtime_hour": self._bedtime_hour, "wake_hour": self._wake_hour,
                "sleep_duration": self._sleep_duration, "last_schedule_update": self._last_schedule_update,
                "sleep_start_at": self._sleep_start_at, "woken_early": self._woken_early,
                "woken_early_at": self._woken_early_at,
            }
            await self.plugin.backlog_store.save(data)
        except Exception: pass

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

    def _decay_session_fatigue(self, session_key: str) -> float:
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

    def _record_sleep_segment(self, duration_seconds: float, started_at: float = 0) -> None:
        if duration_seconds <= 0: return
        now = time.time()
        start = started_at if started_at > 0 else now - duration_seconds
        self._sleep_segments.append((now, duration_seconds))
        self._last_sleep_start = start
        self._last_sleep_end = now
        cutoff = now - 86400
        self._sleep_segments = [(ts, d) for ts, d in self._sleep_segments if ts > cutoff]
        import asyncio as _asyncio
        try: _asyncio.create_task(self.save_state())
        except Exception: pass

    def _total_sleep_24h(self, min_duration: float = 0) -> float:
        now = time.time(); cutoff = now - 86400
        real = sum(d for ts, d in self._sleep_segments if ts > cutoff and d >= min_duration)
        # 有效记录为 0（无记录或只有微nap）→ 默认 8h
        return real if real > 0 else (8 * 3600)

    def _sleep_debt_fatigue(self) -> float:
        total = self._total_sleep_24h(min_duration=600)
        if total >= 8 * 3600: return 0.0
        return min(30.0, ((8 * 3600 - total) / 3600) * 6.0)

    def calculate_fatigue(self, session_key: str) -> float:
        circadian = self._circadian_fatigue()
        session = self._decay_session_fatigue(session_key)
        global_load = self._global_load_fatigue()
        debt = self._sleep_debt_fatigue()
        return min(100.0, circadian + session + global_load + debt)

    # ── 苏醒/睡眠状态机 ──

    def check_sleeping(self, session_key: str, attention_score: float = 0.0) -> bool:
        """检查指定会话是否在睡眠中。
        入睡条件：① 到了就寝时间  ② 疲劳 > 80 撑不住了
        苏醒条件：到了起床时间、@/关键词强制唤醒
        （不再基于空闲超时——注意力门控已处理回复判定）"""
        if session_key.startswith("private:"):
            return False

        now = time.time()
        import datetime as _dt
        hour = _dt.datetime.now().hour + _dt.datetime.now().minute / 60.0
        fatigue = self.calculate_fatigue(session_key)

        was_sleeping = self._sleeping.get(session_key, False)

        # ── 入睡判定 ──
        if not was_sleeping:
            # 强制唤醒保护期：基础 120s + 每次唤醒累加 60s（上限 300s）
            last_active = self._last_active.get(session_key, 0)
            protection = self.AWAKE_IDLE_TIMEOUT + self._wake_penalty.get(session_key, 0)
            if now - last_active < protection:
                return False
            # 疲劳太高撑不住了
            if fatigue > 80:
                self._sleeping[session_key] = True
                self._record_sleep_start()
                return True
            # 到了就寝时间
            if hour >= self._bedtime_hour or hour < self._wake_hour:
                self._sleeping[session_key] = True
                self._record_sleep_start()
                return True
            return False

        # ── 苏醒判定 ──
        # 到了起床时间且疲劳不太高 → 自然醒
        if self._wake_hour <= hour < self._bedtime_hour:
            if fatigue < 60:
                self._sleeping[session_key] = False
                return False
        # 还在睡
        if self._sleep_start_at == 0:
            self._record_sleep_start()
        return True

    def should_wake(self, session_key: str, *, is_mentioned: bool, has_keyword: bool) -> bool:
        """判断是否应该被唤醒并处理此消息。"""
        is_asleep = self.check_sleeping(session_key)
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

    def mark_active_and_fatigue(self, session_key: str) -> None:
        """标记会话活跃（消息已处理）。"""
        self._last_active[session_key] = time.time()
        self._wake_penalty[session_key] = 0  # clear penalty on active chat
        # 每次处理回复后增加会话疲劳
        self._add_session_fatigue(session_key)

    def force_awake(self, session_key: str) -> None:
        """强制唤醒（@/关键词触发）。如果在睡眠中被叫醒，产生起床气。"""
        now = time.time()
        was_asleep = self.check_sleeping(session_key)
        self._sleeping[session_key] = False
        self._last_active[session_key] = now
        if was_asleep:
            current_penalty = self._wake_penalty.get(session_key, 0)
            self._wake_penalty[session_key] = min(600, current_penalty + 120)

        # 检测是否在睡眠时间被强制唤醒（有起床气）
        if was_asleep and self._sleep_start_at > 0:
            slept = now - self._sleep_start_at
            self._record_sleep_segment(slept, started_at=self._sleep_start_at)
            planned = self._sleep_duration * 3600
            # 至少睡 3 小时才可能起床气
            if slept >= 10800 and slept < planned * 0.5:
                self._woken_early = True
                self._woken_early_at = now
                self._sleep_duration = max(6.0, slept / 3600)
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
                    slept = now - self._sleep_start_at
                    self._record_sleep_segment(slept, started_at=self._sleep_start_at)
                    self._sleep_start_at = 0
                    self._woken_early = False
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

        # ── 精力状态 ──
        rough = self._circadian_fatigue() + self._global_load_fatigue() + self._sleep_debt_fatigue()
        if rough < 15:    ctx += "精力充沛，思维活跃。\n"
        elif rough < 30:  ctx += "精力尚可，正常状态。\n"
        elif rough < 50:  ctx += "有点累了，回复可以简短些。\n"
        elif rough < 70:  ctx += "挺累的，不想说太多话。\n"
        else:             ctx += "非常疲惫，只想简单应付一下。\n"

        # ── 睡眠状态 ──
        real_segments = [(ts, d) for ts, d in self._sleep_segments if d >= 600]
        has_history = bool(real_segments)
        total_24h = sum(d for _, d in real_segments) / 3600 if has_history else 8
        debt_h = max(0, 8 - total_24h)

        if self._sleep_start_at > 0:
            slept_sec = now_ts - self._sleep_start_at
            slept_h = int(slept_sec / 3600); slept_m = int((slept_sec % 3600) / 60)
            debt_tail = f"，近24h仅睡{total_24h:.1f}h" if (has_history and debt_h > 0.5) else ""
            ctx += f"你正在睡觉，已睡{slept_h}h{slept_m}m{debt_tail}。如果被叫醒按性格自然反应。\n"
        elif self._woken_early and now_ts - self._woken_early_at < self._bedtime_grumpiness_seconds:
            last_slept = (self._sleep_segments[-1][1] / 3600) if self._sleep_segments else 0
            debt_tail = f"，近24h只睡了{total_24h:.1f}h" if (has_history and debt_h > 0.5) else ""
            ctx += f"你刚被叫醒（只睡了{last_slept:.1f}h）{debt_tail}。可能有点起床气，但既然被叫醒了就正常参与话题，不要一直念叨自己困或者想睡觉。\n"
        elif self._woken_early and now_ts - self._woken_early_at < 1800:
            ctx += "被叫醒有一会了，差不多清醒了，正常聊天。\n"
            self._woken_early = False
        else:
            last_actual = self._sleep_segments[-1][1] / 3600 if self._sleep_segments else 0
            if self._last_sleep_end > 0 and self._last_sleep_start > 0:
                import datetime as _dt
                start_dt = _dt.datetime.fromtimestamp(self._last_sleep_start)
                end_dt = _dt.datetime.fromtimestamp(self._last_sleep_end)
                parts = [f"入睡 {start_dt.strftime('%H:%M')} — {end_dt.strftime('%H:%M')}，睡了{last_actual:.1f}h"]
            else:
                bh = int(self._bedtime_hour); bm = int((self._bedtime_hour - bh) * 60)
                wh = int(self._wake_hour); wm = int((self._wake_hour - wh) * 60)
                parts = [f"作息约{bh:02d}:{bm:02d}睡 — {wh:02d}:{wm:02d}起"]
            if not has_history:
                parts.append(f"近24h睡够{total_24h:.0f}h，精力充沛")
            elif debt_h > 0.5:
                parts.append(f"近24h只睡了{total_24h:.1f}h，欠{debt_h:.1f}h")
            elif total_24h > 1:
                parts.append(f"近24h睡够{total_24h:.1f}h，精力恢复良好")
            ctx += "。".join(parts) + "。\n"

        return ctx

    # ── 疲劳提示词 ──

    def get_fatigue_prompt(self, session_key: str) -> str:
        """获取疲劳状态提示词（精力层面，时段信息见 TIME 层）。"""
        fatigue = self.calculate_fatigue(session_key)
        sleeping = self.check_sleeping(session_key)
        hour = __import__("time").localtime().tm_hour
        recently_woken = self._woken_early and (time.time() - self._woken_early_at < self._bedtime_grumpiness_seconds)

        # 层级行为约束
        for threshold, text in self._FATIGUE_TIERS:
            if fatigue <= threshold:
                status = "，睡眠中" if sleeping else ""
                parts = [f"## 当前精力状态（疲劳 {fatigue:.0f}/100{status}）"]
                # 刚被叫醒：虽然有起床气/累，但要正常参与对话，不要一直说困
                if recently_woken and not sleeping:
                    parts.append("你刚被叫醒，可能还有点困，但既然醒了就正常参与话题吧。不要一直说自己困或者想睡觉——自然聊天就好。")
                else:
                    parts.append(text)
                if not recently_woken and fatigue > 60:
                    parts.append("如果没什么必要回的，可以直接不回。")
                # 疲劳高 + 深夜 → 劝休息（刚醒不劝）
                if not recently_woken and fatigue > 45 and (hour < 6 or hour >= 23):
                    parts.append("现在已经很晚了，你也很累。如果对方还在聊，可以劝他们早点休息。")
                # 疲劳低 + 深夜 → 有人在陪你熬夜，精神点
                elif fatigue <= 30 and (hour < 6 or hour >= 23):
                    parts.append("虽然现在是深夜，但有人还在和你聊天，你的精神意外地还不错。不用刻意催睡觉，保持自然的聊天状态就好。")
                return "\n".join(parts)

        return ""
