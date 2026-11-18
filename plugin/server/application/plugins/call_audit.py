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
import threading
import time
from collections import deque
from datetime import datetime, timezone

_LOGGER_NAME = "neko.plugin_call_audit"
MAX_BYTES = 4 * 1024 * 1024
BACKUP_COUNT = 3

_ring: deque[dict] = deque(maxlen=1000)
_listener: logging.handlers.QueueListener | None = None
_lock = threading.Lock()
_closed = False
_abandoned: logging.handlers.QueueListener | None = None  # writer a timed-out close left behind
dropped = 0  # records not written (queue full, or after close); may race across threads


class _DroppingQueueHandler(logging.handlers.QueueHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        # Queue full: drop and count instead of printing a traceback.
        global dropped
        dropped += 1


def audit_directory():
    from plugin.logging_config import resolve_plugin_log_directory

    return resolve_plugin_log_directory(log_subdir="plugin_call_audit")


def _audit_logger_locked() -> logging.Logger | None:
    global _listener, _abandoned
    log = logging.getLogger(_LOGGER_NAME)
    if _closed:
        return None
    if _abandoned is not None:
        # A writer left by a timed-out close still owns the file. Ask it to
        # stop (its queue may have room now) and take over once it has exited.
        if _abandoned._thread.is_alive():
            try:
                _abandoned.queue.put_nowait(None)
            except queue.Full:
                pass
            return None
        for handler in _abandoned.handlers:
            handler.close()
        _abandoned = None
    if _listener is not None:
        return log
    directory = audit_directory()
    directory.mkdir(parents=True, exist_ok=True)
    # delay=True: the writer thread opens the file on its first record.
    file_handler = logging.handlers.RotatingFileHandler(
        directory / "call_audit.jsonl", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
        encoding="utf-8", delay=True,
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
    global dropped
    item = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    _ring.append(item)
    try:
        # Check and enqueue (non-blocking) under the lock close() takes, so
        # everything accepted before a close is queued ahead of its sentinel.
        with _lock:
            log = _audit_logger_locked()
            if log is None:
                dropped += 1
            else:
                # A full queue makes QueueHandler drop the record (handleError).
                log.info(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        pass


def entries() -> list[dict]:
    """Newest first."""
    return list(reversed(_ring))


def close(timeout: float = 1.0) -> bool:
    """Flush what is queued within ``timeout`` and stop writing to the file.

    Bounded on purpose (``QueueListener.stop`` waits without a limit and
    fails on a full queue). Later records stay in the ring only. Returns
    whether the flush finished.
    """
    global _listener, _closed, _abandoned
    with _lock:
        _closed = True
        listener, _listener = _listener, None
    if listener is None:
        return True
    deadline = time.monotonic() + timeout
    try:
        listener.queue.put(None, timeout=timeout)  # QueueListener's stop sentinel
        listener._thread.join(max(0.0, deadline - time.monotonic()))
    except queue.Full:
        pass
    if listener._thread.is_alive():
        # The stuck daemon writer still owns the file: no second writer on it
        # until it has exited (see _audit_logger_locked).
        with _lock:
            _abandoned = listener
        return False
    for handler in listener.handlers:
        handler.close()
    return True


def reopen() -> None:
    """Accept file writes again after a shutdown (the server can restart in-process)."""
    global _closed
    with _lock:
        _closed = False
