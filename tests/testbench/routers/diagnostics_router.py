"""Diagnostics API (P19).

Exposes the process-level error ring buffer (``pipeline.diagnostics_store``)
and the per-session JSONL logs (``tests/testbench_data/logs/``) so the
frontend Diagnostics workspace can render Errors + Logs subpages.

Endpoints
---------
Errors (process-wide ring buffer, cleared on restart):

* ``GET    /api/diagnostics/errors``         — newest-first list with
  pagination + filters (source / level / session_id / search).
* ``GET    /api/diagnostics/errors/{id}``    — one full entry (including
  ``detail`` dict not returned in list shape — though list does include
  ``detail`` for now, this endpoint stays for future ``detail`` elision).
* ``POST   /api/diagnostics/errors``         — frontend-reported error
  ingestion. ``errors_bus`` posts here so tab crashes don't lose state.
* ``DELETE /api/diagnostics/errors``         — clear the ring buffer.

Logs (per-session JSONL, persisted on disk):

* ``GET    /api/diagnostics/logs/sessions``  — list ``{session_id: [YYYYMMDD, ...]}``
  from the log directory (so UI can show a dropdown of "which session / date").
* ``GET    /api/diagnostics/logs``           — tail + filter the JSONL for
  one ``session_id + date`` combo. Returns newest-first, capped.
* ``GET    /api/diagnostics/logs/export``    — raw JSONL file as
  ``text/plain`` (content-disposition attachment).

Design notes
------------
* **No session lock**: diagnostics endpoints must work even when the
  session is busy doing something else (that's precisely when users come
  here). We only read files / in-memory state; never mutate
  ``session_store`` state.
* **Log path resolution**: we accept ``session_id`` exactly as it appears
  in the filename pattern ``<session_id>-<date>.jsonl``; no glob. This
  prevents path traversal by confining reads to ``LOGS_DIR`` via
  ``Path.resolve().is_relative_to()`` checks.
* **Tail vs full**: logs files can grow across a long session. We read the
  file once and apply filters in Python — keyword + level filter always
  run. For perf, ``limit`` defaults to 200 (newest); ``offset`` lets the
  UI page older. Beyond ~5000 rows per day we'd need log rotation, which
  P24's README will call out as a known limit.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from tests.testbench import config as tb_config
from tests.testbench.logger import (
    anon_logger,
    cleanup_old_logs,
    collect_logs_usage,
    python_logger,
)
from tests.testbench.pipeline import diagnostics_store
from tests.testbench.pipeline.diagnostics_ops import all_ops_payload

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


# ── Errors ─────────────────────────────────────────────────────────

_ALLOWED_CLIENT_SOURCES = {"http", "sse", "js", "promise", "resource", "synthetic"}
_ALLOWED_LEVELS = {"info", "warning", "error", "fatal"}


class _ClientErrorReport(BaseModel):
    """Payload for ``POST /api/diagnostics/errors``.

    Matches the shape the browser ``core/errors_bus.js`` already produces
    minus the client-side ``id`` (server regenerates it for uniqueness).
    """

    source: str = Field(default="unknown")
    type: str = Field(default="Error")
    message: str = Field(default="")
    level: str = Field(default="error")
    url: Optional[str] = None
    method: Optional[str] = None
    status: Optional[int] = None
    session_id: Optional[str] = None
    user_agent: Optional[str] = None
    detail: dict[str, Any] = Field(default_factory=dict)


@router.get("/errors")
def list_errors(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    source: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None, min_length=1, max_length=200),
    op_type: Optional[str] = Query(None, max_length=200),
    include_info: bool = Query(False),
) -> dict[str, Any]:
    """Return newest-first errors matching the given filters.

    ``op_type`` — P24 §15.2 D / F7 Option B. Exact-match on the
    ``DiagnosticsError.type`` field; accepts a comma-separated list
    for "any of" semantics. Used by Errors subpage's Security filter
    buttons (integrity_check / judge_extra_context_override /
    timestamp_coerced).

    ``include_info`` — P25 hotfix 2026-04-23. Default ``False``:
    hide level=info entries (e.g. P25 ``avatar_interaction_simulated``
    audit replays) from the "recent problems" default view. Pass
    ``True`` from the UI's "包含 info 级" checkbox to surface them.
    If the caller passes an explicit ``level=`` that filter wins
    and ``include_info`` is ignored (see
    ``diagnostics_store.list_errors`` for precedence details).
    """
    return diagnostics_store.list_errors(
        limit=limit,
        offset=offset,
        source=source,
        level=level,
        session_id=session_id,
        search=search,
        op_type=op_type,
        include_info=include_info,
    )


@router.get("/errors/{error_id}")
def get_error(error_id: str) -> dict[str, Any]:
    entry = diagnostics_store.get_by_id(error_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"error {error_id} not found")
    return entry.to_dict()


@router.get("/ops")
def list_internal_ops() -> dict[str, Any]:
    """Return the catalog of known ``record_internal`` op types.

    Used by Diagnostics → Errors (F7 Security subpage) to populate an
    "op" filter dropdown without the frontend hardcoding a duplicate
    list. Single source of truth is
    :data:`tests.testbench.pipeline.diagnostics_ops.OP_CATALOG`.

    Each item shape::

        {
            "op": "integrity_check",
            "category": "data_integrity",
            "severity": "warning",
            "description": "..."
        }

    See ``P24_BLUEPRINT §4.1.5``.
    """
    return {"ops": all_ops_payload()}


@router.post("/errors")
async def ingest_error(report: _ClientErrorReport, request: Request) -> dict[str, Any]:
    """Accept a frontend-reported error into the ring buffer.

    Silently normalises unknown sources / levels to ``unknown`` / ``error``
    rather than rejecting: an error-reporting endpoint should never itself
    raise because the payload was shaped oddly.
    """
    src = report.source if report.source in _ALLOWED_CLIENT_SOURCES else "unknown"
    lvl = report.level if report.level in _ALLOWED_LEVELS else "error"
    ua = report.user_agent or request.headers.get("user-agent")
    entry = diagnostics_store.record(
        source=src,  # type: ignore[arg-type]
        type=report.type or "Error",
        message=report.message or "",
        level=lvl,  # type: ignore[arg-type]
        session_id=report.session_id,
        url=report.url,
        method=report.method,
        status=report.status,
        user_agent=ua,
        detail=report.detail or {},
    )
    return {"ok": True, "error": entry.to_dict()}


@router.delete("/errors")
def clear_errors(source: Optional[str] = Query(None)) -> dict[str, Any]:
    removed = diagnostics_store.clear(source=source)
    return {"ok": True, "removed": removed, "remaining": diagnostics_store.snapshot_count()}


# ── Logs (per-session JSONL) ───────────────────────────────────────

_LOG_SUFFIX = ".jsonl"


def _safe_log_path(session_id: str, date: str) -> Path:
    """Resolve a session/date combo to a file under ``LOGS_DIR``.

    Raises 400/404 for traversal attempts / missing files rather than
    leaking filesystem internals.
    """
    if not session_id or "/" in session_id or "\\" in session_id or ".." in session_id:
        raise HTTPException(status_code=400, detail="invalid session_id")
    if not date or len(date) != 8 or not date.isdigit():
        raise HTTPException(status_code=400, detail="date must be YYYYMMDD")
    candidate = tb_config.session_log_path(session_id, date).resolve()
    root = tb_config.LOGS_DIR.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path outside logs dir") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"no log file for {session_id} on {date}")
    return candidate


def _scan_log_filenames() -> list[tuple[str, str]]:
    """Return ``[(session_id, date), ...]`` parsed from ``LOGS_DIR``.

    Silently skips malformed filenames. Sort is newest-first by date
    (lex sort works for YYYYMMDD).
    """
    logs_dir = tb_config.LOGS_DIR
    if not logs_dir.exists():
        return []
    entries: list[tuple[str, str]] = []
    for fp in logs_dir.iterdir():
        if not fp.is_file() or fp.suffix != _LOG_SUFFIX:
            continue
        stem = fp.stem  # "<session_id>-YYYYMMDD"
        if len(stem) < 10 or stem[-9] != "-" or not stem[-8:].isdigit():
            continue
        session_id, date = stem[:-9], stem[-8:]
        entries.append((session_id, date))
    entries.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return entries


@router.get("/logs/sessions")
def list_log_sessions() -> dict[str, Any]:
    """Return ``{sessions: [...], all_dates: [...], anon_id: "_anon"}``.

    Each session is reported once with its date list newest-first; the
    overall list is sorted by (newest date descending, session_id).
    ``all_dates`` is the **union** of every session's dates, used by the
    Logs subpage when the user picks "all sessions" mode so the date
    dropdown still has sensible choices.
    """
    by_session: dict[str, list[str]] = {}
    all_dates_set: set[str] = set()
    for sid, date in _scan_log_filenames():
        by_session.setdefault(sid, []).append(date)
        all_dates_set.add(date)
    out: list[dict[str, Any]] = []
    for sid, dates in by_session.items():
        dates_sorted = sorted(set(dates), reverse=True)
        out.append({"session_id": sid, "dates": dates_sorted, "latest": dates_sorted[0]})
    out.sort(key=lambda row: (row["latest"], row["session_id"]), reverse=True)
    all_dates = sorted(all_dates_set, reverse=True)
    return {
        "sessions": out,
        "all_dates": all_dates,
        "anon_id": "_anon",
    }


def _iter_log_records(path: Path) -> list[dict[str, Any]]:
    """Read one JSONL file into a list of records, skipping malformed lines.

    A malformed line is surfaced as a synthetic record so the UI can still
    show "hey this line failed to parse" rather than silently dropping it.
    """
    records: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    records.append(json.loads(raw))
                except (json.JSONDecodeError, ValueError) as exc:
                    records.append({
                        "ts": None,
                        "level": "ERROR",
                        "op": "log.parse_failed",
                        "session_id": None,
                        "payload": {"line": lineno, "raw_sample": raw[:200]},
                        "error": f"{type(exc).__name__}: {exc}",
                    })
    except OSError as exc:
        python_logger().warning("diagnostics: read log %s failed: %s", path, exc)
        raise HTTPException(status_code=500, detail=f"read log failed: {exc}") from exc
    return records


def _match_filters(
    records: list[dict[str, Any]],
    *,
    level: Optional[str],
    op: Optional[str],
    keyword: Optional[str],
) -> list[dict[str, Any]]:
    if level:
        wanted_level = level.upper()
        records = [r for r in records if (r.get("level") or "").upper() == wanted_level]
    if op:
        records = [r for r in records if (r.get("op") or "") == op]
    if keyword:
        needle = keyword.lower()
        filtered = []
        for r in records:
            hay_parts = [
                r.get("op") or "",
                r.get("error") or "",
                json.dumps(r.get("payload") or {}, ensure_ascii=False, default=str),
            ]
            if any(needle in p.lower() for p in hay_parts):
                filtered.append(r)
        records = filtered
    return records


def _load_records_merged(date: str) -> list[dict[str, Any]]:
    """Load & time-sort records from every session file for the given ``date``.

    Used by the "all sessions" mode. Records from different files are
    interleaved by their ``ts`` so the UI sees one unified timeline.
    Records without a valid ``ts`` sink to the bottom via empty-string fallback.
    """
    merged: list[dict[str, Any]] = []
    for sid, d in _scan_log_filenames():
        if d != date:
            continue
        path = tb_config.LOGS_DIR / f"{sid}-{d}{_LOG_SUFFIX}"
        if not path.exists():
            continue
        recs = _iter_log_records(path)
        # Make sure each record has session_id so the UI can badge merged rows.
        for r in recs:
            r.setdefault("session_id", sid)
        merged.extend(recs)
    merged.sort(key=lambda r: str(r.get("ts") or ""))
    return merged


@router.get("/logs")
def tail_logs(
    session_id: str = Query(..., min_length=1),
    date: str = Query(..., min_length=8, max_length=8),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    level: Optional[str] = Query(None),
    op: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None, min_length=1, max_length=200),
) -> dict[str, Any]:
    """Tail a JSONL log file with filters applied. Returns newest-first.

    Pass ``session_id='*'`` (or ``'all'``) to merge every session's file for
    the given date into one unified timeline — handy when the tester just
    wants "show me everything that happened on 20260420" without having to
    click through each session.
    """
    all_mode = session_id in ("*", "all")
    if all_mode:
        records = _load_records_merged(date)
    else:
        path = _safe_log_path(session_id, date)
        records = _iter_log_records(path)
    total = len(records)
    filtered = _match_filters(records, level=level, op=op, keyword=keyword)
    filtered.reverse()  # newest-first
    matched = len(filtered)
    page = filtered[offset: offset + limit]
    # Collect the unique op set across unfiltered records for the UI's
    # op-facet filter — cheap since the file is already in memory.
    op_facet: dict[str, int] = {}
    level_facet: dict[str, int] = {}
    for r in records:
        op_v = r.get("op") or "(none)"
        op_facet[op_v] = op_facet.get(op_v, 0) + 1
        lv = (r.get("level") or "").upper()
        if lv:
            level_facet[lv] = level_facet.get(lv, 0) + 1
    return {
        "session_id": session_id,
        "date": date,
        "total": total,
        "matched": matched,
        "items": page,
        "facets": {
            "op": sorted(op_facet.items(), key=lambda kv: (-kv[1], kv[0]))[:40],
            "level": sorted(level_facet.items(), key=lambda kv: (-kv[1], kv[0])),
        },
    }


@router.get("/logs/export")
def export_log(
    session_id: str = Query(..., min_length=1),
    date: str = Query(..., min_length=8, max_length=8),
) -> FileResponse:
    """Download the raw JSONL file. Useful for grep / offline triage."""
    path = _safe_log_path(session_id, date)
    filename = f"{session_id}-{date}.jsonl"
    return FileResponse(
        path=str(path),
        media_type="application/x-ndjson",
        filename=filename,
        headers={"Cache-Control": "no-store"},
    )


# ── Retention / usage (P19 hotfix 2) ───────────────────────────────

@router.get("/logs/retention")
def get_log_retention() -> dict[str, Any]:
    """Return current retention policy + disk usage for the Logs subpage.

    Shape::

        {
          "retention_days": 14,
          "cleanup_interval_seconds": 43200,
          "logs_dir": ".../tests/testbench_data/logs",
          "usage": {
            "total_files": 68,
            "total_bytes": 854016,
            "by_date": [{"date": "20260420", "files": 8, "bytes": 12345}, ...],
            "other_files": 0
          }
        }
    """
    usage = collect_logs_usage()
    return {
        "retention_days": tb_config.LOG_RETENTION_DAYS,
        "cleanup_interval_seconds": tb_config.LOG_CLEANUP_INTERVAL_SECONDS,
        "logs_dir": usage["logs_dir"],
        "usage": usage,
        "debug_enabled": bool(tb_config.LOG_DEBUG_ENABLED),
    }


class _DebugToggleRequest(BaseModel):
    """Body for ``POST /api/diagnostics/logs/debug``. Just the flag."""

    enabled: bool = Field(..., description="True to start writing DEBUG entries, False to stop.")


@router.get("/logs/debug")
def get_log_debug_state() -> dict[str, Any]:
    """Return whether DEBUG-level entries are currently landing on disk."""
    return {
        "enabled": bool(tb_config.LOG_DEBUG_ENABLED),
        "env_var": "TESTBENCH_LOG_DEBUG",
    }


@router.post("/logs/debug")
def set_log_debug_state(req: _DebugToggleRequest) -> dict[str, Any]:
    """Hot-toggle the DEBUG-on-disk flag without a server restart.

    Applies immediately to subsequent writes (``SessionLogger.log_sync``
    reads the flag fresh each call). Existing on-disk entries are not
    touched. The toggle itself is logged as an INFO op so the audit
    trail shows "from this point on, DEBUG entries may appear".
    """
    previous = bool(tb_config.LOG_DEBUG_ENABLED)
    tb_config.LOG_DEBUG_ENABLED = bool(req.enabled)  # type: ignore[misc]
    anon_logger().log_sync(
        "diagnostics.debug_toggle",
        level="INFO",
        payload={"previous": previous, "enabled": bool(req.enabled)},
    )
    python_logger().info(
        "diagnostics: LOG_DEBUG_ENABLED %s → %s",
        previous, bool(req.enabled),
    )
    return {"ok": True, "previous": previous, "enabled": bool(req.enabled)}


@router.post("/logs/cleanup")
def run_log_cleanup(
    retention_days: Optional[int] = Query(
        None,
        ge=0,
        le=3650,
        description="Override retention for this call only. Today's file is never deleted.",
    ),
    dry_run: bool = Query(False, description="Preview without deleting."),
) -> dict[str, Any]:
    """Force-run the log cleanup and return the result.

    The ``retention_days`` query param overrides :data:`config.LOG_RETENTION_DAYS`
    **for this single call only** — it does not persist. Use the env var
    ``TESTBENCH_LOG_RETENTION_DAYS`` to change the process-wide default.
    """
    result = cleanup_old_logs(
        retention_days=retention_days,
        dry_run=dry_run,
    )
    if result["deleted"] and not dry_run:
        python_logger().info(
            "manual log cleanup: removed %d file(s), freed %d bytes",
            result["deleted"], result["bytes_freed"],
        )
    result["usage"] = collect_logs_usage()
    return result
