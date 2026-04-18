"""Per-session structured JSONL logger for the testbench.

Each session writes a line-delimited JSON stream to
``tests/testbench_data/logs/<session_id>-YYYYMMDD.jsonl``. Each record looks
like:

    {"ts": "2026-04-17T22:30:00", "level": "INFO", "op": "chat.send",
     "session_id": "...", "payload": {...}, "error": null}

The logger is deliberately decoupled from :mod:`utils.logger_config` to
avoid inheriting file rotation paths meant for the main application.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from tests.testbench.config import LOGS_DIR, session_log_path

# Python ``logging`` Logger used for console mirroring and library-style calls.
_PY_LOGGER = logging.getLogger("testbench")
_PY_LOGGER.setLevel(logging.INFO)
if not _PY_LOGGER.handlers:
    _stream_handler = logging.StreamHandler()
    _stream_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] testbench: %(message)s")
    )
    _PY_LOGGER.addHandler(_stream_handler)
    _PY_LOGGER.propagate = False


def python_logger() -> logging.Logger:
    """Return the shared Python Logger for ``testbench`` (console + lib use)."""
    return _PY_LOGGER


class SessionLogger:
    """Append-only JSONL writer scoped to a single session.

    Call :meth:`log` for structured records or :meth:`error` for exceptions.
    All disk writes go through ``asyncio.to_thread`` to avoid blocking the
    event loop; a synchronous :meth:`log_sync` variant is provided for
    places that cannot await (e.g. middleware).
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # ── internals ──────────────────────────────────────────────────

    def _current_path(self) -> Path:
        return session_log_path(self.session_id, datetime.now().strftime("%Y%m%d"))

    @staticmethod
    def _serialize(record: dict[str, Any]) -> str:
        try:
            return json.dumps(record, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            # Never let logging itself crash the request.
            safe = {
                "ts": record.get("ts"),
                "level": record.get("level", "ERROR"),
                "op": "log.serialize_failed",
                "session_id": record.get("session_id"),
                "payload": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
            return json.dumps(safe, ensure_ascii=False)

    def _append(self, record: dict[str, Any]) -> None:
        line = self._serialize(record) + "\n"
        path = self._current_path()
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            _PY_LOGGER.warning(
                "SessionLogger: failed to write %s: %s", path, exc,
            )

    # ── public API ─────────────────────────────────────────────────

    def log_sync(
        self,
        op: str,
        *,
        level: str = "INFO",
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Append one record synchronously (used from non-async paths)."""
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "op": op,
            "session_id": self.session_id,
            "payload": payload or {},
            "error": error,
        }
        self._append(record)
        if level in ("WARNING", "ERROR"):
            getattr(_PY_LOGGER, level.lower())("[%s] %s: %s", self.session_id, op, error or payload)

    async def log(
        self,
        op: str,
        *,
        level: str = "INFO",
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Append one record asynchronously."""
        await asyncio.to_thread(
            self.log_sync, op, level=level, payload=payload, error=error,
        )

    def error(self, op: str, exc: BaseException, *, payload: dict[str, Any] | None = None) -> None:
        """Convenience for exception logging from sync contexts."""
        self.log_sync(op, level="ERROR", payload=payload, error=f"{type(exc).__name__}: {exc}")


# A single anonymous logger used when no session is active yet (e.g. at boot).
_ANON_SESSION_ID = "_anon"
_anon_logger: SessionLogger | None = None


def anon_logger() -> SessionLogger:
    """Return a process-level fallback SessionLogger used before a real
    session exists (boot-time errors, health checks, etc.).
    """
    global _anon_logger
    if _anon_logger is None:
        _anon_logger = SessionLogger(_ANON_SESSION_ID)
    return _anon_logger
