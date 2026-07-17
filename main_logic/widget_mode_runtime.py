# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Runtime state and lifecycle coordination for Widget Mode."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Protocol, TypedDict

logger = logging.getLogger(__name__)

WIDGET_MODE_PROTOCOL_VERSION = 1
PRESSURE_THRESHOLD_PERCENT = 85.0
SAMPLE_INTERVAL_SECONDS = 5.0
SUSTAINED_SAMPLE_COUNT = 6
LAST_RESOURCE_SAMPLE_LIMIT = SUSTAINED_SAMPLE_COUNT
ACTIVITY_CONFIRM_SNAPSHOT_COUNT = 3
ACTIVITY_CONFIRM_MIN_SECONDS = 10.0
ACTIVITY_SIGNAL_TTL_SECONDS = 15.0
COMPACTION_ACK_TIMEOUT_SECONDS = 5.0
COMPACTION_RETRY_BACKOFF_SECONDS = 30.0
RENDERER_SUSPENSION_DELAY_SECONDS = 90.0
USER_RESTORE_COOLDOWN_SECONDS = 10 * 60
WINDOW_REGISTRATION_TTL_SECONDS = 20.0
WIDGET_MODE_ACTIVITY_SOURCE = "widget_mode_activity_compaction"

DEFAULT_WIDGET_MODE_SETTINGS = {
    "activity_response": "disabled",
}
VALID_ACTIVITY_RESPONSE_POLICIES = {
    "disabled",
    "observe_only",
    "compact_on_confirm",
}


class ResourceSample(TypedDict, total=False):
    ts: float
    cpu_percent: float | None
    memory_percent: float | None
    gpu_percent: float | None
    gpu_vram_percent: float | None
    neko_cpu_percent: float | None
    neko_memory_mb: float | None
    errors: dict[str, str]


class ResourceSampler(Protocol):
    def __call__(self) -> ResourceSample: ...


class EventBroadcaster(Protocol):
    async def __call__(self, payload: dict[str, Any]) -> int: ...


_PSUTIL_IMPORT_TRIED = False
_PSUTIL: Any = None
_GPU_DISABLED_UNTIL = 0.0
_METRIC_ERROR_LOGGED: dict[str, str] = {}


