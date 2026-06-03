from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from typing import Any

from .awareness_buffer import ActivityBuffer
from .models import ActivitySnapshot, ActivitySummary
from .screen_classifier import classify_app_from_title

# OS-layer foreground categories (WindowObservation.category) use a
# different vocabulary than the local OCR/app-title classifier.  This
# table normalises them so ActivityBuffer summaries don't mix two
# taxonomies in app_distribution / current_app.
_OS_CATEGORY_TO_APP_TYPE: dict[str, str] = {
    "gaming": "game",
    "work": "work",
    "entertainment": "entertainment",
    "communication": "communication",
}


class _AwarenessRunnerMixin:
    def start_awareness_loop(self) -> None:
        if self.is_awareness_active():
            return
        if self._ocr_pipeline is None:
            self.logger.warning("awareness loop skipped: OCR pipeline not initialized")
            return
        self._buffer = ActivityBuffer(
            window_seconds=self._cfg.awareness.context_window_minutes * 60,
            snapshot_interval=self._cfg.awareness.snapshot_interval_seconds,
        )
        self._last_awareness_push_at = 0.0
        self._awareness_idle_ticks = 0
        self._consecutive_os_read_failures = 0
        self._awareness_task = asyncio.create_task(self._run_awareness_loop())
        self._awareness_task.add_done_callback(self._on_awareness_task_done)

    def stop_awareness_loop(self) -> None:
        task = self._awareness_task
        self._buffer = None
        self._last_awareness_push_at = 0.0
        self._awareness_idle_ticks = 0
        self._consecutive_os_read_failures = 0
        if task is not None and not task.done():
            task.cancel()

    async def _await_awareness_stop(self) -> None:
        task = self._awareness_task
        self._awareness_task = None
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.logger.warning("study awareness task cleanup failed: {}", exc)

    def is_awareness_active(self) -> bool:
        task = self._awareness_task
        return self._buffer is not None and task is not None and not task.done()

    def _on_awareness_task_done(self, task: asyncio.Task[None]) -> None:
        if self._awareness_task is task:
            self._awareness_task = None
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            self._buffer = None
            self.logger.warning("study awareness task failed: {}", exc)

    async def _run_awareness_loop(self) -> None:
        while self._buffer is not None:
            await self.awareness_tick()
            await asyncio.sleep(self._awareness_sleep_seconds())

    def _awareness_sleep_seconds(self) -> float:
        base = max(1.0, float(self._cfg.awareness.snapshot_interval_seconds))
        if self._awareness_idle_ticks >= 3:
            return max(base, 15.0)
        return base

    async def _read_awareness_activity_snapshot(self, *, now: float):
        if not self._cfg.awareness.os_signals_enabled:
            return None

        tracker = getattr(self, "_activity_tracker", None)
        if tracker is None:
            return None
        return await tracker.get_snapshot(
            now=now,
            include_enrichment=False,
            tick_followups=False,
        )

    async def _read_awareness_os_activity(self, *, now: float):
        activity_snap = None
        foreground_category = None
        os_signals_available = False
        if not self._cfg.awareness.os_signals_enabled:
            return activity_snap, foreground_category, os_signals_available
        try:
            activity_snap = await self._read_awareness_activity_snapshot(now=now)
            os_signals_available = activity_snap is not None and bool(
                getattr(activity_snap, "os_signals_available", True)
            )
            if os_signals_available:
                active_window = getattr(activity_snap, "active_window", None)
                foreground_category = getattr(active_window, "category", None)
        except Exception:
            fails = getattr(self, "_consecutive_os_read_failures", 0) + 1
            self._consecutive_os_read_failures = fails
            if fails <= 3:
                self.logger.warning(
                    "study awareness activity snapshot failed "
                    "(consecutive=%d)",
                    self._consecutive_os_read_failures,
                    exc_info=True,
                )
            else:
                self.logger.error(
                    "study awareness activity snapshot FAILED "
                    "%d consecutive times — OS signals may be permanently "
                    "unavailable; check tracker / system collector health",
                    self._consecutive_os_read_failures,
                    exc_info=True,
                )
        else:
            self._consecutive_os_read_failures = 0
        return activity_snap, foreground_category, os_signals_available

    async def _record_private_awareness_activity(
        self,
        buffer: ActivityBuffer,
        *,
        timestamp: float,
    ) -> None:
        await buffer.add(
            ActivitySnapshot(
                timestamp=timestamp,
                first_seen_at=timestamp,
                app_type="private",
                activity_type="private",
                classify_method="os_signal",
                ocr_text_snippet="",
                window_title="",
            )
        )
        self._awareness_idle_ticks += 1

    async def _capture_awareness_lightweight(self, pipeline: Any):
        try:
            return await asyncio.to_thread(pipeline.capture_lightweight)
        except Exception:
            self.logger.warning("awareness_tick capture failed", exc_info=True)
            return None

    def _classify_awareness_snapshot(
        self,
        snapshot: Any,
        foreground_category: str | None,
    ):
        if not hasattr(snapshot, "app_type"):
            return snapshot
        mapped = _OS_CATEGORY_TO_APP_TYPE.get(foreground_category or "")
        if mapped is not None:
            return replace(snapshot, app_type=mapped)
        if getattr(snapshot, "app_type", "") != "unknown":
            return snapshot
        return replace(
            snapshot,
            app_type=classify_app_from_title(getattr(snapshot, "window_title", "")),
        )

    def _observe_awareness_supervision(
        self,
        snapshot: Any,
        *,
        activity_snap: Any | None,
        os_signals_available: bool,
        foreground_category: str | None,
    ) -> None:
        if self._supervision is None:
            return
        supervision_category = foreground_category
        if supervision_category == "own_app" or not (
            self._cfg.awareness.distraction_detection
        ):
            supervision_category = None
        self._supervision.observe_activity(
            ocr_text=snapshot.ocr_text_snippet,
            sensor_available=snapshot.status in {"ok", "empty"},
            idle_seconds=(
                activity_snap.system_idle_seconds
                if activity_snap is not None and os_signals_available
                else None
            ),
            foreground_category=supervision_category,
        )

    async def _record_awareness_snapshot(
        self,
        buffer: ActivityBuffer,
        snapshot: Any,
    ) -> None:
        activity = snapshot.to_activity_snapshot()
        if activity is None:
            self._awareness_idle_ticks += 1
            return
        await buffer.add(activity)
        if activity.app_type in ("other", "unknown") and activity.activity_type in (
            "idle",
            "",
        ):
            self._awareness_idle_ticks += 1
        else:
            self._awareness_idle_ticks = 0

    async def awareness_tick(self) -> None:
        buffer = self._buffer
        pipeline = self._ocr_pipeline
        if buffer is None or pipeline is None:
            return

        ts = time.time()
        activity_snap, foreground_category, os_signals_available = (
            await self._read_awareness_os_activity(now=ts)
        )
        if activity_snap is not None and (
            getattr(activity_snap, "state", "") == "private"
            or foreground_category == "private"
        ):
            await self._record_private_awareness_activity(buffer, timestamp=ts)
            return

        snapshot = await self._capture_awareness_lightweight(pipeline)
        if snapshot is None or snapshot.status == "capture_failed":
            self._awareness_idle_ticks += 1
            return

        snapshot = self._classify_awareness_snapshot(snapshot, foreground_category)
        self._observe_awareness_supervision(
            snapshot,
            activity_snap=activity_snap,
            os_signals_available=os_signals_available,
            foreground_category=foreground_category,
        )
        await self._record_awareness_snapshot(buffer, snapshot)

        if self._should_push_context():
            summary = await buffer.summarize()
            await self._push_awareness_context(summary)

    def _should_push_context(self) -> bool:
        if self._cfg.awareness.push_to_llm_mode == "blind":
            return False
        interval = self._cfg.awareness.push_to_llm_interval_seconds
        now = time.monotonic()
        return now - self._last_awareness_push_at >= interval

    async def _push_awareness_context(self, summary: ActivitySummary) -> None:
        mode = self._cfg.awareness.push_to_llm_mode
        self._last_awareness_push_at = time.monotonic()
        self.push_message(
            visibility=[],
            ai_behavior="read" if mode == "read" else "respond",
            parts=[
                {
                    "type": "text",
                    "text": (
                        "[环境感知] "
                        + json.dumps(self._summary_for_llm(summary), ensure_ascii=False)
                    ),
                }
            ],
            source="awareness",
            priority=0,
        )

    @staticmethod
    def _summary_for_llm(
        summary: ActivitySummary,
    ) -> dict[str, str | float | list[str]]:
        return {
            key: value
            for key, value in summary.items()
            if key != "app_distribution"
        }
