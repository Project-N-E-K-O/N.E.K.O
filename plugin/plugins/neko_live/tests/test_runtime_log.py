"""Contract tests for privacy-safe runtime logging.

The plugin used to be invisible in production: 3 `logger` call sites across 281
modules, and a real tester's bundle covering ~40 minutes of live-room
connection contained zero lines about danmaku received, selected, skipped or
dispatched. That made "the room was quiet" and "events arrived but were
silently dropped" indistinguishable.

What is under test here is that the fix stays cheap and safe: it reuses the
already-sanitized timeline records, keeps log volume bounded without a timer,
and can never emit viewer text.
"""
from __future__ import annotations

from types import SimpleNamespace

from plugin.plugins.neko_live.core.runtime_log import RuntimeLog
from plugin.plugins.neko_live.core.runtime_timeline import record_timeline


class _Logger:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def _record(self, level: str, template: str, *args: object) -> None:
        try:
            self.lines.append((level, template % args))
        except Exception:
            self.lines.append((level, template))

    def info(self, template: str, *args: object) -> None:
        self._record("info", template, *args)

    def warning(self, template: str, *args: object) -> None:
        self._record("warning", template, *args)

    @property
    def text(self) -> str:
        return "\n".join(line for _level, line in self.lines)


def _runtime(logger: _Logger | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        plugin=SimpleNamespace(logger=logger),
        runtime_log=RuntimeLog(),
    )


def _note(runtime: SimpleNamespace, **item: object) -> None:
    base = {"stage": "pipeline.received", "status": "received", "reason": ""}
    base.update(item)
    runtime.runtime_log.note(runtime, base)


# ── immediate lines ──────────────────────────────────────────────────────

def test_dispatcher_outcome_logs_immediately():
    logger = _Logger()
    runtime = _runtime(logger)

    _note(runtime, stage="dispatcher.push", status="pushed", reason="dispatcher.pushed")

    assert any("dispatcher.push" in line for _l, line in logger.lines)
    assert any("pushed" in line for _l, line in logger.lines)


def test_failures_log_immediately_at_warning():
    logger = _Logger()
    runtime = _runtime(logger)

    _note(runtime, stage="pipeline.route", status="failed", reason="exception")

    assert ("warning", ) != ()
    assert any(level == "warning" for level, _line in logger.lines)


def test_ordinary_records_do_not_log_immediately():
    # A busy room emits ~15 records per danmaku; mirroring each one would make
    # the log unusable.
    logger = _Logger()
    runtime = _runtime(logger)

    for _ in range(5):
        _note(runtime, stage="pipeline.received", status="received")

    assert logger.lines == []


# ── throttled summary ────────────────────────────────────────────────────

def test_summary_flushes_on_volume_without_a_timer():
    logger = _Logger()
    runtime = _runtime(logger)

    for _ in range(70):
        _note(runtime, stage="live_events.select", status="dropped", reason="cooldown")

    summaries = [line for _l, line in logger.lines if "live summary" in line]
    assert len(summaries) == 1
    assert "records=" in summaries[0]
    assert "cooldown" in summaries[0]


def test_summary_is_bounded_with_many_distinct_reasons():
    logger = _Logger()
    runtime = _runtime(logger)

    for index in range(70):
        _note(runtime, stage=f"stage{index}", status=f"st{index}", reason=f"r{index}")

    summary = next(line for _l, line in logger.lines if "live summary" in line)
    # Bounded projection: a long session must not grow the line without limit.
    assert len(summary) < 600


def test_zero_record_flush_still_reports():
    # This is the case the tester's bundle could not answer: a session that
    # received nothing must say so explicitly.
    logger = _Logger()
    runtime = _runtime(logger)

    runtime.runtime_log.flush(runtime, "disconnect")

    assert any("records=0" in line for _l, line in logger.lines)
    assert any("disconnect" in line for _l, line in logger.lines)


def test_reset_clears_counters():
    logger = _Logger()
    runtime = _runtime(logger)
    _note(runtime, stage="pipeline.received", status="received")

    runtime.runtime_log.reset()
    runtime.runtime_log.flush(runtime, "connect")

    assert any("records=0" in line for _l, line in logger.lines)


# ── privacy ──────────────────────────────────────────────────────────────

