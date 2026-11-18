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
import os
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
# Decisions get their own ring so frequent forward entries cannot evict them.
_decision_ring: deque[dict] = deque(maxlen=500)
_listener: logging.handlers.QueueListener | None = None
_lock = threading.Lock()
_closed = False
_abandoned: logging.handlers.QueueListener | None = None  # writer a timed-out close left behind
dropped = 0  # records not written (queue full, unwritable file, after close); may race


def _count_drop() -> None:
    global dropped
    dropped += 1


class _DroppingQueueHandler(logging.handlers.QueueHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        _count_drop()  # queue full: drop and count, no traceback


class _AuditFileHandler(logging.handlers.RotatingFileHandler):
    """Opens (and creates the directory) lazily, in the writer thread."""

    def _open(self):
        os.makedirs(os.path.dirname(self.baseFilename), exist_ok=True)
        return super()._open()

    def handleError(self, record: logging.LogRecord) -> None:
        _count_drop()  # unwritable file: count it, never echo the record


def audit_directory():
    from plugin.logging_config import resolve_plugin_log_directory

    return resolve_plugin_log_directory(log_subdir="plugin_call_audit")


def _audit_logger_locked() -> logging.Logger | None:
    global _listener, _abandoned
    log = logging.getLogger(_LOGGER_NAME)
    if _closed:
        return None
    if _listener is not None:
        return log
    if _abandoned is not None:
        # A closed writer may still own the file. Ask it to stop (its queue
        # may have room now) and take over only once it has exited.
        if _abandoned._thread.is_alive():
            try:
                _abandoned.queue.put_nowait(None)
            except queue.Full:
                pass
            return None
        for handler in _abandoned.handlers:
            handler.close()
        _abandoned = None
    # delay=True: the writer thread opens the file on its first record.
    file_handler = _AuditFileHandler(
        os.path.join(audit_directory(), "call_audit.jsonl"), maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT, encoding="utf-8", delay=True,
    )
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    records: queue.Queue = queue.Queue(maxsize=2048)
    listener = logging.handlers.QueueListener(records, file_handler)
    listener.start()  # publish only once it runs, so a failed start can retry
    log.handlers[:] = [_DroppingQueueHandler(records)]
    log.setLevel(logging.INFO)
    log.propagate = False
    _listener = listener
    return log


def record(entry: dict) -> None:
    """Keep ``entry`` in the ring and queue it for the file; never raises."""
    global dropped
    item = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    (_decision_ring if entry.get("kind") == "decision" else _ring).append(item)
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
    return sorted([*_ring, *_decision_ring], key=lambda item: item["ts"], reverse=True)


def close(timeout: float = 1.0) -> bool:
    """Flush what is queued within ``timeout`` and stop writing to the file.

    Bounded on purpose (``QueueListener.stop`` waits without a limit and
    fails on a full queue). Later records stay in the ring only. Returns
    whether the writer has exited.
    """
    global _listener, _closed, _abandoned
    with _lock:
        _closed = True
        if _listener is not None:
            # Until it is seen exiting, the closing writer still owns the file.
            _abandoned, _listener = _listener, None
            try:
                _abandoned.queue.put_nowait(None)  # QueueListener's stop sentinel
            except queue.Full:
                pass
        listener = _abandoned
    if listener is None:
        return True
    deadline = time.monotonic() + timeout
    while listener._thread.is_alive() and time.monotonic() < deadline:
        try:
            listener.queue.put(None, timeout=0.05)
        except queue.Full:
            pass
        listener._thread.join(min(0.05, max(0.0, deadline - time.monotonic())))
    if listener._thread.is_alive():
        # logging.shutdown() at exit would wait on the stuck handler's lock.
        with logging._lock:
            logging._handlerList[:] = [
                ref for ref in logging._handlerList if ref() not in listener.handlers
            ]
        return False
    with _lock:
        if _abandoned is listener:
            _abandoned = None
    for handler in listener.handlers:
        handler.close()
    return True


def reopen() -> None:
    """Accept file writes again after a shutdown (the server can restart in-process)."""
    global _closed
    with _lock:
        _closed = False
