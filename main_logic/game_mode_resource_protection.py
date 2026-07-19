# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Game Mode Beta runtime resource protection.

The feature uses Activity Tracker's exact-game signal to lower avatar runtime
cost without changing the active model or entering cat form.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PRESSURE_THRESHOLD_PERCENT = 85.0
SAMPLE_INTERVAL_SECONDS = 5.0
SUSTAINED_SAMPLE_COUNT = 6
LAST_SAMPLE_LIMIT = SUSTAINED_SAMPLE_COUNT

GAME_CONFIRM_SNAPSHOT_COUNT = 3
GAME_CONFIRM_MIN_SECONDS = 10.0
RESOURCE_SIGNAL_TTL_SECONDS = 30.0
RESOURCE_DIAGNOSTIC_SAMPLE_INTERVAL_SECONDS = 30.0
RESOURCE_TARGET_FPS = 15
DEEP_SLEEP_DELAY_SECONDS = 90.0
WINDOW_REGISTRATION_TTL_SECONDS = 20.0

DEFAULT_GAME_MODE_SETTINGS = {
    "resource_protection_on_game": True,
    "compact_pet_window_enabled": True,
}

MetricSample = dict[str, Any]
Sampler = Callable[[], MetricSample]
Broadcaster = Callable[[dict[str, Any]], Awaitable[int]]

_PSUTIL_IMPORT_TRIED = False
_PSUTIL: Any = None
_NEKO_PROCESS: Any = None
_GPU_DISABLED_UNTIL = 0.0
_METRIC_ERROR_LOGGED: dict[str, str] = {}


