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
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from tests.testbench import config as _tb_config
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
        """Append one record synchronously (used from non-async paths).

        DEBUG-level writes are gated on :data:`config.LOG_DEBUG_ENABLED` —
        when the flag is off, the call is a silent no-op (no disk write,
        no console echo). We read the flag *each call* so runtime toggles
        via ``POST /api/diagnostics/logs/debug`` take effect immediately.
        """
        if level == "DEBUG" and not _tb_config.LOG_DEBUG_ENABLED:
            return
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

    def debug(
        self,
        op: str,
        *,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Shorthand for ``log_sync(level='DEBUG', ...)``.

        Default gated off; flip on from the Logs subpage or with
        ``TESTBENCH_LOG_DEBUG=1`` at boot when diagnosing issues. Use
        for per-request *echo* entries (rendered previews, purely-read
        endpoints, keystroke-rate state changes) that have no standalone
        forensic value once the feature is working.
        """
        self.log_sync(op, level="DEBUG", payload=payload, error=error)

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


# ── Log retention / usage helpers (P19 hotfix 2) ───────────────────

# Matches ``<session_id>-YYYYMMDD.jsonl``. Session id allows letters, digits,
# underscore, dash (matches our sandbox id format + ``_anon``). The strict
# regex is intentional: anything else in ``LOGS_DIR`` (README / backups /
# zip archives) is off-limits to the cleaner.
_LOG_FILE_RE = re.compile(r"^(?P<sid>[A-Za-z0-9_\-]+)-(?P<date>\d{8})\.jsonl$")


def _parse_log_filename(name: str) -> tuple[str, date] | None:
    m = _LOG_FILE_RE.match(name)
    if not m:
        return None
    sid = m.group("sid")
    raw_date = m.group("date")
    try:
        parsed = datetime.strptime(raw_date, "%Y%m%d").date()
    except ValueError:
        return None
    return sid, parsed


def collect_logs_usage(*, now: datetime | None = None) -> dict[str, Any]:
    """Scan :data:`LOGS_DIR` and summarise current disk usage.

    Returns a dict with ``total_files`` / ``total_bytes`` / ``by_date``
    (sorted newest-first, each entry ``{"date": "YYYYMMDD", "files": N,
    "bytes": B}``). Non-jsonl files are ignored in the counts but reported
    under ``other_files`` so the UI can surface stray artefacts.
    """
    _ = now  # reserved for future relative stats (e.g. age buckets)
    logs_dir = _tb_config.LOGS_DIR
    total_files = 0
    total_bytes = 0
    by_date: dict[str, dict[str, int]] = {}
    other_files = 0
    if logs_dir.exists():
        for entry in logs_dir.iterdir():
            if not entry.is_file():
                continue
            parsed = _parse_log_filename(entry.name)
            if parsed is None:
                other_files += 1
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            total_files += 1
            total_bytes += size
            key = parsed[1].strftime("%Y%m%d")
            bucket = by_date.setdefault(key, {"files": 0, "bytes": 0})
            bucket["files"] += 1
            bucket["bytes"] += size
    date_rows = [
        {"date": k, "files": v["files"], "bytes": v["bytes"]}
        for k, v in sorted(by_date.items(), reverse=True)
    ]
    return {
        "total_files": total_files,
        "total_bytes": total_bytes,
        "by_date": date_rows,
        "other_files": other_files,
        "logs_dir": str(logs_dir),
    }


def cleanup_old_logs(
    *,
    retention_days: int | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete JSONL log files older than ``today - retention_days``.

    Parameters
    ----------
    retention_days:
        Override :data:`config.LOG_RETENTION_DAYS`. Must be ``>= 0``.
    now:
        Override the current timestamp (used by tests to make the 'today'
        cutoff deterministic).
    dry_run:
        If True, report what *would* be deleted without actually removing
        files (useful for preview UIs + unit tests).

    Guarantees
    ----------
    * **Today's file is never deleted**, even if ``retention_days == 0`` —
      an active writer may still be appending. The cutoff is a strict
      less-than on the date stamp, not ``<=``.
    * Only files matching ``<sid>-YYYYMMDD.jsonl`` are considered. Any
      README / backup / stray file is left untouched.
    * Directory is scanned under a fresh ``listdir`` each call; callers
      don't need to lock.
    """
    default_days = _tb_config.LOG_RETENTION_DAYS
    effective_days = default_days if retention_days is None else retention_days
    if effective_days < 0:
        effective_days = default_days
    today = (now or datetime.now()).date()
    cutoff = today - timedelta(days=effective_days)

    deleted: list[dict[str, Any]] = []
    kept = 0
    bytes_freed = 0
    scan_errors: list[str] = []

    logs_dir = _tb_config.LOGS_DIR
    if not logs_dir.exists():
        return {
            "deleted": 0,
            "kept": 0,
            "bytes_freed": 0,
            "retention_days": effective_days,
            "cutoff_date": cutoff.strftime("%Y%m%d"),
            "today": today.strftime("%Y%m%d"),
            "deleted_files": [],
            "scan_errors": scan_errors,
            "dry_run": dry_run,
        }

    for entry in logs_dir.iterdir():
        if not entry.is_file():
            continue
        parsed = _parse_log_filename(entry.name)
        if parsed is None:
            continue
        _sid, parsed_date = parsed
        # Today's file always kept regardless of retention policy.
        if parsed_date >= cutoff or parsed_date >= today:
            kept += 1
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            size = 0
        if dry_run:
            deleted.append({"name": entry.name, "bytes": size})
            bytes_freed += size
            continue
        try:
            entry.unlink()
        except OSError as exc:
            scan_errors.append(f"{entry.name}: {exc}")
            continue
        deleted.append({"name": entry.name, "bytes": size})
        bytes_freed += size

    return {
        "deleted": len(deleted),
        "kept": kept,
        "bytes_freed": bytes_freed,
        "retention_days": effective_days,
        "cutoff_date": cutoff.strftime("%Y%m%d"),
        "today": today.strftime("%Y%m%d"),
        "deleted_files": deleted,
        "scan_errors": scan_errors,
        "dry_run": dry_run,
    }