def test_viewer_text_and_uid_never_reach_the_log():
    logger = _Logger()
    runtime = SimpleNamespace(
        plugin=SimpleNamespace(logger=logger),
        runtime_log=RuntimeLog(),
        runtime_timeline=[],
        _timeline_salt=b"salt",
    )
    event = SimpleNamespace(
        uid="42",
        source="live_danmaku",
        danmaku_text="私密弹幕内容",
        trace_id="",
    )

    # Drive through the real timeline entry point so the test covers the
    # sanitization the log depends on rather than a hand-built record.
    record_timeline(
        runtime,
        event,
        stage="dispatcher.push",
        status="pushed",
        reason="queued_to_neko(target=none)",
    )

    assert "私密弹幕内容" not in logger.text
    assert "42" not in logger.text
    # The allowlisted reason code survives; the raw dispatcher string does not.
    assert "dispatcher.pushed" in logger.text


def test_unknown_reason_codes_are_dropped_not_echoed():
    logger = _Logger()
    runtime = SimpleNamespace(
        plugin=SimpleNamespace(logger=logger),
        runtime_log=RuntimeLog(),
        runtime_timeline=[],
        _timeline_salt=b"salt",
    )
    event = SimpleNamespace(uid="42", source="live_danmaku", trace_id="")

    record_timeline(
        runtime,
        event,
        stage="dispatcher.push",
        status="pushed",
        reason="viewer said something private",
    )

    assert "private" not in logger.text


# ── robustness ───────────────────────────────────────────────────────────

def test_missing_logger_is_a_noop():
    runtime = _runtime(None)
    _note(runtime, stage="dispatcher.push", status="pushed")
    runtime.runtime_log.flush(runtime, "disconnect")


def test_raising_logger_never_breaks_the_live_path():
    class _Broken:
        def info(self, *_args: object) -> None:
            raise RuntimeError("handler closed")

        def warning(self, *_args: object) -> None:
            raise RuntimeError("handler closed")

    runtime = SimpleNamespace(
        plugin=SimpleNamespace(logger=_Broken()),
        runtime_log=RuntimeLog(),
    )
    _note(runtime, stage="dispatcher.push", status="pushed")
    runtime.runtime_log.flush(runtime, "disconnect")


def test_timeline_still_records_when_logging_is_unavailable():
    runtime = SimpleNamespace(runtime_timeline=[], _timeline_salt=b"salt")
    event = SimpleNamespace(uid="42", source="live_danmaku", trace_id="")

    record_timeline(runtime, event, stage="pipeline.received", status="received")

    assert len(runtime.runtime_timeline) == 1


def test_status_projection_is_counts_only():
    runtime = _runtime(_Logger())
    _note(runtime, stage="pipeline.received", status="received")

    status = runtime.runtime_log.status()
    assert status["runtime_log_records"] == 1
    assert set(status) == {"runtime_log_records", "runtime_log_pending"}


# ── the question a real log bundle must be able to answer ────────────────

def test_connect_then_disconnect_with_no_danmaku_is_visible():
    """The exact gap found in a tester's bundle: ~40 minutes of live-room
    connection produced no plugin line at all, so "the room was quiet" and
    "events arrived but were dropped" could not be told apart. A connect
    summary followed by a disconnect summary with records=0 answers it."""
    logger = _Logger()
    runtime = _runtime(logger)

    runtime.runtime_log.flush(runtime, "connect")
    runtime.runtime_log.flush(runtime, "disconnect")

    lines = [line for _l, line in logger.lines if "live summary" in line]
    assert len(lines) == 2
    assert "connect" in lines[0] and "records=0" in lines[0]
    assert "disconnect" in lines[1] and "records=0" in lines[1]


def test_connect_then_traffic_then_disconnect_shows_the_funnel():
    """The opposite case: events did arrive, and the disconnect summary must
    show where they stopped."""
    logger = _Logger()
    runtime = _runtime(logger)
    runtime.runtime_log.flush(runtime, "connect")

    for _ in range(10):
        _note(runtime, stage="live_events.select", status="dropped", reason="cooldown")
    _note(runtime, stage="dispatcher.push", status="pushed", reason="dispatcher.pushed")
    runtime.runtime_log.flush(runtime, "disconnect")

    summary = [line for _l, line in logger.lines if "live summary" in line][-1]
    assert "records=11" in summary
    assert "cooldown" in summary
    assert "dropped" in summary


def test_flush_starts_a_new_incremental_summary_window():
    logger = _Logger()
    runtime = _runtime(logger)
    _note(runtime, stage="event_bus", status="published", reason="event.published")

    runtime.runtime_log.flush(runtime, "first")
    runtime.runtime_log.flush(runtime, "second")

    lines = [line for _level, line in logger.lines if "live summary" in line]
    assert "records=1" in lines[-2]
    assert "records=0" in lines[-1]
