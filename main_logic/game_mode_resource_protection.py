# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Game Mode Beta resource pressure protection.

This module owns the runtime-only state for Game Mode Beta. The feature is a
manual opt-in resource pressure guard: once enabled, sustained system CPU,
memory, or GPU pressure asks the frontend to enter the existing cat form.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

PRESSURE_THRESHOLD_PERCENT = 85.0
SAMPLE_INTERVAL_SECONDS = 5.0
SUSTAINED_SAMPLE_COUNT = 6
MANUAL_RESTORE_COOLDOWN_SECONDS = 10 * 60
LAST_SAMPLE_LIMIT = SUSTAINED_SAMPLE_COUNT

MetricSample = dict[str, Any]
Sampler = Callable[[], MetricSample]
Broadcaster = Callable[[dict[str, Any]], Awaitable[int]]

_PSUTIL_IMPORT_TRIED = False
_PSUTIL: Any = None
_GPU_DISABLED_UNTIL = 0.0
_METRIC_ERROR_LOGGED: dict[str, str] = {}


def _remember_metric_error(metric: str, error: Any) -> str:
    message = str(error)[:160]
    if _METRIC_ERROR_LOGGED.get(metric) != message:
        _METRIC_ERROR_LOGGED[metric] = message
        logger.warning("[GameModeBeta] %s sample unavailable: %s", metric, message)
    return message


def _load_psutil() -> Any:
    global _PSUTIL_IMPORT_TRIED, _PSUTIL
    if not _PSUTIL_IMPORT_TRIED:
        _PSUTIL_IMPORT_TRIED = True
        try:
            import psutil  # type: ignore
            _PSUTIL = psutil
            try:
                psutil.cpu_percent(interval=None)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("[GameModeBeta] psutil unavailable: %s", exc)
            _PSUTIL = None
    return _PSUTIL


def _read_nvidia_gpu_sample(now: float) -> dict[str, Any]:
    global _GPU_DISABLED_UNTIL
    if now < _GPU_DISABLED_UNTIL:
        return {"gpu_percent": None, "gpu_vram_percent": None, "gpu_error": "cooldown"}

    try:
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "").strip() or "nvidia-smi failed")

        gpu_values: list[float] = []
        vram_values: list[float] = []
        for raw_line in completed.stdout.splitlines():
            parts = [p.strip() for p in raw_line.split(",")]
            if len(parts) < 3:
                continue
            try:
                gpu_values.append(float(parts[0]))
                used = float(parts[1])
                total = float(parts[2])
                if total > 0:
                    vram_values.append((used / total) * 100.0)
            except (TypeError, ValueError):
                continue

        if not gpu_values and not vram_values:
            raise RuntimeError("nvidia-smi returned no usable gpu rows")
        return {
            "gpu_percent": max(gpu_values) if gpu_values else None,
            "gpu_vram_percent": max(vram_values) if vram_values else None,
            "gpu_error": None,
        }
    except Exception as exc:
        _GPU_DISABLED_UNTIL = now + 60.0
        return {"gpu_percent": None, "gpu_vram_percent": None, "gpu_error": _remember_metric_error("gpu", exc)}


