"""In-memory diagnostics error store (P19).

Complements the per-session JSONL logs by keeping a **short ring buffer**
of the most recent structured errors, regardless of whether a session is
active. Three production-side sinks feed it:

1. :func:`record_from_exception_handler` — FastAPI's global
   ``@app.exception_handler(Exception)``; the exception has already been
   written to ``python_logger`` + session JSONL at that point.
2. :func:`record_from_client` — the browser posts frontend runtime errors
   (window error / unhandledrejection / SSE disconnects / structured
   HTTP 4xx/5xx captured by ``core/api.js``) via
   ``POST /api/diagnostics/errors``. This way a tab crash / navigation
   doesn't lose the stack.
3. :func:`record_internal` — pipeline modules can call this directly when
   they want to surface a warning without raising (e.g. "judger returned
   unparsable JSON, falling back to text"). Equivalent shape to the HTTP
   one, level may be ``warning``.

The ring buffer is intentionally process-local and not persisted:
JSONL logs are the source of truth for "what happened historically",
this store is the source of truth for "what's recent that users need
to look at". A server restart clears it — pair with ``boot_id`` in the
frontend if the Errors subpage needs to detect restarts.

Threading
---------
FastAPI runs handlers on an asyncio loop; sync routers run on a thread.
We lock with ``threading.Lock`` (not ``asyncio.Lock``) so sync and
async callers share a mutex cheaply.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional

#: Hard ceiling for ring buffer; oldest entries drop first.
MAX_ERRORS = 200

ErrorSource = Literal[
    "middleware",  # FastAPI exception_handler
    "pipeline",    # internal pipeline code, not a HTTP-level crash
    "http",        # browser-observed HTTP 4xx/5xx
    "sse",         # browser-observed EventSource error
    "js",          # browser window.onerror
    "promise",     # browser unhandledrejection
    "resource",    # browser resource (img/script/link) load error
    "synthetic",   # test injection
    "unknown",
]

ErrorLevel = Literal["info", "warning", "error", "fatal"]


@dataclass
class DiagnosticsError:
    """One structured error record. JSON-friendly, all fields optional
    except ``id`` / ``at`` / ``source`` / ``type`` / ``message``."""

    id: str
    at: str                         # ISO-8601 seconds precision
    source: ErrorSource
    level: ErrorLevel
    type: str                       # exception class or synthetic tag
    message: str
    # ── context (all optional) ────────────────────────────────────
    session_id: Optional[str] = None
    url: Optional[str] = None
    method: Optional[str] = None
    status: Optional[int] = None
    trace_digest: Optional[str] = None
    user_agent: Optional[str] = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_LOCK = threading.Lock()
_BUFFER: list[DiagnosticsError] = []
_COUNTER = 0


def _next_id() -> str:
    """Generate a short monotonic id. ``e`` + base36(ms) + counter so
    the id sorts roughly chronologically and is globally unique within
    one process run.
    """
    global _COUNTER
    _COUNTER += 1
    ms = int(time.time() * 1000)
    suffix = uuid.uuid4().hex[:4]
    return f"e{ms:x}{_COUNTER:x}{suffix}"


def _push(entry: DiagnosticsError) -> None:
    with _LOCK:
        _BUFFER.append(entry)
        overflow = len(_BUFFER) - MAX_ERRORS
        if overflow > 0:
            del _BUFFER[:overflow]


def record(
    *,
    source: ErrorSource,
    type: str,
    message: str,
    level: ErrorLevel = "error",
    session_id: Optional[str] = None,
    url: Optional[str] = None,
    method: Optional[str] = None,
    status: Optional[int] = None,
    trace_digest: Optional[str] = None,
    user_agent: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> DiagnosticsError:
    """Push one error into the ring buffer and return the stored entry."""
    entry = DiagnosticsError(
        id=_next_id(),
        at=datetime.now().isoformat(timespec="seconds"),
        source=source,
        level=level,
        type=(type or "Error") or "Error",
        message=(message or "").strip() or "(no message)",
        session_id=session_id,
        url=url,
        method=method,
        status=status,
        trace_digest=trace_digest,
        user_agent=user_agent,
        detail=detail or {},
    )
    _push(entry)
    return entry


def record_internal(
    op: str,
    message: str,
    *,
    level: ErrorLevel = "warning",
    session_id: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> DiagnosticsError:
    """Convenience for pipeline modules. ``op`` becomes the ``type``."""
    return record(
        source="pipeline",
        type=op,
        message=message,
        level=level,
        session_id=session_id,
        detail=detail,
    )


def list_errors(
    *,
    limit: int = 50,
    offset: int = 0,
    source: Optional[str] = None,
    level: Optional[str] = None,
    session_id: Optional[str] = None,
    search: Optional[str] = None,
) -> dict[str, Any]:
    """Return ``{total, matched, items}`` newest-first after filtering."""
    with _LOCK:
        snapshot = list(_BUFFER)
    total = len(snapshot)
    # Filter (case-insensitive search on message + type + url).
    items = list(reversed(snapshot))  # newest-first
    if source:
        items = [e for e in items if e.source == source]
    if level:
        items = [e for e in items if e.level == level]
    if session_id:
        items = [e for e in items if e.session_id == session_id]
    if search:
        needle = search.lower()
        items = [
            e for e in items
            if needle in (e.message or "").lower()
            or needle in (e.type or "").lower()
            or needle in (e.url or "").lower()
        ]
    matched = len(items)
    paged = items[offset: offset + limit] if limit > 0 else items[offset:]
    return {
        "total": total,
        "matched": matched,
        "items": [e.to_dict() for e in paged],
    }


def get_by_id(error_id: str) -> Optional[DiagnosticsError]:
    with _LOCK:
        for entry in _BUFFER:
            if entry.id == error_id:
                return entry
    return None


def clear(*, source: Optional[str] = None) -> int:
    """Drop errors (optionally filtered by ``source``) and return the
    number removed. ``source=None`` wipes everything.
    """
    with _LOCK:
        before = len(_BUFFER)
        if source is None:
            _BUFFER.clear()
            return before
        keep = [e for e in _BUFFER if e.source != source]
        removed = before - len(keep)
        _BUFFER[:] = keep
        return removed


def snapshot_count() -> int:
    """Read-only view of how many errors are currently buffered."""
    with _LOCK:
        return len(_BUFFER)
