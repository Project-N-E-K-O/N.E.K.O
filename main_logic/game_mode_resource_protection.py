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
MANUAL_RESTORE_COOLDOWN_SECONDS = 10 * 60
LAST_SAMPLE_LIMIT = SUSTAINED_SAMPLE_COUNT

GAME_CONFIRM_SNAPSHOT_COUNT = 3
GAME_CONFIRM_MIN_SECONDS = 10.0
GAME_SIGNAL_TTL_SECONDS = 15.0
RESOURCE_SIGNAL_TTL_SECONDS = 30.0
RESOURCE_DIAGNOSTIC_SAMPLE_INTERVAL_SECONDS = 30.0
RESOURCE_TARGET_FPS = 15
GAME_SMART_SAMPLE_COUNT = 2
GAME_SMART_THRESHOLDS = {
    "cpu_percent": ("cpu", 70.0),
    "memory_percent": ("memory", 80.0),
    "gpu_percent": ("gpu", 90.0),
}
SWITCH_ACK_TIMEOUT_SECONDS = 5.0
SWITCH_RETRY_BACKOFF_SECONDS = 30.0
DEEP_SLEEP_DELAY_SECONDS = 90.0
WINDOW_REGISTRATION_TTL_SECONDS = 20.0

DEFAULT_GAME_MODE_SETTINGS = {
    "auto_cat_on_game": False,
    "game_trigger_mode": "smart",
    "resource_protection_on_game": True,
    "compact_pet_window_enabled": True,
}
VALID_GAME_TRIGGER_MODES = {"smart", "instant"}

MetricSample = dict[str, Any]
Sampler = Callable[[], MetricSample]
Broadcaster = Callable[[dict[str, Any]], Awaitable[int]]

_PSUTIL_IMPORT_TRIED = False
_PSUTIL: Any = None
_GPU_DISABLED_UNTIL = 0.0
_METRIC_ERROR_LOGGED: dict[str, str] = {}