def collect_resource_sample() -> MetricSample:
    now = time.time()
    sample: MetricSample = {
        "ts": now,
        "cpu_percent": None,
        "memory_percent": None,
        "gpu_percent": None,
        "gpu_vram_percent": None,
        "neko_cpu_percent": None,
        "neko_memory_mb": None,
        "errors": {},
    }

    psutil = _load_psutil()
    if psutil is not None:
        try:
            sample["cpu_percent"] = float(psutil.cpu_percent(interval=None))
        except Exception as exc:
            sample["errors"]["cpu"] = _remember_metric_error("cpu", exc)
        try:
            sample["memory_percent"] = float(psutil.virtual_memory().percent)
        except Exception as exc:
            sample["errors"]["memory"] = _remember_metric_error("memory", exc)
        try:
            proc = psutil.Process()
            cpu_count = psutil.cpu_count() or 1
            sample["neko_cpu_percent"] = float(proc.cpu_percent(interval=None)) / float(cpu_count)
            sample["neko_memory_mb"] = float(proc.memory_info().rss) / (1024 * 1024)
        except Exception as exc:
            sample["errors"]["neko_process"] = _remember_metric_error("neko_process", exc)
    else:
        sample["errors"]["psutil"] = "unavailable"

    gpu_sample = _read_nvidia_gpu_sample(now)
    sample.update({
        "gpu_percent": gpu_sample.get("gpu_percent"),
        "gpu_vram_percent": gpu_sample.get("gpu_vram_percent"),
    })
    if gpu_sample.get("gpu_error"):
        sample["errors"]["gpu"] = gpu_sample.get("gpu_error")

    return sample


async def broadcast_game_mode_event(payload: dict[str, Any]) -> int:
    delivered = 0
    try:
        from main_routers.shared_state import get_session_manager
        session_manager = get_session_manager()
    except Exception as exc:
        logger.debug("[GameModeBeta] no session manager for broadcast: %s", exc)
        return 0

    for name in list(session_manager.keys()):
        try:
            core = session_manager.get(name)
            ws = getattr(core, "websocket", None)
            if ws is None or not hasattr(ws, "send_json"):
                continue
            client_state = getattr(ws, "client_state", None)
            state_name = str(client_state).upper()
            if client_state is not None and "CONNECTED" not in state_name:
                continue
            await ws.send_json(payload)
            delivered += 1
        except Exception as exc:
            logger.debug("[GameModeBeta] broadcast to %s failed: %s", name, exc)
    return delivered