class WidgetModeSettingsStore:
    """Persist Widget Mode settings without reading any legacy settings file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None

    def _resolve_path(self) -> Path:
        if self._path is not None:
            return self._path
        from utils.config_manager import get_config_manager

        return Path(get_config_manager().get_runtime_config_path("widget_mode_settings.json"))

    def load_settings(self) -> dict[str, Any]:
        try:
            with self._resolve_path().open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.warning("[WidgetMode] failed to load settings: %s", exc)
            return {}
        return raw if isinstance(raw, dict) else {}

    def save(self, payload: dict[str, Any]) -> None:
        from utils.file_utils import atomic_write_json

        try:
            path = self._resolve_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, payload, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("[WidgetMode] failed to persist settings: %s", exc)
            raise

    async def save_async(self, payload: dict[str, Any]) -> None:
        await asyncio.to_thread(self.save, payload)


def _remember_metric_error(metric: str, error: Any) -> str:
    message = str(error)[:160]
    if _METRIC_ERROR_LOGGED.get(metric) != message:
        _METRIC_ERROR_LOGGED[metric] = message
        logger.warning("[WidgetMode] %s sample unavailable: %s", metric, message)
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
            logger.warning("[WidgetMode] psutil unavailable: %s", exc)
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
            parts = [part.strip() for part in raw_line.split(",")]
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
        return {
            "gpu_percent": None,
            "gpu_vram_percent": None,
            "gpu_error": _remember_metric_error("gpu", exc),
        }


def collect_resource_sample() -> ResourceSample:
    now = time.time()
    sample: ResourceSample = {
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
    errors = sample["errors"]
    if psutil is not None:
        try:
            sample["cpu_percent"] = float(psutil.cpu_percent(interval=None))
        except Exception as exc:
            errors["cpu"] = _remember_metric_error("cpu", exc)
        try:
            sample["memory_percent"] = float(psutil.virtual_memory().percent)
        except Exception as exc:
            errors["memory"] = _remember_metric_error("memory", exc)
        try:
            proc = psutil.Process()
            cpu_count = psutil.cpu_count() or 1
            sample["neko_cpu_percent"] = float(proc.cpu_percent(interval=None)) / float(cpu_count)
            sample["neko_memory_mb"] = float(proc.memory_info().rss) / (1024 * 1024)
        except Exception as exc:
            errors["neko_process"] = _remember_metric_error("neko_process", exc)
    else:
        errors["psutil"] = "unavailable"
    gpu_sample = _read_nvidia_gpu_sample(now)
    sample["gpu_percent"] = gpu_sample.get("gpu_percent")
    sample["gpu_vram_percent"] = gpu_sample.get("gpu_vram_percent")
    if gpu_sample.get("gpu_error"):
        errors["gpu"] = str(gpu_sample["gpu_error"])
    return sample


async def discard_widget_mode_event(_payload: dict[str, Any]) -> int:
    return 0


class WidgetModeCoordinator:
    def __init__(
        self,
        *,
        sampler: ResourceSampler = collect_resource_sample,
        broadcaster: EventBroadcaster = discard_widget_mode_event,
        time_fn: Any = time.time,
        store: WidgetModeSettingsStore | None = None,
    ) -> None:
        self._sampler = sampler
        self._broadcaster = broadcaster
        self._time = time_fn
        self._store = store
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._windows: dict[str, dict[str, Any]] = {}
        self._window_acks: dict[str, dict[str, Any]] = {}
        self._renderer_suspension_acks: dict[str, bool] = {}
        persisted = self._store.load_settings() if self._store is not None else {}
        self._settings = self._normalize_settings(persisted)
        raw_suppressed_until = persisted.get("suppressed_until")
        self._persisted_suppressed_until = (
            float(raw_suppressed_until)
            if isinstance(raw_suppressed_until, (int, float))
            else None
        )
        self._state = self._new_state()

    def set_event_broadcaster(self, broadcaster: EventBroadcaster) -> None:
        self._broadcaster = broadcaster

    @staticmethod
    def _normalize_settings(raw: dict[str, Any] | None) -> dict[str, str]:
        source = raw if isinstance(raw, dict) else {}
        policy = source.get("activity_response")
        if policy not in VALID_ACTIVITY_RESPONSE_POLICIES:
            policy = DEFAULT_WIDGET_MODE_SETTINGS["activity_response"]
        return {"activity_response": policy}

    def _new_state(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "resource_pressure_state": "normal",
            "last_resource_samples": [],
            "high_resource_sample_count": 0,
            "last_resource_reason": None,
            "activity_signal_count": 0,
            "activity_first_seen_at": None,
            "activity_last_seen_at": None,
            "activity_confirmed": False,
            "activity_signal_available": True,
            "activity_signal_error_count": 0,
            "activity_diagnostic_high_count": 0,
            "compaction_cycle_id": None,
            "compaction_phase": "idle",
            "compaction_source": None,
            "compaction_started_at": None,
            "compaction_ack_deadline": None,
            "expected_window_count": 0,
            "compaction_ack_success_count": 0,
            "compaction_ack_failure_count": 0,
            "owned_window_count": 0,
            "renderer_suspension_due_at": None,
            "renderer_suspension_success_count": 0,
            "user_restore_active": False,
            "suppressed_until": None,
            "retry_not_before": None,
            "last_event": None,
        }

    def snapshot(self) -> dict[str, Any]:
        snap = dict(self._state)
        snap.pop("expected_window_ids", None)
        snap["last_resource_samples"] = list(self._state.get("last_resource_samples") or [])
        snap["settings"] = dict(self._settings)
        snap["registered_window_count"] = len(self._active_window_ids(self._time()))
        return snap

    def settings_snapshot(self) -> dict[str, str]:
        return dict(self._settings)

    def _active_window_ids(self, now: float) -> list[str]:
        return [
            window_id
            for window_id, meta in self._windows.items()
            if now - float(meta.get("last_seen") or 0.0) <= WINDOW_REGISTRATION_TTL_SECONDS
            and meta.get("window_type") == "pet"
            and meta.get("widget_mode_capable") is True
        ]

    async def _persist_locked(self) -> None:
        if self._store is None:
            return
        payload: dict[str, Any] = dict(self._settings)
        payload["suppressed_until"] = self._persisted_suppressed_until
        await self._store.save_async(payload)

    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        async with self._lock:
            if enabled:
                self._state["enabled"] = True
                now = self._time()
                if self._persisted_suppressed_until and self._persisted_suppressed_until > now:
                    self._state["suppressed_until"] = self._persisted_suppressed_until
                    self._state["user_restore_active"] = True
                elif self._persisted_suppressed_until is not None:
                    self._persisted_suppressed_until = None
                    await self._persist_locked()
                self._state["last_event"] = {"type": "enabled", "ts": now}
                self._ensure_task_locked()
                logger.info("[WidgetMode] enabled")
            else:
                if self._state.get("compaction_phase") != "idle":
                    await self._broadcast_restore_request_locked("widget-mode-disabled")
                self._state = self._new_state()
                self._window_acks.clear()
                self._renderer_suspension_acks.clear()
                self._cancel_task_locked()
                logger.info("[WidgetMode] disabled and runtime state cleared")
            return self.snapshot()

    async def update_settings(self, *, activity_response: str) -> dict[str, str]:
        if activity_response not in VALID_ACTIVITY_RESPONSE_POLICIES:
            raise ValueError("activity_response policy is invalid")
        async with self._lock:
            previous_settings = dict(self._settings)
            previous_state = dict(self._state)
            self._settings = {"activity_response": activity_response}
            if activity_response == "disabled":
                self._clear_activity_candidate_locked()
            try:
                await self._persist_locked()
            except Exception:
                self._settings = previous_settings
                self._state = previous_state
                raise
            return self.settings_snapshot()

    async def register_window(
        self,
        *,
        pet_instance_id: str,
        window_type: str = "pet",
        signal_capabilities: dict[str, Any] | None = None,
        widget_mode_protocol_version: int | None = None,
        widget_mode_compaction_lease_v1: bool = False,
    ) -> dict[str, Any]:
        window_id = str(pet_instance_id or "").strip()
        if not window_id:
            raise ValueError("pet_instance_id required")
        async with self._lock:
            now = self._time()
            normalized_type = "pet" if window_type == "pet" else str(window_type or "unknown")
            protocol_compatible = widget_mode_protocol_version == WIDGET_MODE_PROTOCOL_VERSION
            capable = bool(
                normalized_type == "pet"
                and protocol_compatible
                and widget_mode_compaction_lease_v1 is True
            )
            self._windows[window_id] = {
                "window_type": normalized_type,
                "signal_capabilities": dict(signal_capabilities or {}),
                "widget_mode_protocol_version": widget_mode_protocol_version,
                "widget_mode_capable": capable,
                "last_seen": now,
            }
            phase = self._state.get("compaction_phase")
            cycle_id = self._state.get("compaction_cycle_id")
            if capable and phase == "compacting" and cycle_id:
                expected = set(self._state.get("expected_window_ids") or [])
                if window_id not in expected:
                    expected.add(window_id)
                    self._state["expected_window_ids"] = sorted(expected)
                    self._state["expected_window_count"] = len(expected)
                    extended_deadline = now + COMPACTION_ACK_TIMEOUT_SECONDS
                    current_deadline = self._state.get("compaction_ack_deadline")
                    if (
                        not isinstance(current_deadline, (int, float))
                        or extended_deadline > current_deadline
                    ):
                        self._state["compaction_ack_deadline"] = extended_deadline
            active_phase = phase in {"compacting", "compacted", "renderer_suspended"}
            return {
                "widget_mode_protocol_version": WIDGET_MODE_PROTOCOL_VERSION,
                "protocol_compatible": protocol_compatible,
                "widget_mode_capable": capable,
                "compaction_cycle_id": cycle_id if active_phase else None,
                "compaction_phase": phase,
                "join_as_compacted": bool(active_phase and capable),
                "renderer_suspension_due_at": self._state.get("renderer_suspension_due_at"),
            }

    async def unregister_window(self, pet_instance_id: str) -> dict[str, Any]:
        async with self._lock:
            window_id = str(pet_instance_id or "").strip()
            await self._unregister_window_locked(window_id)
            return self.snapshot()

    async def _unregister_window_locked(self, window_id: str) -> None:
        self._windows.pop(window_id, None)
        removed_ack = self._window_acks.pop(window_id, None)
        self._renderer_suspension_acks.pop(window_id, None)
        phase = self._state.get("compaction_phase")
        if phase == "compacting":
            expected = set(self._state.get("expected_window_ids") or [])
            if window_id in expected:
                expected.discard(window_id)
                self._state["expected_window_ids"] = sorted(expected)
                self._state["expected_window_count"] = len(expected)
                self._refresh_ack_counts_locked()
                if not expected:
                    await self._fail_compaction_locked("targets-disconnected")
                elif expected.issubset(self._window_acks):
                    await self._settle_expected_acks_locked(expected)
        if removed_ack and removed_ack.get("status") == "compacted":
            self._refresh_owner_counts_locked()
            if self._state["owned_window_count"] == 0 and phase in {
                "compacted",
                "renderer_suspended",
            }:
                self._end_cycle_locked()
        self._state["renderer_suspension_success_count"] = sum(
            self._renderer_suspension_acks.values()
        )

    async def acknowledge_compaction(
        self,
        *,
        compaction_cycle_id: str,
        pet_instance_id: str,
        status: str,
    ) -> dict[str, Any]:
        async with self._lock:
            phase = self._state.get("compaction_phase")
            if (
                compaction_cycle_id != self._state.get("compaction_cycle_id")
                or phase not in {"compacting", "compacted", "renderer_suspended"}
            ):
                return self.snapshot()
            window_id = str(pet_instance_id or "").strip()
            meta = self._windows.get(window_id)
            if not window_id or not meta or meta.get("widget_mode_capable") is not True:
                return self.snapshot()
            normalized = status if status in {
                "compacted",
                "already_compacted",
                "restored",
                "failed",
            } else "failed"
            previous = self._window_acks.get(window_id)
            if normalized == "restored" and (not previous or previous.get("status") != "compacted"):
                return self.snapshot()
            self._window_acks[window_id] = {"status": normalized, "ts": self._time()}
            self._refresh_ack_counts_locked()
            self._refresh_owner_counts_locked()
            if phase == "compacting":
                expected = set(self._state.get("expected_window_ids") or [])
                if expected and expected.issubset(self._window_acks):
                    await self._settle_expected_acks_locked(expected)
            elif normalized == "restored" and self._state["owned_window_count"] == 0:
                self._end_cycle_locked()
            elif normalized == "compacted" and phase == "renderer_suspended":
                await self._broadcaster({
                    "type": "widget_mode_renderer_suspension_requested",
                    "source": WIDGET_MODE_ACTIVITY_SOURCE,
                    "compaction_cycle_id": compaction_cycle_id,
                    "pet_instance_ids": [window_id],
                    "timestamp": self._time(),
                })
            return self.snapshot()

    async def acknowledge_renderer_suspension(
        self,
        *,
        compaction_cycle_id: str,
        pet_instance_id: str,
        success: bool,
    ) -> dict[str, Any]:
        async with self._lock:
            if (
                compaction_cycle_id != self._state.get("compaction_cycle_id")
                or self._state.get("compaction_phase") != "renderer_suspended"
            ):
                return self.snapshot()
            window_id = str(pet_instance_id or "").strip()
            owner = self._window_acks.get(window_id)
            if owner and owner.get("status") == "compacted":
                self._renderer_suspension_acks[window_id] = success is True
                self._state["renderer_suspension_success_count"] = sum(
                    self._renderer_suspension_acks.values()
                )
            return self.snapshot()

    async def mark_user_restore(self, pet_instance_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            if not self._state.get("enabled") or self._state.get("compaction_phase") == "idle":
                return self.snapshot()
            now = self._time()
            suppressed_until = now + USER_RESTORE_COOLDOWN_SECONDS
            previous_persisted_suppressed_until = self._persisted_suppressed_until
            self._persisted_suppressed_until = suppressed_until
            try:
                await self._persist_locked()
            except Exception:
                self._persisted_suppressed_until = previous_persisted_suppressed_until
                raise
            self._state["suppressed_until"] = suppressed_until
            self._state["user_restore_active"] = True
            target = str(pet_instance_id or "").strip()
            for window_id, ack in self._window_acks.items():
                if ack.get("status") == "compacted" and (not target or target == window_id):
                    ack["status"] = "restored"
            self._refresh_owner_counts_locked()
            self._state["last_event"] = {"type": "user_restore", "ts": now}
            if self._state["owned_window_count"] == 0:
                self._end_cycle_locked(preserve_last_event=True)
            return self.snapshot()

    async def ingest_activity_signal(
        self,
        *,
        active: bool,
        available: bool,
        observed_at: float | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            now = self._time() if observed_at is None else float(observed_at)
            if self._settings["activity_response"] == "disabled":
                self._clear_activity_candidate_locked()
                return self.snapshot()
            if not available:
                await self._mark_activity_unavailable_locked(now, increment_error=False)
                return self.snapshot()
            self._state["activity_signal_available"] = True
            self._state["activity_signal_error_count"] = 0
            self._state["activity_last_seen_at"] = now
            if not active:
                self._clear_activity_candidate_locked(keep_last_seen=True)
                return self.snapshot()
            if int(self._state.get("activity_signal_count") or 0) == 0:
                self._state["activity_first_seen_at"] = now
            self._state["activity_signal_count"] = int(self._state.get("activity_signal_count") or 0) + 1
            first_seen = float(self._state.get("activity_first_seen_at") or now)
            confirmed = bool(
                self._state["activity_signal_count"] >= ACTIVITY_CONFIRM_SNAPSHOT_COUNT
                and now - first_seen >= ACTIVITY_CONFIRM_MIN_SECONDS
            )
            self._state["activity_confirmed"] = confirmed
            if confirmed and self._settings["activity_response"] == "compact_on_confirm":
                await self._trigger_compaction_locked(
                    reason="activity-confirmed",
                    duration_seconds=now - first_seen,
                )
            return self.snapshot()

    async def reset_activity_candidate(self, reason: str = "external-reset") -> dict[str, Any]:
        async with self._lock:
            self._clear_activity_candidate_locked()
            self._state["last_event"] = {
                "type": "activity_candidate_reset",
                "reason": reason,
                "ts": self._time(),
            }
            return self.snapshot()

    async def record_activity_signal_error(self) -> dict[str, Any]:
        async with self._lock:
            await self._mark_activity_unavailable_locked(self._time(), increment_error=True)
            return self.snapshot()

    async def trigger_debug_compaction(
        self,
        reason: str = "debug",
        percent: float = 99.0,
    ) -> dict[str, Any]:
        async with self._lock:
            if not self._state.get("enabled"):
                self._state["enabled"] = True
                self._ensure_task_locked()
            self._state["last_resource_reason"] = {
                "metric": reason,
                "percent": percent,
                "diagnostic_only": True,
            }
            await self._trigger_compaction_locked(reason="debug", duration_seconds=0.0)
            return self.snapshot()

    def _ensure_task_locked(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="widget_mode_resource_monitor")

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
            now = self._time()
            await self._maintain_lifecycle_locked(now)
            await self._apply_resource_sample_locked(sample, now)
            return self.snapshot()

    async def _apply_resource_sample_locked(self, sample: ResourceSample, now: float) -> None:
        samples = list(self._state.get("last_resource_samples") or [])
        samples.append(sample)
        self._state["last_resource_samples"] = samples[-LAST_RESOURCE_SAMPLE_LIMIT:]
        reason, percent = self._high_pressure_reason(sample)
        if reason is None:
            self._state["resource_pressure_state"] = "normal"
            self._state["high_resource_sample_count"] = 0
            self._state["last_resource_reason"] = None
        else:
            self._state["resource_pressure_state"] = "high"
            self._state["high_resource_sample_count"] = int(
                self._state.get("high_resource_sample_count") or 0
            ) + 1
            self._state["last_resource_reason"] = {
                "metric": reason,
                "percent": percent,
                "observed_at": now,
            }
            if self._state.get("activity_confirmed"):
                self._state["activity_diagnostic_high_count"] = int(
                    self._state.get("activity_diagnostic_high_count") or 0
                ) + 1
        suppressed_until = self._state.get("suppressed_until")
        if isinstance(suppressed_until, (int, float)) and suppressed_until <= now:
            self._state["suppressed_until"] = None
            self._state["user_restore_active"] = False
            self._persisted_suppressed_until = None
            await self._persist_locked()

    @staticmethod
    def _high_pressure_reason(sample: ResourceSample) -> tuple[str | None, float | None]:
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

    async def _trigger_compaction_locked(self, *, reason: str, duration_seconds: float) -> None:
        now = self._time()
        if self._state.get("compaction_phase") != "idle" or not self._compaction_allowed_locked(now):
            return
        expected_window_ids = self._active_window_ids(now)
        if not expected_window_ids:
            self._state["retry_not_before"] = now + COMPACTION_RETRY_BACKOFF_SECONDS
            self._state["last_event"] = {
                "type": "compaction_skipped",
                "reason": "no-capable-pet-window",
                "ts": now,
            }
            return
        cycle_id = uuid.uuid4().hex
        self._state.update({
            "compaction_cycle_id": cycle_id,
            "compaction_phase": "compacting",
            "compaction_source": WIDGET_MODE_ACTIVITY_SOURCE,
            "compaction_started_at": now,
            "compaction_ack_deadline": now + COMPACTION_ACK_TIMEOUT_SECONDS,
            "expected_window_ids": sorted(expected_window_ids),
            "expected_window_count": len(expected_window_ids),
            "compaction_ack_success_count": 0,
            "compaction_ack_failure_count": 0,
            "owned_window_count": 0,
            "renderer_suspension_due_at": None,
            "renderer_suspension_success_count": 0,
            "user_restore_active": False,
        })
        self._window_acks.clear()
        self._renderer_suspension_acks.clear()
        self._clear_activity_candidate_locked()
        delivered = await self._broadcaster({
            "type": "widget_mode_compaction_requested",
            "source": WIDGET_MODE_ACTIVITY_SOURCE,
            "compaction_cycle_id": cycle_id,
            "reason": reason,
            "duration_seconds": duration_seconds,
            "ack_timeout_seconds": COMPACTION_ACK_TIMEOUT_SECONDS,
            "renderer_suspension_after_seconds": RENDERER_SUSPENSION_DELAY_SECONDS,
            "timestamp": now,
        })
        if delivered <= 0:
            await self._fail_compaction_locked("not-delivered")
            return
        self._state["last_event"] = {
            "type": "compaction_requested",
            "delivered": delivered,
            "ts": now,
        }

    def _compaction_allowed_locked(self, now: float) -> bool:
        if not self._state.get("enabled"):
            return False
        suppressed_until = self._state.get("suppressed_until")
        if isinstance(suppressed_until, (int, float)) and suppressed_until > now:
            return False
        retry_not_before = self._state.get("retry_not_before")
        return not isinstance(retry_not_before, (int, float)) or retry_not_before <= now

    def _clear_activity_candidate_locked(self, *, keep_last_seen: bool = False) -> None:
        last_seen = self._state.get("activity_last_seen_at") if keep_last_seen else None
        self._state["activity_signal_count"] = 0
        self._state["activity_first_seen_at"] = None
        self._state["activity_last_seen_at"] = last_seen
        self._state["activity_confirmed"] = False
        self._state["activity_diagnostic_high_count"] = 0

    async def _mark_activity_unavailable_locked(self, now: float, *, increment_error: bool) -> None:
        was_available = self._state.get("activity_signal_available") is True
        self._state["activity_signal_available"] = False
        if increment_error:
            self._state["activity_signal_error_count"] = int(
                self._state.get("activity_signal_error_count") or 0
            ) + 1
        if was_available:
            self._state["last_event"] = {"type": "activity_signal_unavailable", "ts": now}
            await self._broadcaster({
                "type": "widget_mode_activity_signal_unavailable",
                "source": WIDGET_MODE_ACTIVITY_SOURCE,
                "timestamp": now,
            })

    def _refresh_ack_counts_locked(self) -> None:
        expected = set(self._state.get("expected_window_ids") or [])
        relevant = [self._window_acks[item] for item in expected if item in self._window_acks]
        self._state["compaction_ack_success_count"] = sum(
            1 for ack in relevant if ack.get("status") in {"compacted", "already_compacted"}
        )
        self._state["compaction_ack_failure_count"] = sum(
            1 for ack in relevant if ack.get("status") == "failed"
        )

    def _refresh_owner_counts_locked(self) -> None:
        self._state["owned_window_count"] = sum(
            1 for ack in self._window_acks.values() if ack.get("status") == "compacted"
        )

    async def _settle_expected_acks_locked(self, expected: set[str]) -> None:
        if any(self._window_acks[item]["status"] == "failed" for item in expected):
            await self._fail_compaction_locked("ack-failed")
        else:
            await self._finalize_compaction_locked()

    async def _finalize_compaction_locked(self) -> None:
        now = self._time()
        self._refresh_owner_counts_locked()
        owned = self._state["owned_window_count"]
        if owned == 0:
            self._state["last_event"] = {"type": "no-owner-cycle", "ts": now}
            self._end_cycle_locked(preserve_last_event=True)
            return
        self._state["compaction_phase"] = "compacted"
        self._state["compaction_ack_deadline"] = None
        self._state["renderer_suspension_due_at"] = now + RENDERER_SUSPENSION_DELAY_SECONDS
        self._state["last_event"] = {"type": "compaction_confirmed", "owned": owned, "ts": now}
        await self._broadcaster({
            "type": "widget_mode_compaction_confirmed",
            "source": WIDGET_MODE_ACTIVITY_SOURCE,
            "compaction_cycle_id": self._state.get("compaction_cycle_id"),
            "renderer_suspension_after_seconds": RENDERER_SUSPENSION_DELAY_SECONDS,
            "timestamp": now,
        })

    async def _fail_compaction_locked(self, reason: str) -> None:
        now = self._time()
        cycle_id = self._state.get("compaction_cycle_id")
        self._state["retry_not_before"] = now + COMPACTION_RETRY_BACKOFF_SECONDS
        self._state["last_event"] = {"type": "compaction_failed", "reason": reason, "ts": now}
        if cycle_id:
            await self._broadcaster({
                "type": "widget_mode_compaction_failed",
                "source": WIDGET_MODE_ACTIVITY_SOURCE,
                "compaction_cycle_id": cycle_id,
                "retry_after_seconds": COMPACTION_RETRY_BACKOFF_SECONDS,
                "timestamp": now,
            })
        self._end_cycle_locked(preserve_last_event=True)

    def _end_cycle_locked(self, *, preserve_last_event: bool = False) -> None:
        last_event = self._state.get("last_event")
        for key, value in (
            ("compaction_cycle_id", None),
            ("compaction_phase", "idle"),
            ("compaction_source", None),
            ("compaction_started_at", None),
            ("compaction_ack_deadline", None),
            ("expected_window_count", 0),
            ("compaction_ack_success_count", 0),
            ("compaction_ack_failure_count", 0),
            ("owned_window_count", 0),
            ("renderer_suspension_due_at", None),
            ("renderer_suspension_success_count", 0),
        ):
            self._state[key] = value
        self._state.pop("expected_window_ids", None)
        self._window_acks.clear()
        self._renderer_suspension_acks.clear()
        if preserve_last_event:
            self._state["last_event"] = last_event

    async def _maintain_lifecycle_locked(self, now: float) -> None:
        expired_window_ids = [
            window_id
            for window_id, meta in self._windows.items()
            if now - float(meta.get("last_seen") or 0.0) > WINDOW_REGISTRATION_TTL_SECONDS
        ]
        for window_id in expired_window_ids:
            await self._unregister_window_locked(window_id)

        last_seen = self._state.get("activity_last_seen_at")
        if (
            self._state.get("activity_signal_available") is True
            and isinstance(last_seen, (int, float))
            and now - last_seen > ACTIVITY_SIGNAL_TTL_SECONDS
        ):
            await self._mark_activity_unavailable_locked(now, increment_error=False)
        if self._state.get("compaction_phase") == "compacting":
            deadline = self._state.get("compaction_ack_deadline")
            if isinstance(deadline, (int, float)) and now >= deadline:
                await self._fail_compaction_locked("ack-timeout")
                return
        if self._state.get("compaction_phase") == "compacted":
            due_at = self._state.get("renderer_suspension_due_at")
            if isinstance(due_at, (int, float)) and now >= due_at:
                owned_ids = [
                    window_id
                    for window_id, ack in self._window_acks.items()
                    if ack.get("status") == "compacted"
                ]
                if not owned_ids:
                    self._end_cycle_locked()
                    return
                self._state["compaction_phase"] = "renderer_suspended"
                self._state["renderer_suspension_due_at"] = None
                self._state["last_event"] = {"type": "renderer_suspension_requested", "ts": now}
                await self._broadcaster({
                    "type": "widget_mode_renderer_suspension_requested",
                    "source": WIDGET_MODE_ACTIVITY_SOURCE,
                    "compaction_cycle_id": self._state.get("compaction_cycle_id"),
                    "pet_instance_ids": owned_ids,
                    "timestamp": now,
                })

    async def _broadcast_restore_request_locked(self, reason: str) -> None:
        cycle_id = self._state.get("compaction_cycle_id")
        target_ids = sorted({
            *self._state.get("expected_window_ids", []),
            *[
                window_id
                for window_id, ack in self._window_acks.items()
                if ack.get("status") == "compacted"
            ],
        })
        if cycle_id:
            await self._broadcaster({
                "type": "widget_mode_compaction_restore_requested",
                "source": WIDGET_MODE_ACTIVITY_SOURCE,
                "compaction_cycle_id": cycle_id,
                "pet_instance_ids": target_ids,
                "reason": reason,
                "timestamp": self._time(),
            })
        self._end_cycle_locked()


widget_mode_coordinator = WidgetModeCoordinator(store=WidgetModeSettingsStore())