class GameModeSettingsStore:
    """Persist only user settings and the manual-restore cooldown."""

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
        self._window_acks: dict[str, dict[str, Any]] = {}
        self._deep_sleep_acks: dict[str, bool] = {}
        persisted = self._store.load_settings() if self._store is not None else {}
        self._settings = self._normalize_settings(persisted)
        raw_suppressed_until = persisted.get("suppressed_until")
        self._persisted_suppressed_until = (
            float(raw_suppressed_until)
            if isinstance(raw_suppressed_until, (int, float))
            else None
        )
        self._state = self._new_state()

    def set_broadcaster(self, broadcaster: Broadcaster) -> None:
        self._broadcaster = broadcaster

    @staticmethod
    def _normalize_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
        source = raw if isinstance(raw, dict) else {}
        mode = source.get("game_trigger_mode")
        if mode not in VALID_GAME_TRIGGER_MODES:
            mode = DEFAULT_GAME_MODE_SETTINGS["game_trigger_mode"]
        return {
            "auto_cat_on_game": source.get("auto_cat_on_game") is True,
            "game_trigger_mode": mode,
            "resource_protection_on_game": source.get("resource_protection_on_game") is not False,
            "compact_pet_window_enabled": source.get("compact_pet_window_enabled") is not False,
        }

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
            "cycle_id": None,
            "cycle_phase": "idle",
            "cycle_trigger": None,
            "cycle_started_at": None,
            "ack_deadline": None,
            "expected_window_count": 0,
            "ack_success_count": 0,
            "ack_failure_count": 0,
            "owned_window_count": 0,
            "deep_sleep_due_at": None,
            "deep_sleep_success_count": 0,
            "game_snapshot_count": 0,
            "game_first_seen_at": None,
            "game_last_signal_at": None,
            "game_confirmed": False,
            "game_smart_high_count": 0,
            "semantic_signal_notice_shown": False,
            "semantic_fuse_enabled": os.environ.get("NEKO_GAME_MODE_SEMANTIC_ENABLED", "1") != "0",
            "semantic_error_count": 0,
            "retry_not_before": None,
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
        snap.pop("expected_window_ids", None)
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
        payload = dict(self._settings)
        payload["suppressed_until"] = self._persisted_suppressed_until
        self._store.save(payload)

    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        async with self._lock:
            if enabled:
                self._state["enabled"] = True
                self._state["pressure_state"] = "normal"
                now = self._time()
                if (
                    isinstance(self._persisted_suppressed_until, (int, float))
                    and self._persisted_suppressed_until > now
                ):
                    self._state["suppressed_until"] = self._persisted_suppressed_until
                elif self._persisted_suppressed_until is not None:
                    self._persisted_suppressed_until = None
                    self._persist_locked()
                self._state["last_event"] = {"type": "enabled", "ts": self._time()}
                self._ensure_task_locked()
                logger.info("[GameModeBeta] enabled")
            else:
                if self._state.get("resource_session_phase") != "idle":
                    await self._restore_resource_session_locked("game-mode-disabled")
                if self._state.get("auto_switch_active"):
                    await self._broadcast_restore_locked("game-mode-disabled")
                if self._store is None:
                    self._persisted_suppressed_until = None
                self._state = self._new_state()
                self._window_acks.clear()
                self._deep_sleep_acks.clear()
                self._cancel_task_locked()
                logger.info("[GameModeBeta] disabled and runtime state cleared")
            return self.snapshot()

    async def set_settings(
        self,
        *,
        auto_cat_on_game: bool,
        game_trigger_mode: str,
        resource_protection_on_game: bool | None = None,
        compact_pet_window_enabled: bool | None = None,
    ) -> dict[str, Any]:
        if game_trigger_mode not in VALID_GAME_TRIGGER_MODES:
            raise ValueError("game_trigger_mode must be 'smart' or 'instant'")
        async with self._lock:
            previous_auto_cat = self._settings["auto_cat_on_game"]
            previous_resource_protection = self._settings["resource_protection_on_game"]
            self._settings = {
                "auto_cat_on_game": auto_cat_on_game is True,
                "game_trigger_mode": game_trigger_mode,
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
            if previous_auto_cat and not self._settings["auto_cat_on_game"]:
                self._clear_game_candidate_locked()
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
            cycle_active = self._state.get("cycle_phase") in {"switching", "protected", "deep_sleep"}
            resource_active = self._state.get("resource_session_phase") != "idle"
            return {
                "cycle_active": cycle_active,
                "cycle_id": self._state.get("cycle_id") if cycle_active else None,
                "cycle_phase": self._state.get("cycle_phase"),
                "join_as_cat": cycle_active and window_type == "pet",
                "deep_sleep_due_at": self._state.get("deep_sleep_due_at"),
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
            removed_ack = self._window_acks.pop(window_id, None)
            self._deep_sleep_acks.pop(window_id, None)
            if self._state.get("cycle_phase") == "switching":
                expected = set(self._state.get("expected_window_ids") or [])
                if window_id in expected:
                    expected.discard(window_id)
                    self._state["expected_window_ids"] = sorted(expected)
                    self._state["expected_window_count"] = len(expected)
                    self._refresh_ack_counts_locked()
                    if not expected:
                        await self._fail_switch_locked("targets-disconnected")
                    elif expected.issubset(self._window_acks):
                        if any(self._window_acks[item]["status"] == "failed" for item in expected):
                            await self._fail_switch_locked("ack-failed")
                        else:
                            await self._finalize_switch_locked()
            if removed_ack and removed_ack.get("status") == "protected":
                owned_count = sum(
                    1 for ack in self._window_acks.values() if ack.get("status") == "protected"
                )
                self._state["owned_window_count"] = owned_count
                self._state["auto_switch_active"] = owned_count > 0
                if (
                    owned_count == 0
                    and self._state.get("cycle_phase") in {"protected", "deep_sleep"}
                ):
                    self._state["pressure_state"] = "normal"
                    self._state["trigger_reason"] = None
                    self._end_cycle_locked()
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

    async def acknowledge_switch(
        self,
        *,
        cycle_id: str,
        pet_instance_id: str,
        status: str,
    ) -> dict[str, Any]:
        async with self._lock:
            phase = self._state.get("cycle_phase")
            if cycle_id != self._state.get("cycle_id") or phase not in {"switching", "protected", "deep_sleep"}:
                return self.snapshot()
            window_id = str(pet_instance_id or "").strip()
            if not window_id:
                return self.snapshot()
            normalized_status = status if status in {"protected", "already_protected", "failed"} else "failed"
            self._window_acks[window_id] = {
                "status": normalized_status,
                "ts": self._time(),
            }
            if phase in {"protected", "deep_sleep"}:
                owned = sum(1 for ack in self._window_acks.values() if ack.get("status") == "protected")
                self._state["owned_window_count"] = owned
                self._state["auto_switch_active"] = owned > 0
                if normalized_status == "failed" and owned == 0:
                    await self._fail_switch_locked("late-join-failed")
                elif normalized_status == "already_protected" and owned == 0:
                    self._state["pressure_state"] = "normal"
                    self._state["trigger_reason"] = None
                    self._state["last_event"] = {"type": "already_protected", "ts": self._time()}
                    self._end_cycle_locked(preserve_last_event=True)
                return self.snapshot()
            self._refresh_ack_counts_locked()
            expected = set(self._state.get("expected_window_ids") or [])
            if expected and expected.issubset(self._window_acks):
                if any(self._window_acks[item]["status"] == "failed" for item in expected):
                    await self._fail_switch_locked("ack-failed")
                else:
                    await self._finalize_switch_locked()
            return self.snapshot()

    async def acknowledge_deep_sleep(
        self,
        *,
        cycle_id: str,
        pet_instance_id: str,
        success: bool,
    ) -> dict[str, Any]:
        async with self._lock:
            if cycle_id != self._state.get("cycle_id") or self._state.get("cycle_phase") != "deep_sleep":
                return self.snapshot()
            window_id = str(pet_instance_id or "").strip()
            if window_id:
                self._deep_sleep_acks[window_id] = success is True
                self._state["deep_sleep_success_count"] = sum(self._deep_sleep_acks.values())
            return self.snapshot()

    async def mark_manual_restore(self, pet_instance_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            now = self._time()
            if self._state.get("enabled") and self._state.get("auto_switch_active"):
                self._state["suppressed_until"] = now + MANUAL_RESTORE_COOLDOWN_SECONDS
                self._persisted_suppressed_until = self._state["suppressed_until"]
                self._persist_locked()
                self._state["manual_override"] = True
                if pet_instance_id:
                    ack = self._window_acks.get(str(pet_instance_id))
                    if ack and ack.get("status") == "protected":
                        ack["status"] = "restored"
                else:
                    for ack in self._window_acks.values():
                        if ack.get("status") == "protected":
                            ack["status"] = "restored"
                owned_count = sum(1 for ack in self._window_acks.values() if ack.get("status") == "protected")
                self._state["owned_window_count"] = owned_count
                self._state["auto_switch_active"] = owned_count > 0
                if not self._state["auto_switch_active"]:
                    self._end_cycle_locked()
                self._state["pressure_state"] = "normal"
                self._state["last_event"] = {"type": "manual_restore", "ts": now}
                logger.info("[GameModeBeta] manual restore cooldown started")
            return self.snapshot()

    async def ingest_game_snapshot(
        self,
        *,
        exact_game: bool,
        valid: bool = True,
        observed_at: float | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            now = self._time() if observed_at is None else float(observed_at)
            self._state["semantic_error_count"] = 0
            await self._ingest_resource_game_snapshot_locked(
                exact_game=exact_game,
                valid=valid,
                now=now,
            )
            if not self._semantic_entry_available_locked(now):
                return self.snapshot()
            if not valid:
                await self._expire_game_signal_locked(now)
                return self.snapshot()
            self._state["semantic_signal_notice_shown"] = False
            self._state["game_last_signal_at"] = now
            if not exact_game:
                self._clear_game_candidate_locked(keep_last_signal=True)
                return self.snapshot()
            if int(self._state.get("game_snapshot_count") or 0) == 0:
                self._state["game_first_seen_at"] = now
            self._state["game_snapshot_count"] = int(self._state.get("game_snapshot_count") or 0) + 1
            first_seen = float(self._state.get("game_first_seen_at") or now)
            confirmed = (
                self._state["game_snapshot_count"] >= GAME_CONFIRM_SNAPSHOT_COUNT
                and now - first_seen >= GAME_CONFIRM_MIN_SECONDS
            )
            self._state["game_confirmed"] = confirmed
            if confirmed and self._settings["game_trigger_mode"] == "instant":
                await self._trigger_locked(
                    "exact_game",
                    {"ts": now, "errors": {}},
                    None,
                    trigger_source="game_semantic",
                    duration_seconds=now - first_seen,
                )
            return self.snapshot()

    async def reset_game_candidate(self, reason: str = "external-reset") -> dict[str, Any]:
        async with self._lock:
            self._clear_game_candidate_locked()
            self._state["last_event"] = {"type": "game_candidate_reset", "reason": reason, "ts": self._time()}
            return self.snapshot()

    async def record_semantic_error(self) -> dict[str, Any]:
        async with self._lock:
            if not self._state.get("enabled") or not self._settings.get("auto_cat_on_game"):
                return self.snapshot()
            count = int(self._state.get("semantic_error_count") or 0) + 1
            self._state["semantic_error_count"] = count
            self._clear_game_candidate_locked()
            if count >= 3:
                self._state["semantic_fuse_enabled"] = False
                self._state["last_event"] = {"type": "semantic_fuse_open", "ts": self._time()}
                logger.warning("[GameModeBeta] semantic entry disabled after repeated local failures")
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
        now = self._time()
        suppressed_until = self._state.get("suppressed_until")
        if isinstance(suppressed_until, (int, float)) and suppressed_until <= now:
            self._state["suppressed_until"] = None
            self._persisted_suppressed_until = None
            self._persist_locked()
            self._state["last_event"] = {"type": "cooldown_ended", "ts": now}
            logger.info("[GameModeBeta] manual restore cooldown ended")

        await self._apply_game_smart_sample_locked(sample, now)

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

    async def _trigger_locked(
        self,
        reason: str,
        sample: MetricSample,
        percent: float | None,
        *,
        trigger_source: str = "resource_pressure",
        duration_seconds: float | None = None,
    ) -> None:
        now = self._time()
        if self._state.get("cycle_phase") != "idle":
            return
        if not self._trigger_allowed_locked(now):
            return
        duration = duration_seconds
        if duration is None:
            duration = max(
                SAMPLE_INTERVAL_SECONDS,
                int(self._state.get("high_sample_count") or SUSTAINED_SAMPLE_COUNT) * SAMPLE_INTERVAL_SECONDS,
            )
        cycle_id = uuid.uuid4().hex
        expected_window_ids = self._active_window_ids(now)
        payload = {
            "type": "game_mode_auto_switch",
            "source": "game_mode_auto",
            "reason": reason,
            "percent": percent,
            "duration_seconds": duration,
            "sample": sample,
            "timestamp": now,
            "cycle_id": cycle_id,
            "trigger_source": trigger_source,
            "ack_timeout_seconds": SWITCH_ACK_TIMEOUT_SECONDS,
            "deep_sleep_after_seconds": DEEP_SLEEP_DELAY_SECONDS,
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
        self._state["cycle_id"] = cycle_id
        self._state["cycle_phase"] = "switching" if expected_window_ids else "protected"
        self._state["cycle_trigger"] = trigger_source
        self._state["cycle_started_at"] = now
        self._state["expected_window_ids"] = expected_window_ids
        self._state["expected_window_count"] = len(expected_window_ids)
        self._state["ack_deadline"] = now + SWITCH_ACK_TIMEOUT_SECONDS if expected_window_ids else None
        self._state["deep_sleep_due_at"] = now + DEEP_SLEEP_DELAY_SECONDS if not expected_window_ids else None
        self._window_acks.clear()
        self._deep_sleep_acks.clear()
        self._clear_game_candidate_locked()
        delivered = await self._broadcaster(payload)
        if delivered <= 0:
            await self._fail_switch_locked("not-delivered")
            return
        self._state["last_event"] = {"type": "auto_switch", "ts": now, "delivered": delivered}
        logger.info(
            "[GameModeBeta] auto switch requested: reason=%s percent=%s duration=%ss delivered=%s",
            reason,
            percent,
            duration,
            delivered,
        )

    def _trigger_allowed_locked(self, now: float) -> bool:
        if not self._state.get("enabled"):
            return False
        suppressed_until = self._state.get("suppressed_until")
        if isinstance(suppressed_until, (int, float)) and suppressed_until > now:
            return False
        retry_not_before = self._state.get("retry_not_before")
        if isinstance(retry_not_before, (int, float)) and retry_not_before > now:
            return False
        return True

    def _semantic_entry_available_locked(self, now: float) -> bool:
        return bool(
            self._state.get("enabled")
            and self._settings.get("auto_cat_on_game")
            and self._state.get("semantic_fuse_enabled")
            and self._state.get("cycle_phase") == "idle"
            and self._trigger_allowed_locked(now)
        )

    async def _apply_game_smart_sample_locked(self, sample: MetricSample, now: float) -> None:
        if not (
            self._semantic_entry_available_locked(now)
            and self._settings.get("game_trigger_mode") == "smart"
            and self._state.get("game_confirmed")
        ):
            self._state["game_smart_high_count"] = 0
            return
        candidates: list[tuple[str, float]] = []
        for key, (metric, threshold) in GAME_SMART_THRESHOLDS.items():
            value = sample.get(key)
            if isinstance(value, (int, float)) and value >= threshold:
                candidates.append((metric, float(value)))
        if not candidates:
            self._state["game_smart_high_count"] = 0
            return
        self._state["game_smart_high_count"] = min(
            GAME_SMART_SAMPLE_COUNT,
            int(self._state.get("game_smart_high_count") or 0) + 1,
        )
        # Smart-mode pressure thresholds are retained as diagnostics only.
        # Exact-game instant mode is the sole semantic auto-transition path.

    def _clear_game_candidate_locked(self, *, keep_last_signal: bool = False) -> None:
        last_signal = self._state.get("game_last_signal_at") if keep_last_signal else None
        self._state["game_snapshot_count"] = 0
        self._state["game_first_seen_at"] = None
        self._state["game_last_signal_at"] = last_signal
        self._state["game_confirmed"] = False
        self._state["game_smart_high_count"] = 0

    async def _expire_game_signal_locked(self, now: float) -> None:
        last_signal = self._state.get("game_last_signal_at")
        if not isinstance(last_signal, (int, float)) or now - last_signal <= GAME_SIGNAL_TTL_SECONDS:
            return
        had_candidate = bool(self._state.get("game_snapshot_count") or self._state.get("game_confirmed"))
        self._clear_game_candidate_locked()
        if had_candidate and not self._state.get("semantic_signal_notice_shown"):
            self._state["semantic_signal_notice_shown"] = True
            await self._broadcaster({
                "type": "game_mode_semantic_signal_unavailable",
                "source": "game_mode_auto",
                "timestamp": now,
            })

    def _refresh_ack_counts_locked(self) -> None:
        expected = set(self._state.get("expected_window_ids") or [])
        relevant = [self._window_acks[item] for item in expected if item in self._window_acks]
        self._state["ack_success_count"] = sum(
            1 for ack in relevant if ack.get("status") in {"protected", "already_protected"}
        )
        self._state["ack_failure_count"] = sum(1 for ack in relevant if ack.get("status") == "failed")

    async def _finalize_switch_locked(self) -> None:
        now = self._time()
        owned = sum(1 for ack in self._window_acks.values() if ack.get("status") == "protected")
        if owned == 0:
            self._state["auto_switch_active"] = False
            self._state["pressure_state"] = "normal"
            self._state["trigger_reason"] = None
            self._state["last_event"] = {"type": "already_protected", "ts": now}
            self._end_cycle_locked(preserve_last_event=True)
            return
        self._state["cycle_phase"] = "protected"
        self._state["ack_deadline"] = None
        self._state["owned_window_count"] = owned
        self._state["auto_switch_active"] = owned > 0
        self._state["pressure_state"] = "protected"
        self._state["deep_sleep_due_at"] = now + DEEP_SLEEP_DELAY_SECONDS if owned else None
        self._state["last_event"] = {"type": "switch_confirmed", "ts": now, "owned": owned}
        await self._broadcaster({
            "type": "game_mode_switch_confirmed",
            "source": "game_mode_auto",
            "cycle_id": self._state.get("cycle_id"),
            "deep_sleep_after_seconds": DEEP_SLEEP_DELAY_SECONDS,
            "timestamp": now,
        })

    async def _fail_switch_locked(self, reason: str) -> None:
        now = self._time()
        self._state["retry_not_before"] = now + SWITCH_RETRY_BACKOFF_SECONDS
        self._state["last_event"] = {"type": "switch_failed", "reason": reason, "ts": now}
        await self._broadcaster({
            "type": "game_mode_switch_failed",
            "source": "game_mode_auto",
            "cycle_id": self._state.get("cycle_id"),
            "retry_after_seconds": SWITCH_RETRY_BACKOFF_SECONDS,
            "timestamp": now,
        })
        self._state["auto_switch_active"] = False
        self._state["pressure_state"] = "normal"
        self._state["trigger_reason"] = None
        self._end_cycle_locked(preserve_last_event=True)

    def _end_cycle_locked(self, *, preserve_last_event: bool = False) -> None:
        last_event = self._state.get("last_event")
        for key, value in (
            ("cycle_id", None),
            ("cycle_phase", "idle"),
            ("cycle_trigger", None),
            ("cycle_started_at", None),
            ("ack_deadline", None),
            ("expected_window_count", 0),
            ("ack_success_count", 0),
            ("ack_failure_count", 0),
            ("owned_window_count", 0),
            ("deep_sleep_due_at", None),
            ("deep_sleep_success_count", 0),
        ):
            self._state[key] = value
        self._state.pop("expected_window_ids", None)
        self._window_acks.clear()
        self._deep_sleep_acks.clear()
        if preserve_last_event:
            self._state["last_event"] = last_event

    async def _maintain_lifecycle_locked(self, now: float) -> None:
        last_resource_signal = self._state.get("resource_last_signal_at")
        if (
            self._state.get("resource_session_phase") != "idle"
            and isinstance(last_resource_signal, (int, float))
            and now - last_resource_signal > RESOURCE_SIGNAL_TTL_SECONDS
        ):
            await self._restore_resource_session_locked("activity-signal-unavailable")
        await self._expire_game_signal_locked(now)
        if self._state.get("cycle_phase") == "switching":
            deadline = self._state.get("ack_deadline")
            if isinstance(deadline, (int, float)) and now >= deadline:
                await self._fail_switch_locked("ack-timeout")
                return
        if self._state.get("cycle_phase") == "protected":
            due_at = self._state.get("deep_sleep_due_at")
            if isinstance(due_at, (int, float)) and now >= due_at:
                owned_ids = [
                    window_id
                    for window_id, ack in self._window_acks.items()
                    if ack.get("status") == "protected"
                ]
                self._state["cycle_phase"] = "deep_sleep"
                self._state["deep_sleep_due_at"] = None
                self._state["last_event"] = {"type": "deep_sleep_requested", "ts": now}
                await self._broadcaster({
                    "type": "game_mode_deep_sleep",
                    "source": "game_mode_auto",
                    "cycle_id": self._state.get("cycle_id"),
                    "pet_instance_ids": owned_ids,
                    "timestamp": now,
                })

    async def _broadcast_restore_locked(self, reason: str) -> None:
        owned_ids = [
            window_id
            for window_id, ack in self._window_acks.items()
            if ack.get("status") == "protected"
        ]
        await self._broadcaster({
            "type": "game_mode_restore",
            "source": "game_mode_auto",
            "cycle_id": self._state.get("cycle_id"),
            "pet_instance_ids": owned_ids,
            "reason": reason,
            "timestamp": self._time(),
        })


protector = GameModeResourceProtector(
    store=None if "pytest" in sys.modules else GameModeSettingsStore(),
)