class GameModeResourceProtector:
    def __init__(
        self,
        *,
        sampler: Sampler = collect_resource_sample,
        broadcaster: Broadcaster = broadcast_game_mode_event,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self._sampler = sampler
        self._broadcaster = broadcaster
        self._time = time_fn
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._state = self._new_state()

    def _new_state(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "pressure_state": "normal",
            "last_samples": [],
            "trigger_reason": None,
            "suppressed_until": None,
            "high_sample_count": 0,
            "auto_switch_active": False,
            "auto_switch_source": None,
            "manual_override": False,
            "prompt_shown": False,
            "last_event": None,
        }

    def snapshot(self) -> dict[str, Any]:
        snap = dict(self._state)
        snap["last_samples"] = list(self._state.get("last_samples") or [])
        return snap

    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        async with self._lock:
            if enabled:
                self._state["enabled"] = True
                self._state["pressure_state"] = "normal"
                self._state["last_event"] = {"type": "enabled", "ts": self._time()}
                self._ensure_task_locked()
                logger.info("[GameModeBeta] enabled")
            else:
                self._state = self._new_state()
                self._cancel_task_locked()
                logger.info("[GameModeBeta] disabled and runtime state cleared")
            return self.snapshot()

    async def mark_manual_restore(self) -> dict[str, Any]:
        async with self._lock:
            now = self._time()
            if self._state.get("enabled") and self._state.get("auto_switch_active"):
                self._state["suppressed_until"] = now + MANUAL_RESTORE_COOLDOWN_SECONDS
                self._state["manual_override"] = True
                self._state["auto_switch_active"] = False
                self._state["pressure_state"] = "normal"
                self._state["last_event"] = {"type": "manual_restore", "ts": now}
                logger.info("[GameModeBeta] manual restore cooldown started")
            return self.snapshot()

    async def debug_trigger(self, reason: str = "debug", percent: float = 99.0) -> dict[str, Any]:
        sample = {
            "ts": self._time(),
            "cpu_percent": percent,
            "memory_percent": None,
            "gpu_percent": None,
            "gpu_vram_percent": None,
            "neko_cpu_percent": None,
            "neko_memory_mb": None,
            "errors": {},
        }
        async with self._lock:
            if not self._state.get("enabled"):
                self._state["enabled"] = True
                self._ensure_task_locked()
            await self._trigger_locked(reason, sample, percent)
            return self.snapshot()

    def _ensure_task_locked(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="game_mode_beta_resource_monitor")

    def _cancel_task_locked(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)
                await self.tick_once()
        except asyncio.CancelledError:
            return

    async def tick_once(self) -> dict[str, Any]:
        async with self._lock:
            if not self._state.get("enabled"):
                return self.snapshot()

        sample = await asyncio.to_thread(self._sampler)
        async with self._lock:
            if not self._state.get("enabled"):
                return self.snapshot()
            await self._apply_sample_locked(sample)
            return self.snapshot()

    async def _apply_sample_locked(self, sample: MetricSample) -> None:
        samples = list(self._state.get("last_samples") or [])
        samples.append(sample)
        self._state["last_samples"] = samples[-LAST_SAMPLE_LIMIT:]

        high_reason, high_percent = self._high_pressure_reason(sample)
        now = self._time()
        suppressed_until = self._state.get("suppressed_until")
        if isinstance(suppressed_until, (int, float)) and suppressed_until <= now:
            self._state["suppressed_until"] = None
            self._state["last_event"] = {"type": "cooldown_ended", "ts": now}
            logger.info("[GameModeBeta] manual restore cooldown ended")

        if high_reason is None:
            if self._state.get("pressure_state") != "normal":
                self._state["pressure_state"] = "normal"
                logger.info("[GameModeBeta] pressure cleared")
            self._state["high_sample_count"] = 0
            return

        self._state["high_sample_count"] = int(self._state.get("high_sample_count") or 0) + 1

        if self._state.get("auto_switch_active"):
            self._state["pressure_state"] = "protected"
            return

        self._state["pressure_state"] = "high"

        if self._state["high_sample_count"] < SUSTAINED_SAMPLE_COUNT:
            return
        if isinstance(self._state.get("suppressed_until"), (int, float)) and self._state["suppressed_until"] > now:
            return

        await self._trigger_locked(high_reason, sample, high_percent)

    def _high_pressure_reason(self, sample: MetricSample) -> tuple[str | None, float | None]:
        candidates: list[tuple[str, float]] = []
        for key, metric in (
            ("cpu_percent", "cpu"),
            ("memory_percent", "memory"),
            ("gpu_percent", "gpu"),
        ):
            value = sample.get(key)
            if isinstance(value, (int, float)) and value >= PRESSURE_THRESHOLD_PERCENT:
                candidates.append((metric, float(value)))
        if not candidates:
            return None, None
        return max(candidates, key=lambda item: item[1])

    async def _trigger_locked(self, reason: str, sample: MetricSample, percent: float | None) -> None:
        now = self._time()
        duration = max(
            SAMPLE_INTERVAL_SECONDS,
            int(self._state.get("high_sample_count") or SUSTAINED_SAMPLE_COUNT) * SAMPLE_INTERVAL_SECONDS,
        )
        payload = {
            "type": "game_mode_auto_switch",
            "source": "game_mode_auto",
            "reason": reason,
            "percent": percent,
            "duration_seconds": duration,
            "sample": sample,
            "timestamp": now,
        }
        self._state["auto_switch_active"] = True
        self._state["auto_switch_source"] = "game_mode_auto"
        self._state["manual_override"] = False
        self._state["prompt_shown"] = True
        self._state["pressure_state"] = "protected"
        self._state["trigger_reason"] = {
            "metric": reason,
            "percent": percent,
            "duration_seconds": duration,
        }
        delivered = await self._broadcaster(payload)
        self._state["last_event"] = {"type": "auto_switch", "ts": now, "delivered": delivered}
        logger.info(
            "[GameModeBeta] auto switch requested: reason=%s percent=%s duration=%ss delivered=%s",
            reason,
            percent,
            duration,
            delivered,
        )


protector = GameModeResourceProtector()
