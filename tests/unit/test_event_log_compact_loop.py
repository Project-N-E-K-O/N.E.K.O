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
