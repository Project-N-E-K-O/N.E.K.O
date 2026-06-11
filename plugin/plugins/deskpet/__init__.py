"""
桌面宠物插件 (DeskPet)

对标 RunCat365：每 3 秒采样 CPU → 公式算速度 → 状态上报。
只在 CPU 首次越过高阈值 / 首次回落时出声吐槽。
无状态机、无LLM、无Web UI —— 极简原则。
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import psutil

from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    lifecycle,
    neko_plugin,
    plugin_entry,
    timer_interval,
)

# ── 常量（对标 RunCat 的硬编码设计，不暴露为配置） ──

_SAMPLE_INTERVAL_S = 3         # 对标 RunCat cpuTimer 的 3s
_PSUTIL_BLOCK_S = 0.5          # psutil 阻塞采样时长
_EMA_ALPHA = 0.3               # EMA 平滑系数
_STRESS_THRESHOLD_PCT = 80.0   # 吐槽触发阈值（%）
_COOLDOWN_S = 300              # 两次吐槽之间最短间隔（秒）
_CONFIRM_COUNT = 2             # 连续确认次数（防抖）

# ── 吐槽台词 ──（同 RunCat 的角色表现，这里用文字代替视觉动画）

_PHRASES_STRESSED = [
    "CPU 好高喵！{}%，风扇在尖叫了 🔥",
    "主人开太多程序了！CPU 已经 {}% 了！",
    "{}%！再这样我要变成烤猫了 🔥🐱",
    "电脑在冒烟...CPU {}%...救命 🆘",
    "我的毛要焦了！CPU {}%！快关几个窗口！",
    "{}% CPU！这不是演习！🔥",
    "呜呜风扇好吵...CPU {}%了...",
]

_PHRASES_RELAXED = [
    "呼...CPU 回到 {}% 了，得救了 🧊",
    "终于凉快下来了，{}% 才正常嘛~",
    "{}%！风扇安静了，舒服~ 😌",
    "危机解除！CPU {}%，又可以摸鱼了 🐟",
]


def _pick(phrases: list[str], cpu: float) -> str:
    return random.choice(phrases).format(int(cpu))


# ── RunCat 核心公式 ──

def runcat_speed_ms(cpu_percent: float) -> float:
    """RunCat365 的动画间隔公式。

    interval = 200 / clamp(cpu/5, 1, 20)

    CPU   0% → 200ms（最慢）
    CPU  50% →  20ms
    CPU 100% →  10ms（最快）
    """
    intensity = max(1.0, min(20.0, cpu_percent / 5.0))
    return 200.0 / intensity


# ── 插件主类 ──

@neko_plugin
class DeskPetPlugin(NekoPluginBase):
    """极简 CPU 宠物 — 对标 RunCat365 的核心循环。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger

        # CPU 状态
        self._smooth_cpu: float = 0.0
        self._smooth_mem: float = 0.0
        self._last_raw_cpu: float | None = None
        self._last_raw_mem: float | None = None
        self._initialized: bool = False

        # 吐槽控制
        self._was_stressed: bool = False   # 上一个周期是否处于高负载
        self._last_phrase_at: float = 0.0  # 上次吐槽的 epoch 时间
        self._stress_count: int = 0        # 连续高负载次数（防抖）
        self._relax_count: int = 0         # 连续正常次数（防抖）

    # ── 生命周期 ──

    @lifecycle(id="startup")
    async def startup(self, **_):
        self.logger.info(
            "DeskPet started — interval=%ss alpha=%s threshold=%s%% cooldown=%ss",
            _SAMPLE_INTERVAL_S, _EMA_ALPHA, _STRESS_THRESHOLD_PCT, _COOLDOWN_S,
        )
        return Ok({"status": "running"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self.logger.info("DeskPet stopped")
        return Ok({"status": "stopped"})

    @lifecycle(id="freeze")
    async def freeze(self, **_):
        self.logger.debug("DeskPet frozen")
        return Ok({"status": "frozen"})

    @lifecycle(id="unfreeze")
    async def unfreeze(self, **_):
        self.logger.debug("DeskPet unfrozen")
        return Ok({"status": "running"})

    # ── 核心轮询（对标 RunCat 的 ObserveCPUTick） ──

    @timer_interval(
        id="cpu_tick",
        seconds=_SAMPLE_INTERVAL_S,
        name="CPU采样",
        description="周期性采样 CPU 使用率并更新状态",
        auto_start=True,
    )
    async def on_cpu_tick(self, **_):
        # 1. 采样 — 阻塞调用扔进线程池，避免卡住事件循环
        def _sample_metrics() -> tuple[float, float]:
            return (
                float(psutil.cpu_percent(interval=_PSUTIL_BLOCK_S)),
                float(psutil.virtual_memory().percent),
            )

        try:
            raw_cpu, raw_mem = await asyncio.to_thread(_sample_metrics)
        except Exception as exc:
            self.logger.warning("DeskPet psutil sampling failed: %s", exc)
            if self._last_raw_cpu is None or self._last_raw_mem is None:
                return
            raw_cpu = self._last_raw_cpu
            raw_mem = self._last_raw_mem
        else:
            self._last_raw_cpu = raw_cpu
            self._last_raw_mem = raw_mem

        # 2. EMA 平滑
        if not self._initialized:
            self._smooth_cpu = float(raw_cpu)
            self._smooth_mem = float(raw_mem)
            self._initialized = True
        else:
            self._smooth_cpu = _EMA_ALPHA * raw_cpu + (1 - _EMA_ALPHA) * self._smooth_cpu
            self._smooth_mem = _EMA_ALPHA * raw_mem + (1 - _EMA_ALPHA) * self._smooth_mem

        # 3. RunCat 公式
        speed_ms = runcat_speed_ms(self._smooth_cpu)

        # 4. 状态上报（每 tick 都上报，无副作用）
        self.report_status({
            "cpu": round(self._smooth_cpu, 1),
            "memory": round(self._smooth_mem, 1),
            "cpu_raw": round(raw_cpu, 1),
            "memory_raw": round(raw_mem, 1),
            "speed_ms": round(speed_ms, 1),
            "stressed": self._smooth_cpu >= _STRESS_THRESHOLD_PCT,
        })

        # 5. 只在关键时刻出声（防抖 + 冷却）
        await self._maybe_speak(self._smooth_cpu)

    # ── 吐槽逻辑 ──

    async def _maybe_speak(self, cpu: float) -> None:
        """对标 RunCat 的视觉动画——我们用文字替代。

        只在 CPU 首次突破阈值（连续确认）或首次回落时出声。
        """
        now = time.time()

        # --- 进入高负载 ---
        if cpu >= _STRESS_THRESHOLD_PCT:
            self._relax_count = 0
            self._stress_count += 1

            if (
                self._stress_count >= _CONFIRM_COUNT
                and not self._was_stressed
                and (now - self._last_phrase_at) >= _COOLDOWN_S
            ):
                phrase = _pick(_PHRASES_STRESSED, cpu)
                self.ctx.push_message(
                    source="deskpet",
                    ai_behavior="respond",
                    visibility=[],
                    parts=[{"type": "text", "text": phrase}],
                    metadata={"cpu": round(cpu, 1), "mood": "stressed"},
                )
                self._was_stressed = True
                self._last_phrase_at = now
                self.logger.info("DeskPet speak(stressed): cpu=%.1f%%", cpu)
            return

        # --- 恢复正常 ---
        self._stress_count = 0
        self._relax_count += 1

        if self._relax_count >= _CONFIRM_COUNT and self._was_stressed:
            phrase = _pick(_PHRASES_RELAXED, cpu)
            self.ctx.push_message(
                source="deskpet",
                ai_behavior="respond",
                visibility=[],
                parts=[{"type": "text", "text": phrase}],
                metadata={"cpu": round(cpu, 1), "mood": "relaxed"},
            )
            self._was_stressed = False
            self._last_phrase_at = now
            self.logger.info("DeskPet speak(relaxed): cpu=%.1f%%", cpu)

    # ── 手动查询入口 ──

    @plugin_entry(
        id="check_cpu",
        name="查询CPU状态",
        description="查询当前CPU使用率和宠物状态",
        llm_result_fields=["cpu", "memory", "stressed", "speed_ms"],
    )
    async def check_cpu(self, **_):
        speed_ms = runcat_speed_ms(self._smooth_cpu)
        return Ok({
            "cpu": round(self._smooth_cpu, 1),
            "memory": round(self._smooth_mem, 1),
            "stressed": self._smooth_cpu >= _STRESS_THRESHOLD_PCT,
            "speed_ms": round(speed_ms, 1),
        })