class GameModeSettingsStore:
    """Persist resource-protection settings only."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None

    def _resolve_path(self) -> Path:
        if self._path is not None:
            return self._path
        from utils.config_manager import get_config_manager

        return Path(get_config_manager().get_runtime_config_path("game_mode_beta_settings.json"))

    def load_settings(self) -> dict[str, Any]:
        try:
            path = self._resolve_path()
            with path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.warning("[GameModeBeta] failed to load settings: %s", exc)
            return {}
        return raw if isinstance(raw, dict) else {}

    def save(self, payload: dict[str, Any]) -> None:
        from utils.file_utils import atomic_write_json

        try:
            path = self._resolve_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, payload, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("[GameModeBeta] failed to persist settings: %s", exc)


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
    global _NEKO_PROCESS
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
            if _NEKO_PROCESS is None:
                _NEKO_PROCESS = psutil.Process()
            proc = _NEKO_PROCESS
            cpu_count = psutil.cpu_count() or 1
            sample["neko_cpu_percent"] = float(proc.cpu_percent(interval=None)) / float(cpu_count)
            sample["neko_memory_mb"] = float(proc.memory_info().rss) / (1024 * 1024)
        except Exception as exc:
            _NEKO_PROCESS = None
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


async def discard_game_mode_event(_payload: dict[str, Any]) -> int:
    return 0


class GameModeResourceProtector:
    def __init__(
        self,
        *,
        sampler: Sampler = collect_resource_sample,
        broadcaster: Broadcaster = discard_game_mode_event,
        time_fn: Callable[[], float] = time.time,
        store: GameModeSettingsStore | None = None,
    ) -> None:
        self._sampler = sampler
        self._broadcaster = broadcaster
        self._time = time_fn
        self._store = store
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._windows: dict[str, dict[str, Any]] = {}
        persisted = self._store.load_settings() if self._store is not None else {}
        self._settings = self._normalize_settings(persisted)
        self._state = self._new_state()

    def set_broadcaster(self, broadcaster: Broadcaster) -> None:
        self._broadcaster = broadcaster

    @staticmethod
    def _normalize_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
        source = raw if isinstance(raw, dict) else {}
        return {
            "resource_protection_on_game": source.get("resource_protection_on_game") is not False,
            "compact_pet_window_enabled": source.get("compact_pet_window_enabled") is not False,
        }

    def _new_state(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "pressure_state": "normal",
            "last_samples": [],
            "high_sample_count": 0,
            "last_event": None,
            "resource_session_id": None,
            "resource_session_phase": "idle",
            "resource_enter_snapshot_count": 0,
            "resource_enter_first_seen_at": None,
            "resource_exit_snapshot_count": 0,
            "resource_exit_first_seen_at": None,
            "resource_last_signal_at": None,
            "resource_manual_exit_latched": False,
            "resource_windows": {},
        }

    def snapshot(self) -> dict[str, Any]:
        snap = dict(self._state)
        snap["last_samples"] = list(self._state.get("last_samples") or [])
        snap["settings"] = dict(self._settings)
        snap["registered_window_count"] = len(self._active_window_ids(self._time()))
        return snap

    def settings_snapshot(self) -> dict[str, Any]:
        return dict(self._settings)

    def _active_window_ids(self, now: float) -> list[str]:
        return [
            window_id
            for window_id, meta in self._windows.items()
            if now - float(meta.get("last_seen") or 0.0) <= WINDOW_REGISTRATION_TTL_SECONDS
            and meta.get("window_type") == "pet"
        ]

    def _persist_locked(self) -> None:
        if self._store is None:
            return
        self._store.save(dict(self._settings))

    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        async with self._lock:
            if enabled:
                self._state["enabled"] = True
                self._state["pressure_state"] = "normal"
                self._state["last_event"] = {"type": "enabled", "ts": self._time()}
                self._ensure_task_locked()
                logger.info("[GameModeBeta] enabled")
            else:
                if self._state.get("resource_session_phase") != "idle":
                    await self._restore_resource_session_locked("game-mode-disabled")
                self._state = self._new_state()
                self._cancel_task_locked()
                logger.info("[GameModeBeta] disabled and runtime state cleared")
            return self.snapshot()

    async def set_settings(
        self,
        *,
        resource_protection_on_game: bool | None = None,
        compact_pet_window_enabled: bool | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            previous_resource_protection = self._settings["resource_protection_on_game"]
            self._settings = {
                "resource_protection_on_game": (
                    previous_resource_protection
                    if resource_protection_on_game is None
                    else resource_protection_on_game is True
                ),
                "compact_pet_window_enabled": (
                    self._settings["compact_pet_window_enabled"]
                    if compact_pet_window_enabled is None
                    else compact_pet_window_enabled is True
                ),
            }
            if previous_resource_protection and not self._settings["resource_protection_on_game"]:
                await self._restore_resource_session_locked("resource-protection-disabled")
                self._clear_resource_candidates_locked()
            self._persist_locked()
            return self.settings_snapshot()

    async def register_window(
        self,
        *,
        pet_instance_id: str,
        window_type: str = "pet",
        signal_capabilities: dict[str, Any] | None = None,
        host_capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        window_id = str(pet_instance_id or "").strip()
        if not window_id:
            raise ValueError("pet_instance_id required")
        async with self._lock:
            now = self._time()
            self._windows[window_id] = {
                "window_type": "pet" if window_type == "pet" else str(window_type or "unknown"),
                "signal_capabilities": dict(signal_capabilities or {}),
                "host_capabilities": dict(host_capabilities or {}),
                "last_seen": now,
            }
            if (
                window_type == "pet"
                and self._state.get("resource_session_phase") != "idle"
            ):
                resource_windows = dict(self._state.get("resource_windows") or {})
                resource_windows[window_id] = self._new_resource_window_state(now)
                self._state["resource_windows"] = resource_windows
            resource_active = self._state.get("resource_session_phase") != "idle"
            return {
                "resource_session_active": resource_active,
                "resource_session_id": self._state.get("resource_session_id") if resource_active else None,
                "resource_session_phase": self._state.get("resource_session_phase"),
                "join_resource_protection": resource_active and window_type == "pet",
                "resource_target_fps": RESOURCE_TARGET_FPS,
                "resource_deep_sleep_after_seconds": DEEP_SLEEP_DELAY_SECONDS,
                "compact_pet_window_enabled": self._settings["compact_pet_window_enabled"],
            }

    async def unregister_window(self, pet_instance_id: str) -> dict[str, Any]:
        async with self._lock:
            window_id = str(pet_instance_id or "").strip()
            self._windows.pop(window_id, None)
            resource_windows = dict(self._state.get("resource_windows") or {})
            resource_windows.pop(window_id, None)
            self._state["resource_windows"] = resource_windows
            return self.snapshot()

    async def acknowledge_resource_phase(
        self,
        *,
        resource_session_id: str,
        pet_instance_id: str,
        phase: str,
        compact_lease: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            if (
                resource_session_id != self._state.get("resource_session_id")
                or self._state.get("resource_session_phase") == "idle"
            ):
                return self.snapshot()
            window_id = str(pet_instance_id or "").strip()
            resource_windows = dict(self._state.get("resource_windows") or {})
            current = resource_windows.get(window_id)
            if current is None:
                return self.snapshot()
            updated = dict(current)
            updated["phase"] = phase
            updated["acknowledged_at"] = self._time()
            if compact_lease is not None:
                updated["compact_lease"] = compact_lease
            updated["error"] = str(error)[:160] if error else None
            resource_windows[window_id] = updated
            self._state["resource_windows"] = resource_windows
            return self.snapshot()

    async def record_resource_interaction(
        self,
        *,
        resource_session_id: str,
        pet_instance_id: str,
        interaction: str,
    ) -> dict[str, Any]:
        async with self._lock:
            if (
                resource_session_id != self._state.get("resource_session_id")
                or self._state.get("resource_session_phase") == "idle"
            ):
                return self.snapshot()
            window_id = str(pet_instance_id or "").strip()
            resource_windows = dict(self._state.get("resource_windows") or {})
            current = resource_windows.get(window_id)
            if current is None:
                return self.snapshot()
            now = self._time()
            updated = dict(current)
            updated.update({
                "phase": "soft_protected",
                "last_interaction_at": now,
                "deep_sleep_due_at": now + DEEP_SLEEP_DELAY_SECONDS,
                "last_interaction": interaction,
                "error": None,
            })
            resource_windows[window_id] = updated
            self._state["resource_windows"] = resource_windows
            return self.snapshot()

    async def exit_resource_session(
        self,
        *,
        resource_session_id: str,
        reason: str = "user-exit",
    ) -> dict[str, Any]:
        async with self._lock:
            if resource_session_id != self._state.get("resource_session_id"):
                return self.snapshot()
            self._state["resource_manual_exit_latched"] = True
            await self._restore_resource_session_locked(reason)
            return self.snapshot()

    def sampling_interval_seconds(self) -> float:
        if (
            self._state.get("resource_session_phase") != "idle"
            and self._settings.get("resource_protection_on_game")
        ):
            return RESOURCE_DIAGNOSTIC_SAMPLE_INTERVAL_SECONDS
        return SAMPLE_INTERVAL_SECONDS

    async def ingest_game_snapshot(
        self,
        *,
        exact_game: bool,
        valid: bool = True,
        observed_at: float | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            now = self._time() if observed_at is None else float(observed_at)
            await self._ingest_resource_game_snapshot_locked(
                exact_game=exact_game,
                valid=valid,
                now=now,
            )
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
                await asyncio.sleep(self.sampling_interval_seconds())
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
            await self._maintain_lifecycle_locked(self._time())
            await self._apply_sample_locked(sample)
            return self.snapshot()

    async def _apply_sample_locked(self, sample: MetricSample) -> None:
        samples = list(self._state.get("last_samples") or [])
        samples.append(sample)
        self._state["last_samples"] = samples[-LAST_SAMPLE_LIMIT:]

        high_reason, _high_percent = self._high_pressure_reason(sample)

        if high_reason is None:
            if self._state.get("pressure_state") != "normal":
                self._state["pressure_state"] = "normal"
                logger.info("[GameModeBeta] pressure cleared")
            self._state["high_sample_count"] = 0
            return

        self._state["high_sample_count"] = int(self._state.get("high_sample_count") or 0) + 1

        self._state["pressure_state"] = "high"
        # Resource pressure is diagnostic only. Sampling remains available to
        # status/debug surfaces, but CPU, memory, or GPU load must never cause
        # a model transition by itself.

    def _new_resource_window_state(self, now: float) -> dict[str, Any]:
        return {
            "phase": "soft_protected",
            "acknowledged_at": None,
            "last_interaction_at": now,
            "deep_sleep_due_at": now + DEEP_SLEEP_DELAY_SECONDS,
            "compact_lease": (
                "pending" if self._settings.get("compact_pet_window_enabled") else "disabled"
            ),
            "error": None,
        }

    def _clear_resource_candidates_locked(self) -> None:
        self._state["resource_enter_snapshot_count"] = 0
        self._state["resource_enter_first_seen_at"] = None
        self._state["resource_exit_snapshot_count"] = 0
        self._state["resource_exit_first_seen_at"] = None

    async def _ingest_resource_game_snapshot_locked(
        self,
        *,
        exact_game: bool,
        valid: bool,
        now: float,
    ) -> None:
        if not self._state.get("enabled") or not self._settings.get("resource_protection_on_game"):
            return

        if not valid:
            last_signal = self._state.get("resource_last_signal_at")
            if (
                self._state.get("resource_session_phase") != "idle"
                and isinstance(last_signal, (int, float))
                and now - last_signal > RESOURCE_SIGNAL_TTL_SECONDS
            ):
                await self._restore_resource_session_locked("activity-signal-unavailable")
            return

        self._state["resource_last_signal_at"] = now
        if exact_game:
            self._state["resource_exit_snapshot_count"] = 0
            self._state["resource_exit_first_seen_at"] = None
            if (
                self._state.get("resource_session_phase") != "idle"
                or self._state.get("resource_manual_exit_latched")
            ):
                self._state["resource_enter_snapshot_count"] = 0
                self._state["resource_enter_first_seen_at"] = None
                return
            if int(self._state.get("resource_enter_snapshot_count") or 0) == 0:
                self._state["resource_enter_first_seen_at"] = now
            self._state["resource_enter_snapshot_count"] = (
                int(self._state.get("resource_enter_snapshot_count") or 0) + 1
            )
            first_seen = float(self._state.get("resource_enter_first_seen_at") or now)
            if (
                self._state["resource_enter_snapshot_count"] >= GAME_CONFIRM_SNAPSHOT_COUNT
                and now - first_seen >= GAME_CONFIRM_MIN_SECONDS
            ):
                await self._start_resource_session_locked(now)
            return

        self._state["resource_enter_snapshot_count"] = 0
        self._state["resource_enter_first_seen_at"] = None
        if (
            self._state.get("resource_session_phase") == "idle"
            and not self._state.get("resource_manual_exit_latched")
        ):
            return
        if int(self._state.get("resource_exit_snapshot_count") or 0) == 0:
            self._state["resource_exit_first_seen_at"] = now
        self._state["resource_exit_snapshot_count"] = (
            int(self._state.get("resource_exit_snapshot_count") or 0) + 1
        )
        first_seen = float(self._state.get("resource_exit_first_seen_at") or now)
        if (
            self._state["resource_exit_snapshot_count"] >= GAME_CONFIRM_SNAPSHOT_COUNT
            and now - first_seen >= GAME_CONFIRM_MIN_SECONDS
        ):
            if self._state.get("resource_session_phase") != "idle":
                await self._restore_resource_session_locked("game-exited")
            self._state["resource_manual_exit_latched"] = False
            self._clear_resource_candidates_locked()

    async def _start_resource_session_locked(self, now: float) -> None:
        resource_session_id = uuid.uuid4().hex
        window_ids = sorted(self._active_window_ids(now))
        self._state["resource_session_id"] = resource_session_id
        self._state["resource_session_phase"] = "soft_protected"
        self._state["resource_windows"] = {
            window_id: self._new_resource_window_state(now)
            for window_id in window_ids
        }
        self._state["resource_manual_exit_latched"] = False
        self._state["resource_enter_snapshot_count"] = 0
        self._state["resource_enter_first_seen_at"] = None
        delivered = await self._broadcaster({
            "type": "game_mode_resource_protection_enter",
            "source": "game_mode_resource_protection",
            "resource_session_id": resource_session_id,
            "pet_instance_ids": window_ids,
            "target_fps": RESOURCE_TARGET_FPS,
            "deep_sleep_after_seconds": DEEP_SLEEP_DELAY_SECONDS,
            "compact_pet_window_enabled": self._settings["compact_pet_window_enabled"],
            "timestamp": now,
        })
        if delivered <= 0:
            self._state["resource_session_id"] = None
            self._state["resource_session_phase"] = "idle"
            self._state["resource_windows"] = {}

    async def _restore_resource_session_locked(self, reason: str) -> None:
        resource_session_id = self._state.get("resource_session_id")
        if not resource_session_id:
            return
        await self._broadcaster({
            "type": "game_mode_resource_protection_restore",
            "source": "game_mode_resource_protection",
            "resource_session_id": resource_session_id,
            "pet_instance_ids": sorted((self._state.get("resource_windows") or {}).keys()),
            "reason": reason,
            "timestamp": self._time(),
        })
        self._state["resource_session_id"] = None
        self._state["resource_session_phase"] = "idle"
        self._state["resource_windows"] = {}
        self._state["resource_enter_snapshot_count"] = 0
        self._state["resource_enter_first_seen_at"] = None

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

    async def _maintain_lifecycle_locked(self, now: float) -> None:
        last_resource_signal = self._state.get("resource_last_signal_at")
        if (
            self._state.get("resource_session_phase") != "idle"
            and isinstance(last_resource_signal, (int, float))
            and now - last_resource_signal > RESOURCE_SIGNAL_TTL_SECONDS
        ):
            await self._restore_resource_session_locked("activity-signal-unavailable")


protector = GameModeResourceProtector(
    store=None if "pytest" in sys.modules else GameModeSettingsStore(),
)
