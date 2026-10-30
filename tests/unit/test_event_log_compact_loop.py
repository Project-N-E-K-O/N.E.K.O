# -*- coding: utf-8 -*-
"""
The events.ndjson compaction background loop (memory server).

Pinned here: it is a fire-and-forget task that never runs on the startup
path; it honors the character lifecycle lease and always returns it; the
cloud save write fence and storage limited mode skip a whole round (an
unreadable fence counts as up); one character failing never stops the
others; an exception anywhere in a round never kills the loop; and alias
keys from characters.json are skipped.
"""
from __future__ import annotations

import asyncio
import inspect
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.event_log import CompactionResult


def _modules():
    from app import memory_server
    from app.memory_server import evidence_loops, gates, runtime

    return memory_server, evidence_loops, gates, runtime


class _Harness:
    """Fake runtime surface for one round."""

    def __init__(self, tmp_path, names, *, lease_none=(), raising=()):
        self.tmp_path = tmp_path
        self.cm = MagicMock()
        self.cm.memory_dir = str(tmp_path)
        self.cm.aload_characters = AsyncMock(return_value={"猫娘": {n: {} for n in names}})
        for n in names:
            if n.strip() == n:
                os.makedirs(os.path.join(str(tmp_path), n), exist_ok=True)
        self.lease_none = set(lease_none)
        self.raising = set(raising)
        self.begun: list[str] = []
        self.ended: list[tuple[str, object]] = []
        self.compacted: list[str] = []
        self.event_log = MagicMock()
        self.event_log.acompact_applied_history = AsyncMock(side_effect=self._compact)

    async def _compact(self, name, *, needs_journal_copy=None, now=None):
        assert callable(needs_journal_copy)
        self.compacted.append(name)
        if name in self.raising:
            raise RuntimeError("disk on fire")
        return CompactionResult("compacted", dropped_lines=3, dropped_bytes=99)

    def begin(self, name):
        if name in self.lease_none:
            return None
        self.begun.append(name)
        return f"lease-{name}"

    def end(self, name, token):
        self.ended.append((name, token))

    def patches(self, *, fenced=False, fence_raises=False, storage_blocked=False):
        _, evidence_loops, _, runtime = _modules()

        def fence(_cm):
            if fence_raises:
                raise OSError("root_state.json is being replaced")
            return fenced

        return [
            patch.object(runtime, "_config_manager", self.cm),
            patch.object(runtime, "event_log", self.event_log),
            patch.object(runtime, "_begin_character_request", self.begin),
            patch.object(runtime, "_end_character_request", self.end),
            patch.object(runtime, "_memory_storage_blocked_after_init", storage_blocked),
            patch("utils.cloudsave_runtime.is_write_fence_active", fence),
            patch.object(evidence_loops, "_event_log_compact_warned", {}),
        ]


async def _run_round(h: _Harness, **kw):
    _, evidence_loops, _, _ = _modules()
    ps = h.patches(**kw)
    for p in ps:
        p.start()
    try:
        await evidence_loops._acompact_event_logs_once()
    finally:
        for p in reversed(ps):
            p.stop()


@pytest.mark.asyncio
async def test_round_compacts_every_admitted_character_and_returns_every_lease(tmp_path):
    h = _Harness(tmp_path, ["悠怡", "喵酱", "小八"], lease_none={"喵酱"}, raising={"悠怡"})
    await _run_round(h)
    assert h.compacted == ["悠怡", "小八"], "租约为 None 的角色不该被压缩；一个角色抛错不能挡住后面的"
    assert sorted(h.ended) == sorted([("悠怡", "lease-悠怡"), ("小八", "lease-小八")]), \
        "租约必须无论成败都归还"


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [
    {"fenced": True},
    {"fence_raises": True},
    {"storage_blocked": True},
])
async def test_fence_or_storage_block_skips_the_whole_round(tmp_path, kw):
    h = _Harness(tmp_path, ["悠怡", "小八"])
    await _run_round(h, **kw)
    assert h.compacted == []
    assert h.begun == []


