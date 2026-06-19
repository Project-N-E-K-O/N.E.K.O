from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
import time

from .models import ActivitySnapshot, ActivitySummary


def _empty_summary() -> ActivitySummary:
    return {
        "current_app": "other",
        "current_activity": "",
        "app_duration_seconds": 0.0,
        "recent_apps": [],
        "total_focus_minutes": 0.0,
        "ocr_text_snippet": "",
        "app_distribution": {},
    }


class ActivityBuffer:
    """Deduplicating ring buffer for recent screen activity snapshots."""

    def __init__(self, window_seconds: int = 300, snapshot_interval: int = 5):
        self._lock = asyncio.Lock()
        self.snapshots: deque[ActivitySnapshot] = deque()
        self.window_seconds = max(1, int(window_seconds or 300))
        self.interval = max(1, int(snapshot_interval or 5))
        self._max_entries = max(1, (self.window_seconds // self.interval) + 1)

    async def add(self, snapshot: ActivitySnapshot) -> None:
        async with self._lock:
            cutoff = float(snapshot.timestamp or time.time()) - self.window_seconds
            while self.snapshots and self.snapshots[0].timestamp < cutoff:
                self.snapshots.popleft()

            if self.snapshots:
                last = self.snapshots[-1]
                same_app = last.app_type == snapshot.app_type
                same_activity = last.activity_type == snapshot.activity_type
                last_hash = str(last._thumbnail_hash or "")
                current_hash = str(snapshot._thumbnail_hash or "")
                same_hash = not last_hash or not current_hash or last_hash == current_hash
                stable_content = (
                    not last.has_content_change and not snapshot.has_content_change
                    and same_hash
                )
                if same_app and same_activity and stable_content:
                    self.snapshots[-1] = replace(
                        snapshot,
                        first_seen_at=last.first_seen_at,
                    )
                    return

            self.snapshots.append(snapshot)
            while len(self.snapshots) > self._max_entries:
                self.snapshots.popleft()

    async def summarize(self) -> ActivitySummary:
        async with self._lock:
            if not self.snapshots:
                return _empty_summary()

            current = self.snapshots[-1]
            app_first_seen_at = current.first_seen_at
            for snapshot in reversed(self.snapshots):
                if snapshot.app_type != current.app_type:
                    break
                app_first_seen_at = snapshot.first_seen_at

            app_counts: dict[str, int] = {}
            for snapshot in self.snapshots:
                app_counts[snapshot.app_type] = app_counts.get(snapshot.app_type, 0) + 1
            total = len(self.snapshots)
            app_distribution = {
                app_type: count / total for app_type, count in app_counts.items()
            }
            window_start = current.timestamp - self.window_seconds
            active_seconds = 0.0
            for snapshot in self.snapshots:
                if snapshot.app_type in ("other", "unknown", "private") or snapshot.activity_type in (
                    "idle",
                    "",
                ):
                    continue
                start = max(window_start, snapshot.first_seen_at)
                end = max(snapshot.timestamp, start)
                active_seconds += max(0.0, end - start) + self.interval

            return {
                "current_app": current.app_type,
                "current_activity": current.activity_type,
                "app_duration_seconds": round(
                    max(0.0, current.timestamp - app_first_seen_at), 1
                ),
                "recent_apps": list(
                    dict.fromkeys(snapshot.app_type for snapshot in self.snapshots)
                ),
                "total_focus_minutes": round(active_seconds / 60, 1),
                "ocr_text_snippet": current.ocr_text_snippet,
                "app_distribution": app_distribution,
            }

    async def is_active(self) -> bool:
        async with self._lock:
            now = time.time()
            recent_threshold = now - self.interval * 2
            for snapshot in reversed(self.snapshots):
                if snapshot.timestamp < recent_threshold:
                    return False
                if snapshot.app_type not in ("other", "unknown", "private") and snapshot.activity_type not in (
                    "idle",
                    "",
                ):
                    return True
            return False
