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

import itertools
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
# ``itertools.count.__next__`` is implemented in C and is atomic under
# CPython's GIL, so we don't need to hold ``_LOCK`` for the increment.
# A naive ``_COUNTER += 1`` is **not** atomic (LOAD/ADD/STORE) and can
# silently lose increments when two threads call :func:`record` concurrently
# (one async route + one sync route running in the threadpool). The uuid4
# suffix already kept the final id globally unique, but a duplicated counter
# would still mis-order ids in the Errors subpage timeline (GH AI-review
# issue #4).
_COUNTER = itertools.count(1)

# P24 Day 10 §14.4 M4: one-shot flag that flips True the first time
# the ring overflows in the current "fill cycle" (from last clear()
# or from cold start). Prevents the overflow notice from flooding
# when the ring is full and every new record evicts an old one.
# Cleared on ``clear()`` or when buffer drops back below MAX_ERRORS.
_RING_FULL_NOTICE_FIRED: bool = False


def _next_id() -> str:
    """Generate a short monotonic id. ``e`` + base16(ms) + counter so
    the id sorts roughly chronologically and is globally unique within
    one process run.
    """
    n = next(_COUNTER)
    ms = int(time.time() * 1000)
    suffix = uuid.uuid4().hex[:4]
    return f"e{ms:x}{n:x}{suffix}"


def _build_ring_full_notice() -> DiagnosticsError:
    """Construct the one-shot "ring overflowed, older entries dropped"
    self-describing entry. Does NOT take the lock — caller already holds
    it inside :func:`_push`.

    Uses the ``DiagnosticsOp`` registry for the op string and message
    prefix so Errors subpage's security filter can co-list it with
    the other maintenance-category events.
    """
    return DiagnosticsError(
        id=_next_id(),
        at=datetime.now().isoformat(timespec="seconds"),
        source="pipeline",
        level="warning",
        type="diagnostics_ring_full",
        message=(
            f"Diagnostics ring buffer reached its {MAX_ERRORS}-entry cap; "
            "older entries are now being evicted."
        ),
        detail={"max_errors": MAX_ERRORS, "cycle_warn_once": True},
    )