@pytest.mark.asyncio
async def test_alias_and_invalid_keys_are_skipped(tmp_path):
    """The journal lock is keyed by the normalized name; a raw key that
    normalizes differently would be a second lock on the same directory."""
    h = _Harness(tmp_path, ["小八 ", "a/b", "悠怡"])
    await _run_round(h)
    assert h.compacted == ["悠怡"]
    assert h.begun == ["悠怡"]


@pytest.mark.asyncio
async def test_missing_character_dir_is_skipped_without_creating_it(tmp_path):
    h = _Harness(tmp_path, ["悠怡"])
    os.rmdir(os.path.join(str(tmp_path), "悠怡"))
    await _run_round(h)
    assert h.compacted == []
    assert h.ended == [("悠怡", "lease-悠怡")]
    assert not os.path.exists(os.path.join(str(tmp_path), "悠怡"))


@pytest.mark.asyncio
async def test_a_failing_round_does_not_stop_the_loop(tmp_path):
    _, evidence_loops, gates, _ = _modules()
    sleeps: list[float] = []
    rounds = {"n": 0}

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 4:
            raise asyncio.CancelledError

    async def flaky_round():
        rounds["n"] += 1
        raise OSError("transient")

    with patch.object(evidence_loops, "_acompact_event_logs_once", flaky_round), \
            patch("app.memory_server.evidence_loops.asyncio.sleep", new=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await evidence_loops._periodic_event_log_compact_loop()

    assert rounds["n"] == 3
    assert sleeps[0] == gates._INITIAL_DELAY_EVENT_LOG_COMPACT == 300
    assert sleeps[1:] == [evidence_loops.EVENT_LOG_COMPACT_INTERVAL_SECONDS] * 3


def test_skip_warnings_are_once_per_process_per_character(caplog):
    _, evidence_loops, _, _ = _modules()
    with patch.object(evidence_loops, "_event_log_compact_warned", {}), \
            patch.object(evidence_loops.logger, "warning") as warn:
        for _ in range(3):
            evidence_loops._log_event_log_compaction("悠怡", CompactionResult("replay_paused"))
            evidence_loops._log_event_log_compaction(
                "悠怡", CompactionResult("nothing_to_drop", pinned=2, pinned_event_ids=("e1", "e2")),
            )
        evidence_loops._log_event_log_compaction("小八", CompactionResult("replay_paused"))
    assert warn.call_count == 3


@pytest.mark.parametrize("outcome", ["write_failed", "journal_unreadable"])
def test_a_journal_that_keeps_failing_is_reported_once(outcome):
    """Persistent IO failures must surface at the default level, once per character."""
    _, evidence_loops, _, _ = _modules()
    with patch.object(evidence_loops, "_event_log_compact_warned", {}), \
            patch.object(evidence_loops.logger, "warning") as warn:
        for _ in range(3):
            evidence_loops._log_event_log_compaction("悠怡", CompactionResult(outcome))
    assert warn.call_count == 1


@pytest.mark.parametrize("outcome", ["busy", "journal_changed", "write_blocked", "not_due"])
def test_transient_or_routine_skips_stay_at_debug(outcome):
    _, evidence_loops, _, _ = _modules()
    with patch.object(evidence_loops, "_event_log_compact_warned", {}), \
            patch.object(evidence_loops.logger, "warning") as warn:
        evidence_loops._log_event_log_compaction("悠怡", CompactionResult(outcome))
    assert warn.call_count == 0


def test_loop_is_spawned_in_the_background_not_awaited_on_startup():
    memory_server, evidence_loops, gates, runtime = _modules()
    src = inspect.getsource(memory_server.ensure_memory_server_runtime_initialized)
    assert "_spawn_background_task(evidence_loops._periodic_event_log_compact_loop())" in src
    assert "await evidence_loops._periodic_event_log_compact_loop" not in src
    assert gates._INITIAL_DELAY_ARCHIVE < gates._INITIAL_DELAY_EVENT_LOG_COMPACT \
        < gates._INITIAL_DELAY_PERSONA_REFINE


def test_facade_reexports_the_new_symbols():
    memory_server, evidence_loops, gates, _ = _modules()
    assert memory_server._INITIAL_DELAY_EVENT_LOG_COMPACT is gates._INITIAL_DELAY_EVENT_LOG_COMPACT
    assert memory_server._periodic_event_log_compact_loop is \
        evidence_loops._periodic_event_log_compact_loop


@pytest.mark.asyncio
async def test_a_real_round_never_logs_payload_text(tmp_path):
    """Counts and event ids only. A real EventLog and the real archive-copy
    check run one round over journals that hit every warning the loop has
    (compacted with a pinned archive event and a pending tail, sentinel not
    found, paused replay), and replay then reads corrupt lines; entry text
    must not reach a single log line on any of those paths."""
    import json
    import logging
    from datetime import datetime, timedelta

    from memory.event_log import (
        EVT_REFLECTION_EVIDENCE_UPDATED,
        EVT_REFLECTION_STATE_CHANGED,
        EventLog,
    )

    _, evidence_loops, _, runtime = _modules()
    mark = "仅正文可见MARK7f3a"
    now = datetime.now()

    def line(event_id, days_ago, *, type_=EVT_REFLECTION_EVIDENCE_UPDATED, payload=None):
        return json.dumps({
            "event_id": event_id, "type": type_,
            "ts": (now - timedelta(days=days_ago)).isoformat(),
            "payload": payload if payload is not None else {"text": mark},
        }, ensure_ascii=False)

    archive = line("arch-1", 50, type_=EVT_REFLECTION_STATE_CHANGED, payload={
        "reflection_id": "r1", "from": "confirmed", "to": "archived",
        "archive_shard_path": "2026-09-01_0123abcd.json",
        "entry_snapshot": {"id": "r1", "text": mark, "archived_at": "2026-09-01T00:00:00"},
    })
    journals = {
        "悠怡": ([line("old", 60), '{"corrupt": "' + mark, archive, line("anchor", 40),
                 line("tail-1", 1), '{"also corrupt": "' + mark], "anchor"),
        "小八": ([line("x1", 60), line("x2", 50)], "ghost"),
        "喵酱": ([line("y1", 60), line("y2", 50)], "y2"),
    }
    for name, (lines, sentinel) in journals.items():
        char_dir = os.path.join(str(tmp_path), name)
        os.makedirs(char_dir)
        with open(os.path.join(char_dir, "events.ndjson"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        with open(os.path.join(char_dir, "events_applied.json"), "w", encoding="utf-8") as f:
            json.dump({"last_applied_event_id": sentinel}, f)

    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    cm.load_root_state.return_value = {"mode": "normal"}
    cm.aload_characters = AsyncMock(return_value={"猫娘": {n: {} for n in journals}})
    with patch("memory.event_log.get_config_manager", return_value=cm):
        log = EventLog()
    log._record_replay_outcome("喵酱", drained=False)

    messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    from memory import event_log as event_log_module

    # The memory-server logger does not propagate, so listen on both
    # module loggers directly.
    loggers = (event_log_module.logger, evidence_loops.logger)
    handler = _Capture(level=logging.DEBUG)
    old_levels = [lg.level for lg in loggers]
    for lg in loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.DEBUG)
    try:
        with patch.object(runtime, "_config_manager", cm), \
                patch.object(runtime, "event_log", log), \
                patch.object(runtime, "_begin_character_request", lambda n: f"lease-{n}"), \
                patch.object(runtime, "_end_character_request", lambda n, t: None), \
                patch.object(runtime, "_memory_storage_blocked_after_init", False), \
                patch("utils.cloudsave_runtime.is_write_fence_active", lambda _cm: False), \
                patch.object(evidence_loops, "_event_log_compact_warned", {}):
            await evidence_loops._acompact_event_logs_once()
        for name in journals:
            log.read_since(name, log.read_sentinel(name))
    finally:
        for lg, level in zip(loggers, old_levels):
            lg.removeHandler(handler)
            lg.setLevel(level)

    text = "\n".join(messages)
    # The round really went down every path this test claims to cover.
    assert "arch-1" in text, "钉住告警没有出现"
    assert "sentinel_not_found" in text
    assert any("喵酱" in m and "暂停" in m for m in messages)
    assert any("悠怡" in m and "删除已应用历史" in m for m in messages)
    assert any("不是有效事件记录" in m for m in messages)
    leaked = [m for m in messages if mark in m]
    assert not leaked, f"日志里出现了条目正文: {leaked[:2]}"
