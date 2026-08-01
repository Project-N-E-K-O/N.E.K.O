"""Privacy-safe runtime logging for NEKO Live.

Why this exists
---------------
The plugin was effectively invisible in production. Across 281 modules the
runtime held 3 `logger` call sites, all in adapters/ingest, so pipeline,
dispatcher, selection, safety guard and the support scheduler were silent. A
real tester's log bundle covering ~40 minutes of live-room connection contained
zero lines about danmaku received, selected, skipped or dispatched — which made
"the room was quiet" and "events arrived but were silently dropped"
indistinguishable, and made every effect change unverifiable.

This module fixes that without opening a new privacy surface: it consumes the
records the runtime timeline already builds, which are sanitized by
construction (uid is HMAC-hashed, reasons come from an allowlist, stage/status
are truncated). No message text, nickname, raw payload or credential can reach
a log line from here.

Volume is the other constraint. A busy room produces roughly fifteen timeline
records per danmaku, so mirroring every record would be unusable. Instead:

* **rare and important events log immediately** — dispatcher outcomes (already
  rate-limited by the output cooldown) and failures;
* **high-frequency outcomes are counted, not printed**, and flushed as one
  summary line every `_FLUSH_EVERY` records or at a session boundary.

The flush is driven by record count rather than a timer, so this adds no
background task, no periodic wakeup, and no work when the room is silent.
"""

from __future__ import annotations

from typing import Any


# One summary per this many timeline records. Chosen so a busy room produces a
# line every few seconds rather than hundreds, while a quiet room still gets a
# summary at the session boundary.
_FLUSH_EVERY = 60
# Stages whose outcome is worth a line of its own: they show whether the plugin
# handed a request to the host, not whether playback actually completed.
_ALWAYS_LOG_STAGES = frozenset({"dispatcher.push", "result.record"})
# Outcomes that always deserve a line regardless of stage.
_ALWAYS_LOG_STATUSES = frozenset({"failed", "degraded"})
# Reason codes are allowlisted upstream in runtime_timeline; cap how many
# distinct ones a single summary reports so one odd session cannot grow a line
# without bound.
_MAX_SUMMARY_REASONS = 6


class RuntimeLog:
    """Counters plus a throttled summary; holds no event content."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._records = 0
        self._since_flush = 0
        self._stages: dict[str, int] = {}
        self._statuses: dict[str, int] = {}
        self._reasons: dict[str, int] = {}

    # ── ingestion ───────────────────────────────────────────────────────
    def note(self, runtime: Any, item: dict[str, Any]) -> None:
        """Consume one already-sanitized timeline record."""
        stage = str(item.get("stage") or "")
        status = str(item.get("status") or "")
        reason = str(item.get("reason") or "")

        self._records += 1
        self._since_flush += 1
        if stage:
            self._stages[stage] = self._stages.get(stage, 0) + 1
        if status:
            self._statuses[status] = self._statuses.get(status, 0) + 1
        if reason:
            self._reasons[reason] = self._reasons.get(reason, 0) + 1

        if stage in _ALWAYS_LOG_STAGES or status in _ALWAYS_LOG_STATUSES:
            self._emit(
                runtime,
                "live event %s -> %s%s",
                stage or "unknown",
                status or "unknown",
                f" ({reason})" if reason else "",
                level="warning" if status in _ALWAYS_LOG_STATUSES else "info",
            )
        if self._since_flush >= _FLUSH_EVERY:
            self.flush(runtime, "throttle")

    # ── summary ─────────────────────────────────────────────────────────
    def flush(self, runtime: Any, reason: str) -> None:
        """Emit one bounded summary line. Safe to call when nothing happened —
        a session boundary with zero records is itself the answer to
        'did any danmaku arrive at all'."""
        top_reasons = sorted(
            self._reasons.items(), key=lambda item: (-item[1], item[0])
        )[:_MAX_SUMMARY_REASONS]
        self._emit(
            runtime,
            "live summary (%s): records=%d stages=%s statuses=%s reasons=%s",
            str(reason or "unknown")[:32],
            self._records,
            self._compact(self._stages),
            self._compact(self._statuses),
            dict(top_reasons),
        )
        self._records = 0
        self._since_flush = 0
        self._stages = {}
        self._statuses = {}
        self._reasons = {}

    def status(self) -> dict[str, Any]:
        return {
            "runtime_log_records": self._records,
            "runtime_log_pending": self._since_flush,
        }

    # ── internals ───────────────────────────────────────────────────────
    @staticmethod
    def _compact(counts: dict[str, int]) -> dict[str, int]:
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8])

    @staticmethod
    def _emit(runtime: Any, template: str, *args: Any, level: str = "info") -> None:
        logger = getattr(getattr(runtime, "plugin", None), "logger", None)
        if logger is None:
            return
        try:
            getattr(logger, level, logger.info)(template, *args)
        except Exception:
            # Diagnostics must never break the live path. A logger that raises
            # (closed handler, misconfigured adapter) is dropped silently.
            pass
