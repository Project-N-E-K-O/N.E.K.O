"""Call-chain audit: an in-memory ring plus a rolling JSONL file.

Entries hold identifiers, capability names and result codes only, never
arguments, return values, message text or exception text. That is why a
dedicated logger is acceptable here despite the rule that private content
must not go through loggers: nothing private is ever handed to it. It does
not propagate, writes one JSON object per line, and is fed through the
standard QueueHandler / QueueListener pair so callers never block on disk.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import queue
from collections import deque
from datetime import datetime, timezone

_LOGGER_NAME = "neko.plugin_call_audit"
MAX_BYTES = 4 * 1024 * 1024
BACKUP_COUNT = 3

_ring: deque[dict] = deque(maxlen=1000)
_listener: logging.handlers.QueueListener | None = None
dropped = 0


class _DroppingQueueHandler(logging.handlers.QueueHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        # Queue full: drop and count instead of printing a traceback.
        global dropped
        dropped += 1


def audit_directory():
    from plugin.logging_config import resolve_plugin_log_directory

    return resolve_plugin_log_directory(log_subdir="plugin_call_audit")


def _audit_logger() -> logging.Logger:
    global _listener
    log = logging.getLogger(_LOGGER_NAME)
    if _listener is None:
        directory = audit_directory()
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            directory / "call_audit.jsonl", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        records: queue.Queue = queue.Queue(maxsize=2048)
        log.handlers[:] = [_DroppingQueueHandler(records)]
        log.setLevel(logging.INFO)
        log.propagate = False
        _listener = logging.handlers.QueueListener(records, file_handler)
        _listener.start()
    return log


def record(entry: dict) -> None:
    """Keep ``entry`` in the ring and queue it for the file; never raises."""
    item = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    _ring.append(item)
    try:
        # A full queue makes QueueHandler drop the record (handleError).
        _audit_logger().info(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        pass


def entries() -> list[dict]:
    """Newest first."""
    return list(reversed(_ring))


def close() -> None:
    """Flush what is queued and stop the writer (called once at shutdown)."""
    global _listener
    listener, _listener = _listener, None
    if listener is not None:
        listener.stop()
        for handler in listener.handlers:
            handler.close()
