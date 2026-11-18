"""Call-chain audit: in-memory ring buffer plus a rolling JSONL file.

Entries carry identifiers, capability names and result codes only: never
arguments, return values, message bodies or exception text. Writing never
blocks the caller: entries go through a bounded queue to one daemon thread,
and a full queue drops the entry and counts it. Not routed through the event
bus. See docs/design/plugin-call-chain.md section 3.8.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from plugin.logging_config import get_logger

logger = get_logger("server.plugins.call_audit")

AUDIT_SUBDIR = "plugin_call_audit"
AUDIT_FILENAME = "call_audit.jsonl"
MAX_FILE_BYTES = 4 * 1024 * 1024
BACKUP_COUNT = 3
RING_SIZE = 1000
QUEUE_SIZE = 2048
DEDUP_WINDOW_SECONDS = 60.0
SUMMARY_INTERVAL_SECONDS = 60.0
MAX_TRACKED_KEYS = 4096

_STOP = object()


def default_audit_path() -> Path:
    from plugin.logging_config import resolve_plugin_log_directory

    return resolve_plugin_log_directory(log_subdir=AUDIT_SUBDIR) / AUDIT_FILENAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CallAudit:
    def __init__(
        self,
        *,
        path_resolver: Callable[[], Path] = default_audit_path,
        ring_size: int = RING_SIZE,
        queue_size: int = QUEUE_SIZE,
        max_bytes: int = MAX_FILE_BYTES,
        backups: int = BACKUP_COUNT,
        dedup_window: float = DEDUP_WINDOW_SECONDS,
        summary_interval: float = SUMMARY_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path_resolver = path_resolver
        self._ring: deque[dict] = deque(maxlen=ring_size)
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._max_bytes = max_bytes
        self._backups = backups
        self._dedup_window = dedup_window
        self._summary_interval = summary_interval
        self._clock = clock
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._dedup: dict[tuple, list] = {}
        self._unattributed: dict[tuple[str, str], int] = {}
        self._last_summary = clock()
        self.dropped = 0
        self.write_errors = 0

    # ── recording ────────────────────────────────────────────────

    def record(self, entry: Mapping[str, object]) -> None:
        item = {"ts": _now_iso(), **entry}
        with self._lock:
            self._ring.append(item)
            if self._closed:
                self.dropped += 1
                return
            self._ensure_writer()
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._lock:
                self.dropped += 1

    def record_denial(self, entry: Mapping[str, object], key: tuple) -> None:
        """Write the first identical would-deny per window; count the rest."""
        now = self._clock()
        with self._lock:
            slot = self._dedup.get(key)
            if slot is not None and now - slot[0] < self._dedup_window:
                slot[1] += 1
                return
            suppressed = slot[1] if slot is not None else 0
            if len(self._dedup) >= MAX_TRACKED_KEYS:
                for stale in [k for k, v in self._dedup.items() if now - v[0] >= self._dedup_window]:
                    del self._dedup[stale]
                if len(self._dedup) >= MAX_TRACKED_KEYS:
                    self._dedup.clear()
            self._dedup[key] = [now, 0]
        self.record({**entry, "suppressed": suppressed})

    def count_unattributed(self, plugin_id: str, capability: str, *, mode: str) -> None:
        with self._lock:
            key = (plugin_id, capability)
            if key in self._unattributed or len(self._unattributed) < MAX_TRACKED_KEYS:
                self._unattributed[key] = self._unattributed.get(key, 0) + 1
            due = self._clock() - self._last_summary >= self._summary_interval
        if due:
            self.flush_summaries(mode=mode)

    def flush_summaries(self, *, mode: str) -> None:
        with self._lock:
            counts, self._unattributed = self._unattributed, {}
            self._last_summary = self._clock()
        per_plugin: dict[str, dict[str, int]] = {}
        for (plugin_id, capability), count in counts.items():
            per_plugin.setdefault(plugin_id, {})[capability] = count
        for plugin_id, plugin_counts in sorted(per_plugin.items()):
            self.record({
                "kind": "unattributed_summary",
                "mode": mode,
                "plugin": plugin_id,
                "counts": plugin_counts,
            })

    # ── reading ──────────────────────────────────────────────────

    def entries(self, *, limit: int = 200, filters: Mapping[str, str] | None = None) -> list[dict]:
        with self._lock:
            items = list(self._ring)
        items.reverse()
        wanted = {k: v for k, v in (filters or {}).items() if v}
        out = []
        for item in items:
            if all(item.get(key) == value for key, value in wanted.items()):
                out.append(dict(item))
                if len(out) >= limit:
                    break
        return out

    # ── writer ───────────────────────────────────────────────────

    def _ensure_writer(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            # Daemon: interpreter exit never waits for the audit.
            self._thread = threading.Thread(
                target=self._run, name="plugin-call-audit", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            batch = [] if item is _STOP else [item]
            stop = item is _STOP
            while not stop:
                try:
                    more = self._queue.get_nowait()
                except queue.Empty:
                    break
                if more is _STOP:
                    stop = True
                else:
                    batch.append(more)
            if batch:
                self._write(batch)
            if stop:
                return

    def _write(self, batch: list[dict]) -> None:
        try:
            path = self._path_resolver()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = "".join(
                json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
                for item in batch
            ).encode("utf-8")
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size and size + len(data) > self._max_bytes:
                self._rotate(path)
            with open(path, "ab") as handle:
                handle.write(data)
        except Exception as exc:
            with self._lock:
                self.write_errors += 1
            logger.warning("plugin call audit write failed: error_type={}", type(exc).__name__)

    def _rotate(self, path: Path) -> None:
        oldest = path.with_name(f"{path.name}.{self._backups}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self._backups - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))

    def close(self, timeout: float = 1.0) -> bool:
        """Best-effort flush within ``timeout``; never blocks longer.

        Returns whether everything queued reached the file. Later records stay
        in the ring buffer only.
        """
        with self._lock:
            self._closed = True
            thread = self._thread
        if thread is None or not thread.is_alive():
            return self._queue.empty()
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                self._queue.put(_STOP, timeout=max(0.0, deadline - time.monotonic()))
                break
            except queue.Full:
                if time.monotonic() >= deadline:
                    return False
        thread.join(max(0.0, deadline - time.monotonic()))
        return not thread.is_alive()

    def reopen(self) -> None:
        """Accept writes again after a shutdown (the server can be restarted)."""
        with self._lock:
            self._closed = False

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"dropped": self.dropped, "write_errors": self.write_errors}


__all__ = ["CallAudit", "default_audit_path"]
