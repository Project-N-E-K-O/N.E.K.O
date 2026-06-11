"""Central enum of all SSE event type strings used by testbench streams.

Background (P24 §14A.3)
------------------------
19 event-type literals were scattered across 4+ files:

* ``chat_runner.py``   — user / wire_built / assistant_start / delta / usage / assistant / done / error / warning (9)
* ``auto_dialog.py``   — start / paused / resumed / error / stopped / simuser_done / turn_begin / turn_done (8)
* ``script_runner.py`` — script_turn_done / script_turn_warnings / script_exhausted (+ error, reused) (3)
* ``chat_router.py``   — error / stopped (top-level wrapper frames; reuse inner names)

The front-end ``composer.js`` / ``auto_banner.js`` / ``sse_client.js``
each had their own ``switch(ev.event) { case 'user': … }`` dispatcher
— any typo / rename meant silent drift.

Policy (deliberate pragmatism, NOT a full migration)
----------------------------------------------------
This module is the **single source of truth** for legal SSE event
names. Callers still use string literals in ``yield { "event": "user" }``
for readability — **we do NOT force ``yield { "event": SseEvent.USER.value }``**
because (a) StrEnum values == their strings anyway, (b) bulk-rewriting
20+ yield sites in chat_runner / auto_dialog incurs more regression
risk than it prevents.

What this gets us:

1. Any new event must be added here first (or the lint below fails).
2. A future ``smoke/p24_lint_drift_smoke.py`` rule (Rule 6, optional)
   can scan ``yield { "event": "X"`` literals and assert they're all
   in :data:`ALL_EVENTS`.
3. Frontend ``static/core/sse_events.js`` mirrors this list so
   dispatcher ``switch`` statements have a matching whitelist.
4. Renaming an event happens in one place (enum value) + a
   project-wide grep-replace — cheaper than finding all 4 dispatchers
   independently.

See also
--------
* ``P24_BLUEPRINT §14A.3`` — the audit that produced this module.
* ``static/core/sse_events.js`` — frontend mirror.
"""
from __future__ import annotations

from enum import StrEnum


class SseEvent(StrEnum):
    """All known SSE ``event`` field values.

    Grouping (for readability; enum order does not matter for semantics):

    * **chat/** core — one user turn + assistant streaming
    * **chat/** meta — diagnostic frames (wire_built, usage)
    * **auto/** — auto-dialog progress (turn_begin / turn_done / paused / ...)
    * **script/** — scripted dialog progress
    * **meta/** — universal across streams (error, warning, stopped, done)
    """

    # ── chat/ (chat_runner.stream_send) ──
    USER = "user"                       # user message appended
    WIRE_BUILT = "wire_built"           # prompt bundle assembled (diagnostic)
    ASSISTANT_START = "assistant_start"  # streaming placeholder created
    DELTA = "delta"                     # token chunk
    USAGE = "usage"                     # token_usage breakdown
    ASSISTANT = "assistant"             # streaming commit (final msg)
    DONE = "done"                       # happy-path end of stream

    # ── auto/ (auto_dialog.run_auto_dialog) ──
    START = "start"                     # auto-dialog session begin
    PAUSED = "paused"                   # paused on user request
    RESUMED = "resumed"                 # resumed from pause
    STOPPED = "stopped"                 # stopped (user abort or finish)
    SIMUSER_DONE = "simuser_done"       # simulated-user turn produced
    TURN_BEGIN = "turn_begin"           # one auto-dialog round starting
    TURN_DONE = "turn_done"             # one auto-dialog round finished

    # ── script/ (script_runner.run_next / run_all) ──
    SCRIPT_TURN_DONE = "script_turn_done"          # one script turn finished
    SCRIPT_TURN_WARNINGS = "script_turn_warnings"  # turn-level soft warnings
    SCRIPT_EXHAUSTED = "script_exhausted"          # script cursor past end

    # ── meta/ (any stream) ──
    ERROR = "error"                     # fatal error (stream ends)
    WARNING = "warning"                 # non-fatal warning (P24 §12.5: ts_coerced)


#: Set of all legal event strings — useful for lint smoke (Rule 6) and
#: for runtime assertions if a dispatcher wants to fail loud on drift.
ALL_EVENTS: frozenset[str] = frozenset(e.value for e in SseEvent)


__all__ = ["SseEvent", "ALL_EVENTS"]