def _push(entry: DiagnosticsError) -> None:
    """Append ``entry`` and enforce the ring-buffer cap.

    When the cap is breached for the first time in the current fill
    cycle we:

    1. Log once at WARNING via the python logger (so stderr/live log
       captures the event even if the UI never opens).
    2. Inject a self-describing ``diagnostics_ring_full`` entry so the
       Errors subpage shows a breadcrumb.
    3. Set the ``_RING_FULL_NOTICE_FIRED`` one-shot flag until
       :func:`clear` resets it (or the buffer organically drops below
       the cap).

    The notice is NEVER evicted by the same overflow that created it:
    it is appended *after* the oversized segment is trimmed, guaranteeing
    it survives into the user-visible ring.
    """
    global _RING_FULL_NOTICE_FIRED
    with _LOCK:
        _BUFFER.append(entry)
        overflow = len(_BUFFER) - MAX_ERRORS
        if overflow > 0:
            del _BUFFER[:overflow]
            if not _RING_FULL_NOTICE_FIRED:
                _RING_FULL_NOTICE_FIRED = True
                # Log to python logger *before* injecting the notice
                # so stderr order reflects "real overflow → breadcrumb".
                # We deliberately use a bare python_logger to avoid the
                # re-entrance that would happen if we called back into
                # :func:`record_internal` (which reacquires _LOCK).
                try:
                    import logging
                    logging.getLogger("testbench").warning(
                        "diagnostics_store: ring buffer full "
                        "(%d entries); older entries will be evicted "
                        "from here on. Fires once per fill cycle.",
                        MAX_ERRORS,
                    )
                except Exception:  # logging must never crash the push
                    pass
                notice = _build_ring_full_notice()
                _BUFFER.append(notice)
                # Re-trim if the notice itself put us over cap.
                overflow2 = len(_BUFFER) - MAX_ERRORS
                if overflow2 > 0:
                    del _BUFFER[:overflow2]
        else:
            # Buffer dropped (e.g. after a targeted clear-by-source);
            # allow the next overflow to fire its own notice.
            if _RING_FULL_NOTICE_FIRED and len(_BUFFER) < MAX_ERRORS:
                _RING_FULL_NOTICE_FIRED = False


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
    """Convenience for pipeline modules. ``op`` becomes the ``type``.

    P24 §4.3 H (2026-04-21): ``detail`` is passed through
    :func:`tests.testbench.pipeline.redact.redact_secrets` as a
    defense-in-depth layer. Callers are **already expected** to not
    shove raw ``session.model_config`` or provider auth state into
    ``detail`` — audit confirmed the 5 existing call sites all pass
    curated dicts without credentials. But the ring buffer is read
    by the Errors subpage (visible to anyone who opens the app on the
    local machine, and anyone on the LAN if ``--host 0.0.0.0``), so
    the cost of this safety net is tiny (deepcopy walk of small dicts)
    vs the cost of one day a future caller accidentally including a
    credential field. No-op on ``None`` input.
    """
    if detail is not None:
        # Lazy import to avoid a circular during module init
        from tests.testbench.pipeline.redact import redact_secrets
        detail = redact_secrets(detail)
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
    op_type: Optional[str] = None,
    include_info: bool = False,
) -> dict[str, Any]:
    """Return ``{total, matched, items}`` newest-first after filtering.

    ``op_type`` — P24 §15.2 D / F7 Option B: exact-match on
    ``DiagnosticsError.type``. Accepts a single op string (e.g.
    ``"integrity_check"``) or a comma-separated list (e.g.
    ``"integrity_check,judge_extra_context_override,timestamp_coerced"``).
    Used by the Security filter buttons on the Errors subpage so
    security-relevant internal ops can be isolated without relying on
    fuzzy ``search`` matching (op strings are tokens, but ``search``
    also matches substrings of user messages, which can drift).

    ``include_info`` (P25 hotfix 2026-04-23) — default ``False``: the
    ring buffer accepts level=``info`` writes (e.g. P25's
    ``external_events._record_and_return`` emits
    ``avatar_interaction_simulated`` / ``agent_callback_simulated`` /
    ``proactive_simulated`` at info level for audit replay). Those
    entries should NOT pollute the Errors subpage's default view
    whose semantic is "recent problems". We default-hide them unless
    either (a) the caller asked for them explicitly via
    ``include_info=True``, or (b) the caller passed ``level="info"``
    to pin the filter onto info level — in which case we respect the
    explicit ask and do NOT double-filter. Ordering: ``level=`` runs
    first and is exact-match; ``include_info`` only fires as a
    **default hide** when ``level=`` was not passed. This keeps the
    existing "show me info only" UI path working.

    .. note:: This is a *default-filter* change, not a breaking API
       change. Callers that already scoped by ``level=`` see
       identical behavior. Callers that passed no level (previously
       saw info + warning + error + fatal mixed) now see warning +
       error + fatal by default and must opt-in for info. Audit of
       in-tree callers done at P25 §3 Day 2 — see task transcript.
    """
    with _LOCK:
        snapshot = list(_BUFFER)
    total = len(snapshot)
    # Filter (case-insensitive search on message + type + url).
    items = list(reversed(snapshot))  # newest-first
    if source:
        items = [e for e in items if e.source == source]
    if level:
        items = [e for e in items if e.level == level]
    elif not include_info:
        # Default-hide info-level entries when caller did not pin a
        # specific level. L14-style "coerce must surface": the UI
        # shows a checkbox (``state.filter.include_info``) whose
        # state maps 1-to-1 to this flag so users always know whether
        # info is hidden. Entries with unexpected/unknown levels
        # (shouldn't happen — ``ErrorLevel`` Literal covers them)
        # fall through as "not info" and remain visible.
        items = [e for e in items if e.level != "info"]
    if session_id:
        items = [e for e in items if e.session_id == session_id]
    if op_type:
        allowed = {tok.strip() for tok in op_type.split(",") if tok.strip()}
        if allowed:
            items = [e for e in items if (e.type or "") in allowed]
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

    Clearing also resets the ring-full one-shot flag so the *next* time
    the buffer fills up the overflow notice fires again — otherwise the
    user would clear to reset, hit the cap again, and see no breadcrumb
    telling them evictions had resumed.
    """
    global _RING_FULL_NOTICE_FIRED
    with _LOCK:
        before = len(_BUFFER)
        if source is None:
            _BUFFER.clear()
            _RING_FULL_NOTICE_FIRED = False
            return before
        keep = [e for e in _BUFFER if e.source != source]
        removed = before - len(keep)
        _BUFFER[:] = keep
        if len(_BUFFER) < MAX_ERRORS:
            _RING_FULL_NOTICE_FIRED = False
        return removed


def snapshot_count() -> int:
    """Read-only view of how many errors are currently buffered."""
    with _LOCK:
        return len(_BUFFER)
